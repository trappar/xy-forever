#include "../native/json.hpp"
#include <stddef.h>
namespace j = xyf_core::json;
extern "C" {
unsigned json_workspace_size() { return sizeof(j::Workspace); }
unsigned json_view(const char* raw, unsigned length, unsigned config, char* out, unsigned capacity) {
    j::Workspace workspace;
    const j::Document d = j::parse(raw,length,workspace,config ? j::config_schema : j::package_schema);
    uint32_t bytes = 0;
    const j::Status status = config ? j::config_text(d,out,capacity,bytes) : j::package_text(d,out,capacity,bytes);
    return status == j::ok ? bytes : 0x80000000u | unsigned(status);
}
}
#ifdef XYF_JSON_SANITIZE
#include <stdio.h>
#include <string.h>
int main(int argc, char** argv) {
    char raw[j::document_bytes + 1], saved[j::document_bytes + 1], out[4096];
    for (int a = 1; a < argc; ++a) {
        FILE* f = fopen(argv[a],"rb"); if (!f) return 1;
        const unsigned bytes = unsigned(fread(raw,1,sizeof(raw),f)); fclose(f);
        memcpy(saved,raw,bytes);
        for (unsigned n = 0; n <= bytes; ++n)
            for (unsigned config = 0; config != 2; ++config) json_view(raw,n,config,out,sizeof(out));
        if (memcmp(raw,saved,bytes)) return 2;
        for (unsigned n = 0; n != bytes; ++n) {
            raw[n] ^= char(0x80); json_view(raw,bytes,0,out,sizeof(out)); raw[n] ^= char(0x80);
            if (memcmp(raw,saved,bytes)) return 3;
        }
    }
    return 0;
}
#endif
