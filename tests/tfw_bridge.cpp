#include "../native/tfw.hpp"
#include <stdio.h>
#include <string.h>
using namespace xyf_core;
namespace {
struct Memory {const Byte* data;U32 size;};
bool read_memory(void* context,U32 offset,Byte* out,U32 count) {
    Memory& memory=*static_cast<Memory*>(context);
    if (offset>memory.size || count>memory.size-offset) return false;
    memcpy(out,memory.data+offset,count);return true;
}
struct Keys {const Byte* key;U32 calls,index;};
bool read_key(void* context,U32 index,Byte* key) {
    Keys& provider=*static_cast<Keys*>(context);++provider.calls;provider.index=index;
    if (!provider.key || (index!=0 && index!=1 && index!=255)) return false;
    memcpy(key,provider.key,32);return true;
}
bool read_file(void* context,U32 offset,Byte* out,U32 count) {
    FILE* file=static_cast<FILE*>(context);
    return fseek(file,offset,SEEK_SET)==0 && fread(out,1,count,file)==count;
}
}
extern "C" int tfw_read_memory(const Byte* bytes,U32 size,const Byte* key,U32 offset,
                               Byte* output,U32 count,U32* index,U32* calls) {
    Memory memory={bytes,size};Keys keys={key,0,0};tfw::Archive archive;
    const tfw::Status status=archive.open({&memory,size,read_memory},{&keys,read_key});
    *index=keys.index;*calls=keys.calls;
    if (status!=tfw::Status::ok) return static_cast<int>(status);
    const input::Reader reader=archive.reader();
    if (!input::read(reader,offset,output,count)) return 100;
    archive.close();return archive.reader().read?101:0;
}
extern "C" int tfw_hash_file(const char* path,const Byte* key,Byte* output) {
    FILE* file=fopen(path,"rb");if (!file) return 102;
    if (fseek(file,0,SEEK_END)) {fclose(file);return 103;}
    const long size=ftell(file);if (size<0 || size>0x10000000) {fclose(file);return 104;}
    Keys keys={key,0,0};tfw::Archive archive;
    const tfw::Status status=archive.open({file,U32(size),read_file},{&keys,read_key});
    if (status!=tfw::Status::ok) {fclose(file);return static_cast<int>(status);}
    const input::Reader reader=archive.reader();Byte buffer[32768];hash::Sha256 hasher;
    for (U32 offset=0;offset<reader.size;) {
        const U32 amount=reader.size-offset<sizeof(buffer)?reader.size-offset:sizeof(buffer);
        if (!input::read(reader,offset,buffer,amount) || !hasher.update(buffer,amount)) {fclose(file);return 105;}
        offset+=amount;
    }
    hash::Digest digest;if (!hasher.finish(digest)) {fclose(file);return 106;}
    memcpy(output,digest.bytes,32);archive.close();fclose(file);return 0;
}
