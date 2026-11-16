// Host driver for native/mod_link (plan 5.4b): feeds the files the linker asks for from a package
// folder on disk and prints the result for xy_forever/modlink.py.
//   mod_link_bridge GENERATION_DIR OUT_MODULE [-m machine]... [-e extension]... [-l LAYOUT]
//                   [-2 PACKAGE/PATH=FILE]   (FILE in place of that file from its second request on)
//   mod_link_bridge --numbers RULES CASE        (numberLayout alone)
//   mod_link_bridge --objects FILE...           (readObject alone)
// RULES: lines "<key> <n>[,<n>...]" (profile.identities). LAYOUT: lines "<id> <slot 0-2> <n> <page>".
// CASE: lines "fixed|layout|machine <id> <kind 0-5> <slot 0-2> <n|-> <page|->".
// Output lines: "status <index> <detail>", "step <n>", "sizes ...", "service", "extensions",
// "object", "number", "name <kind> <value> <name>", "usage ...", "scratch", "largest", "slowest",
// "work <units> <step>" (the most work units one step took: XYF_WORK, built with -DXYF_MOD_WORK_COUNT).
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "../native/mod_internal.hpp"

using namespace xyf_core;
using namespace xyf_core::mod_link;

namespace xyf_core { namespace mod_link { unsigned long long xyf_mod_work = 0; } }
static const U32 max_steps = 100000;       // a link that never finishes is reported, not waited for

static bool load(const char* path, std::vector<Byte>& out) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return false;
    out.clear();
    Byte buffer[65536];
    size_t n;
    while ((n = std::fread(buffer, 1, sizeof buffer, f)) > 0) out.insert(out.end(), buffer, buffer + n);
    std::fclose(f);
    return true;
}

static void list(const char* text, U32* values, U32& count) {
    count = 0;
    while (*text && count < max_rule_list) {
        char* end;
        values[count++] = U32(std::strtoul(text, &end, 10));
        text = *end == ',' ? end + 1 : end;
        if (end == text && *text) break;
    }
}

static bool read_rules(const char* path, Rules& rules) {
    std::memset(&rules, 0, sizeof rules);
    FILE* f = std::fopen(path, "r");
    if (!f) return false;
    char key[64], value[4096];
    while (std::fscanf(f, "%63s %4095s", key, value) == 2) {
        U32 one = U32(std::strtoul(value, 0, 10));
        if (!std::strcmp(key, "engines")) rules.engines = one;
        else if (!std::strcmp(key, "engine_none")) rules.engine_none = one;
        else if (!std::strcmp(key, "pages")) rules.pages = one;
        else if (!std::strcmp(key, "page_none")) rules.page_none = one;
        else if (!std::strcmp(key, "effect_page")) rules.effect_page = one;
        else if (!std::strcmp(key, "stock_engines")) list(value, rules.stock_engines, rules.stock_engine_count);
        else if (!std::strcmp(key, "sample_pages")) list(value, rules.sample_pages, rules.sample_page_count);
        else if (!std::strcmp(key, "effects")) list(value, rules.effects, rules.effect_count);
        else if (!std::strcmp(key, "players")) list(value, rules.players, rules.player_count);
    }
    std::fclose(f);
    return true;
}

static void optional(const char* text, bool& has, int& value) {
    has = std::strcmp(text, "-") != 0;
    value = has ? std::atoi(text) : 0;
}

