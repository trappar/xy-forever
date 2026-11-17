#ifndef XYF_MENU_VIEW_HPP
#define XYF_MENU_VIEW_HPP
#include "xyf_menu.hpp"
#include "ui_pages.hpp"
// Draws the XY Forever page for a menu state (full repaint). status: one
// line under the main menu (link state, last result); may be null. chrome: the USB mark, the
// activity line and the build (ui_pages.hpp); may be null.
namespace xyf { namespace menu {
void draw(uint16_t* fb, const State& state, const char* status, const ui::Chrome* chrome);
} }
#endif
