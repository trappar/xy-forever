#ifndef XYF_CONVERGE_HPP
#define XYF_CONVERGE_HPP
#include <stdint.h>
#include "xyf_config.hpp"
// Converge before boot (#197). The device boots once its actual state is the state its inputs ask for. The inputs:
// the base, the mod selection (its built= text and count), Mod memory and safe mode. The actual state: the saved
// record (config.json), the stamp the record's slot holds now, the library's default and which content is installed.
// plan() compares the two and returns only the operations still needed, always in this order: build (the selection
// into the spare slot), switch (the target's content), record (config.json), default (the target); the boot follows.
// A switch always brings the record and the default after it: the config job ends the switch's journal. Running a
// plan and planning again returns nothing (tests/native/converge_test.cpp). xyf_mods_io's converge sequence runs the plan.
// Pure and header-only: static functions (an inline, weak function is a register call on Blackfin).
namespace xyf { namespace converge {
enum Op : uint32_t { op_build = 1, op_switch = 2, op_record = 4, op_default = 8 };
struct Inputs {
    const char* base;                   // the stock library entry the setup is built on
    const char* built;                  // built= of the selection (config::built_text); "": unknown, it rebuilds
    uint32_t count, expanded, safe;     // mods selected; Mod memory's 4 MiB layout; safe mode
};
struct Actual {
    const config::Record* record;       // the saved record (state present; else no setup is saved)
    const char* stamp; // the record's slot's info.txt sha256= now ("": not in the library, unreadable)
    const char* default_id; // the library's default entry ("": none)
    uint32_t base_installed, slot_installed; // the base's / the record's slot's content is installed
};
struct Plan {
    uint32_t ops; // Op bits
    const char* target; // the entry that boots: the base or the record's slot (0: the slot a build makes)
};
// Safe mode as a record keeps it (config::parse: it needs mods and a slot).
static inline uint32_t safe_kept(const Inputs& in, const config::Record& r) {
    return in.safe && in.count && r.slot[0] ? 1u : 0u;
}
// The record already says these inputs, as a record written for them would. No record: no mods, standard memory.
static inline bool recorded(const Inputs& in, const config::Record& r) {
    if (r.state != config::present) return !in.count && !in.expanded;
    return config::same(r.base, in.base) && r.count == in.count && r.expanded == in.expanded &&
           r.safe == safe_kept(in, r) && (!in.count || in.safe || config::same(r.built, in.built));
}
// The record's slot still holds the selection's build: built= the same and its stamp unchanged.
static inline bool built(const Inputs& in, const Actual& a) {
    const config::Record& r = *a.record;
    return r.state == config::present && r.slot[0] && r.stamp[0] && in.built[0] && config::same(r.built, in.built) &&
           config::same(a.stamp, r.stamp);
}
static inline Plan plan(const Inputs& in, const Actual& a) {
    const config::Record& r = *a.record;
    Plan p = {0, a.default_id};
    if (r.state != config::present && !in.count && !in.expanded) return p; // no setup: the default boots as it is
    const bool mods = in.count && !in.safe; // safe mode: the base with its official content
    if (mods && !built(in, a)) {
        p.ops = op_build | op_switch | op_record | op_default;
        p.target = 0;
        return p;
    }
    p.target = mods ? r.slot : in.base;
    if (!(mods ? a.slot_installed : a.base_installed)) p.ops |= op_switch | op_record | op_default;
    if (!recorded(in, r)) p.ops |= op_record;
    if (!config::same(a.default_id, p.target)) p.ops |= op_default;
    return p;
}
// The fingerprint (step B): after every convergence the sequence stores mark_path, the inputs' SHA-256 (the record's
// bytes selected by the configuration reader; base, profile and generation, mods=, built=,
// memory=, safe=) and what they converged to (the default). A plain power-on (file_boot.cpp a_converge) boots at once
// when the stored text is the one these inputs and the library's default make and nothing is in doubt (Doubt), else B
// converges first. Any other text (missing, another format, malformed) is no match: the sequence writes it again.
static const char mark_path[] = "/yaffs2/xy-forever/converged.txt";
static const char mark_old[] = "/yaffs2/xy-forever/converged.old";
static const char mark_part[] = "/yaffs2/xy-forever/converged.txt.part";
static const uint32_t mark_max = 160;
enum Doubt : uint32_t { doubt_mark = 1, doubt_job = 2, doubt_switching = 4, doubt_default = 8 };
// "format=1\ninputs=<64 hex>\ndefault=<id>\n" into out: bytes, 0 when it does not fit.
static inline uint32_t mark_text(char* out, uint32_t capacity, const uint8_t* digest, const char* target) {
    static const char hex[] = "0123456789abcdef";
    config::Out o = {out, out + capacity, true};
    config::put(o, "format=1\ninputs=");
    char d[3];
    d[2] = 0;
    for (uint32_t i = 0; i != 32; ++i) {
        d[0] = hex[digest[i] >> 4];
        d[1] = hex[digest[i] & 15u];
        config::put(o, d);
    }
    config::put(o, "\ndefault=");
    config::put(o, target);
    config::put(o, "\n");
    return o.ok ? uint32_t(o.at - out) : 0u;
}
static inline bool mark_same(const char* want, uint32_t n, const uint8_t* stored, uint32_t length) {
    if (!n || n != length) return false;
    for (uint32_t i = 0; i != n; ++i)
        if (uint8_t(want[i]) != stored[i]) return false;
    return true;
}
}}
#endif
