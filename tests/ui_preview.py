"""Render XY Forever screens on the host (same C++ as the device) to PNG.
Usage: uv run tests/ui_preview.py [OUTDIR] (mise run preview)  (default: private/xy-forever/previews). Offline only."""

import sys
import tempfile
from array import array
from pathlib import Path

import xyf_hosttest
from PIL import Image

WIDTH, HEIGHT, STRIDE = 480, 216, 256


def build():
    """The screens' C++ (the extension's ui module: hosttest/bind/ui.cpp)."""
    return xyf_hosttest.module('ui')


def framebuffer(lib):
    return array('H', bytes(2 * lib.ui_framebuffer_halfwords()))


def rgb888(v):
    return bytes(((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31))


def pixels(fb):
    """Device layout -> rows of RGB888 (x left to right, y top to bottom)."""
    halves = fb[: WIDTH * STRIDE]
    colour = {v: rgb888(v) for v in set(halves)}  # a screen has few colours: each converted once
    last = (WIDTH - 1) * STRIDE  # x = 0 is the device's last column
    return [b''.join(map(colour.__getitem__, halves[last + y :: -STRIDE])) for y in range(HEIGHT)]


def png(path, rows, scale=2):
    image = Image.frombytes('RGB', (WIDTH, HEIGHT), b''.join(rows))
    image.resize((WIDTH * scale, HEIGHT * scale), Image.Resampling.NEAREST).save(path, 'PNG')


def main():
    if sys.argv[1:2] and sys.argv[1].startswith('-'):
        print(__doc__)
        return
    from xy_forever.paths import PREVIEWS

    out = Path(sys.argv[1]) if sys.argv[1:] else PREVIEWS
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        lib = build()
        fb = framebuffer(lib)
        lib.ui_render_menu(fb, 0, b'')
        png(out / 'menu.png', pixels(fb))
        lib.ui_render_menu(fb, 3, b'Not available in this build.')
        png(out / 'menu-status.png', pixels(fb))
        lib.ui_render_message(fb)
        png(out / 'info.png', pixels(fb))
        lib.ui_render_crash(fb, 0x26, 0x8AB03D14, 0x8AB05A20, 0x00030003)
        png(out / 'crash.png', pixels(fb))
        lib.ui_render_where(fb, 3)
        png(out / 'where.png', pixels(fb))  # a wait past 500 ms (#114)
        lib.ui_render_library(fb, 3, 7)
        png(out / 'library.png', pixels(fb))
        lib.ui_render_confirm(fb, 1)
        png(out / 'confirm.png', pixels(fb))
        lib.ui_render_progress(fb, 1300000, 2000000)
        png(out / 'progress.png', pixels(fb))
        lib.ui_render_home(fb, 0)
        png(out / 'home-empty.png', pixels(fb))
        # Menu flow driven through the device state machine and view.
        lib.flow_start(1)
        lib.flow_draw(fb, b'')
        png(out / 'flow-1-main.png', pixels(fb))
        lib.flow_safe(b'Boot without mods', 0)
        lib.flow_key(54)
        lib.flow_draw(fb, b'')
        png(out / 'flow-2-safe-mode.png', pixels(fb))
        # #25, #197: Boot without mods (PLAY, at once): the progress screen, drawn from the home menu (never the Mods page).
        lib.mods_reset()
        lib.mods_ready(b'', b'')
        lib.mods_saving(0, 1, 2, 3)
        lib.mods_progress_title(b'Booting without mods')
        lib.mods_progress(1200)
        lib.mods_draw(fb)
        png(out / 'flow-3b-safe-mode-running.png', pixels(fb))
        # #21: the Settings page (Mod memory, Menu at power-on, USB mode, Ask before USB control, Screen saver, About)
        # and the About page.
        lib.flow_start(1)
        lib.flow_settings(2, 0, b'')
        lib.flow_settings_rows(1, 0, 0)
        lib.flow_key(53)
        lib.flow_key(51)
        lib.flow_key(54)
        lib.flow_draw(fb, b'')
        png(out / 'flow-4-settings.png', pixels(fb))
        for _ in range(4):
            lib.flow_key(54)
        lib.flow_key(51)
        lib.flow_draw(fb, b'')
        png(out / 'flow-5-about.png', pixels(fb))
        # #21: USB mode's option list (PLAY on the row), then the page with that row picked, saving (#191: marked until
        # its write at the quiet moment), and Mod memory's pick likewise.
        lib.flow_key(52)
        for _ in range(3):
            lib.flow_key(53)
        lib.flow_key(51)
        lib.flow_options(0)
        lib.flow_key(54)
        lib.flow_draw(fb, b'')
        png(out / 'flow-6-options.png', pixels(fb))
        lib.flow_key(51)
        lib.flow_settings_rows(1, 1, 0)
        lib.flow_settings_marked(1 << 2)
        lib.flow_draw(fb, b'')
        png(out / 'flow-7-unsaved.png', pixels(fb))
        lib.flow_settings_marked(0)
        lib.flow_settings(3, 1, b'')
        lib.flow_settings_rows(1, 0, 0)
        lib.flow_key(53)
        lib.flow_key(53)
        lib.flow_draw(fb, b'')
        png(out / 'flow-8-memory-pending.png', pixels(fb))
        # #315: the Screen saver row, its option list, and the saver itself (its start, and later after bounces).
        lib.flow_settings(2, 0, b'')
        for _ in range(4):
            lib.flow_key(54)
        lib.flow_draw(fb, b'')
        png(out / 'flow-9-screen-saver.png', pixels(fb))
        lib.flow_key(51)
        lib.flow_options(0)
        lib.flow_draw(fb, b'')
        png(out / 'flow-10-saver-options.png', pixels(fb))
        lib.flow_key(52)
        lib.ui_render_saver(fb, 0, 0)
        png(out / 'saver-1-start.png', pixels(fb))
        lib.ui_render_saver(fb, 450, 0)
        png(out / 'saver-2-later.png', pixels(fb))
        stage2_previews(tmp, out / 'stage2')
    print('\n'.join(str(p) for p in sorted(out.glob('**/*.png'))))


def stage2_previews(tmp, out):
    """Main stage 2 (ABI 2): the real stage-2 program (target/stage2_menu.cpp)
    run against a scripted stage 1 (tests/stage2_menu_bridge.cpp); each frame
    is what it presents through begin_frame/end_frame."""
    from test_xyf_stage2_menu import PLAY, PLUS, Stage2Program, build_stage2, press

    out.mkdir(parents=True, exist_ok=True)
    p = Stage2Program(build_stage2(tmp, 'preview'))
    p.run(lost=0)
    png(out / 'stage2-1-main.png', pixels(p.fb))
    p.run(press(PLUS) + press(PLAY), lost=4)
    png(out / 'stage2-2-safe-row.png', pixels(p.fb))  # nothing saved: why
    p.run(lost=0, note='Not available in this build.')
    png(out / 'stage2-3-note.png', pixels(p.fb))
    q = Stage2Program(build_stage2(tmp, 'preview-empty'))
    q.run(lost=0, blob=None)
    png(out / 'stage2-4-no-library.png', pixels(q.fb))


if __name__ == '__main__':
    main()
