#ifndef XYF_MODS_VIEW_HPP
#define XYF_MODS_VIEW_HPP
#include "xyf_mods.hpp"
#include "ui_pages.hpp"
// Draws the Mods page (ui_gfx; the compact title bar, rows and hint line of the other pages): the filter
// and how many listed rows are on and the generation in the title bar, the memory meter on its edge,
// one line for the memory and the status (or a result), and the packages the filter shows with their
// toggles (9 rows, scrolling), and the hint line (a full repaint). While a job runs, the building screen: one bar for the whole
// build (progress()), the step under it, the percentage, the footer. Frame remembers what the
// framebuffer holds; while it holds this job's building screen only the parts that changed are drawn (the
// bar's new part, a changed text line over its own background), never a full-screen or full-bar repaint.
// The caller clears frame.drawn whenever it draws anything else. chrome: the USB mark in the title bar and
// the activity line in place of the hint line (on the building screen: the line below the footer), both
// redrawn alone when only they changed; the build is not shown here.
namespace xyf { namespace mods {
static const uint32_t visible_rows = 9;
struct Frame { uint32_t drawn, job, bar_px, step, percent, done, total, footer, usb; const char* activity; };
void draw(uint16_t* fb, const Page& page, const ui::Chrome* chrome, Frame& frame);
// First visible row (of the listed rows) keeping the selection on screen.
uint32_t first_row(uint32_t rows, uint32_t at);
} }
#endif
