// target/xyf_converge.hpp (#197): the plan table, then every combination of inputs and actual state run through a
// model of the operations and planned again (nothing left).
#include "doctest.h"
#include "xyf_converge.hpp"
#include <stdio.h>
#include <string.h>
namespace xc = xyf::converge;
namespace xg = xyf::config;
namespace {
const char base[] = "op-1.2.1", other_base[] = "op-1.2.2";
// The device as the operations change it: the record, each slot's stamp, the default, the content installed.
struct World {
    xg::Record rec;
    char stamp[2][65];                  // mods-a, mods-b
    char def[32], installed[32];
    uint32_t builds;
};
const char* slot_name(uint32_t i) { return i ? "mods-b" : "mods-a"; }
uint32_t slot_index(const char* id) { return strcmp(id, "mods-b") == 0 ? 1u : strcmp(id, "mods-a") == 0 ? 0u : 2u; }
void set(char* out, uint32_t room, const char* s) { snprintf(out, room, "%s", s); }
xc::Actual actual(const World& w) {
    const uint32_t s = slot_index(w.rec.slot);
    return xc::Actual{&w.rec, s < 2 ? w.stamp[s] : "", w.def, 0, w.rec.slot[0] && strcmp(w.installed, w.rec.slot) == 0};
}
// The actual state for the inputs: their base's content installed or not.
xc::Actual actual_for(const World& w, const xc::Inputs& in) {
    xc::Actual a = actual(w);
    a.base_installed = strcmp(w.installed, in.base) == 0;
    return a;
}
// Runs plan p's operations on w as the converge sequence does (xyf_mods_save.ipp).
void run(const xc::Plan& p, const xc::Inputs& in, World& w) {
    char slot[8], stamp[65];
    set(slot, sizeof(slot), w.rec.slot);
    set(stamp, sizeof(stamp), w.rec.stamp);
    if (p.ops & xc::op_build) {                         // the spare slot: never the record's
        const uint32_t spare = slot_index(w.rec.slot) == 0 ? 1u : 0u;
        set(slot, sizeof(slot), slot_name(spare));
        snprintf(w.stamp[spare], sizeof(w.stamp[spare]), "%064u", ++w.builds);
        set(stamp, sizeof(stamp), w.stamp[spare]);
    }
    const char* target = p.target ? p.target : slot;
    char kept[32];
    set(kept, sizeof(kept), target);
    if (p.ops & xc::op_switch) set(w.installed, sizeof(w.installed), kept);
    if (p.ops & xc::op_record) {
        xg::Record& r = w.rec;
        const bool built = (p.ops & xc::op_build) != 0;
        if (built) set(r.built, sizeof(r.built), in.built);
        set(r.slot, sizeof(r.slot), slot);
        set(r.stamp, sizeof(r.stamp), stamp);
        r.state = xg::present;
        set(r.base, sizeof(r.base), in.base);
        r.count = in.count;
        r.expanded = in.expanded;
        r.safe = xc::safe_kept(in, r);
    }
    if (p.ops & xc::op_default) set(w.def, sizeof(w.def), kept);
}
World world(uint32_t state, uint32_t count, uint32_t slot, const char* built, uint32_t stamp_ok, uint32_t safe,
            uint32_t expanded, const char* rec_base, const char* def, const char* installed) {
    World w;
    memset(&w, 0, sizeof(w));
    snprintf(w.stamp[0], 65, "%064u", 7u);
    snprintf(w.stamp[1], 65, "%064u", 8u);
    xg::clear(w.rec, state ? xg::present : xg::none);
    if (state) {
        set(w.rec.base, sizeof(w.rec.base), rec_base);
        w.rec.count = count;
        w.rec.expanded = expanded;
        if (slot < 2) {
            set(w.rec.slot, sizeof(w.rec.slot), slot_name(slot));
            set(w.rec.built, sizeof(w.rec.built), built);
            set(w.rec.stamp, sizeof(w.rec.stamp), stamp_ok ? w.stamp[slot] : "0000");
        }
        w.rec.safe = safe && count && slot < 2;           // as config::parse reads it
    }
    set(w.def, sizeof(w.def), def);
    set(w.installed, sizeof(w.installed), installed);
    w.builds = 100;
    return w;
}
uint32_t ops(const World& w, const xc::Inputs& in) { return xc::plan(in, actual_for(w, in)).ops; }
const uint32_t B = xc::op_build, S = xc::op_switch, R = xc::op_record, D = xc::op_default;
void table() {
    const xc::Inputs none = {base, "", 0, 0, 0}, two = {base, "A", 2, 0, 0}, safe = {base, "A", 2, 0, 1};
    // No setup saved, nothing selected: the default boots as it is (whatever it is).
    CHECK(ops(world(0, 0, 2, "", 0, 0, 0, base, "mods-a", "mods-a"), none) == 0);
    // The first Save: build, switch, record, default.
    CHECK(ops(world(0, 0, 2, "", 0, 0, 0, base, base, base), two) == (B | S | R | D));
    // Saved and applied: nothing; again with Mod memory changed alone (no mods): the record only.
    const World applied = world(1, 2, 0, "A", 1, 0, 0, base, "mods-a", "mods-a");
    CHECK(ops(applied, two) == 0);
    CHECK(ops(world(1, 0, 0, "A", 1, 0, 0, base, base, base), xc::Inputs{base, "", 0, 1, 0}) == R);
    // Boot without mods: the base's content, the record (safe=1), the default. Then in safe mode: nothing.
    CHECK(ops(applied, safe) == (S | R | D));
    const World in_safe = world(1, 2, 0, "A", 1, 1, 0, base, base, base);
    CHECK(ops(in_safe, safe) == 0);
    // Restore: the slot's content back, the record (safe=0), the default; a stale slot builds first.
    CHECK(ops(in_safe, two) == (S | R | D));
    CHECK(ops(world(1, 2, 0, "A", 0, 1, 0, base, base, base), two) == (B | S | R | D));
    // The slot gone from the library ("(rebuild)"), another selection, another base: a build.
    World gone = applied;
    gone.stamp[0][0] = 0;
    CHECK(xc::plan(two, xc::Actual{&gone.rec, "", "mods-a", 0, 0}).ops == (B | S | R | D));
    CHECK(ops(applied, xc::Inputs{base, "C", 2, 0, 0}) == (B | S | R | D));
    CHECK(ops(applied, xc::Inputs{other_base, "", 2, 0, 0}) == (B | S | R | D));
    // Only the default differs (set over USB): the default; its content gone too: the switch, record, default.
    CHECK(ops(world(1, 2, 0, "A", 1, 0, 0, base, "", "mods-a"), two) == D);
    CHECK(ops(world(1, 2, 0, "A", 1, 0, 0, base, "mods-a", base), two) == (S | R | D));
    // All off saved (the slot kept as the spare): nothing; the same mods on again: no build.
    const World off = world(1, 0, 0, "A", 1, 0, 0, base, base, base);
    CHECK(ops(off, none) == 0);
    CHECK(ops(off, two) == (S | R | D));
    // Safe mode asked with no slot (never built): the base as it is.
    CHECK(ops(world(1, 2, 2, "", 0, 0, 0, base, base, base), safe) == 0);
}
// Every combination: the plan's rules hold, and after running it nothing is left to do.
void every() {
    const char* const builts[] = {"", "A", "C"};
    const char* const entries[] = {base, "mods-a", "mods-b", other_base, ""};
    uint32_t cases = 0;
    for (uint32_t state = 0; state != 2; ++state)
        for (uint32_t rc = 0; rc != 2; ++rc)
            for (uint32_t slot = 0; slot != 3; ++slot)
                for (uint32_t rb = 1; rb != 3; ++rb)
                    for (uint32_t ok = 0; ok != 2; ++ok)
                        for (uint32_t rs = 0; rs != 2; ++rs)
                            for (uint32_t re = 0; re != 2; ++re)
                                for (uint32_t rbase = 0; rbase != 2; ++rbase)
                                    for (uint32_t d = 0; d != 5; ++d)
                                        for (uint32_t i = 0; i != 4; ++i)
                                            for (uint32_t ic = 0; ic != 2; ++ic)
                                                for (uint32_t ib = 0; ib != 3; ++ib)
                                                    for (uint32_t ie = 0; ie != 2; ++ie)
                                                        for (uint32_t is = 0; is != 2; ++is)
                                                            for (uint32_t ibase = 0; ibase != 2; ++ibase) {
                                                                if (!state && (rc || slot != 2 || rb != 1 || ok || rs ||
                                                                               re || rbase))
                                                                    continue;   // no record: one row
                                                                if (ic && !ib)
                                                                    continue; // "" (unknown) is the menu's: a sequence computes it
                                                                World w = world(state, rc * 2, slot, builts[rb], ok, rs,
                                                                                re, rbase ? other_base : base,
                                                                                entries[d], entries[i]);
                                                                const xc::Inputs in = {ibase ? other_base : base,
                                                                                       ic ? builts[ib] : "", ic * 2u,
                                                                                       ie, is};
                                                                const xc::Plan p = xc::plan(in, actual_for(w, in));
                                                                CHECK((!(p.ops & B) ||
                                                                       (p.ops & S))); // a build is switched to
                                                                CHECK((
                                                                    !(p.ops & S) ||
                                                                    ((p.ops & R) &&
                                                                     (p.ops &
                                                                      D)))); // a switch: the record and default after it
                                                                CHECK((p.target == 0) == ((p.ops & B) != 0));
                                                                run(p, in, w);
                                                                const xc::Plan again = xc::plan(in, actual_for(w, in));
                                                                CHECK_MESSAGE(again.ops == 0,
                                                                              "case " << cases << ": ops " << p.ops
                                                                                      << " then " << again.ops);
                                                                ++cases;
                                                            }
    CHECK(cases > 10000);
}
// The fingerprint (step B): its exact text; the stored text matches only byte for byte (another default, digest,
// format, a longer or shorter file: no match); a text that does not fit is none.
void mark() {
    uint8_t digest[32];
    for (uint32_t i = 0; i != 32; ++i) digest[i] = uint8_t(i * 9 + 1);
    char text[xc::mark_max];
    const uint32_t n = xc::mark_text(text, sizeof(text), digest, "mods-a");
    char want[xc::mark_max];
    int w = snprintf(want, sizeof(want), "format=1\ninputs=");
    for (uint32_t i = 0; i != 32; ++i) w += snprintf(want + w, sizeof(want) - uint32_t(w), "%02x", digest[i]);
    w += snprintf(want + w, sizeof(want) - uint32_t(w), "\ndefault=mods-a\n");
    CHECK((n == uint32_t(w) && memcmp(text, want, n) == 0));
    const uint8_t* same = reinterpret_cast<const uint8_t*>(want);
    CHECK(xc::mark_same(text, n, same, n));
    CHECK((!xc::mark_same(text, n, same, n - 1) && !xc::mark_same(text, 0, same, 0)));
    char other[xc::mark_max];
    CHECK(!xc::mark_same(other, xc::mark_text(other, sizeof(other), digest, "mods-b"), same, n));
    digest[31] ^= 1;
    CHECK(!xc::mark_same(other, xc::mark_text(other, sizeof(other), digest, "mods-a"), same, n));
    char changed[sizeof(want)];
    memcpy(changed, want, n);
    changed[0] = 'F';
    CHECK(!xc::mark_same(text, n, reinterpret_cast<const uint8_t*>(changed), n));
    CHECK(xc::mark_text(other, 40, digest, "mods-a") == 0);
    CHECK(xc::mark_text(other, sizeof(other), digest, "") == n - 6); // no default: "default=" alone
}
} // namespace

TEST_CASE("converge: the plan table") { table(); }
TEST_CASE("converge: every combination, planned again, gives nothing") { every(); }
TEST_CASE("converge: the fingerprint") { mark(); }
