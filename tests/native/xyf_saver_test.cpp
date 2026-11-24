// target/xyf_saver.hpp (#315): the screen saver's start, wake, hold, setting and frames, on a driven clock.
#include "doctest.h"
#include <cstring>
#include "xyf_saver.hpp"
#include "xyf_settings.hpp"
using namespace xyf;
static const uint32_t minute = 60000, play = menu::key_play, plus = menu::key_plus;

TEST_CASE("saver: starts after the idle time, the waking press and its release go nowhere") {
    saver::State s;
    saver::start(s, 1000);
    CHECK((!s.on && s.x == saver::max_x / 2 && s.y == saver::max_y / 2));
    CHECK(!saver::pass(s, 1000 + minute - 1, minute, false, false, false));
    CHECK(!s.on);
    CHECK(saver::pass(s, 1000 + minute, minute, false, false, false));   // started: draw it
    CHECK(s.on);
    CHECK(!saver::pass(s, 1000 + minute + saver::frame_ms - 1, minute, false, false, false));   // no frame yet
    CHECK(saver::pass(s, 1000 + minute + saver::frame_ms, minute, false, false, false));        // one frame: it moved
    CHECK((s.x == saver::max_x / 2 + saver::step && s.y == saver::max_y / 2 + saver::step));
    const uint32_t woke = 1000 + 2 * minute;
    CHECK(saver::input(s, play, true, woke));          // PLAY wakes it: nothing else (never a boot)
    CHECK((!s.on && s.woke == play));
    CHECK(!saver::input(s, plus, true, woke + 1));     // another key meanwhile: the menu's
    CHECK(!saver::input(s, plus, false, woke + 2));
    CHECK(saver::input(s, play, false, woke + 3));     // the waking key's release: nowhere either
    CHECK(!saver::input(s, play, true, woke + 4));     // then the menu accepts input as normal
    CHECK(!saver::input(s, play, false, woke + 5));
    CHECK(!saver::pass(s, woke + 5 + minute - 1, minute, false, false, false));   // idle counts from the last key
    CHECK(saver::pass(s, woke + 5 + minute, minute, false, false, false));
}

TEST_CASE("saver: a knob turn wakes it and is activity; a key held across it") {
    saver::State s;
    saver::start(s, 0);
    CHECK(!saver::input(s, saver::none, true, 30000));   // a turn: activity
    CHECK(!saver::pass(s, minute, minute, false, false, false));
    CHECK(saver::pass(s, 30000 + minute, minute, false, false, false));
    CHECK(saver::input(s, saver::none, true, 30000 + minute + 5));   // a turn wakes it: moves nothing
    CHECK((!s.on && s.woke == saver::none));
    CHECK(!saver::input(s, saver::none, true, 30000 + minute + 6));  // the next turn moves the menu
    // A key held from before the start: its release reaches the menu (only a press wakes).
    CHECK(!saver::input(s, menu::key_shift, true, 200000));
    CHECK(saver::pass(s, 200000 + minute, minute, false, false, false));
    CHECK(!saver::input(s, menu::key_shift, false, 200000 + minute + 1));
    CHECK(s.on);
}

