#ifndef XYF_BOOT_SERVICES_HPP
#define XYF_BOOT_SERVICES_HPP
#include <stdint.h>
namespace xyf_core { namespace tfw { struct KeyProvider; } }

// Small services shared by the cold code: the loader-owned path rule and the
// device update key (for the planned .tfw install; linked by no build yet).
namespace xy_forever { namespace services {
typedef uint8_t Byte;
typedef uint32_t U32;
enum class Error { ok, unavailable, path, open, seek, size, range, read, close,
                   directory, capacity, duplicate, display, selection };
enum class Phase { unknown, live_bootloader, cold_after_teardown, running_os };
static const U32 path_capacity = 240;

// Only files below the loader-owned NAND directory are accepted. This does not
// create, update, mount, unmount, rename or remove anything.
bool loader_path(const char* path);

struct OtpIO {
    void* user;
    bool (*prepare)(void*);
    U32 (*read_selector)(void*, U32 selector, Byte* scratch, U32 capacity);
};
// The only supported key indices are0/1 and ROM selectors3..6. Scratch is512
// bytes and is scrubbed before every ROM read and on every return. Failed reads
// scrub the entire caller key. No key is stored in a filesystem or source file.
bool read_update_key(const OtpIO& io, U32 index, Byte key32[32]);
// This ROM-only provider supports the cold loader. Exact retained old OTP
// wrapper CRC is checked; running OS context is refused.
bool stock121_otp_provider(Phase phase, xyf_core::tfw::KeyProvider& output);
} }
#endif