static int numbers_only(const char* rules_path, const char* case_path) {
    static Rules rules;
    if (!read_rules(rules_path, rules)) return 2;
    FILE* f = std::fopen(case_path, "r");
    if (!f) return 2;
    std::vector<Number> fixed, layout;
    std::vector<std::string> ids;
    std::vector<numbers::Machine> machines;
    char role[16], id[64], n[32], page[32];
    unsigned kind, slot;
    ids.reserve(4096);
    while (std::fscanf(f, "%15s %63s %u %u %31s %31s", role, id, &kind, &slot, n, page) == 6) {
        Number e; std::memset(&e, 0, sizeof e);
        std::strncpy(e.id, id, id_bytes - 1);
        e.identity.slot = slot;
        optional(n, e.identity.has_number, e.identity.number);
        optional(page, e.identity.has_page, e.identity.page);
        if (!std::strcmp(role, "fixed")) fixed.push_back(e);
        else if (!std::strcmp(role, "layout")) layout.push_back(e);
        else {
            ids.push_back(id);
            numbers::Machine m; m.id = 0; m.kind = kind; m.requested = e.identity;
            machines.push_back(m);
        }
    }
    std::fclose(f);
    for (size_t i = 0; i != machines.size(); ++i) machines[i].id = ids[i].c_str();
    const U32 capacity = 1024;
    std::vector<U32> work(numbers::work_bytes(capacity) / 4 + 1);
    std::vector<Number> out(capacity);
    char detail[detail_bytes] = {0};
    Text text = {detail, detail_bytes, 0};
    U32 count = 0;
    Status status = numbers::layout(rules, fixed.data(), U32(fixed.size()), layout.data(), U32(layout.size()),
                                    machines.data(), U32(machines.size()), reinterpret_cast<Byte*>(work.data()),
                                    out.data(), capacity, count, text);
    std::printf("status %u %s\n", unsigned(status), detail);
    for (U32 i = 0; i != count && status == Status::ok; ++i)
        std::printf("number %s %u %d %d\n", out[i].id, out[i].identity.slot, out[i].identity.number, out[i].identity.page);
    return status == Status::ok ? 0 : 1;
}

// readObject's checks alone on each file: one "status <index> <section name>" line per file.
static int objects_only(int count, char** files) {
    std::vector<Byte> work(elf::work_bytes(elf::max_bytes)), data;
    for (int i = 0; i != count; ++i) {
        if (!load(files[i], data)) return 2;
        elf::Header h;
        const Byte* name = 0; U32 length = 0;
        Status status = elf::check(data.empty() ? 0 : data.data(), U32(data.size()), work.data(), U32(work.size()), h, name, length);
        std::printf("status %u %.*s\n", unsigned(status), int(length), reinterpret_cast<const char*>(name));
    }
    return 0;
}

