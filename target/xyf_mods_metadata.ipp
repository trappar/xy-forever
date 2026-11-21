namespace record = xyf::package_record;
namespace json = xyf_core::json;
struct MetadataWork {
    uint8_t raw[json::document_bytes];
    record::Workspace parse;
    char text[xyf::manifest::max_text];
};
static_assert(sizeof(MetadataWork) <= usb_service::mods_metadata_bytes, "Mods metadata work fits in the idle report slot");

uint32_t read_selected(const char* path, uint8_t* buffer, uint32_t capacity, uint32_t& bytes) {
    namespace P = xyf::usbp;
    fc::Open opened;
    uint32_t code = fc::read_file(path, 0, false, buffer, capacity, bytes, usb_service::page_mount(), &opened);
    if (opened.fd >= 0) {
        const fc::Reply closed = fc::close(opened.fd);
        if (closed.outcome) code = failed(closed);
        else if (closed.value) code = P::s_storage;
    }
    return code == P::s_not_found ? P::s_storage : code;
}

// A raw ENOENT permits a directory lookup. Only an absent basename selects legacy.
uint32_t read_named(const char* path, fc::NameScan& scan, uint8_t* buffer, uint32_t capacity, uint32_t& bytes) {
    namespace P = xyf::usbp;
    auto absent = [path, &scan]() {
        const uint32_t code = fc::scan_name(path, scan, usb_service::page_mount());
        return code == s_ok ? uint32_t(P::s_storage) : code == fc::scan_again ? uint32_t(P::s_busy) : code;
    };
    if (scan.folder) return absent();
    uint8_t stat[fc::record_bytes];
    const fc::Reply found = fc::stat(path, stat);
    if (found.outcome) return failed(found);
    if (found.value != 0) return found.error == fc::enoent ? absent() : fc::change_status(found.error);
    if ((P::get32(stat + 8) & 0xf000u) != 0x8000u) return P::s_storage;
    return read_selected(path, buffer, capacity, bytes);
}

MetadataWork* load_native_metadata(uint32_t& bytes) {
    auto* work = static_cast<MetadataWork*>(usb_service::mods_metadata_work());
    io.why = read_named(io.path, io.metadata_scan, work->raw, sizeof(work->raw), bytes);
    return io.why == s_ok ? work : nullptr;
}

const char* metadata(bool native, xyf::manifest::Type expected, uint32_t& bytes) {
    if (!native) return read_text(io.path, bytes) ? reinterpret_cast<const char*>(io.buffer) : nullptr;
    MetadataWork* const work = load_native_metadata(bytes);
    if (!work) return nullptr;
    const json::Status status = record::view(reinterpret_cast<const char*>(work->raw), bytes, work->parse, expected, work->text, bytes);
    io.newer |= status == json::version;
    io.why = status == json::ok ? s_ok : xyf::usbp::s_refused;
    return status == json::ok ? work->text : nullptr;
}
