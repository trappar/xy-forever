#ifndef XYF_JOB_JOURNAL_HPP
#define XYF_JOB_JOURNAL_HPP
#include "xyf_store.hpp"
#include "xyf_crc32.hpp"
#include "xyf_package_record.hpp"
#include "../native/hash.hpp"
// Durable intent for a content job. Canonical text + CRC; published by rename only after read-back.
// A .old is authoritative when job.txt is absent. .part is never authoritative.
namespace xyf { namespace journal {
static const char path[] = "/yaffs2/xy-forever/job.txt";
static const char old[] = "/yaffs2/xy-forever/job.old";
static const char part[] = "/yaffs2/xy-forever/job.txt.part";
static const uint32_t capacity = 512;
struct Identity { uint8_t info[32], list[32]; };
struct Record { char target[32], previous[32]; Identity to, from; uint32_t write_default, undo; };
static inline void copy(char* to, const char* from) { while ((*to++ = *from++)) {} }
static inline bool same(const char* a, const char* b) { while (*a && *a == *b) { ++a; ++b; } return *a == *b; }
static inline void hex(char* out, const uint8_t* data, uint32_t n) {
    static const char digits[] = "0123456789abcdef";
    for (uint32_t i = 0; i != n; ++i) { out[2*i] = digits[data[i] >> 4]; out[2*i+1] = digits[data[i] & 15]; }
    out[2*n] = 0;
}
static inline int digit(char c) { return c >= '0' && c <= '9' ? c-'0' : c >= 'a' && c <= 'f' ? c-'a'+10 : -1; }
static inline bool unhex(const char* t, uint8_t* out, uint32_t n) {
    for (uint32_t i = 0; i != n; ++i) { const int a = digit(t[2*i]), b = digit(t[2*i+1]); if (a < 0 || b < 0) return false; out[i] = uint8_t(a*16+b); }
    return true;
}
static inline bool equal(const Identity& a, const Identity& b) {
    uint8_t d = 0; for (uint32_t i = 0; i != 32; ++i) d |= (a.info[i]^b.info[i]) | (a.list[i]^b.list[i]); return !d;
}
static inline uint32_t append(char* t, uint32_t n, const char* s) { while (*s && n+1 < capacity) t[n++] = *s++; t[n] = 0; return n; }
static inline uint32_t format(const Record& r, char* t) {
    uint32_t n = append(t, 0, "version=1\ntarget="); n = append(t,n,r.target); n=append(t,n,"\nprevious="); n=append(t,n,r.previous);
    n=append(t,n,r.write_default ? "\ndefault=1" : "\ndefault=0"); n=append(t,n,r.undo ? "\nundo=1\n" : "\nundo=0\n");
    const uint8_t* values[4] = {r.to.info,r.to.list,r.from.info,r.from.list};
    const char* keys[4] = {"target_info=","target_list=","previous_info=","previous_list="}; char h[65];
    for (uint32_t i=0;i!=4;++i) { n=append(t,n,keys[i]); hex(h,values[i],32); n=append(t,n,h); n=append(t,n,"\n"); }
    const uint32_t crc=crc::update(0,reinterpret_cast<const uint8_t*>(t),n);
    uint8_t b[4]; for (uint32_t i=0;i!=4;++i) b[i]=uint8_t(crc >> ((3-i)*8)); hex(h,b,4);
    n=append(t,n,"crc32="); n=append(t,n,h); return append(t,n,"\n");
}
// 1 valid, -2 newer version, -1 malformed. Exact canonical form bounds all fields and rejects duplicates.
static inline int parse(const char* t, uint32_t n, Record& r) {
    if (n < 10 || n >= capacity) return -1;
    const char prefix[]="version="; for (uint32_t i=0;i!=8;++i) if(t[i]!=prefix[i]) return -1;
    uint32_t version=0, v=8;
    while(v<n && t[v]>='0' && t[v]<='9') { if(version>100000u)return -2; version=version*10+uint32_t(t[v++]-'0'); }
    if(v<n && t[v]=='\n' && version>1) return -2;
    for(uint32_t i=0;i!=sizeof(r);++i) reinterpret_cast<uint8_t*>(&r)[i]=0;
    const char* keys[10]={"version=","target=","previous=","default=","undo=","target_info=","target_list=","previous_info=","previous_list=","crc32="};
    uint32_t at=0;
    for(uint32_t k=0;k!=10;++k) {
        for(const char* p=keys[k];*p;++p) if(at==n || t[at++]!=*p) return -1;
        const uint32_t start=at; while(at<n && t[at]!='\n') ++at; if(at==n) return -1;
        const uint32_t bytes=at-start;
        if(k==0 && (bytes!=1 || t[start]!='1')) return -1;
        if(k==1 || k==2) { if(!bytes || bytes>31) return -1; char* p=k==1?r.target:r.previous; for(uint32_t i=0;i!=bytes;++i)p[i]=t[start+i]; p[bytes]=0; if(!store::valid_id(p))return -1; }
        if(k==3 || k==4) { if(bytes!=1 || (t[start]!='0' && t[start]!='1'))return -1; (k==3?r.write_default:r.undo)=uint32_t(t[start]-'0'); }
        if(k>=5 && k<=8) { uint8_t* v=k==5?r.to.info:k==6?r.to.list:k==7?r.from.info:r.from.list; if(bytes!=64 || !unhex(t+start,v,32))return -1; }
        ++at;
    }
    char expected[capacity]; const uint32_t want=format(r,expected); if(want!=n)return -1;
    for(uint32_t i=0;i!=n;++i)if(t[i]!=expected[i])return -1;
    return 1;
}
// One source identity: info bytes and exact files.sha256 bytes (absent only for legacy/official entries).
// Never trusts a missing/empty manifest as an official target. One bounded IO per step.
struct Source {
    Identity identity; xyf_core::hash::Sha256 sha; uint32_t stage, which, bytes, mods, legacy;
    int fd; char folder[240], name[240]; uint8_t buffer[xyf_core::json::document_bytes];
    package_record::Workspace record; // owned content slot, never the firmware stack
};
static inline bool source_begin(Source& s,const char* id) {
    for(uint32_t i=0;i!=sizeof(s);++i)reinterpret_cast<uint8_t*>(&s)[i]=0;
    s.fd=-1; if(!store::valid_id(id))return false;
    copy(s.folder,"/yaffs2/xy-forever/firmware/"); uint32_t n=0;while(s.folder[n])++n;copy(s.folder+n,id);return true;
}
template<class Io> int source_step(Source& s,Io& io) {
    if(s.stage==0) {
        copy(s.name,s.folder); uint32_t n=0;while(s.name[n])++n;
        copy(s.name+n,s.which?"/files.sha256":s.legacy?"/info.txt":"/manifest.json");
        s.fd=io.open(s.name,false);
        if(s.fd==-2 && !s.which && !s.legacy){s.legacy=1;return 0;}
        if(s.fd==-2 && s.which && !s.mods)return 1;
        if(s.fd<0)return -1;
        s.sha.reset();s.bytes=0;s.stage=1;return 0;
    }
    if(s.stage==1) {
        const uint32_t limit=s.which?0x100000u:s.legacy?store::max_text:xyf_core::json::document_bytes;
        const uint32_t room=limit-s.bytes, want=room<1024?room:1024;
        uint8_t extra; uint8_t* out=s.which?s.buffer:s.buffer+s.bytes;
        const int n=io.read(s.fd,want?out:&extra,want?want:1);
        if(n<0 || uint32_t(n)>want) { io.close(s.fd);s.fd=-1;return -1; }
        if(n) {s.sha.update(out,uint32_t(n));s.bytes+=uint32_t(n);return 0;}
        s.stage=2;return 0;
    }
    const int c=io.close(s.fd);s.fd=-1;if(c)return -1;
    xyf_core::hash::Digest d;s.sha.finish(d);uint8_t* to=s.which?s.identity.list:s.identity.info;
    for(uint32_t i=0;i!=32;++i)to[i]=d.bytes[i];
    // The stored document is hashed to completion before its semantic view is made.
    if(!s.which) {
        store::Info info={};
        const bool valid=s.legacy?store::parse_info(reinterpret_cast<const char*>(s.buffer),s.bytes,info):
            package_record::info(reinterpret_cast<const char*>(s.buffer),s.bytes,s.record,info);
        if(!s.bytes || !valid)return -1;
        s.mods=info.has_generation; s.which=1;s.stage=0;return 0;
    }
    return 1;
}
// Small-file reader/writer for intent. A complete read-back precedes every publication rename.
struct File { uint32_t stage,n,at,had; int fd; char text[capacity]; uint8_t buffer[capacity]; };
static inline void begin(File& f) { f.stage=f.n=f.at=f.had=0;f.fd=-1; }
template<class Io> int read_step(File& f,Io& io) {
    if(f.stage==0) { f.fd=io.open(f.had?old:path,false);if(f.fd==-2) {if(!f.had){f.had=1;return 0;}return 2;}if(f.fd<0)return -1;f.stage=1;return 0; }
    if(f.stage==1) {const int n=io.read(f.fd,f.buffer,capacity);if(n<0 || uint32_t(n)>=capacity-f.n){io.close(f.fd);f.fd=-1;return -1;}if(n){for(int i=0;i!=n;++i)f.text[f.n++]=char(f.buffer[i]);return 0;}f.stage=2;return 0;}
    const int c=io.close(f.fd);f.fd=-1;return c?-1:1;
}
template<class Io> int write_step(File& f,Io& io) {
    switch(f.stage) {
    case 0:f.fd=io.open(part,true);if(f.fd<0)return -1;f.at=0;f.stage=1;return 0;
    case 1:{const int n=io.write(f.fd,reinterpret_cast<const uint8_t*>(f.text+f.at),f.n-f.at);if(n<=0 || uint32_t(n)>f.n-f.at)return -1;f.at+=uint32_t(n);if(f.at==f.n)f.stage=2;return 0;}
    case 2:if(io.close(f.fd))return -1;f.fd=-1;f.stage=3;return 0;
    case 3:f.fd=io.open(part,false);if(f.fd<0)return -1;f.at=0;f.stage=4;return 0;
    case 4:{const int n=io.read(f.fd,f.buffer,capacity);if(n<0 || uint32_t(n)>f.n-f.at)return -1;if(!n){if(f.at!=f.n)return -1;f.stage=5;return 0;}for(int i=0;i!=n;++i)if(f.buffer[i]!=uint8_t(f.text[f.at+i]))return -1;f.at+=uint32_t(n);return 0;}
    case 5:if(io.close(f.fd))return -1;f.fd=-1;f.stage=6;return 0;
    case 6:f.fd=io.open(path,false);if(f.fd==-2){f.stage=9;return 0;}if(f.fd<0)return -1;f.stage=7;return 0;
    case 7:if(io.close(f.fd))return -1;f.fd=-1;f.stage=8;return 0;
    case 8:if(io.unlink(old)<0)return -1;f.stage=11;return 0;
    case 11:if(io.rename(path,old)<0)return -1;f.stage=9;return 0;
    case 9:if(io.rename(part,path)<0)return -1;f.stage=10;return 0;
    default:return io.unlink(old)<0?-1:1;
    }
}
} }
#endif
