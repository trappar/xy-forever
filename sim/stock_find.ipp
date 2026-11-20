// Part of stock.cpp, included inside its anonymous namespace (after stock_files.ipp).
// ---- yaffs lookup (A's final-link guard only) ---------------------------------------------------
// 889fa2be find(relative, path, depth, equivalent, &parent, &notdir, &loop) (1.2.1 listing): the object a path
// names WITHOUT following its final link (links inside the path are followed, depth > 5 sets loop), and the
// folder that holds that name. With relative, path starts there (a relative link's own parent folder); else at
// the mount root. The final "." / ".." are the folder / its parent. Objects are fake yaffs_obj records in emulated
// memory, one per host path whose folders hold no link: parent +0x1c (0 at the root), type +0x64 (1 file, 2 link,
// 3 folder), link text +0x68 (a pointer to it, stored after the record). A host link to an absolute host path in
// the folder stands for a /yaffs2 link (the tests make them so). Hardlinks look like files on the host.
const uint32_t objects_base = 0x8aaa0000u, object_bytes = 0x200u, object_text = 0x70u;
const uint32_t objects_max = (0x8ab00000u - objects_base) / object_bytes;
std::vector<std::string> object_paths;

std::string link_text(const std::string& where) {
    char text[1024];
    const ssize_t n = readlink(where.c_str(), text, sizeof(text) - 1);
    if (n < 0) return std::string();
    std::string alias(text, size_t(n));
    char real[PATH_MAX];
    const std::string root = shared->root, resolved = realpath(shared->root, real) ? real : root;
    for (const std::string& host_root : {root, resolved})
        if (alias.compare(0, host_root.size(), host_root) == 0 &&
            (alias.size() == host_root.size() || alias[host_root.size()] == '/'))
            return "/yaffs2" + alias.substr(host_root.size());
    return alias;
}
uint32_t object_type(const std::string& where) {
    struct stat st;
    if (lstat(where.c_str(), &st)) return 0;
    return S_ISLNK(st.st_mode) ? 2u : S_ISDIR(st.st_mode) ? 3u : 1u;
}
std::string parent_of(const std::string& where) {
    if (where == shared->root) return std::string();
    return where.substr(0, where.rfind('/'));
}
uint32_t object_at(const std::string& where) {
    if (where.empty()) return 0;
    uint32_t i = 0;
    while (i != object_paths.size() && object_paths[i] != where) ++i;
    if (i == object_paths.size()) {
        if (i == objects_max) crash("simulated yaffs objects exhausted");
        object_paths.push_back(where);
    }
    const uint32_t at = objects_base + i * object_bytes;
    memset(host(at), 0, object_bytes);
    w32(at + 0x1c, object_at(parent_of(where)));
    w32(at + 0x64, object_type(where));
    if (r32(at + 0x64) == 2) {
        const std::string alias = link_text(where);
        if (alias.size() >= object_bytes - object_text) crash("simulated link text too long");
        memcpy(host(at + object_text), alias.c_str(), alias.size() + 1);
        w32(at + 0x68, at + object_text);
    }
    return at;
}
std::string object_path(uint32_t at) {
    const uint32_t i = (at - objects_base) / object_bytes;
    if (at < objects_base || i >= object_paths.size() || (at - objects_base) % object_bytes) crash("not a yaffs object");
    return object_paths[i];
}
std::string find_path(const std::string& relative, const char* path, int depth, std::string* parent, int* loop);
// Stock FollowLink (889fa134): a link's text from the root ("/...") or from the link's own folder.
std::string follow(std::string where, int depth, int* loop) {
    while (!where.empty() && object_type(where) == 2) {
        const std::string alias = link_text(where);
        where = find_path(alias[0] == '/' ? std::string() : parent_of(where), alias.c_str(), depth++, 0, loop);
    }
    return where;
}
std::string find_path(const std::string& relative, const char* path, int depth, std::string* parent, int* loop) {
    if (parent) parent->clear();
    if (depth > 5) { if (loop) *loop = 1; return std::string(); }
    std::string dir = relative;
    const char* rest = path;
    if (dir.empty()) {
        if (!mounted() || strncmp(path, "/yaffs2", 7) || (path[7] && path[7] != '/')) return std::string();
        dir = shared->root; rest = path + 7;
    }
    for (;;) {
        while (*rest == '/') ++rest;
        const char* name = rest;
        while (*rest && *rest != '/') ++rest;
        const std::string part(name, size_t(rest - name));
        const char* after = rest;
        while (*after == '/') ++after;
        if (!*after) {                                  // the final name: not followed
            if (parent) *parent = dir;
            if (part.empty() || part == ".") return dir;
            if (part == "..") return parent_of(dir);
            const std::string where = dir + "/" + part;
            return object_type(where) ? where : std::string();
        }
        if (part == "..") dir = parent_of(dir);
        else if (part != ".") {
            const std::string where = dir + "/" + part;
            dir = object_type(where) ? follow(where, depth, loop) : std::string();
            if (!dir.empty() && object_type(dir) != 3) dir.clear();
        }
        if (dir.empty()) return std::string();
    }
}
uint32_t find_object(uint32_t relative, const char* path, int depth, uint32_t* parent, int* notdir, int* loop) {
    (void)notdir;
    std::string folder;
    const std::string where = find_path(relative ? object_path(relative) : std::string(), path, depth, &folder, loop);
    if (parent) *parent = object_at(folder);
    return object_at(where);
}
