// ------------------------------------------------------------------- JSON (RFC 8259), bounded, iterative
#if defined(__bfin__)
// Keep the shared catalog and pack parser compact in the stage-2 image.
#pragma GCC push_options
#pragma GCC optimize ("Os")
#endif
bool ws(Byte c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }
bool digit(Byte c) { return c >= '0' && c <= '9'; }
bool hexdigit(Byte c) { return digit(c) || ((c | 0x20) >= 'a' && (c | 0x20) <= 'f'); }
U32 string_end(const Byte* p, U32 n, U32 i) {   // the closing quote of the string at i, or none
    for (U32 j = i + 1; j < n;) {
        const Byte c = p[j];
        if (c == '"') return j;
        if (c < 0x20) return none;
        if (c == '\\') {
            if (j + 1 >= n) return none;
            const Byte e = p[j + 1];
            if (e == 'u') {
                if (n - j < 6) return none;
                for (U32 k = 2; k != 6; ++k) if (!hexdigit(p[j + k])) return none;
                j += 6;
            } else {
                const char* valid = "\"\\/bfnrt";
                while (*valid && Byte(*valid) != e) ++valid;
                if (!*valid) return none;
                j += 2;
            }
            continue;
        }
        if (c < 0x80) { ++j; continue; }
        if (c < 0xc2 || c > 0xf4) return none;
        const U32 more = c < 0xe0 ? 1 : c < 0xf0 ? 2 : 3;
        if (n - j <= more) return none;
        // The first continuation excludes overlongs, surrogates and values above U+10FFFF.
        const Byte low = c == 0xe0 ? 0xa0 : c == 0xf0 ? 0x90 : 0x80;
        const Byte high = c == 0xed ? 0x9f : c == 0xf4 ? 0x8f : 0xbf;
        if (p[j + 1] < low || p[j + 1] > high) return none;
        for (U32 k = 2; k <= more; ++k)
            if ((p[j + k] & 0xc0) != 0x80) return none;
        j += more + 1;
    }
    return none;
}
U32 number_end(const Byte* p, U32 n, U32 i) {   // the last byte of the number at i, or none
    U32 j = i;
    if (p[j] == '-') ++j;
    if (j >= n || !digit(p[j])) return none;
    if (p[j] == '0') ++j;
    else while (j < n && digit(p[j])) ++j;
    if (j < n && p[j] == '.') {
        if (++j >= n || !digit(p[j])) return none;
        while (j < n && digit(p[j])) ++j;
    }
    if (j < n && (p[j] == 'e' || p[j] == 'E')) {
        if (++j < n && (p[j] == '+' || p[j] == '-')) ++j;
        if (j >= n || !digit(p[j])) return none;
        while (j < n && digit(p[j])) ++j;
    }
    return j - 1;
}
U32 literal_end(const Byte* p, U32 n, U32 i) {
    const char* tail = p[i] == 't' ? "rue" : p[i] == 'f' ? "alse" : "ull";
    for (++i; *tail; ++i, ++tail)
        if (i == n || p[i] != Byte(*tail)) return none;
    return i - 1;
}
// A pack folder ID is printable ASCII. Compare decoded JSON strings so the same
// ID/key spelled as a Unicode escape still resolves to the same folder.
bool ascii_string(const Byte* text, U32 begin, U32 end, const char* wanted) {
    for (U32 i = begin + 1; i < end;) {
        U32 c = text[i++];
        if (c == '\\') {
            c = text[i++];
            if (c == 'u') {
                c = 0;
                for (U32 j = 0; j < 4; ++j) {
                    const Byte d = text[i++];
                    c = c * 16 + (digit(d) ? d - '0' : (d | 32) - 'a' + 10);
                }
            } else if (c != '"' && c != '/' && c != '\\') return false;
        }
        if (!*wanted || U32(Byte(*wanted++)) != c) return false;
    }
    return !*wanted;
}
// Where a top-level key's array closes, and the last byte before that.
struct ArrayEnd { U32 close, last, found; };
// p[0..n) holds exactly one JSON value, at most `levels` deep. For each top-level object key keys[k]
// whose value is an array, ends[k] says where the array closes. The top-level key whose string starts
// at `mark`: `marked` = the last byte of its value when that is a number, else none.
bool json(const Byte* p, U32 n, const char* const* keys, U32 key_count, ArrayEnd* ends, U32 levels, U32 mark,
          U32& marked, const char* pack_id = 0) {
    enum Want { value, value_or_close, key_or_close, key, colon, comma_or_close };
    Byte stack[max_depth];
    U32 depth = 0, previous = none;
    int current = -1, array = -1;
    Want want = value;
    bool complete = false, marking = false, folder_value = false, saw_folder = false;
    marked = none;
    for (U32 k = 0; k != key_count; ++k) { ends[k].close = ends[k].last = none; ends[k].found = 0; }
    for (U32 i = 0; i < n;) {
        const Byte c = p[i];
        if (ws(c)) { ++i; continue; }
        if (complete) return false;
        bool closing = false;
        U32 end = i;
        if (want == key_or_close && c == '}') closing = true;
        else if (want == value_or_close && c == ']') closing = true;
        else if (want == comma_or_close) {
            if (c == ',') { want = stack[depth - 1] == '{' ? key : value; previous = i++; continue; }
            if (c != (stack[depth - 1] == '{' ? '}' : ']')) return false;
            closing = true;
        } else if (want == key || want == key_or_close) {
            if (c != '"' || (end = string_end(p, n, i)) == none) return false;
            if (depth == 1) {
                if (pack_id && ascii_string(p, i, end, "folder")) {
                    if (saw_folder) return false;
                    folder_value = saw_folder = true;
                }
                marking = i == mark;
                current = -1;
                for (U32 k = 0; k != key_count; ++k) {
                    const U32 len = length(keys[k], 32);
                    bool match = end - i - 1 == len;
                    for (U32 x = 0; match && x != len; ++x) match = p[i + 1 + x] == Byte(keys[k][x]);
                    if (match) current = int(k);
                }
            }
            want = colon; previous = end; i = end + 1;
            continue;
        } else if (want == colon) {
            if (c != ':') return false;
            want = value; previous = i++;
            continue;
        } else if (folder_value) {
            if (c != '"' || (end = string_end(p, n, i)) == none || !ascii_string(p, i, end, pack_id)) return false;
            folder_value = false;
        } else if (c == '{' || c == '[') {
            if (depth == levels) return false;   // levels <= max_depth
            marking = false;
            stack[depth++] = c;
            if (c == '[' && depth == 2 && stack[0] == '{') array = current;
            want = c == '{' ? key_or_close : value_or_close;
            previous = i++;
            continue;
        } else if (c == '"') end = string_end(p, n, i);
        else if (c == '-' || digit(c)) {
            end = number_end(p, n, i);
            if (marking) marked = end;
        } else if (c == 't' || c == 'f' || c == 'n') end = literal_end(p, n, i);
        else return false;
        marking = false;
        if (closing) {
            if (c == ']' && depth == 2 && stack[0] == '{' && array >= 0) {
                ends[array].close = i; ends[array].last = previous; ++ends[array].found;
            }
            if (--depth == 1) array = -1;
        } else if (end == none) return false;
        previous = end; i = end + 1;
        if (depth == 0) complete = true;
        else want = comma_or_close;
    }
    return complete;
}
#if defined(__bfin__)
#pragma GCC pop_options
#endif
