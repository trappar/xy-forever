#include "xyf_state.hpp"
#include "xyf_crc32.hpp"
namespace xyf { namespace state {
namespace {
void zero(void* p, uint32_t n) { for (uint32_t i = 0; i != n; ++i) static_cast<char*>(p)[i] = 0; }
void copy_state(store::State& to, const store::State& from) {
    const char* f = reinterpret_cast<const char*>(&from); char* t = reinterpret_cast<char*>(&to);
    for (uint32_t i = 0; i != sizeof(store::State); ++i) t[i] = f[i];
}
bool hex8(const char* s, uint32_t n, uint32_t& out) {
    if (n != 8) return false;
    uint32_t v = 0;
    for (uint32_t i = 0; i != 8; ++i) {
        const char c = s[i];
        const uint32_t d = c >= '0' && c <= '9' ? uint32_t(c - '0') : c >= 'a' && c <= 'f' ? uint32_t(c - 'a' + 10) : 16;
        if (d == 16) return false;
        v = v << 4 | d;
    }
    out = v;
    return true;
}
struct SeqVisit { bool found; uint32_t seq; };
void seq_visit(void* context, const char* key, const char* value) {
    SeqVisit& v = *static_cast<SeqVisit*>(context);
    if (!(key[0] == 's' && key[1] == 'e' && key[2] == 'q' && !key[3])) return;
    uint32_t n = 0; bool ok = *value != 0;
    for (; *value; ++value) {
        if (*value < '0' || *value > '9' || n > 429496728u) { ok = false; break; }
        n = n * 10 + uint32_t(*value - '0');
    }
    if (ok) { v.found = true; v.seq = n; }
}
uint32_t decimal(uint32_t v, char* out) {           // no division (no libgcc in the cold build)
    static const uint32_t powers[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
    uint32_t n = 0;
    for (uint32_t p = 0; p != 10; ++p) {
        char d = '0';
        while (v >= powers[p]) { v -= powers[p]; ++d; }
        if (n || d != '0' || p == 9) out[n++] = d;
    }
    out[n] = 0;
    return n;
}
}

bool parse_slot(const char* text, uint32_t length, Slot& out) {
    zero(&out, sizeof(out));
    if (!text || length < 16 || length > store::max_text || text[length - 1] != '\n') return false;
    // The crc32 line is the last line: "crc32=" + 8 hex + "\n".
    const uint32_t line = length - 15;
    const char* tag = "crc32=";
    for (uint32_t i = 0; i != 6; ++i) if (text[line + i] != tag[i]) return false;
    if (line && text[line - 1] != '\n') return false;
    uint32_t expected;
    if (!hex8(text + line + 6, 8, expected)) return false;
    if (crc::update(0, reinterpret_cast<const uint8_t*>(text), line) != expected) return false;
    SeqVisit seq = {false, 0};
    if (store::parse_key_values(text, line, seq_visit, &seq) < 0 || !seq.found) return false;
    if (!store::parse_state(text, line, out.state)) return false;
    out.valid = true;
    out.seq = seq.seq;
    return true;
}

uint32_t write_slot(const store::State& state, uint32_t seq, char* out, uint32_t capacity) {
    uint32_t n = store::write_state(state, out, capacity);
    if (!n && capacity) {                              // empty state is valid: only "seq" + crc
        bool empty = !state.default_id[0] && !state.once[0] && !state.stage2[0] && !state.stage2_previous[0] &&
                     !state.stage2_tries;
        if (!empty) return 0;
    }
    char digits[11];
    const uint32_t d = decimal(seq, digits);
    if (n + 4 + d + 1 + 15 + 1 > capacity) return 0;
    const char* key = "seq=";
    for (uint32_t i = 0; i != 4; ++i) out[n++] = key[i];
    for (uint32_t i = 0; i != d; ++i) out[n++] = digits[i];
    out[n++] = '\n';
    const uint32_t crc = crc::update(0, reinterpret_cast<const uint8_t*>(out), n);
    const char* tag = "crc32=";
    for (uint32_t i = 0; i != 6; ++i) out[n++] = tag[i];
    for (int shift = 28; shift >= 0; shift -= 4) out[n++] = "0123456789abcdef"[(crc >> shift) & 15u];
    out[n++] = '\n';
    out[n] = 0;
    return n;
}

void consider(Choice& choice, Source source, const char* text, uint32_t length) {
    if (source == source_legacy) {
        if (choice.source != source_none) return;      // a valid slot always wins
        store::State legacy;
        if (store::parse_state(text, length, legacy)) { choice.source = source_legacy; choice.seq = 0; copy_state(choice.state, legacy); }
        return;
    }
    Slot slot;
    if (!parse_slot(text, length, slot)) return;
    const bool better = choice.source == source_none || choice.source == source_legacy || slot.seq > choice.seq;
    if (!better) return;
    choice.source = source; choice.seq = slot.seq;
    copy_state(choice.state, slot.state);
}

void next_write(const Choice& choice, Source& target, uint32_t& seq) {
    target = choice.source == source_a ? source_b : source_a;   // replace the older/invalid slot
    seq = choice.source == source_a || choice.source == source_b ? choice.seq + 1 : 1;
}
} }
