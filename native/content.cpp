#include "content.hpp"
namespace xyf_core { namespace content {
namespace {
bool same(const char* a,const char* b) {
    for (U32 i=0;i<257;++i) {if (a[i]!=b[i]) return false;if (!a[i]) return true;}
    return false;
}
bool allowed(const package::Item& item) {
    if (item.destination.role!=package::Role::content || item.size>64u*1024u*1024u) return false;
    char source[280];const char prefix[]="firmware/blackfin/";U32 offset=0;
    for (U32 i=0;i<sizeof(prefix)-1;++i) source[offset++]=prefix[i];
    U32 n=0;while (n<257 && item.destination.relative[n]) {
        if (offset>=sizeof(source)-1) return false;
        source[offset++]=item.destination.relative[n++];
    }
    if (n==257) return false;
    source[offset]=0;package::Destination result;
    return package::classify(source,offset,result)==package::Status::ok &&
           result.role==package::Role::content && same(result.relative,item.destination.relative);
}
const package::Item* find(const package::Item* items,U32 count,const char* path) {
    for (U32 i=0;i<count;++i)
        if (items[i].destination.role==package::Role::content && same(items[i].destination.relative,path)) return &items[i];
    return 0;
}
bool hash_file(const IO& io,const char* path,U32 size,bool staged,hash::Digest& digest) {
    Byte bytes[512];hash::Sha256 hasher;
    for (U32 at=0;at<size;) {
        const U32 count=size-at<sizeof(bytes)?size-at:sizeof(bytes);
        if (!(staged?io.read_staged(io.context,path,at,bytes,count):io.read_target(io.context,path,at,bytes,count)) ||
            !hasher.update(bytes,count)) return false;
        at+=count;
    }
    return hasher.finish(digest);
}
Status current(const IO& io,const char* path,bool& exists,U32& size,hash::Digest& digest) {
    if (!io.target_size(io.context,path,exists,size)) return Status::io;
    if (!exists) return Status::ok;
    if (size>64u*1024u*1024u) return Status::modified;
    return hash_file(io,path,size,false,digest)?Status::ok:Status::io;
}
bool matches(const package::Item* item,U32 size,const hash::Digest& digest) {
    return item && size==item->size && hash::equal(digest,item->digest);
}
bool callbacks(const IO& io) {return io.target_size && io.read_target && io.read_staged && io.replace && io.remove_matching;}
Status validate(const package::Item* items,U32 count) {
    for (U32 i=0;i<count;++i) if (items[i].destination.role==package::Role::content) {
        if (!allowed(items[i])) return Status::policy;
        for (U32 j=0;j<i;++j) if (items[j].destination.role==package::Role::content &&
            same(items[i].destination.relative,items[j].destination.relative)) return Status::policy;
    }
    return Status::ok;
}
}
Status plan(const package::Manifest& desired,const package::Item* previous,U32 previous_count,
            bool remove_old,const IO& io,Operation* operations,U32 capacity,Plan& result) {
    result.operations=0;result.count=0;
    if (!desired.items || !desired.count || desired.count>4096 || (previous_count && !previous) ||
        previous_count>4096 || !callbacks(io) || (!operations && capacity)) return Status::argument;
    if (validate(desired.items,desired.count)!=Status::ok || validate(previous,previous_count)!=Status::ok) return Status::policy;
    U32 used=0;
    for (U32 i=0;i<desired.count;++i) {
        const package::Item& item=desired.items[i];if (item.destination.role!=package::Role::content) continue;
        if (used==capacity) return Status::capacity;
        const package::Item* old=find(previous,previous_count,item.destination.relative);
        bool exists=false;U32 size=0;hash::Digest digest;
        const Status status=current(io,item.destination.relative,exists,size,digest);if (status!=Status::ok) return status;
        Kind kind=Kind::install;
        if (exists && matches(&item,size,digest)) kind=Kind::keep;
        else if (exists && !matches(old,size,digest)) return Status::modified;
        operations[used++]={kind,&item,old};
    }
    if (remove_old) for (U32 i=0;i<previous_count;++i) {
        const package::Item& item=previous[i];
        if (item.destination.role!=package::Role::content || find(desired.items,desired.count,item.destination.relative)) continue;
        if (used==capacity) return Status::capacity;
        bool exists=false;U32 size=0;hash::Digest digest;
        const Status status=current(io,item.destination.relative,exists,size,digest);if (status!=Status::ok) return status;
        if (exists && !matches(&item,size,digest)) return Status::modified;
        if (exists) operations[used++]={Kind::erase,0,&item};
    }
    result.operations=operations;result.count=used;return Status::ok;
}
Status apply(const Plan& plan,const IO& io,Report& report) {
    report={0,0,0};
    if ((plan.count && !plan.operations) || plan.count>8192 || !callbacks(io)) return Status::argument;
    for (U32 i=0;i<plan.count;++i) {
        const Operation& op=plan.operations[i];
        if (op.kind!=Kind::keep && op.kind!=Kind::install && op.kind!=Kind::erase) return Status::policy;
        if ((op.kind==Kind::erase && (op.desired || !op.previous)) ||
            (op.kind!=Kind::erase && !op.desired) ||
            (op.desired && !allowed(*op.desired)) || (op.previous && !allowed(*op.previous)) ||
            (op.desired && op.previous && !same(op.desired->destination.relative,op.previous->destination.relative)))
            return Status::policy;
        const char* path=op.desired?op.desired->destination.relative:op.previous->destination.relative;
        bool exists=false;U32 size=0;hash::Digest digest;
        const Status status=current(io,path,exists,size,digest);if (status!=Status::ok) return status;
        if (op.desired && exists && matches(op.desired,size,digest)) continue;
        if (op.kind==Kind::keep || (exists && !matches(op.previous,size,digest))) return Status::modified;
        if (op.kind==Kind::install) {
            if (!hash_file(io,path,op.desired->size,true,digest) || !hash::equal(digest,op.desired->digest))
                return Status::source;
        }
    }
    for (U32 i=0;i<plan.count;++i) {
        const Operation& op=plan.operations[i];
        const char* path=op.desired?op.desired->destination.relative:op.previous->destination.relative;
        bool exists=false;U32 size=0;hash::Digest digest;
        Status status=current(io,path,exists,size,digest);if (status!=Status::ok) return status;
        if ((op.desired && exists && matches(op.desired,size,digest)) || (!op.desired && !exists)) {
            ++report.kept;continue;
        }
        if (exists && !matches(op.previous,size,digest)) return Status::modified;
        if (op.kind==Kind::install) {
            if (!io.replace(io.context,path,op.desired->size,op.desired->digest)) return Status::replace;
            status=current(io,path,exists,size,digest);if (status!=Status::ok) return status;
            if (!exists || !matches(op.desired,size,digest)) return Status::verify;
            ++report.installed;
        } else if (op.kind==Kind::erase) {
            if (!io.remove_matching(io.context,path,op.previous->size,op.previous->digest)) return Status::replace;
            if (!io.target_size(io.context,path,exists,size) || exists) return Status::verify;
            ++report.removed;
        } else return Status::modified;
    }
    return Status::ok;
}
} }
