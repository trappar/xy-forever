#include "../native/package_policy.hpp"
extern "C" int package_destination(const char* name, unsigned int size,
    unsigned int* role, char* output) {
    xyf_core::package::Destination result;
    const xyf_core::package::Status status=xyf_core::package::classify(name,size,result);
    *role=static_cast<unsigned int>(result.role);
    for (unsigned int i=0;i<257;++i) {output[i]=result.relative[i];if (!output[i]) break;}
    return static_cast<int>(status);
}
extern "C" int package_version(const unsigned char* bytes, unsigned int size,
                                 unsigned int* fields) {
    xyf_core::package::Version version;
    const xyf_core::package::Status status=xyf_core::package::parse_version(bytes,size,version);
    if (status==xyf_core::package::Status::ok)
        for (unsigned int i=0;i<4;++i) fields[i]=version.field[i];
    return static_cast<int>(status);
}
