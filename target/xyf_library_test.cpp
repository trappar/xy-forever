// Host test for the XY Forever library scanner with an in-memory filesystem
// that offers the same direct_* calls as the cold read-only storage session.
#include "xyf_library.hpp"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
using namespace xyf;
using xy_forever::services::Error;
static unsigned checks;
static void require(bool ok, int line) { ++checks; if (!ok) { fprintf(stderr, "check %u (line %d) failed\n", checks, line); abort(); } }
#define REQUIRE(x) require((x), __LINE__)

struct File { const char* path; const char* text; };
struct Dir { const char* path; const char* names[24]; bool regular[24]; unsigned count; };
struct FakeFs {
    File files[32]; unsigned file_count;
    Dir dirs[4]; unsigned dir_count;
    struct Open { const File* file; uint32_t position; } open[8];
    int open_fds, open_dirs;
    const Dir* dir; unsigned dir_at; uint8_t dirent[80];
    bool fail_closedir, opened_layout;
    int direct_open(const char* path) {
        if (strstr(path, "/layout.json")) opened_layout = true;
        for (unsigned i = 0; i != file_count; ++i)
            if (!strcmp(files[i].path, path))
                for (int h = 0; h != 8; ++h) if (!open[h].file) { open[h].file = &files[i]; open[h].position = 0; ++open_fds; return h; }
        return -1;
    }
    int direct_read(int fd, uint8_t* out, uint32_t count) {
        Open& o = open[fd];
        const char* text = o.file->text ? o.file->text : "LDR";
        const uint32_t size = uint32_t(strlen(text));
        if (count > size - o.position) count = size - o.position;
        memcpy(out, text + o.position, count); o.position += count; return int(count);
    }
    int direct_close(int fd) { open[fd].file = 0; --open_fds; return 0; }
    uint32_t direct_opendir(const char* path) {
        for (unsigned i = 0; i != dir_count; ++i)
            if (!strcmp(dirs[i].path, path)) { dir = &dirs[i]; dir_at = 0; ++open_dirs; return 0x1234; }
        return 0;
    }
    const uint8_t* direct_readdir(uint32_t handle) {
        if (handle != 0x1234 || dir_at == dir->count) return 0;
        memset(dirent, 0xee, sizeof(dirent));
        dirent[library::dirent_type] = dir->regular[dir_at] ? 8 : 4;
        strcpy(reinterpret_cast<char*>(dirent + library::dirent_name), dir->names[dir_at]);
        ++dir_at; return dirent;
    }
    int direct_closedir(uint32_t) { --open_dirs; return fail_closedir ? -1 : 0; }
};

static uint8_t scratch[library::scratch_needed];
static library::Library lib;

