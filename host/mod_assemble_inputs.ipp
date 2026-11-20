// One package as the job holds it: its files.sha256 listing and every listed file, whole.
namespace record = xyf::package_record;
struct Package {
    std::string name;                         // "base", a machine id, "ext-<id>"
    std::string metadata;
    std::vector<std::pair<std::string, std::string>> listing;   // sha256 hex, path
    std::map<std::string, Bytes> files;
    Bytes metadata_view;
};
struct Inputs { std::vector<Package> packages; Bytes stock, catalog, official; };   // packages[0]: the base

bool load_package(const std::string& root, const std::string& name, Package& p) {
    p.name = name;
    const std::string folder = root + "/" + name + "/";
    struct stat st;
    if (lstat((folder + record::native_name).c_str(), &st) == 0) {
        if ((!S_ISREG(st.st_mode) && (stat((folder + record::native_name).c_str(), &st) != 0 || !S_ISREG(st.st_mode))) ||
            st.st_size > json::document_bytes) return false;
        p.metadata = record::native_name;
    } else if (errno == ENOENT) p.metadata = record::legacy_name;
    else return false;
    Bytes list;
    if (!slurp(folder + "files.sha256", list)) return false;
    std::istringstream in(std::string(list.begin(), list.end()));
    std::string line;
    while (std::getline(in, line)) {
        if (line.size() < 67 || line.compare(64, 2, "  ")) return false;
        p.listing.push_back({line.substr(0, 64), line.substr(66)});
        const std::string& path = p.listing.back().second;
        if (!slurp(folder + path, p.files[path])) return false;
    }
    return true;
}
bool load(const Job& job, Inputs& in) {
    in.packages.assign(1, Package());
    if (!load_package(job.generation, "base", in.packages[0])) return false;
    for (auto& m : job.machines) {
        in.packages.push_back(Package());
        if (!load_package(job.generation, m, in.packages.back())) return false;
    }
    for (auto& e : job.extensions) {
        in.packages.push_back(Package());
        if (!load_package(job.generation, "ext-" + e, in.packages.back())) return false;
    }
    for (auto& m : job.unlinked) {
        in.packages.push_back(Package());
        if (!load_package(job.generation, m, in.packages.back())) return false;
    }
    return slurp(job.stock, in.stock) && slurp(job.catalog, in.catalog) && slurp(job.official, in.official);
}
const Package* package_named(const Inputs& in, const char* name) {
    for (auto& p : in.packages) if (p.name == name) return &p;
    return 0;
}
const std::string* listed_sha(const Package& p, const std::string& path) {
    for (auto& f : p.listing) if (f.second == path) return &f.first;
    return 0;
}

const Bytes* file_data(const Package& p, const std::string& path) {
    const auto it = p.files.find(path);
    return it == p.files.end() ? nullptr : &it->second;
}
const Bytes* info_data(const Package& p) {
    return p.metadata == record::native_name ? &p.metadata_view : file_data(p, record::legacy_name);
}
json::Status metadata_view(Package& p, xyf::manifest::Type expected) {
    const Bytes* raw = file_data(p, p.metadata);
    if (!raw || raw->size() > json::document_bytes) return json::size;
    record::Workspace work;
    p.metadata_view.resize(xyf::manifest::max_text);
    uint32_t bytes = 0;
    const auto status = record::view(reinterpret_cast<const char*>(raw->data()), U32(raw->size()), work, expected,
                                     reinterpret_cast<char*>(p.metadata_view.data()), bytes);
    p.metadata_view.resize(status == json::ok ? bytes : 0);
    return status;
}

// The key/value adapter used by the legacy linker and patch interfaces.
std::map<std::string, std::string> info_of(const Package& p) {
    std::map<std::string, std::string> out;
    const Bytes* data = info_data(p);
    if (!data) return out;
    std::istringstream in(std::string(data->begin(), data->end()));
    std::string line;
    while (std::getline(in, line)) {
        const size_t eq = line.find('=');
        if (line.empty() || line[0] == '#' || eq == std::string::npos) continue;
        out[line.substr(0, eq)] = line.substr(eq + 1);
    }
    return out;
}
bool number_of(const std::map<std::string, std::string>& info, const char* key, U32& value) {
    auto it = info.find(key);
    if (it == info.end() || it->second.empty()) return false;
    char* end = 0;
    const unsigned long long v = std::strtoull(it->second.c_str(), &end, 0);
    if (*end || v > 0xffffffffull) return false;
    value = U32(v);
    return true;
}