TEST_CASE("saver: a question or an error holds it off and ends it; off never starts it; USB work") {
    saver::State s;
    saver::start(s, 0);
    CHECK(!saver::pass(s, 10 * minute, minute, true, false, false));   // held: never times out into it
    CHECK(!s.on);
    CHECK(!saver::pass(s, 10 * minute + minute - 1, minute, false, false, false)); // a full idle time after the hold
    CHECK(saver::pass(s, 11 * minute, minute, false, false, false));
    CHECK(saver::pass(s, 11 * minute + 1, minute, true, false, false)); // the question appears: it ends (redraw)
    CHECK(!s.on);
    CHECK(!saver::pass(s, 100 * minute, 0, false, false, false)); // off (settings.txt saver=off)
    CHECK(!s.on);
    CHECK(!saver::pass(s, 100 * minute + 1, minute, false, true, true)); // USB work is activity
    CHECK(!saver::pass(s, 101 * minute, minute, false, false, false));
    CHECK(saver::pass(s, 101 * minute + 1, minute, false, false, false));
    const int32_t x = s.x;
    CHECK(
        !saver::pass(s, 102 * minute, minute, false, true, true)); // USB work under it: it stays, the mark holds still
    CHECK((s.on && s.x == x));
    CHECK(saver::pass(s, 102 * minute + saver::frame_ms, minute, false, false, false));
    CHECK(s.x != x);
    // A build (still, not activity): it starts during one, and holds the mark still while it runs.
    CHECK(saver::input(s, plus, true, 200 * minute));
    CHECK(!saver::pass(s, 200 * minute + 1, minute, false, false, true));
    CHECK(saver::pass(s, 201 * minute, minute, false, false, true));
    const int32_t held = s.x;
    CHECK(!saver::pass(s, 202 * minute, minute, false, false, true));
    CHECK((s.on && s.x == held));
    menu::State m;
    menu::start(m, nullptr, true);
    CHECK((!saver::holds(m, false) && saver::holds(m, true))); // the #65 question
    m.storage = menu::storage_unavailable;
    CHECK(saver::holds(m, false));
    m.storage = menu::storage_ready;
    m.screen = menu::Screen::packs;
    m.pack_confirm = true;
    CHECK(saver::holds(m, false)); // a content pack's confirm dialog
}

TEST_CASE("saver: the mark bounces inside the screen, changing colour at each bounce") {
    saver::State s;
    saver::start(s, 0);
    saver::pass(s, minute, minute, false, false, false);
    uint32_t bounces = 0, color = s.color;
    bool left = false, right = false, top = false, bottom = false;
    for (uint32_t t = minute + saver::frame_ms; t < minute + 2000 * saver::frame_ms; t += saver::frame_ms) {
        REQUIRE(saver::pass(s, t, minute, false, false, false));
        REQUIRE((s.x >= 0 && s.x <= saver::max_x && s.y >= 0 && s.y <= saver::max_y));
        left |= s.x < saver::step;
        right |= s.x > saver::max_x - saver::step;
        top |= s.y < saver::step;
        bottom |= s.y > saver::max_y - saver::step;
        if (s.color != color) ++bounces;
        color = s.color;
        REQUIRE(color < saver::color_count);
    }
    CHECK((left && right && top && bottom && bounces > 10));
    CHECK((saver::mark_w == gfx::mark_width(saver::outer, saver::offset) && saver::max_x > 0 && saver::max_y > 0));
}

TEST_CASE("saver: the setting (settings.txt saver=)") {
    using namespace settings;
    Record r;
    parse("", 0, r);
    CHECK((r.saver == saver_1 && saver_ms(r.saver) == minute)); // default: 1 minute
    const char* const words[] = {"saver=1\n", "saver=2\n", "saver=5\n", "saver=10\n", "saver=off\n"};
    const uint32_t ms[] = {minute, 2 * minute, 5 * minute, 10 * minute, 0};
    for (uint32_t i = 0; i != saver_modes; ++i) {
        parse(words[i], uint32_t(std::strlen(words[i])), r);
        CHECK((r.saver == i && saver_ms(i) == ms[i]));
        char out[max_text];
        Record back;
        parse(out, format(out, sizeof(out), r), back);
        CHECK(back.saver == i); // round trip
    }
    parse("saver=3\n", 8, r);
    CHECK(r.saver == saver_1); // only exact values count
    parse("saver=OFF\n", 10, r);
    CHECK(r.saver == saver_1);
    CHECK((saver_option_count == 5 && !std::strcmp(saver_text(saver_off), "Off") &&
           !std::strcmp(saver_text(saver_1), "1 minute") && !std::strcmp(label(row_saver), "Screen saver")));
}
