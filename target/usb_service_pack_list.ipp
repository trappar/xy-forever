// Installed packs under content/dlc. One paged listing request per menu pass, with no retained handles.
// The folder id is displayed when a direct child is a regular file named manifest.json.
enum PackKind : uint8_t { plain_pack, archive_pack, retained_pack, enabled_pack };
struct PackList {
    U32 page, outer, inner, skip;
    bool inbox, kept, has_manifest, has_record;
    uint8_t kinds[xyf::menu::packs_per_page];
    char folder[file_engine::max_path], id[file_client::max_name + 1];
    char ids[xyf::menu::packs_per_page][file_engine::max_path];
    const char* names[xyf::menu::packs_per_page];
};
static PackList packs __attribute__((section(".data"))) = {};
static bool pack_list_add(xyf::menu::State& s, const char* id, PackKind kind) {
    if (packs.skip) {
        --packs.skip;
        return false;
    }
    if (s.pack_count == xyf::menu::packs_per_page) {
        s.pack_more = s.pack_ready = true;
        s.pack_note = 0;
    } else {
        const U32 index = s.pack_count++;
        xj::copy(packs.ids[index], id);
        packs.names[index] = packs.ids[index];
        packs.kinds[index] = kind;
    }
    return true;
}
static bool pack_list_step(xyf::menu::State& s) {
    const U32 per_page = xyf::menu::packs_per_page;
    if (packs.page != s.pack_page || packs.inbox != s.pack_inbox) {
        packs.outer = 0;
        packs.kept = false;
        packs.skip = s.pack_page * per_page;
        packs.inbox = s.pack_inbox;
        s.pack_kinds = packs.kinds;
        packs.page = s.pack_page;
        packs.folder[0] = 0;
        s.pack_count = s.pack_selected = 0;
        s.pack_more = s.pack_ready = false;
        s.pack_note = "Reading content packs...";
        return true;
    }
    if (s.pack_ready) return false;
    if (!menu_link::usb_idle() || page_busy()) return false;
    if (!a_serving()) {
        s.pack_ready = true;
        s.pack_note = "Storage unavailable.   STOP  back";
        return true;
    }
    const char* root = s.pack_inbox ? "/yaffs2/xy-forever/inbox" : "/yaffs2/content/dlc";
#if defined(XYF_TFW_CHECK)
    if (packs.kept) root = kept_packs;
#endif
    ContentIo io = {};
    char name[file_client::max_name + 1];
    uint32_t kind = 0;
    const bool child = packs.folder[0] != 0;
    const int found = io.list_one(child ? packs.folder : root, child ? packs.inner++ : packs.outer++, name, kind);
    if (found < 0) {
        s.pack_ready = true;
        s.pack_note = "Cannot read content packs.   STOP  back";
        return true;
    }
    if (!child) {
        if (!found) {
#if defined(XYF_TFW_CHECK)
            if (s.pack_inbox && !packs.kept) {
                packs.kept = true;
                packs.outer = 0;
                return false;
            }
#endif
            s.pack_ready = true;
            s.pack_note = s.pack_count ? 0 :
                          s.pack_inbox ? "Inbox empty. SHIFT+PLAY installed." :
                                         "No installed packs. SHIFT+PLAY inbox.";
            return true;
        }
#if defined(XYF_TFW_CHECK)
        if (s.pack_inbox && !packs.kept && kind == 0 && pack_import_name(name))
            return pack_list_add(s, name, archive_pack);
#endif
        if (kind == 1 && join(packs.folder, root, "/", name)) {
            xj::copy(packs.id, name);
            packs.inner = 0;
            packs.has_manifest = packs.has_record = false;
        } else packs.folder[0] = 0;
        return false;
    }
    if (!found) {
        packs.folder[0] = 0;
#if defined(XYF_TFW_CHECK)
        char records[file_engine::max_path];                               // its records (#195): an imported pack
        packs.has_record = packs.has_manifest && join(records, xyf::blob::pack_records, "/", packs.id) &&
                           pack_kind(io, records, sp::record_name) == 0;
#endif
        if (!packs.has_manifest || (packs.kept && !packs.has_record)) return false;
        const auto type = packs.kept ? retained_pack : packs.has_record ? enabled_pack : plain_pack;
        return pack_list_add(s, packs.id, type);
    }
    if (kind == 0 && xj::same(name, "manifest.json")) packs.has_manifest = true;
    return false;
}
