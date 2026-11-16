#include "xyf_mods_view.hpp"
#include "ui_gfx.hpp"
#include "ui_pages.hpp"
namespace xyf { namespace mods {
namespace {
using namespace gfx;
// 8.6: the compact title bar (ui_pages) with the filter and count; the memory meter on its edge, one line for
// the memory and the status (or a result), then 9 rows of 18 px above the hint line (8.2: 7 of 20, 3 not packages).
const int margin = 16, row_height = 18, meter_y = ui::compact_bar, meter_h = 4, line_y = ui::compact_bar + 7,
          rows_top = 43, hint_y = height - 11;
const int bar_y = ui::compact_top + 10, bar_h = 16, step_y = bar_y + bar_h + 8, detail_y = step_y + 22,
          footer_y = height - 30, bar_w = width - 2 * margin;
const Color red = 0xf9e7, green = 0x4f2c;      // rgb(255, 60, 60), rgb(76, 228, 96)

void put(char*& at, const char* end, const char* s) { while (*s && at < end) *at++ = *s++; }
void put_number(char*& at, const char* end, uint32_t v) { char d[12]; decimal(d, v); put(at, end, d); }
void box(uint16_t* fb, int x, int y, bool on, Color c) {
    fill(fb, x, y, 12, 1, c); fill(fb, x, y + 11, 12, 1, c); fill(fb, x, y, 1, 12, c); fill(fb, x + 11, y, 1, 12, c);
    if (on) fill(fb, x + 3, y + 3, 6, 6, c);
}
// The building screen. full: everything (the title bar clears the screen); else only the parts whose
// inputs differ from what frame says is drawn, each over its own background in the same framebuffer.
void building(uint16_t* fb, const Page& p, const ui::Chrome* chrome, Frame& f) {
    const bool full = !f.drawn || f.job != p.job;
    char line[64];
    char* at = line;
    const char* end = line + sizeof(line) - 1;
    if (full) {
        if (p.save) put(at, end, "Saving changes");     // 8.4a: the save sequence
        else { put(at, end, "Building "); put(at, end, p.job_name); }
        *at = 0;
        ui::compact_header(fb, line, "", chrome);
        fill(fb, margin, bar_y, bar_w, bar_h, dark);
        f.drawn = 1; f.job = p.job; f.bar_px = 0;
    }
    const uint32_t px = ui::scale(uint32_t(bar_w), p.bar, bar_full);
    if (px > f.bar_px) { fill(fb, margin + int(f.bar_px), bar_y, int(px - f.bar_px), bar_h, accent); f.bar_px = px; }
    // The step (5.4e2: step 8 is B's commit into the library).
    const bool cancelling = p.request == request_cancel;
    const uint32_t phase = p.job_phase > 7 ? 7 : p.job_phase;
    const uint32_t step = cancelling ? 0xffffu : p.save && p.save_stage ? 8u + p.save_stage : p.job_commit ? 8u : phase ? phase : 1u;
    if (full || step != f.step) {
        at = line;
        if (cancelling) put(at, end, "Cancelling...");
        else if (p.save && p.save_stage) put(at, end, p.save_stage == 1 ? "Installing content" : "Saving settings");
        else if (p.save && !(p.save & save_build)) put(at, end, "Checking");
        else if (p.save) { put(at, end, "Building: "); put(at, end, p.job_commit ? "Adding to the library" : phase_text(phase)); }
        else { put(at, end, "Step "); put_number(at, end, step); put(at, end, " of 8: ");
               put(at, end, p.job_commit ? "Adding to the library" : phase_text(phase)); }
        *at = 0;
        fill(fb, margin, step_y, bar_w, 16, black);
        text(fb, margin, step_y, line, white, 2);
        f.step = step;
    }
    const uint32_t percent = ui::scale(100, p.bar, bar_full);
    if (full || percent != f.percent || p.job_done != f.done || p.job_total != f.total) {
        fill(fb, margin, detail_y, bar_w, 8, black);
        at = line; put_number(at, end, percent); put(at, end, "%"); *at = 0;
        text(fb, margin, detail_y, line, grey, 1);
        if (p.job_total) {
            at = line; put_number(at, end, p.job_done); put(at, end, " / "); put_number(at, end, p.job_total); *at = 0;
            text(fb, width - margin - text_width(line, 1), detail_y, line, grey, 1);
        }
        f.percent = percent; f.done = p.job_done; f.total = p.job_total;
    }
    // The commit cannot be cancelled: its footer says so, and STOP there gets the line (refuse_cancel).
    const bool said = p.line == line_info && p.text[0];
    const uint32_t footer = said ? 1u : p.job_commit || p.save_stage ? 2u : 3u;
    if (full || footer != f.footer) {
        fill(fb, margin, footer_y, width - 2 * margin, 8, black);
        text(fb, margin, footer_y, said ? p.text : p.job_commit || p.save_stage ? "Almost done: it cannot be cancelled now.      Keep the OP-XY powered." :
                                                    "STOP  cancel      Keep the OP-XY powered.", accent, 1);
        f.footer = footer;
    }
    // 8.7: the USB mark and the activity line (below the footer), each over its own background.
    const uint32_t usb = (chrome && chrome->usb ? 1u : 0u) | (chrome && chrome->mtp ? 2u : 0u);   // 188: both marks
    const char* activity = chrome && chrome->activity && *chrome->activity ? chrome->activity : 0;
    if (!full && usb != f.usb) {
        fill(fb, width - margin - ui::marks_w, ui::compact_mark_y, ui::marks_w, ui::mark_h, black);
        ui::usb_mark(fb, width - margin, ui::compact_mark_y, usb & 1u, usb & 2u);
    }
    if (full || activity != f.activity) {
        fill(fb, margin, hint_y, bar_w, 8, black);
        ui::footer_line(fb, hint_y, "", chrome);
    }
    f.usb = usb; f.activity = activity;
}
}

uint32_t first_row(uint32_t rows, uint32_t at) {
    if (rows <= visible_rows || at < visible_rows / 2) return 0;
    const uint32_t first = at - visible_rows / 2;
    return first + visible_rows > rows ? rows - visible_rows : first;
}

void draw(uint16_t* fb, const Page& p, const ui::Chrome* chrome, Frame& frame) {
    if (p.view == View::building) { building(fb, p, chrome, frame); return; }
    frame.drawn = 0;
    char line[96], title[40];
    const char* end = line + sizeof(line) - 1;
    char* at = title;
    put(at, title + sizeof(title) - 1, "Mods");     // 8.6: "Mods - Synths 3/57": the filter, listed rows on / listed
    if (p.shown) {
        const char* tend = title + sizeof(title) - 1;
        put(at, tend, " - "); put(at, tend, filter_name(p.filter)); put(at, tend, " ");
        put_number(at, tend, on_count(p)); put(at, tend, "/"); put_number(at, tend, p.listed);
    }
    *at = 0;
    at = line;
    if (p.generation[0]) {                      // 8.7: the generation alone when the profile with it would be cut
        uint32_t n = 0, g = 0;
        while (p.profile[n]) ++n;
        while (p.generation[g]) ++g;
        if (n + 3 + g <= ui::subtitle_room(title)) { put(at, end, p.profile); put(at, end, " / "); }
        put(at, end, p.generation);
    }
    *at = 0;
    ui::compact_header(fb, title, line, chrome);
    // The memory meter on the title bar's edge: the estimate for the toggles against the mods region (module_bytes;
    // 4.8m2: of the pending Mod memory layout).
    const uint32_t used_bytes = used(p), limit = memory_limit(p), services = shared(p);
    const bool over = limit && used_bytes > limit;
    fill(fb, margin, meter_y, bar_w, meter_h, dark);
    if (limit) fill(fb, margin, meter_y, over ? bar_w : int(ui::scale(uint32_t(bar_w), used_bytes, limit)), meter_h, over ? red : accent);
    // 8.6: one line: a result (or the reading line) when there is one, else the memory and the status (8.4a's first
    // row: what Continue will do with the toggles), the status right-aligned.
    if (p.line != line_none && p.text[0])
        text(fb, margin, line_y, p.text, p.line == line_error ? red : p.line == line_done ? green : accent, 1);
    else {
        at = line;
        if (limit) {
            put(at, end, "Memory "); put_number(at, end, (used_bytes + 1023u) >> 10);
            put(at, end, " of "); put_number(at, end, limit >> 10); put(at, end, " KiB, ");
            if (over) { put_number(at, end, (used_bytes - limit + 1023u) >> 10); put(at, end, " over"); }
            else if (services) { put_number(at, end, (services + 1023u) >> 10); put(at, end, " shared"); }   // 4.8m2
            else { put_number(at, end, (limit - used_bytes) >> 10); put(at, end, " free"); }
        } else put(at, end, p.view == View::reading ? "Memory: reading..." : "Memory: -");
        *at = 0;
        text(fb, margin, line_y, line, over ? red : white, 1);
        const char* why = save_refusal(p);
        const char* status = line;
        Color color = grey;
        at = line;
        if (over) { status = "Too large: turn something off"; color = red; }
        else if (why && p.view != View::reading) { status = why; color = red; }
        else if (!changed(p) || p.view == View::reading) status = "No changes";
        else {
            const uint32_t n = chosen(p, false) + chosen(p, true);
            if (n) { put_number(at, end, n); put(at, end, n == 1 ? " mod on" : " mods on"); }
            else put(at, end, "All off");
            put(at, end, ": applied when you boot");
            color = accent;
        }
        *at = 0;
        text(fb, width - margin - text_width(status, 1), line_y, status, color, 1);
    }
    // Rows: the packages the filter shows (8.6: nothing else).
    const uint32_t rows = p.listed, selected = position(p), first = first_row(rows, selected);
    for (uint32_t i = 0; i < visible_rows && first + i < rows; ++i) {
        const uint32_t r = first + i;
        const int y = rows_top + int(i) * row_height;
        const bool on = r == selected;
        if (on) fill(fb, margin - 6, y - 1, width - 2 * margin + 12, row_height - 1, white);
        const Color fg = on ? black : white, soft = on ? dark : grey;
        const Package& m = p.packages[p.list[r]];
        // 8.4d: a thin line above the first row of each kind (no extra row)
        if (r && p.packages[p.list[r - 1]].kind != m.kind) fill(fb, margin, y - 2, width - 2 * margin, 1, grey);
        box(fb, margin + 4, y + 1, (m.flags & flag_on) != 0, fg);
        text(fb, margin + 24, y, text_at(p, m.name), fg, 2);
        at = line;
        put(at, end, kind_name(m.kind)); put(at, end, "  "); put_number(at, end, (m.bytes + 1023u) >> 10);
        put(at, end, " KiB");
        *at = 0;
        text(fb, width - margin - 4 - text_width(line, 1), y + 4, line, soft, 1);
    }
    if (rows > visible_rows) {                  // scroll bar, as ui_pages draw_list
        const int track = int(visible_rows) * row_height - 2;
        const int knob = int(ui::scale(uint32_t(track), visible_rows, rows));
        const int pos = int(ui::scale(uint32_t(track - knob), first, rows - visible_rows));
        fill(fb, width - 6, rows_top - 1, 2, track, dark);
        fill(fb, width - 6, rows_top - 1 + pos, 2, knob, accent);
    }
    const int empty_y = rows_top + 2;           // (nothing listed: no scrolling)
    if (!p.shown && p.view == View::list) text(fb, margin + 4, empty_y, "No mod packages on this OP-XY.", grey, 2);
    else if (p.shown && !p.listed) text(fb, margin + 4, empty_y, "Nothing on.", grey, 2);
    // 8.6: the SHIFT keys named here (no row of their own).
    ui::footer_line(fb, hint_y, p.listed ? "PLAY on/off    SHIFT+turn filter    SHIFT+PLAY all on/off    STOP back" :
                                           "SHIFT+turn filter    STOP back", chrome);
}
} }
