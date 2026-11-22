// Included by cold_storage.cpp in its stock namespace.
namespace {
__attribute__((noinline)) int y_open(const char* path, int flags, int mode) {
    return call<int(const char*, int, int)>(0x889fbfe6u)(path, flags, mode);
}
__attribute__((noinline)) int y_write(int fd, const void* data, U32 count) {
    return call<int(int, const void*, U32)>(0x889fbb0cu)(fd, data, count);
}
__attribute__((noinline)) int y_read(int fd, void* out, U32 count) {
    return call<int(int, void*, U32)>(0x889fb928u)(fd, out, count);
}
__attribute__((noinline)) int y_close(int fd) { return call<int(int)>(0x889fb6fau)(fd); }
}

#if defined(XYF_USB_FILE_WRITE) && defined(XYF_USB_SET_DEFAULT) && defined(XYF_ONE_SESSION)
int Stock121ReadOnlySession::managed_blob_open(const char* path, bool writing) {
    namespace ml = managed_blob_link;
    if (!fresh_ || !mounted_ || !direct_ok_ || media_guarded(device_) || word(device_ + 0xe4) != 1 ||
        word(device_ + 0xe8) != 0 || diagnostic_.handles != U32(writing) ||
        !(writing ? ml::temporary_path(path, true) : ml::valid_blob_path(path)))
        return -1;
    const int fd = y_open(path, writing ? 0xc1 : 0, writing ? 0444 : 0);
    if (fd >= 0) ++diagnostic_.handles;
    return fd;
}

Result Stock121ReadOnlySession::managed_blob_copy(int input, int output, U32 bytes) {
    if (input < 0 || output < 0 || input == output || !fresh_ || !mounted_ || !direct_ok_ || diagnostic_.handles != 2 ||
        media_guarded(device_) || bytes > managed_blob_link::piece_bytes || word(device_ + 0xe4) != 1 ||
        word(device_ + 0xe8) != 0)
        return Result::write_refused;
    unsigned char chunk[managed_blob_link::piece_bytes];
    const int count = y_read(input, chunk, bytes ? bytes : 1);
    if (count != int(bytes)) return Result::verify_failed;
    if (bytes && y_write(output, chunk, bytes) != count) return Result::write_failed;
    return Result::ok;
}

int Stock121ReadOnlySession::managed_blob_create(const char* path) {
    if (!fresh_ || !mounted_ || !direct_ok_ || media_guarded(device_) || word(device_ + 0xe4) != 1 ||
        word(device_ + 0xe8) != 0 || diagnostic_.handles || !managed_blob_link::scratch_path(path))
        return -1;
    const int fd = y_open(path, 0xc1, 0444);                         // new only (O_EXCL): never an existing link
    if (fd >= 0) ++diagnostic_.handles;
    return fd;
}

Result Stock121ReadOnlySession::managed_blob_write(int output, const void* data, U32 bytes) {
    if (output < 0 || !data || !bytes || bytes > managed_blob_link::piece_bytes || !fresh_ || !mounted_ ||
        !direct_ok_ || diagnostic_.handles != 1 || media_guarded(device_) || word(device_ + 0xe4) != 1 ||
        word(device_ + 0xe8) != 0)
        return Result::write_refused;
    return y_write(output, data, bytes) == int(bytes) ? Result::ok : Result::write_failed;
}
#endif
