// Installed packs under content/dlc. One paged listing request per menu pass, with no retained handles.
// The folder id is displayed when a direct child is a regular file named manifest.json.
struct PackList {
    U32 page, outer, inner, skip, next;
    bool inbox;
    char folder[file_engine::max_path], id[file_client::max_name + 1];
    char ids[xyf::menu::packs_per_page][file_engine::max_path];
    const char* names[xyf::menu::packs_per_page];
};
static PackList packs __attribute__((section(".data"))) = {};
static bool pack_list_step(xyf::menu::State& s) {
    const U32 per_page = xyf::menu::packs_per_page;
    if (packs.page != s.pack_page || packs.inbox != s.pack_inbox) {
        // Forward: resume at the first unshown pack. Backward: count complete packs from the start.
        const bool forward = packs.inbox == s.pack_inbox && packs.page != ~0u && s.pack_page == packs.page + 1;
        packs.outer = forward ? packs.next : 0;
        packs.skip = forward ? 0 : s.pack_page * per_page;
        packs.inbox = s.pack_inbox;
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
            s.pack_ready = true;
            s.pack_note = s.pack_count ? 0 :
                          s.pack_inbox ? "Inbox empty. SHIFT+PLAY installed." :
                                         "No installed packs. SHIFT+PLAY inbox.";
            return true;
        }
        if (kind == 1 && join(packs.folder, root, "/", name)) {
            xj::copy(packs.id, name);
            packs.inner = 0;
        } else packs.folder[0] = 0;
        return false;
    }
    if (!found) {
        packs.folder[0] = 0;
        return false;
    }
    if (kind != 0 || !xj::same(name, "manifest.json")) return false;
    packs.folder[0] = 0;
    if (packs.skip) {
        --packs.skip;
        return false;
    }
    if (s.pack_count == per_page) {
        packs.next = packs.outer - 1;
        s.pack_more = s.pack_ready = true;
        s.pack_note = 0;
    } else {
        xj::copy(packs.ids[s.pack_count], packs.id);
        packs.names[s.pack_count] = packs.ids[s.pack_count];
        ++s.pack_count;
    }
    return true;
}
