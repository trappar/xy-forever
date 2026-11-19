// MTP responder core (see mtp_responder.hpp). Freestanding: no heap, no libc calls.
#include "mtp_responder.hpp"

namespace xyf_core { namespace mtp {
namespace {
U32 get16(const Byte* p) { return p[0] | (U32(p[1]) << 8); }
U32 get32(const Byte* p) { return p[0] | (U32(p[1]) << 8) | (U32(p[2]) << 16) | (U32(p[3]) << 24); }
void put16(Byte* p, U32 v) { p[0] = Byte(v); p[1] = Byte(v >> 8); }
void put32(Byte* p, U32 v) { put16(p, v); put16(p + 2, v >> 16); }
void copy(Byte* out, const Byte* in, U32 n) { for (U32 i = 0; i != n; ++i) out[i] = in[i]; }
U32 length(const char* s) { U32 n = 0; while (s[n]) ++n; return n; }
// a * b as two words (no 64-bit library call on the target).
void mul64(U32 a, U32 b, U32& hi, U32& lo) {
    const U32 al = a & 0xffff, ah = a >> 16, bl = b & 0xffff, bh = b >> 16;
    const U32 ll = al * bl, lh = al * bh, hl = ah * bl, hh = ah * bh;
    const U32 mid = (ll >> 16) + (lh & 0xffff) + (hl & 0xffff);
    lo = (ll & 0xffff) | (mid << 16);
    hi = hh + (lh >> 16) + (hl >> 16) + (mid >> 16);
}
// A dataset writer (PTP little-endian fields, strings as u8 count + UTF-16LE with the NUL).
struct Writer {
    Byte* p; U32 n;
    void u16(U32 v) { put16(p + n, v); n += 2; }
    void u32(U32 v) { put32(p + n, v); n += 4; }
    void unit(U32 v, U32& units) { if (units < max_name) { u16(v); ++units; } }
    void str(const char* s) {
        if (!*s) { p[n++] = 0; return; }
        const U32 at = n++;
        U32 units = 0;
        const Byte* b = reinterpret_cast<const Byte*>(s);
        while (*b) {
            U32 c = *b++, extra = c >= 0xf0 && c < 0xf8 ? 3 : c >= 0xe0 ? 2 : c >= 0xc0 ? 1 : 0;
            if (extra) {
                U32 v = c & (0x3f >> extra), i = 0;
                while (i != extra && (b[i] & 0xc0) == 0x80) v = (v << 6) | (b[i++] & 0x3f);
                if (i == extra) { c = v; b += extra; }
            }
            if (c > 0xffff) { c -= 0x10000; unit(0xd800 | (c >> 10), units); unit(0xdc00 | (c & 0x3ff), units); }
            else unit(c, units);
        }
        u16(0);
        p[at] = Byte(units + 1);
    }
    void array16(const U16* v, U32 count) { u32(count); for (U32 i = 0; i != count; ++i) u16(v[i]); }
};
// A PTP string at data[0..size) to UTF-8 in out (max_name bytes + NUL); the bytes read, 0 if malformed.
U32 read_string(const Byte* data, U32 size, char* out) {
    if (!size) return 0;
    const U32 units = data[0];
    if (!units || 1 + 2 * units > size || get16(data + 1 + 2 * (units - 1)) != 0) return 0;
    U32 n = 0;
    for (U32 i = 0; i + 1 < units; ++i) {
        U32 c = get16(data + 1 + 2 * i);
        if (c >= 0xd800 && c < 0xdc00 && i + 2 < units) {
            const U32 d = get16(data + 3 + 2 * i);
            if (d >= 0xdc00 && d < 0xe000) { c = 0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00); ++i; }
        }
        const U32 need = c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
        if (!c || n + need > max_name) return 0;
        if (need == 1) out[n++] = char(c);
        else {
            out[n++] = char(need == 2 ? 0xc0 | (c >> 6) : need == 3 ? 0xe0 | (c >> 12) : 0xf0 | (c >> 18));
            for (U32 k = need - 1; k; --k) out[n++] = char(0x80 | ((c >> (6 * (k - 1))) & 0x3f));
        }
    }
    out[n] = 0;
    return 1 + 2 * units;
}
bool valid_name(const char* name) {
    if (!name[0] || (name[0] == '.' && (!name[1] || (name[1] == '.' && !name[2])))) return false;
    for (const char* p = name; *p; ++p) if (*p == '/' || *p == '\\' || Byte(*p) < 0x20) return false;
    return true;
}
bool prefix(const char* s, const char* p) { while (*p) if (*s++ != *p++) return false; return true; }
const char root[] = "/yaffs2";
const U16 operations[] = { op_get_device_info, op_open_session, op_close_session, op_get_storage_ids,
    op_get_storage_info, op_get_object_handles, op_get_object_info, op_get_object, op_delete_object,
    op_send_object_info, op_send_object, op_get_partial_object, op_get_device_prop_desc, op_get_device_prop_value,
    op_get_object_props_supported, op_get_object_prop_desc, op_get_object_prop_value, op_set_object_prop_value };
// T2c: the object properties (code, data type), the same for every format.
const U16 object_props[][2] = { {p_storage_id, d_u32}, {p_object_format, d_u16}, {p_protection_status, d_u16},
    {p_object_size, d_u64}, {p_object_file_name, d_string}, {p_date_modified, d_string}, {p_parent_object, d_u32},
    {p_puoid, d_u128}, {p_name, d_string} };
const U32 object_prop_count = sizeof(object_props) / sizeof(object_props[0]);
U32 prop_type(U32 prop) {
    for (U32 i = 0; i != object_prop_count; ++i) if (object_props[i][0] == prop) return object_props[i][1];
    return 0;
}
const U16 properties[] = { U16(prop_friendly_name) };
const char friendly_name[] = "OP-XY";
// The operations that reach the files (FileOps::prepare first).
bool uses_files(U32 code) {
    return code == op_get_storage_info || code == op_get_object_handles || code == op_get_object_info ||
           code == op_get_object || code == op_get_partial_object || code == op_delete_object ||
           code == op_send_object_info || code == op_send_object || code == op_set_object_prop_value;
}
const U16 formats[] = { f_undefined, f_association };
const U32 no_params[3] = {0, 0, 0};
}

bool writable(const char* path) {
    if (!prefix(path, "/yaffs2/") || !path[8]) return false;
    const char* name = path + 8;
    U32 n = 0;
    while (name[n] && name[n] != '/') ++n;
    auto named = [name, n](const char* word) {                    // ASCII case ignored
        U32 i = 0;
        while (i != n && word[i] && (name[i] | (name[i] >= 'A' && name[i] <= 'Z' ? 0x20 : 0)) == word[i]) ++i;
        return i == n && !word[i];
    };
    if (named("upgrade")) return false;                             // the stock updater's inbox and below
    return name[n] || !named("bootloader.dev");                    // the marker itself
}

void Responder::init(const FileOps& file_ops) {
    ops = file_ops;
    cancelling = resetting = 0; data_flag = 0;
    phase = p_idle; stage = session = tid = nparams = 0; code = 0;
    data_total = data_got = data_seen = in_len = in_pos = out_len = out_pos = 0;
    fd = -1; left = failed = result = target = skip = more = count = offset = opened = 0;
    pending_handle = pending_size = 0;
    hits = 0; piece = chunk;
    clear_objects();
}

void Responder::clear_objects() {
    for (U32 i = 0; i != max_handles; ++i) objects[i].used = 0;
    names_used = 0;
    pending_handle = 0;
}

void Responder::invalidate() { for (U32 i = 0; i != max_handles; ++i) objects[i].fresh = 0; }

U32 Responder::staged_length() const { return out_len - out_pos >= 4 ? get32(out + out_pos) : 0; }

U32 Responder::handles_used() const {
    U32 n = 0;
    for (U32 i = 0; i != max_handles; ++i) n += objects[i].used;
    return n;
}

void Responder::respond(U16 response, U32 n, U32 a, U32 b, U32 c) {
    Byte* p = out + out_len;
    put32(p, 12 + 4 * n); put16(p + 4, t_response); put16(p + 6, response); put32(p + 8, tid);
    const U32 values[3] = {a, b, c};
    for (U32 i = 0; i != n; ++i) put32(p + 12 + 4 * i, values[i]);
    out_len += 12 + 4 * n;
    phase = p_idle;
    stage = 0;
    if (ops.log) ops.log(ops.context, code, params, nparams, response, data_flag);
}

Byte* Responder::begin_data(U32 payload) {
    Byte* p = out + out_len;
    put32(p, 12 + payload); put16(p + 4, t_data); put16(p + 6, code); put32(p + 8, tid);
    out_len += 12;
    data_flag |= 2;
    return out + out_len;
}

void Responder::end_data(U32 payload) { out_len += payload; }

U32 Responder::take(Byte* destination, U32 room) {
    if (cancelling) return 0;
    U32 n = out_len - out_pos;
    if (n > room) n = room;
    copy(destination, out + out_pos, n);
    out_pos += n;
    if (out_pos == out_len) out_pos = out_len = 0;
    return n;
}

bool Responder::receive(const Byte* data, U32 count_in) {
    if (cancelling || out_pos != out_len || phase == p_work || phase == p_close || count_in > buffer_bytes) return false;
    if (phase == p_idle) {
        if (count_in >= 12 && get16(data + 4) == t_data) {        // a data phase nobody asked for: drop all of it
            const U32 total = get32(data);
            data_total = total > 12 ? total - 12 : 0;
            data_got = count_in - 12 < data_total ? count_in - 12 : data_total;
            if (ops.log) ops.log(ops.context, get16(data + 6), no_params, 0, 0, 3);
            if (data_got < data_total) { phase = p_data; code = 0; data_seen = 1; }
            return true;
        }
        command(data, count_in);
        return true;
    }
    // p_data
    if (code == op_send_object && in_pos != in_len) return false;
    if (!data_seen) {
        if (count_in < 12 || get16(data + 4) != t_data || get16(data + 6) != code || get32(data) < 12) {
            respond(r_general_error);                            // not the data phase: end the transaction
            return true;
        }
        data_total = get32(data) - 12;
        data_seen = 1;
        data_flag |= 1;
        data += 12; count_in -= 12;
    }
    U32 n = data_total - data_got;
    if (n > count_in) n = count_in;
    data_got += n;
    if (code == op_send_object) { copy(in, data, n); in_len = n; in_pos = 0; }
    else if (code == op_send_object_info || code == op_set_object_prop_value) {
        U32 room = buffer_bytes - in_len, k = n < room ? n : room;
        copy(in + in_len, data, k); in_len += k;
        if (k != n) failed = r_invalid_dataset;
    }
    if (data_got == data_total) { phase = code ? p_work : p_idle; stage = code == op_send_object ? stage : 0; }
    return true;
}

void Responder::command(const Byte* data, U32 count_in) {
    tid = count_in >= 12 ? get32(data + 8) : 0;
    code = count_in >= 8 ? U16(get16(data + 6)) : 0;
    data_flag = 0; nparams = 0;
    for (U32 i = 0; i != 5; ++i) params[i] = 0;
    if (count_in < 12 || get32(data) != count_in || get16(data + 4) != t_command || count_in > 32 || (count_in & 3)) {
        respond(r_general_error);
        return;
    }
    nparams = (count_in - 12) / 4;
    for (U32 i = 0; i != 5; ++i) params[i] = i < nparams ? get32(data + 12 + 4 * i) : 0;
    start();
}

bool Responder::parent_of(U32 param, U32& parent) const {
    if (param == 0 || param == 0xffffffffu) { parent = 0; return true; }
    if (!valid(param) || objects[param - 1].kind != 1) return false;
    parent = param;
    return true;
}

bool Responder::path_of(U32 handle, char* destination) const {
    U32 chain[32], depth = 0;
    for (U32 h = handle; h; h = objects[h - 1].parent) {
        if (depth == 32 || h > max_handles || !objects[h - 1].used) return false;   // valid(h), inline
        chain[depth++] = h;
    }
    U32 n = 0;
    for (const char* r = root; *r; ++r) destination[n++] = *r;
    while (depth) {
        const Object& o = objects[chain[--depth] - 1];
        if (n + 1 + o.name_len >= max_path) return false;
        destination[n++] = '/';
        for (U32 i = 0; i != o.name_len; ++i) destination[n++] = names[o.name_at + i];
    }
    destination[n] = 0;
    return true;
}

U32 Responder::find(U32 parent, const char* name) const {
    const U32 n = length(name);
    for (U32 i = 0; i != max_handles; ++i) {
        const Object& o = objects[i];
        if (!o.used || o.parent != parent || o.name_len != n) continue;
        U32 k = 0;
        while (k != n && names[o.name_at + k] == name[k]) ++k;
        if (k == n) return i + 1;
    }
    return 0;
}

U32 Responder::add(U32 parent, const char* name, U32 kind, U32 size) {
    U32 h = find(parent, name);
    if (!h) {
        const U32 n = length(name);
        if (n > max_name || names_used + n > name_pool) return 0;
        for (U32 i = 0; i != max_handles && !h; ++i) if (!objects[i].used) h = i + 1;
        if (!h) return 0;
        Object& o = objects[h - 1];
        o.parent = U16(parent); o.name_at = U16(names_used); o.name_len = Byte(n); o.used = 1; o.fresh = 0;
        for (U32 i = 0; i != n; ++i) names[names_used + i] = name[i];
        names_used += n;
    }
    objects[h - 1].kind = Byte(kind);
    objects[h - 1].size = size;
    return h;
}

void Responder::forget(U32 handle) {
    Object& o = objects[handle - 1];
    o.used = 0;
    if (o.name_at + o.name_len == names_used) names_used = o.name_at;
    for (bool changed = true; changed;) {                       // children of a forgotten folder
        changed = false;
        for (U32 i = 0; i != max_handles; ++i)
            if (objects[i].used && objects[i].parent && !objects[objects[i].parent - 1].used) { objects[i].used = 0; changed = true; }
    }
    if (pending_handle == handle) pending_handle = 0;
}

U32 Responder::error_code(int error) const {
    switch (error) {
    case f_not_found: return r_invalid_object_handle;
    case f_refused: return r_access_denied;
    case f_link: return r_device_busy;
    default: return r_general_error;
    }
}

void Responder::start() {
    stage = 0; failed = 0; fd = -1; opened = 0;
    const bool data_phase = code == op_send_object_info || code == op_send_object || code == op_set_object_prop_value;
    if (data_phase) { phase = p_data; data_total = data_got = data_seen = in_len = in_pos = 0; }
    bool known = code == op_get_object_prop_list;                // answered, not listed (mtp_responder.hpp)
    for (U32 i = 0; i != sizeof(operations) / sizeof(operations[0]); ++i) known |= operations[i] == code;
    if (!known) { respond(r_not_supported); return; }
    if (!session && code != op_get_device_info && code != op_open_session) {
        if (data_phase) failed = r_session_not_open; else respond(r_session_not_open);
        return;
    }
    if (code == op_get_object_info && cached_info()) return;     // speed: no file call, no prepare
    if (uses_files(code) && ops.prepare) {
        const U32 refusal = ops.prepare(ops.context);
        if (refusal) { if (data_phase) failed = refusal; else respond(U16(refusal)); return; }
    }
    switch (code) {
    case op_get_device_info: {
        Writer w = {out + out_len + 12, 0};
        w.u16(100); w.u32(6); w.u16(100); w.str("microsoft.com: 1.0;"); w.u16(0);
        w.array16(operations, sizeof(operations) / sizeof(operations[0]));
        w.array16(0, 0); w.array16(properties, 1); w.array16(0, 0);  // events, device properties, capture formats
        w.array16(formats, 2);
        w.str("XY Forever"); w.str("OP-XY"); w.str("1"); w.str("0");
        begin_data(w.n); end_data(w.n);
        respond(r_ok);
        return;
    }
    case op_open_session:
        if (!params[0]) respond(r_invalid_parameter);
        else if (session) respond(r_session_already_open, 1, session);
        else { clear_objects(); session = params[0]; respond(r_ok); }
        return;
    case op_close_session:
        session = 0; clear_objects(); respond(r_ok);
        return;
    case op_get_device_prop_desc: case op_get_device_prop_value: {
        if (params[0] != prop_friendly_name) { respond(r_device_prop_not_supported); return; }
        Writer w = {out + out_len + 12, 0};
        if (code == op_get_device_prop_desc) {
            w.u16(prop_friendly_name); w.u16(0xffff); w.p[w.n++] = 0;  // string, get only
            w.str(friendly_name); w.str(friendly_name); w.p[w.n++] = 0;  // default, current, no form
        } else w.str(friendly_name);
        begin_data(w.n); end_data(w.n);
        respond(r_ok);
        return;
    }
    case op_get_storage_ids: {
        Byte* p = begin_data(8);
        put32(p, 1); put32(p + 4, storage_id);
        end_data(8);
        respond(r_ok);
        return;
    }
    case op_get_storage_info:
        if (params[0] != storage_id) respond(r_invalid_storage_id); else phase = p_work;
        return;
    case op_get_object_handles:
        if (params[0] != storage_id && params[0] != 0xffffffffu) respond(r_invalid_storage_id);
        else if (!parent_of(params[2], target)) respond(r_invalid_parent);
        else phase = p_work;
        return;
    case op_get_object_info: case op_get_object: case op_get_partial_object: case op_delete_object:
        if (!valid(params[0])) { respond(r_invalid_object_handle); return; }
        target = params[0];
        if (!path_of(target, path)) { respond(r_general_error); return; }
        if (code == op_delete_object && !writable(path)) { respond(r_object_write_protected); return; }
        phase = p_work;
        return;
    case op_get_object_props_supported: {
        Writer w = {out + out_len + 12, 0};
        w.u32(object_prop_count);
        for (U32 i = 0; i != object_prop_count; ++i) w.u16(object_props[i][0]);
        begin_data(w.n); end_data(w.n);
        respond(r_ok);
        return;
    }
    case op_get_object_prop_desc: {
        const U32 type = prop_type(params[0]);
        if (!type) { respond(r_invalid_object_prop_code); return; }
        Writer w = {out + out_len + 12, 0};
        w.u16(params[0]); w.u16(type); w.p[w.n++] = params[0] == p_object_file_name ? 1 : 0;   // get/set
        const U32 zeros = type == d_u16 ? 2 : type == d_u32 ? 4 : type == d_u64 ? 8 : type == d_u128 ? 16 : 1;
        for (U32 i = 0; i != zeros; ++i) w.p[w.n++] = 0;          // the default: 0 or the empty string
        w.u32(0); w.p[w.n++] = 0;                                 // group, no form
        begin_data(w.n); end_data(w.n);
        respond(r_ok);
        return;
    }
    case op_get_object_prop_value: {
        if (!valid(params[0])) { respond(r_invalid_object_handle); return; }
        if (!prop_type(params[1])) { respond(r_invalid_object_prop_code); return; }
        Byte* p = out + out_len + 12;
        const U32 n = prop_value(p, params[0], params[1]);
        if (!n) { respond(r_general_error); return; }
        begin_data(n); end_data(n);
        respond(r_ok);
        return;
    }
    case op_get_object_prop_list: {                              // one object, depth 0
        if (params[0] == 0 || params[0] == 0xffffffffu || params[4]) { respond(r_specification_by_depth_unsupported); return; }
        if (!valid(params[0])) { respond(r_invalid_object_handle); return; }
        if (params[2] == 0) { respond(r_specification_by_group_unsupported); return; }
        if (params[2] != 0xffffffffu && !prop_type(params[2])) { respond(r_invalid_object_prop_code); return; }
        Byte* p = out + out_len + 12;
        U32 n = 4, count = 0;
        for (U32 i = 0; i != object_prop_count; ++i) {
            const U32 prop = object_props[i][0];
            if (params[2] != 0xffffffffu && params[2] != prop) continue;
            put32(p + n, params[0]); put16(p + n + 4, prop); put16(p + n + 6, object_props[i][1]);
            const U32 k = prop_value(p + n + 8, params[0], prop);
            if (!k) { respond(r_general_error); return; }
            n += 8 + k; ++count;
        }
        put32(p, count);
        begin_data(n); end_data(n);
        respond(r_ok);
        return;
    }
    default:  // SendObjectInfo, SendObject, SetObjectPropValue: p_data
        return;
    }
}

U32 Responder::prop_value(Byte* p, U32 handle, U32 prop) {
    char where[max_path];
    if (!valid(handle) || !path_of(handle, where)) return 0;
    const Object& o = objects[handle - 1];
    char name[max_name + 1];
    for (U32 i = 0; i != o.name_len; ++i) name[i] = names[o.name_at + i];
    name[o.name_len] = 0;
    Writer w = {p, 0};
    switch (prop) {
    case p_storage_id: w.u32(storage_id); break;
    case p_object_format: w.u16(o.kind ? f_association : f_undefined); break;
    case p_protection_status: w.u16(writable(where) ? 0 : 1); break;
    case p_object_size: w.u32(o.kind ? 0 : o.size); w.u32(0); break;
    case p_object_file_name: case p_name: w.str(name); break;
    case p_date_modified: w.str(fixed_date); break;
    case p_parent_object: w.u32(o.parent); break;
    case p_puoid: {  // the handle and an FNV-1a hash of the path
        U32 hash = 0x811c9dc5u;
        for (const char* c = where; *c; ++c) hash = (hash ^ Byte(*c)) * 0x01000193u;
        w.u32(handle); w.u32(hash); w.u32(0); w.u32(0);
        break;
    }
    default: return 0;
    }
    return w.n;
}

bool Responder::cached_info() {
    if (!session || !valid(params[0]) || !objects[params[0] - 1].fresh) return false;
    char where[max_path];
    if (!path_of(params[0], where)) return false;
    ++hits;
    object_info(params[0], where);
    return true;
}

void Responder::object_info(U32 handle, const char* where) {
    const Object& o = objects[handle - 1];
    char name[max_name + 1];
    for (U32 i = 0; i != o.name_len; ++i) name[i] = names[o.name_at + i];
    name[o.name_len] = 0;
    Writer w = {out + out_len + 12, 0};
    w.u32(storage_id); w.u16(o.kind ? f_association : f_undefined); w.u16(writable(where) ? 0 : 1);
    w.u32(o.kind ? 0 : o.size); w.u16(0); w.u32(0); w.u32(0); w.u32(0); w.u32(0); w.u32(0); w.u32(0);
    w.u32(o.parent); w.u16(o.kind ? 1 : 0); w.u32(0); w.u32(0);
    w.str(name); w.str(fixed_date); w.str(fixed_date); w.str("");
    begin_data(w.n); end_data(w.n);
    respond(r_ok);
}

bool Responder::rename_object(U32 handle, const char* name) {
    const U32 n = length(name);
    Object& o = objects[handle - 1];
    if (o.name_at + o.name_len == names_used) names_used = o.name_at;   // the old name was the pool's last
    if (n > max_name || names_used + n > name_pool) { forget(handle); return false; }
    for (U32 i = 0; i != n; ++i) names[names_used + i] = name[i];
    o.name_at = U16(names_used); o.name_len = Byte(n);
    names_used += n;
    return true;
}

bool Responder::step() {
    if (cancelling) {
        // One file call per step: close the open file, then remove the upload (#4: never the name); then idle.
        if (fd >= 0) { ops.close(ops.context, fd); fd = -1; return true; }
        if (code == op_send_object && opened == 1 && path_of(pending_handle, path)) {
            opened = 0;
            ops.commit(ops.context, path, false);
            return true;
        }
        if (code == op_send_object && pending_handle && pending_new) forget(pending_handle);
        if (ops.log) ops.log(ops.context, code, params, nparams, 0, 4);   // 4: cancelled
        phase = p_idle; stage = 0; opened = 0; code = 0;
        in_len = in_pos = out_len = out_pos = 0;
        if (resetting) { session = 0; clear_objects(); resetting = 0; }
        cancelling = 0;
        return true;
    }
    if (phase == p_work || (phase == p_data && code == op_send_object)) return work();
    return false;
}

bool Responder::stream() {
    if (out_pos) {                                                // compact the staged IN bytes
        for (U32 i = out_pos; i != out_len; ++i) out[i - out_pos] = out[i];
        out_len -= out_pos; out_pos = 0;
    }
    U32 want = left < piece ? left : piece;
    const U32 room = buffer_bytes - out_len;
    if (room < want) {                                           // wait for the host to take bytes
        if (room < (want < chunk ? want : chunk)) return false;
        want = room;                                             // #144: a larger piece fills the room
    }
    if (!failed) {
        const int r = ops.read(ops.context, fd, out + out_len, want);
        if (r <= 0 || U32(r) > want) failed = r_incomplete_transfer;
        else want = U32(r);
    }
    if (failed) for (U32 i = 0; i != want; ++i) out[out_len + i] = 0;  // keep the promised length
    out_len += want; left -= want;
    return true;
}

bool Responder::work() {
    switch (code) {
    case op_get_storage_info: {
        U32 block = 0, blocks = 0, free_blocks = 0, th = 0, tl = 0, fh = 0, fl = 0;
        if (ops.space(ops.context, root, block, blocks, free_blocks) >= 0) {
            mul64(block, blocks, th, tl); mul64(block, free_blocks, fh, fl);
        }
        Writer w = {out + out_len + 12, 0};
        w.u16(3); w.u16(2); w.u16(0);                            // fixed RAM, generic hierarchical, read-write
        w.u32(tl); w.u32(th); w.u32(fl); w.u32(fh); w.u32(0xffffffffu);
        w.str("OP-XY"); w.str("yaffs2");
        begin_data(w.n); end_data(w.n);
        respond(r_ok);
        return true;
    }
    case op_get_object_handles: {
        if (stage == 0) {
            if (!path_of(target, path)) { respond(r_general_error); return true; }
            for (U32 i = 0; i != max_handles; ++i) if (objects[i].parent == target) objects[i].fresh = 0;
            skip = count = 0; stage = 1;
        }
        U32 n = 0;
        more = 0;
        const int r = ops.list(ops.context, path, skip, entries, page, n, more);
        if (r < 0) { respond(U16(error_code(r))); return true; }
        if (n > page) n = page;
        for (U32 i = 0; i != n; ++i) {
            const Entry& e = entries[i];
            if (e.kind > 1 || !valid_name(e.name)) continue;
            if (params[1] && params[1] != (e.kind ? f_association : f_undefined)) continue;
            const U32 h = add(target, e.name, e.kind, e.size);
            if (!h || count == max_handles) { respond(r_general_error); return true; }
            objects[h - 1].fresh = 1;
            listed[count++] = U16(h);
        }
        skip += n;
        if (more && n) return true;
        Byte* p = begin_data(4 + 4 * count);
        put32(p, count);
        for (U32 i = 0; i != count; ++i) put32(p + 4 + 4 * i, listed[i]);
        end_data(4 + 4 * count);
        respond(r_ok);
        return true;
    }
    case op_get_object_info: {
        Entry e;
        const int r = ops.stat(ops.context, path, e);
        if (r < 0) { respond(U16(error_code(r))); return true; }
        Object& o = objects[target - 1];
        if (e.kind <= 1) o.kind = Byte(e.kind);
        o.size = o.kind ? 0 : e.size;
        o.fresh = 1;
        object_info(target, path);
        return true;
    }
    case op_get_object: case op_get_partial_object:
        switch (stage) {
        case 0: {
            Entry e;
            const int r = ops.stat(ops.context, path, e);
            if (r < 0) { respond(U16(error_code(r))); return true; }
            if (e.kind != 0) { respond(r_invalid_object_handle); return true; }
            offset = code == op_get_object ? 0 : params[1];
            left = offset < e.size ? e.size - offset : 0;
            if (code == op_get_partial_object && left > params[2]) left = params[2];
            result = left;
            stage = 1;
            return true;
        }
        case 1: {
            const int r = ops.open(ops.context, path, false);
            if (r < 0) { respond(U16(error_code(r))); return true; }
            fd = r;
            stage = offset ? 2 : 3;
            return true;
        }
        case 2:
            if (ops.seek(ops.context, fd, offset) < 0) { failed = r_general_error; stage = 5; return true; }
            stage = 3;
            return true;
        case 3:
            begin_data(left);
            stage = 4;
            return true;
        case 4:
            if (left) return stream();
            stage = 5;
            return true;
        default:
            ops.close(ops.context, fd);
            fd = -1;
            if (failed) respond(U16(failed));
            else if (code == op_get_partial_object) respond(r_ok, 1, result);
            else respond(r_ok);
            return true;
        }
    case op_delete_object: {
        const bool folder = objects[target - 1].kind == 1;
        const int r = folder ? ops.rmdir(ops.context, path) : ops.unlink(ops.context, path);
        if (r >= 0 || r == f_not_found) forget(target);
        U16 answer = U16(error_code(r));
        if (r >= 0) answer = r_ok;
        else if (r == f_not_empty) answer = r_partial_deletion;
        else if (r == f_refused) answer = r_object_write_protected;
        respond(answer);
        return true;
    }
    case op_send_object_info: {
        if (stage == 1) {                                         // mkdir for a folder
            const int r = ops.mkdir(ops.context, path);
            if (r < 0 && r != f_exists) { respond(r == f_refused ? U16(r_access_denied) : U16(error_code(r))); return true; }
            const U32 h = add(target, entries[0].name, 1, 0);
            if (h) objects[h - 1].fresh = 1;
            if (!h) respond(r_store_full); else respond(r_ok, 3, storage_id, target, h);
            return true;
        }
        if (failed) { respond(U16(failed)); return true; }
        if (params[0] != storage_id && params[0] != 0) { respond(r_invalid_storage_id); return true; }
        if (!parent_of(params[1], target)) { respond(r_invalid_parent); return true; }
        if (in_len < 53) { respond(r_invalid_dataset); return true; }
        const U32 format = get16(in + 4), size = get32(in + 8);
        char* name = entries[0].name;
        if (!read_string(in + 52, in_len - 52, name) || !valid_name(name)) { respond(r_invalid_dataset); return true; }
        if (!path_of(target, path)) { respond(r_general_error); return true; }
        U32 n = length(path), k = length(name);
        if (n + 1 + k >= max_path) { respond(r_invalid_dataset); return true; }
        path[n] = '/';
        for (U32 i = 0; i <= k; ++i) path[n + 1 + i] = name[i];
        if (!writable(path) || !within_depth(path, format == f_association)) { respond(r_access_denied); return true; }
        if (format == f_association) { stage = 1; return true; }
        pending_new = !find(target, name);                       // #4: a failed upload keeps an old file
        const U32 h = add(target, name, 0, size);
        if (!h) { respond(r_store_full); return true; }
        objects[h - 1].fresh = 0;                                 // fresh once SendObject wrote it
        pending_handle = h; pending_size = size;
        respond(r_ok, 3, storage_id, target, h);
        return true;
    }
    case op_set_object_prop_value: {                             // T2c: ObjectFileName, a rename
        char* to = entries[1].name;                               // the new path (max_path <= max_name + 2)
        if (stage == 1) {
            const int r = ops.rename(ops.context, path, to);
            if (r < 0) {
                respond(r == f_refused ? U16(r_access_denied) : r == f_exists ? U16(r_invalid_object_prop_value) :
                        U16(error_code(r)));
                return true;
            }
            rename_object(target, entries[0].name);
            respond(r_ok);
            return true;
        }
        if (failed) { respond(U16(failed)); return true; }
        if (!valid(params[0])) { respond(r_invalid_object_handle); return true; }
        if (params[1] != p_object_file_name) { respond(r_access_denied); return true; }
        target = params[0];
        if (!path_of(target, path)) { respond(r_general_error); return true; }
        if (!writable(path)) { respond(r_access_denied); return true; }
        char* name = entries[0].name;
        if (!read_string(in, in_len, name) || !valid_name(name)) { respond(r_invalid_object_prop_value); return true; }
        U32 cut = length(path);
        while (cut && path[cut - 1] != '/') --cut;                // path[0..cut) ends with '/'
        const U32 k = length(name);
        if (cut + k >= max_path) { respond(r_invalid_object_prop_value); return true; }
        for (U32 i = 0; i != cut; ++i) to[i] = path[i];
        for (U32 i = 0; i <= k; ++i) to[cut + i] = name[i];
        U32 same = 0;
        while (path[same] && path[same] == to[same]) ++same;
        if (!path[same] && !to[same]) { respond(r_ok); return true; }   // the same name
        if (!writable(to)) { respond(r_access_denied); return true; }
        stage = 1;                                                // A refuses an existing name (EEXIST)
        return true;
    }
    case op_send_object: return send_object();                  // mtp_responder_send.ipp
    default:
        respond(r_general_error);
        return true;
    }
}
#include "mtp_responder_send.ipp"
} }
