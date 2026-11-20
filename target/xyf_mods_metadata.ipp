namespace record = xyf::package_record;
namespace json = xyf_core::json;
struct MetadataWork {
    uint8_t raw[json::document_bytes];
    record::Workspace parse;
    char text[xyf::manifest::max_text];
};
static_assert(sizeof(MetadataWork) <= usb_service::mods_metadata_bytes, "Mods metadata work fits in the idle report slot");

// A raw ENOENT permits a directory lookup. Only an absent basename selects
// legacy. A later open/read/close failure belongs to the selected native record.
MetadataWork* load_native_metadata(uint32_t& bytes) {
    namespace P = xyf::usbp;
    auto absent = []() {
        const uint32_t code = fc::scan_name(io.path, io.metadata_scan, usb_service::page_mount());
        io.why = code == s_ok ? uint32_t(P::s_storage) : code == fc::scan_again ? uint32_t(P::s_busy) : code;
    };
    if (io.metadata_scan.folder) { absent(); return nullptr; }
    uint8_t stat[fc::record_bytes];
    const fc::Reply found = fc::stat(io.path, stat);
    if (found.outcome) { io.why = failed(found); return nullptr; }
    if (found.value != 0) {
        if (found.error == fc::enoent) absent();
        else io.why = fc::change_status(found.error);
        return nullptr;
    }
    if ((P::get32(stat + 8) & 0xf000u) != 0x8000u) { io.why = P::s_storage; return nullptr; }
    auto& work = *static_cast<MetadataWork*>(usb_service::mods_metadata_work());
    fc::Open opened;
    uint32_t code = fc::read_file(io.path, 0, false, work.raw, sizeof(work.raw), bytes, usb_service::page_mount(), &opened);
    if (opened.fd >= 0) {
        const fc::Reply closed = fc::close(opened.fd);
        if (closed.outcome) code = failed(closed);
        else if (closed.value) code = P::s_storage;
    }
    if (code != s_ok) { io.why = code == P::s_not_found ? P::s_storage : code; return nullptr; }
    io.why = s_ok;
    return &work;
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
