#include "xyf_job_journal.hpp"
#include <cassert>
#include <map>
#include <string>
#include <stdexcept>
#include <cstring>
#include <cstdio>
namespace J=xyf::journal;
struct Cut {};
struct Io {
    std::map<std::string,std::string> files;
    struct F { std::string name; unsigned at; bool write; };
    std::map<int,F> handles;
    int next=1,calls=0,cut=0;
    void tick(){if(++calls==cut)throw Cut();}
    bool lost()const{return false;}
    int open(const char* p,bool w){tick();if(!w&&!files.count(p))return -2;if(w)files[p]="";int fd=next++;handles[fd]={p,0,w};return fd;}
    int read(int fd,uint8_t* out,uint32_t n){tick();auto& f=handles.at(fd);auto& s=files.at(f.name);n=std::min<unsigned>(n,s.size()-f.at);memcpy(out,s.data()+f.at,n);f.at+=n;return n;}
    int write(int fd,const uint8_t* in,uint32_t n){tick();auto& f=handles.at(fd);n=std::min<unsigned>(n,31);files[f.name].append(reinterpret_cast<const char*>(in),n);return n;}
    int close(int fd){tick();handles.erase(fd);return 0;}
    int unlink(const char* p){tick();return files.erase(p)?0:1;}
    int rename(const char* from,const char* to){tick();if(!files.count(from)||files.count(to))return -1;files[to]=files[from];files.erase(from);return 0;}
};
int write(Io& io,const J::Record& r){J::File f;J::begin(f);f.n=J::format(r,f.text);int out=0;for(int guard=0;!out&&guard<1000;++guard)out=J::write_step(f,io);return out;}
int read(Io& io,J::Record& r){J::File f;J::begin(f);int out=0;for(int guard=0;!out&&guard<100;++guard)out=J::read_step(f,io);return out==1?J::parse(f.text,f.n,r):out;}
int main(){
    J::Record a={};J::copy(a.target,"mods-a");J::copy(a.previous,"stock");a.write_default=1;
    for(unsigned i=0;i!=32;++i){a.to.info[i]=uint8_t(i);a.to.list[i]=uint8_t(i+1);a.from.info[i]=uint8_t(i+2);a.from.list[i]=uint8_t(i+3);}
    char text[J::capacity];unsigned n=J::format(a,text);J::Record r;assert(J::parse(text,n,r)==1);text[8]='2';assert(J::parse(text,n,r)==-2);text[8]='1';text[n-3]^=1;assert(J::parse(text,n,r)==-1);
    // Every IO boundary of initial publication and replacement (undo); old or new durable intent only.
    Io complete;assert(write(complete,a)==1);int steps=complete.calls;
    for(int at=1;at<=steps+1;++at){Io io;io.cut=at;try{write(io,a);}catch(Cut&){}io.cut=0;io.handles.clear();int found=read(io,r);assert(found==2||(found==1&&r.undo==0));}
    J::Record undo=a;undo.undo=1;Io updated=complete;updated.calls=0;assert(write(updated,undo)==1);steps=updated.calls;
    for(int at=1;at<=steps+1;++at){Io io=complete;io.calls=0;io.cut=at;try{write(io,undo);}catch(Cut&){}io.cut=0;io.handles.clear();assert(read(io,r)==1);assert(r.undo==0||r.undo==1);assert(write(io,undo)==1);assert(read(io,r)==1&&r.undo==1);}
    Io io;J::Source source;assert(J::source_begin(source,"mods-a"));int out=0;while(!out)out=J::source_step(source,io);assert(out<0); // missing is never official
    io.files["/yaffs2/xy-forever/firmware/mods-a/info.txt"]="name=Test\ngeneration=test\n";
    J::source_begin(source,"mods-a");out=0;while(!out)out=J::source_step(source,io);assert(out<0); // assembled list required
    io.files["/yaffs2/xy-forever/firmware/mods-a/files.sha256"]="bytes\n";
    J::source_begin(source,"mods-a");out=0;while(!out)out=J::source_step(source,io);assert(out==1);
    J::Identity first=source.identity;io.files["/yaffs2/xy-forever/firmware/mods-a/files.sha256"]="changed\n";
    J::source_begin(source,"mods-a");out=0;while(!out)out=J::source_step(source,io);assert(out==1&&!J::equal(first,source.identity));
    puts("journal publication/source checks passed");
}
