// Compose the production readers, rather than decrypting to an intermediate ZIP.
// The only key is the public synthetic vector from test_tfw.py. No device I/O.
#include "../native/tfw.hpp"
#include "../native/package.hpp"
#include <cstdio>
#include <vector>
using namespace xyf_core;
struct Source { std::vector<Byte> bytes; };
bool read_bytes(void* context, U32 offset, Byte* out, U32 count) {
    const Source& source = *static_cast<Source*>(context);
    if (offset > source.bytes.size() || count > source.bytes.size() - offset) return false;
    for (U32 i=0;i<count;++i) out[i]=source.bytes[offset+i];
    return true;
}
bool synthetic_key(void*, U32 index, Byte out[32]) {
    if (index>1) return false;
    for (U32 i=0;i<32;++i) out[i]=Byte(i);
    return true;
}
int main(int argc, char** argv) {
    if (argc!=2) return 2;
    std::FILE* file=std::fopen(argv[1],"rb");
    if (!file || std::fseek(file,0,SEEK_END)) return 2;
    long size=std::ftell(file);
    if (size<0 || size>256*1024*1024 || std::fseek(file,0,SEEK_SET)) return 2;
    Source source; source.bytes.resize(static_cast<U32>(size));
    if (std::fread(source.bytes.data(),1,source.bytes.size(),file)!=source.bytes.size()) return 2;
    std::fclose(file);
    input::Reader reader={&source,static_cast<U32>(source.bytes.size()),read_bytes};
    tfw::KeyProvider keys={0,synthetic_key};
    tfw::Archive decrypted;
    tfw::Status ts=decrypted.open(reader,keys);
    if (ts!=tfw::Status::ok) {
        std::printf("{\"phase\":\"tfw\",\"status\":%u}\n",static_cast<U32>(ts));return 1;
    }
    std::vector<zip::Entry> entries(4096);zip::Archive archive;
    input::Reader clear=decrypted.reader();
    zip::Reader zip_reader={clear.context,clear.size,clear.read};
    zip::Status zs=zip::open(zip_reader,zip::Limits(),entries.data(),4096,archive);
    if (zs!=zip::Status::ok) {
        std::printf("{\"phase\":\"zip\",\"status\":\"%s\"}\n",zip::status_name(zs));return 1;
    }
    std::vector<Byte> scratch(8*1024*1024);
    package::Workspace workspace;workspace.scratch=scratch.data();workspace.capacity=static_cast<U32>(scratch.size());
    std::vector<package::Item> items(4096);package::Manifest manifest={};
    const Range ranges[]={{0x1000,0x10000}};Policy policy={ranges,1,0,0};
    package::AdmissionStatus ps=package::admit(archive,policy,workspace,items.data(),4096,manifest);
    U32 excluded=0;
    if (ps==package::AdmissionStatus::ok)
        for (U32 i=0;i<manifest.count;++i)
            if (manifest.items[i].destination.role==package::Role::excluded_bootloader) ++excluded;
    std::printf("{\"phase\":\"admission\",\"status\":\"%s\",\"items\":%u,\"excluded\":%u}\n",
        package::admission_status_name(ps),manifest.count,excluded);
    return ps==package::AdmissionStatus::ok?0:1;
}