int main(int argc, char** argv) {
    if (argc == 4 && !std::strcmp(argv[1], "--numbers")) return numbers_only(argv[2], argv[3]);
    if (argc >= 2 && !std::strcmp(argv[1], "--objects")) return objects_only(argc - 2, argv + 2);
    if (argc == 3 && !std::strcmp(argv[1], "--records")) {    // scratch_bytes with that records limit
        Limits limits; limits.records = U32(std::strtoul(argv[2], 0, 10));
        std::printf("%u\n", scratch_bytes(limits));
        return 0;
    }
    if (argc < 3) { std::fprintf(stderr, "usage\n"); return 2; }
    std::vector<const char*> machines, extensions;
    std::vector<Number> layout;
    bool has_layout = false;
    std::string swap_name, swap_file;
    U32 swap_requests = 0;
    for (int i = 3; i + 1 < argc; i += 2) {
        if (!std::strcmp(argv[i], "-m")) machines.push_back(argv[i + 1]);
        else if (!std::strcmp(argv[i], "-e")) extensions.push_back(argv[i + 1]);
        else if (!std::strcmp(argv[i], "-2") && std::strchr(argv[i + 1], '=')) {
            swap_name.assign(argv[i + 1], std::strchr(argv[i + 1], '='));
            swap_file = std::strchr(argv[i + 1], '=') + 1;
        }
        else if (!std::strcmp(argv[i], "-l")) {
            has_layout = true;
            FILE* f = std::fopen(argv[i + 1], "r");
            if (!f) return 2;
            char id[64]; unsigned slot; int number, page;
            while (std::fscanf(f, "%63s %u %d %d", id, &slot, &number, &page) == 4) {
                Number n; std::memset(&n, 0, sizeof n);
                std::strncpy(n.id, id, id_bytes - 1);
                n.identity.slot = slot; n.identity.has_number = n.identity.has_page = true;
                n.identity.number = number; n.identity.page = page;
                layout.push_back(n);
            }
            std::fclose(f);
        }
    }
    Selection sel;
    sel.machines = machines.data(); sel.machine_count = U32(machines.size());
    sel.extensions = extensions.data(); sel.extension_count = U32(extensions.size());
    sel.layout = layout.data(); sel.layout_count = U32(layout.size()); sel.has_layout = has_layout;
    Limits limits;
    U32 bytes = scratch_bytes(limits);
    std::vector<U32> scratch(bytes / 4 + 1);
    Workspace ws = {reinterpret_cast<Byte*>(scratch.data()), bytes};
    const U32 module_capacity = 2080768u;
    std::vector<Byte> module(module_capacity);
    Result result;
    Status status = begin(sel, limits, ws, module.data(), module_capacity, result);
    std::vector<Byte> file;
    size_t largest = 0;
    double slowest = 0;
    std::string slowest_file;
    Request request;
    U32 zero_steps = 0, steps = 0, work_step = 0;
    unsigned long long work = 0;
    while (status == Status::ok && next(ws, request)) {
        if (++steps > max_steps) { std::printf("runaway %u\n", steps); return 4; }
        std::string path = std::string(argv[1]) + "/" + request.package + "/" + request.path;
        if (std::string(request.package) + "/" + request.path == swap_name && ++swap_requests > 1) path = swap_file;
        if (!*request.path) { file.clear(); ++zero_steps; }      // a zero step: no file
        else if (!load(path.c_str(), file)) { std::printf("missing %s/%s\n", request.package, request.path); return 3; }
        if (file.size() > largest) largest = file.size();
        std::string name = std::string(request.package) + "/" + request.path;
        auto start = std::chrono::steady_clock::now();
        xyf_mod_work = 0;
        status = step(ws, file.empty() ? 0 : file.data(), U32(file.size()), result);
        if (xyf_mod_work > work) { work = xyf_mod_work; work_step = result.step; }
        double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count();
        if (us > slowest) { slowest = us; slowest_file = name; }
    }
    std::printf("status %u %s\n", unsigned(result.status), result.detail);
    std::printf("step %u\n", result.step);
    std::printf("zero_steps %u\n", zero_steps);
    std::printf("scratch %u\nlargest %zu\nobject_bytes %u\n", bytes, largest, result.object_bytes);
    std::printf("slowest %.0f %s\n", slowest, slowest_file.c_str());
    std::printf("work %llu %u\n", work, work_step);
    Usage u; std::memset(&u, 0, sizeof u);
    usage(ws, u);
    std::printf("usage objects=%u sections=%u names=%u records=%u text=%u absent=%u fallbacks=%u numbers=%u\n",
                u.objects, u.sections, u.names, u.records, u.text_bytes, u.absent, u.fallbacks, u.numbers);
    if (result.status != Status::ok) return 1;
    std::printf("sizes %u %u %u %u\n", result.sizes.initialized, result.sizes.bss, result.sizes.allocated, result.sizes.available);
    const char* name; U32 length, value, kind;
    for (U32 i = 0; service_at(ws, i, name, length); ++i)
        if (result.services & (1u << i)) std::printf("service %.*s\n", int(length), name);
    std::printf("extensions %u\n", result.extensions);
    for (U32 i = 0; object_at(ws, i, name, length); ++i) std::printf("object %.*s\n", int(length), name);
    Number n;
    for (U32 i = 0; number_at(ws, i, n); ++i)
        std::printf("number %s %u %d %d\n", n.id, n.identity.slot, n.identity.number, n.identity.page);
    for (U32 i = 0; name_at(ws, i, name, length, value, kind); ++i)
        if (kind) std::printf("name %u %u %.*s\n", kind, value, int(length), name);
    FILE* out = std::fopen(argv[2], "wb");
    if (!out || std::fwrite(module.data(), 1, result.sizes.initialized, out) != result.sizes.initialized) return 2;
    std::fclose(out);
    return 0;
}