int main() {
    static FakeFs fs;
    unsigned n = 0;
    fs.files[n++] = {"/yaffs2/xy-forever/state.txt", "default=opxy-121\nonce=granular\n"};
    fs.files[n++] = {"/yaffs2/xy-forever/firmware/opxy-121/info.txt", "name=OP-XY 1.2.1\nkind=official\nbytes=2373336\ncrc32=89d08ddd\n"};
    fs.files[n++] = {"/yaffs2/xy-forever/firmware/opxy-121/os.ldr", 0};
    fs.files[n++] = {"/yaffs2/xy-forever/firmware/granular/os.ldr", 0};
    fs.files[n++] = {"/yaffs2/xy-forever/firmware/granular/info.txt", "machines=2\n"};
    fs.files[n++] = {"/yaffs2/xy-forever/firmware/granular/layout.json", "invalid external leftovers"};
    fs.files[n++] = {"/yaffs2/xy-forever/firmware/broken/info.txt", "name=Broken\nbytes=10\n"};
    fs.files[n++] = {"/yaffs2/xy-forever/firmware/broken/os.ldr", 0};
    fs.file_count = n;
    fs.dirs[0] = {"/yaffs2/xy-forever/firmware", {".", "..", "opxy-121", "granular", "broken", "Bad Name", "notes.txt", "noimage",
                  "granular"}, {false, false, false, false, false, false, true, false, false}, 9};
    fs.dir_count = 1;

    REQUIRE(library::scan_direct(fs, lib, scratch, 100) == Error::capacity);
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok);
    REQUIRE(fs.open_fds == 0 && fs.open_dirs == 0);
    REQUIRE(lib.has_state && !strcmp(lib.state.default_id, "opxy-121") && !strcmp(lib.state.once, "granular"));
    REQUIRE(lib.count == 4);                                   // broken, granular, noimage, opxy-121 (sorted)
    REQUIRE(!strcmp(lib.entries[0].id, "broken") && !strcmp(lib.entries[1].id, "granular"));
    REQUIRE(!strcmp(lib.entries[2].id, "noimage") && !strcmp(lib.entries[3].id, "opxy-121"));
    REQUIRE(lib.entries[0].usable && lib.entries[0].info.has_bytes && lib.entries[0].info.bytes == 10);
    REQUIRE(lib.entries[1].usable && lib.entries[1].machines == 2 && !fs.opened_layout);
    REQUIRE(!strcmp(lib.entries[1].info.name, "granular"));   // no info.txt -> id as name
    REQUIRE(!lib.entries[2].usable && !lib.entries[2].image_present);
    REQUIRE(lib.entries[3].usable && lib.entries[3].info.kind == store::kind_official &&
            lib.entries[3].info.crc32 == 0x89d08dddu && !strcmp(lib.entries[3].info.name, "OP-XY 1.2.1"));
    REQUIRE(lib.skipped == 3);                                 // "Bad Name", duplicate "granular", noimage

    // Count metadata is optional. Leftover layout.json never supplies a selection or affects boot.
    fs.files[4].text = "machines=invalid\n";
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok);
    REQUIRE(lib.entries[1].usable && lib.entries[1].machines == 0 && !fs.opened_layout);
    fs.files[4].text = "machines=0\n";
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok);
    REQUIRE(lib.entries[1].usable && lib.entries[1].info.has_machines && lib.entries[1].machines == 0);

    uint32_t index = 99;
    REQUIRE(library::boot_choice(lib, index) == library::Choice::once && index == 1);
    strcpy(lib.state.once, "noimage");
    REQUIRE(library::boot_choice(lib, index) == library::Choice::by_default && index == 3);
    strcpy(lib.state.default_id, "missing");
    REQUIRE(library::boot_choice(lib, index) == library::Choice::menu);

    // A text file exactly at capacity is accepted; one byte more is rejected.
    static char exact[4097]; memset(exact, '#', 4096); exact[4096] = 0;
    fs.files[0].text = exact;
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok && lib.has_state);
    static char over[4098]; memset(over, '#', 4097); over[4097] = 0;
    fs.files[0].text = over;
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok && !lib.has_state);
    REQUIRE(fs.open_fds == 0);
    fs.fail_closedir = true;
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::directory && lib.count == 0);
    fs.fail_closedir = false;
    // An assembled mod set (info.txt with generation) is listed, its content not installed: not usable,
    // never the boot choice even as default or once (content_installed).
    fs.files[0].text = "default=mods-a\nonce=mods-a\n";
    fs.files[fs.file_count++] = {"/yaffs2/xy-forever/firmware/mods-a/info.txt",
        "type=firmware\nname=OP-XY mods\nkind=open-xy\nbytes=10\ngeneration=0.0.6-370a949e96aa\n"};
    fs.files[fs.file_count++] = {"/yaffs2/xy-forever/firmware/mods-a/os.ldr", 0};
    fs.dirs[0].names[1] = "mods-a"; fs.dirs[0].regular[1] = false;
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok);
    const int32_t mods = library::find(lib, "mods-a");
    REQUIRE(mods >= 0 && lib.entries[mods].image_present && lib.entries[mods].content_missing &&
            !lib.entries[mods].usable && !strcmp(lib.entries[mods].info.name, "OP-XY mods"));
    REQUIRE(!library::content_installed(lib.content, lib.entries[mods]) && library::content_installed(lib.content, lib.entries[0]));
    REQUIRE(library::boot_choice(lib, index) == library::Choice::menu);
    // The content state record decides. Installed for mods-a: mods-a usable (the boot choice), the
    // stock entries not; switching (or a malformed record): nothing; official again: the stock entries.
    const int32_t stock = library::find(lib, "opxy-121");
    REQUIRE(stock >= 0 && lib.entries[stock].usable && lib.content.kind == content::official);
    fs.files[fs.file_count++] = {"/yaffs2/xy-forever/content-state.txt", "state=installed\nentry=mods-a\nfile=display/x.svg\n"};
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok && lib.content.kind == content::installed);
    REQUIRE(lib.entries[mods].usable && !lib.entries[mods].content_missing && !lib.entries[stock].usable &&
            lib.entries[stock].content_missing);
    REQUIRE(library::boot_choice(lib, index) == library::Choice::once && index == uint32_t(mods));
    const char* const unbootable[4] = {"state=switching\ntarget=mods-a\nfile=display/x.svg\n", "state=installed\nentry=mods-a",
                                       "state=installed\nentry=Mods-a\n", "garbage"};
    for (const char* text : unbootable) {
        fs.files[fs.file_count - 1].text = text;
        REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok && lib.content.kind == content::switching);
        REQUIRE(!lib.entries[mods].usable && !lib.entries[stock].usable && library::boot_choice(lib, index) == library::Choice::menu);
    }
    fs.files[fs.file_count - 1].text = "state=installed\nentry=mods-b\n";
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok && !lib.entries[mods].usable && !lib.entries[stock].usable);
    fs.files[fs.file_count - 1].path = "/yaffs2/xy-forever/content-state.old";   // only the .old: a cut record write
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok && lib.content.kind == content::switching);
    REQUIRE(!lib.entries[mods].usable && !lib.entries[stock].usable);
    fs.files[fs.file_count - 1].path = "/yaffs2/xy-forever/content-state.txt";
    fs.files[fs.file_count - 1].text = "state=official\n";
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok && !lib.entries[mods].usable && lib.entries[stock].usable);
    REQUIRE(fs.open_fds == 0);
    --fs.file_count;
    fs.dir_count = 0;                                          // no firmware folder: empty library, not an error
    REQUIRE(library::scan_direct(fs, lib, scratch, sizeof(scratch)) == Error::ok && lib.count == 0);
    REQUIRE(library::boot_choice(lib, index) == library::Choice::menu);
    printf("%u checks passed\n", checks);
    return 0;
}
