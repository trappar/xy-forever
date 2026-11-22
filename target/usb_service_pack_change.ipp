// Menu pack transaction. The inbox is already staged. Only a checked, complete folder is renamed.
// While the transaction owns this workspace, the usual job exclusion gates MTP, serial and the idle sweep.
namespace {
enum PackStep {
    pc_xy,
    pc_content,
    pc_store,
    pc_dlc,
    pc_source,
    pc_target,
    pc_tree,
    pc_manifest,
    pc_parent,
    pc_commit,
    pc_cleanup
};
struct PackChange {
    PackStep step;
    bool install, make_parent, has_manifest;
    uint32_t index, at, bytes, more, level, skips[file_engine::max_folders + 1];
    char id[128], source[file_engine::max_path], target[file_engine::max_path], path[file_engine::max_path];
    uint8_t buffer[4096];
    Tree tree;
};
static PackChange& pack_work() { return *reinterpret_cast<PackChange*>(job_work_end - 0x10000u); }
static_assert(sizeof(PackChange) <= 0x10000u, "pack transaction workspace");
static const char reserved_pack_file[] = "xyf-pack.txt";
static const char pack_root[] = "/yaffs2/content/dlc", pack_inbox[] = "/yaffs2/xy-forever/inbox";
static void pack_reset_scan(PackChange& c) {
    c.index = c.at = c.bytes = 0;
    c.more = 1;
}
static void pack_advance(PackChange& c, PackStep step) {
    c.step = step;
    pack_reset_scan(c);
}
static void pack_finish(xyf::menu::State& s, const char* message) {
    menu_pack_active = 0;
    s.pack_busy = false;
    s.pack_result = message;
    // Refresh the listing on the next pass, including both sides of a successful rename.
    s.pack_page = 0;
    packs.page = ~0u;
}
static void pack_start(xyf::menu::State& s) {
    s.pack_execute = false;
    const char* id = s.pack_selected < s.pack_count ? s.pack_names[s.pack_selected] : "";
    if (!xyf::manifest::pack_name(id)) {
        s.pack_result = "Invalid pack folder.";
        return;
    }
    if (page_busy() || uploading() || !menu_link::usb_idle() || !a_serving()) {
        s.pack_result = "Storage busy or unavailable.";
        return;
    }
#if defined(XYF_TFW_CHECK)
    const auto kind = packs.kinds[s.pack_selected];
    if (kind == archive_pack) {
        const U32 code = signed_pack_start(id);
        if (code) {
            pack_finish(s, "Cannot start signed pack import.");
            return;
        }
        s.pack_busy = s.pack_importing = true;
        s.pack_result = 0;
        return;
    }
    if (kind == retained_pack || kind == enabled_pack) {
        const bool enable = kind == retained_pack;
        const U32 code = signed_pack_toggle(id, enable);
        pack_finish(s, code == P::s_refused && enable ? "Compatibility unknown." :
                       code                           ? "Cannot change pack." :
                       enable                         ? "Pack enabled." :
                                                        "Pack disabled.");
        return;
    }
#endif
    PackChange& c = pack_work();
    c.install = s.pack_inbox;
    c.make_parent = c.has_manifest = false;
    xj::copy(c.id, id);
    join(c.source, c.install ? pack_inbox : pack_root, "/", id);
    if (c.install) join(c.target, pack_root, "/", id);
    else {
        char suffix[16];
        decimal_text(suffix, now());
        join(c.target, job_tmp, "/pkg-v1-pack-", suffix);
    }
    pack_advance(c, pc_xy);
    s.pack_busy = true;
    s.pack_result = 0;
    menu_pack_active = 1;
}
// One entry from a cached file.list page. Read/list errors never mean "missing".
static int pack_next(PackChange& c, const char* folder, char* name, uint32_t& kind) {
    if (c.at == c.bytes) {
        if (!c.more) return 0;
        uint32_t count = 0;
        const U32 code = file_client::list(folder, c.index, c.buffer, 1024, count, c.more, c.bytes, mounted);
        c.at = 0;
        if (code != P::s_ok || (!count && c.more)) return -1;
        if (!count) return 0;
    }
    const uint8_t* entry = c.buffer + c.at;
    const U32 n = entry[5];
    for (U32 i = 0; i != n; ++i) name[i] = char(entry[6 + i]);
    name[n] = 0;
    kind = entry[0];
    c.at += 6 + n;
    ++c.index;
    return 1;
}
static void pack_check_path(xyf::menu::State& s, PackChange& c) {
    const char* folder = 0;
    const char* name = 0;
    switch (c.step) {
    case pc_xy:
        folder = "/yaffs2";
        name = "xy-forever";
        break;
    case pc_content:
        folder = "/yaffs2";
        name = "content";
        break;
    case pc_store:
        folder = "/yaffs2/xy-forever";
        name = c.install ? "inbox" : "tmp";
        break;
    case pc_dlc:
        folder = "/yaffs2/content";
        name = "dlc";
        break;
    case pc_source:
        folder = c.install ? pack_inbox : pack_root;
        name = c.id;
        break;
    default:
        folder = c.install ? pack_root : job_tmp;
        for (name = c.target + text_bytes(c.target); name > c.target && name[-1] != '/'; --name) {}
        break;
    }
    char child[file_client::max_name + 1];
    uint32_t kind = 0;
    const bool target_missing = c.step == pc_target && c.make_parent;
    const int found = target_missing ? 0 : pack_next(c, folder, child, kind);
    if (found < 0) {
        pack_finish(s, "Cannot list pack folders.");
        return;
    }
    if (found && !xj::same(name, child)) return;
    if (c.step == pc_target) {
        if (found) {
            pack_finish(s, c.install ? "Pack ID already occupied." : "Cleanup folder occupied.");
            return;
        }
        c.level = 0;
        xj::copy(c.path, c.source);
        pack_advance(c, pc_tree);
        return;
    }
    if (!found && ((c.step == pc_dlc && c.install) || (c.step == pc_store && !c.install))) c.make_parent = true;
    else if (!found || kind != 1) {
        pack_finish(s, "Pack path is missing or not a folder.");
        return;
    }
    pack_advance(c, PackStep(U32(c.step) + 1));
}
static void pack_check_tree(xyf::menu::State& s, PackChange& c) {
    char name[file_client::max_name + 1];
    uint32_t kind = 0;
    const int found = pack_next(c, c.path, name, kind);
    if (found < 0) {
        pack_finish(s, "Cannot read pack folder.");
        return;
    }
    if (!found) {
        if (!c.level) {
            if (!c.has_manifest) {
                pack_finish(s, "Missing pack manifest.");
                return;
            }
            pack_advance(c, pc_manifest);
            return;
        }
        U32 n = text_bytes(c.path);
        while (n && c.path[n - 1] != '/') --n;
        c.path[n - 1] = 0;
        pack_reset_scan(c);
        c.index = c.skips[--c.level];
        return;
    }
    if (kind > 1 || !xyf::manifest::pack_name(name)) {
        pack_finish(s, "Unsupported pack entry.");
        return;
    }
    if (!c.level) {
        if (xj::same(name, reserved_pack_file)) {
            pack_finish(s, "Signed pack record is reserved.");
            return;
        }
        if (xj::same(name, "info.txt") || xj::same(name, "os.ldr")) {
            pack_finish(s, "Not a sound pack.");
            return;
        }
        if (xj::same(name, "manifest.json") && kind == 0) c.has_manifest = true;
    }
    char child[file_engine::max_path];
    const U32 relative = text_bytes(c.path) - text_bytes(c.source) + 1 + text_bytes(name);
    if (!join(child, c.path, "/", name) || text_bytes(c.target) + relative >= file_engine::max_path ||
        (kind == 1 && file_engine::depth(child) > file_engine::max_folders)) {
        pack_finish(s, "Pack path exceeds device limits.");
        return;
    }
    if (kind == 1) {
        c.skips[c.level++] = c.index;
        xj::copy(c.path, child);
        pack_reset_scan(c);
    }
}
static bool pack_change_step(xyf::menu::State& s) {
    const bool requested = s.pack_execute;
    if (requested) pack_start(s);
#if defined(XYF_TFW_CHECK)
    if (s.pack_importing) {
        if (signed_pack_active) {
            if (s.pack_cancel) signed_pack().cancelled = true;
            s.pack_cancel = false;
            return requested;
        }
        s.pack_importing = s.pack_cancel = false;
        const char* message = "Pack imported. Compatibility unknown.";
        switch (signed_pack_error) {
        case P::s_ok: break;
        case P::s_cancelled: message = "Pack import cancelled."; break;
        case P::s_too_large: message = "Not enough storage."; break;
        case P::s_state: message = "Pack ID already occupied."; break;
        case P::s_crc:
        case P::s_image:
        case P::s_sha: message = "Invalid signed sound pack."; break;
        default: message = "Pack import failed."; break;
        }
        pack_finish(s, message);
        return true;
    }
#endif
    if (!menu_pack_active) return requested;
    PackChange& c = pack_work();
    if (c.step <= pc_target) pack_check_path(s, c);
    else if (c.step == pc_tree) pack_check_tree(s, c);
    else if (c.step == pc_manifest) {
        uint32_t bytes = 0;
        const U32 code = join(c.path, c.source, "/manifest.json") ?
                             file_client::read_file(c.path, 0, false, c.buffer, sizeof(c.buffer), bytes, mounted) :
                             U32(P::s_refused);
        if (code != P::s_ok || !xyf_core::mod_content::pack_manifest(c.buffer, bytes, c.id))
            pack_finish(s, "Invalid manifest or folder ID.");
        else c.step = c.make_parent ? pc_parent : pc_commit;
    } else if (c.step == pc_parent) {
        const U32 code = tree_change(0x26u, c.install ? pack_root : job_tmp);
        if (code != P::s_ok) pack_finish(s, "Cannot create pack folder.");
        else c.step = pc_commit;
    } else if (c.step == pc_commit) {
        U32 code = enter_writes();
        uint32_t error = 0;
        if (code == P::s_ok) {
            storage_used = 1;
            code = file_client::rename(c.source, c.target, error, mounted);
            writes_due = 1;
            writes_ms = now();
        }
        if (code != P::s_ok) pack_finish(s, "Pack rename failed.");
        else if (c.install) pack_finish(s, "Pack installed.");
        else if (tree_begin(c.tree, c.target)) c.step = pc_cleanup;
        else pack_finish(s, "Pack removed; cleanup pending.");
    } else {
        const U32 result = tree_step(c.tree, c.buffer);
        if (result != tree_more)
            pack_finish(s, result == tree_done ? "Pack removed." : "Pack removed; cleanup pending.");
    }
    return requested || !menu_pack_active;
}
}
