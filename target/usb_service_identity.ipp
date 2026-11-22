// Part of usb_service.cpp, included there at this point: not compiled alone.
// ---- Build identity: hello version= and image=, the menu's corner and Info page ----------------
// version: the release version (XYF_VERSION; xy_forever/release.py). image: the first 8 hex digits of
// the SHA-256 of the os.ldr this boot runs, which name its build (builds/<image>/). B hashes the
// image the stock bootloader read from the boot partition into the RAM-boot source 0x8a000000
// (plain there, also when the partition holds it encrypted), so a build installed through the dev
// menu is identified too, and no boot partition is selected or read again. Its length is the LDR
// stream's (the first record's argument + 16, native/ldr.cpp). It counts only while it is an XY
// Forever image (its cold descriptor): a library hand-off that failed may have left another image
// there. Read only. Never on the menu's path: image_step() hashes image_slice bytes per home_pump
// pass from the menu's start; image_finish() completes the rest at once (HELLO).
const U32 boot_source = 0x8a000000u, boot_source_max = 0x800000u, image_slice = 0x1000u;
enum ImageState : U32 { image_none, image_hashing, image_known, image_unknown };
U32 image_hash_storage[(sizeof(xyf_core::hash::Sha256) + 3) / 4] __attribute__((section(".data"))) = {0};
xyf_core::hash::Sha256& image_hash() { return *reinterpret_cast<xyf_core::hash::Sha256*>(image_hash_storage); }
volatile U32 image_bytes __attribute__((section(".data"))) = 0;
volatile U32 image_done __attribute__((section(".data"))) = 0;
char image_text[16] __attribute__((section(".data"))) = "image ...";         // the Info page's line, once settled

void image_label_set(const char* tail) {
    for (U32 i = 0; i != sizeof(image_text) - 6; ++i) {
        image_text[6 + i] = *tail;
        if (*tail) ++tail;
    }
    image_text[sizeof(image_text) - 1] = 0;
}
void image_begin() {
    Result& r = result;
    r.release = XYF_VERSION_CODE;
    r.image_state = image_unknown;
    r.image_ms = now();                                 // the start; the time it took once known
    const uint8_t* s = reinterpret_cast<const uint8_t*>(boot_source);
    uint8_t sum = 0;
    for (U32 i = 0; i != 16; ++i) sum ^= s[i];
    const U32 code = P::get32(s), bytes = P::get32(s + 12) + 16;
    if (sum || code >> 24 != 0xadu || (code & 0xffffu) != 0x5004u || bytes < 32 || bytes > boot_source_max ||
        !has_descriptor(s, bytes)) {
        image_label_set("unknown");
        r.image_ms = 0;
        return;
    }
    image_hash().reset();
    image_bytes = bytes;
    image_done = 0;
    r.image_state = image_hashing;
}
// Hashes up to limit bytes; at the end the result words and the label.
void image_hash_some(U32 limit) {
    Result& r = result;
    if (r.image_state == image_none) image_begin();
    if (r.image_state != image_hashing) return;
    const U32 start = cycles_div1024();
    const U32 n = image_bytes - image_done < limit ? image_bytes - image_done : limit;
    image_hash().update(reinterpret_cast<const uint8_t*>(boot_source) + image_done, n);
    image_done = image_done + n;
    r.image_kcycles += cycles_div1024() - start;
    if (image_done != image_bytes) return;
    xyf_core::hash::Digest digest;
    if (!image_hash().finish(digest)) {
        r.image_state = image_unknown;
        r.image_ms = 0;
        image_label_set("unknown");
        return;
    }
    r.image = U32(digest.bytes[0]) << 24 | U32(digest.bytes[1]) << 16 | U32(digest.bytes[2]) << 8 | digest.bytes[3];
    char hex[9];
    for (U32 i = 0; i != 8; ++i) hex[i] = "0123456789abcdef"[r.image >> (28 - 4 * i) & 15u];
    hex[8] = 0;
    image_label_set(hex);
    r.image_state = image_known;
    r.image_ms = now() - r.image_ms;
}
void image_step() { image_hash_some(image_slice); }
void image_finish() { image_hash_some(boot_source_max); }

// The hello text with this boot's image, (#65) Ask before USB control and its question's state, and (#191) whether
// USB mode leaves serial on: the compiled text says image=unknown, confirm=1, trust=none and serial=on (usb_proto_ids.hpp).
constexpr bool starts(const char* t, const char* k) { return !*k || (*t == *k && starts(t + 1, k + 1)); }
constexpr U32 length(const char* s) { return *s ? 1 + length(s + 1) : 0; }
constexpr U32 key_value(const char* t, const char* key, U32 i = 0) {   // where key's value starts; ~0u: no such key
    return !t[i] ? ~0u : starts(t + i, key) ? i + length(key) : key_value(t, key, i + 1);
}
const U32 image_at = key_value(XYF_USBP_HELLO_TEXT, ";image="),
          confirm_at = key_value(XYF_USBP_HELLO_TEXT, ";confirm="),
          trust_at = key_value(XYF_USBP_HELLO_TEXT, ";trust="), serial_at = key_value(XYF_USBP_HELLO_TEXT, ";serial=");
U32 hello_text(uint8_t* p, U32 n, U32 room) {
    image_finish();
    const char* text = XYF_USBP_HELLO_TEXT;
    const bool known = result.image_state == image_known;
#if defined(XYF_SETTINGS)
    const char confirm = xy_forever::usb_service::confirm_on() ? '1' : '0';
    const char* const trust = xy_forever::usb_service::trust_word();
    const char* const serial = xy_forever::usb_service::serial_off() ? "off" : "on";
#else
    const char confirm = '0', *const trust = "none", *const serial = "on";   // no question, no USB mode
#endif
    for (U32 i = 0; text[i] && n != room; ++i) {
        if (i == confirm_at) {
            p[n++] = uint8_t(confirm);
            continue;
        }
        if (i == trust_at || i == serial_at) {
            for (const char* w = i == trust_at ? trust : serial; *w && n != room; ++w) p[n++] = uint8_t(*w);
            i += i == trust_at ? sizeof("none") - 2 : sizeof("on") - 2;   // the loop's ++i passes the last letter
            continue;
        }
        if (known && i == image_at) {
            for (U32 k = 0; k != 8 && n != room; ++k) p[n++] = uint8_t(image_text[6 + k]);
            i += sizeof("unknown") - 2;                 // the loop's ++i passes the last letter
            continue;
        }
        p[n++] = uint8_t(text[i]);
    }
    return n;
}
