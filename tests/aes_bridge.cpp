#include "../native/aes.hpp"
extern "C" int aes_decrypt(const unsigned char* key,const unsigned char* input,
                            unsigned char* output) {
    xyf_core::crypto::Aes256 aes;
    if (aes.decrypt(input,output) || !aes.initialize(key) || !aes.decrypt(input,output)) return 1;
    aes.erase();return aes.decrypt(input,output)?2:0;
}
