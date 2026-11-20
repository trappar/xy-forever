"""Render XY Forever screens on the host (same C++ as the device) to PNG.
Usage: python3 ui_preview.py [OUTDIR]  (default: private/xy-forever/previews). Offline only."""
import ctypes as C, struct, subprocess, sys, tempfile, zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT, STRIDE = 480, 216, 256


def build(out, run=subprocess.run):
    """Compile the screens into out/ui.dylib and load it (run: the tests pass a cached compile)."""
    lib = Path(out) / 'ui.dylib'
    run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
        '-ffreestanding', '-fno-builtin', '-fno-exceptions', '-fno-rtti',
        str(ROOT / 'target/ui_gfx.cpp'), str(ROOT / 'target/ui_screens.cpp'),
        str(ROOT / 'target/ui_pages.cpp'), str(ROOT / 'tests/ui_preview_bridge.cpp'),
        str(ROOT / 'tests/ui_pages_bridge.cpp'), str(ROOT / 'target/xyf_store.cpp'), str(ROOT / 'target/xyf_info.cpp'),
        str(ROOT / 'target/xyf_library.cpp'), str(ROOT / 'target/xyf_state.cpp'), str(ROOT / 'target/xyf_crc32.cpp'), str(ROOT / 'target/xyf_catalog.cpp'),
        str(ROOT / 'target/xyf_menu.cpp'), str(ROOT / 'target/xyf_menu_view.cpp'),
        str(ROOT / 'tests/ui_flow_bridge.cpp'), str(ROOT / 'target/xyf_mods.cpp'), str(ROOT / 'target/xyf_mods_view.cpp'),
        str(ROOT / 'tests/ui_mods_bridge.cpp'), '-o', str(lib)], check=True, capture_output=True)
    return C.CDLL(str(lib))


def framebuffer(lib):
    return (C.c_uint16 * lib.ui_framebuffer_halfwords())()


def pixels(fb):
    """Device layout -> rows of RGB888 (x left to right, y top to bottom)."""
    rows = []
    for y in range(HEIGHT):
        row = bytearray()
        for x in range(WIDTH):
            v = fb[(WIDTH - 1 - x) * STRIDE + y]
            row += bytes(((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31))
        rows.append(bytes(row))
    return rows


def png(path, rows, scale=2):
    lines = []
    for r in rows:
        wide = b''.join(r[x:x + 3] * scale for x in range(0, len(r), 3))
        lines += [b'\0' + wide] * scale
    raw = b''.join(lines)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    w, h = WIDTH * scale, HEIGHT * scale
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def main():
    if sys.argv[1:2] and sys.argv[1].startswith('-'):
        print(__doc__); return
    from xy_forever.paths import PREVIEWS
    out = Path(sys.argv[1]) if sys.argv[1:] else PREVIEWS; out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        lib = build(tmp)
        fb = framebuffer(lib)
        lib.ui_render_menu(fb, 0, b''); png(out / 'menu.png', pixels(fb))
        lib.ui_render_menu(fb, 4, b'Uninstall: not available yet.'); png(out / 'menu-status.png', pixels(fb))
        lib.ui_render_message(fb); png(out / 'info.png', pixels(fb))
        lib.ui_render_crash(fb, 0x26, 0x8ab03d14, 0x8ab05a20, 0x00030003); png(out / 'crash.png', pixels(fb))
        lib.ui_render_where(fb, 3); png(out / 'where.png', pixels(fb))   # a wait past 500 ms (#114)
        lib.ui_render_library(fb, 3, 7); png(out / 'library.png', pixels(fb))
        lib.ui_render_confirm(fb, 1); png(out / 'confirm.png', pixels(fb))
        lib.ui_render_progress(fb, 1300000, 2000000); png(out / 'progress.png', pixels(fb))
        lib.ui_render_home(fb, 0); png(out / 'home-empty.png', pixels(fb))
        # Menu flow driven through the device state machine and view.
        lib.flow_start(1); lib.flow_draw(fb, b''); png(out / 'flow-1-main.png', pixels(fb))
        lib.flow_key(54); lib.flow_key(51); lib.flow_draw(fb, b''); png(out / 'flow-2-library.png', pixels(fb))
        lib.flow_key(52); lib.flow_key(54); lib.flow_key(54); lib.flow_key(54); lib.flow_key(51)   # Uninstall
        lib.flow_draw(fb, b''); png(out / 'flow-3-confirm.png', pixels(fb))
        # #21: the Settings page (Mod memory, Menu at power-on, USB mode, About) and the About page.
        lib.flow_start(1); lib.flow_settings(2, 0, b''); lib.flow_settings_rows(1, 0, 0)
        lib.flow_key(53); lib.flow_key(51); lib.flow_key(54); lib.flow_draw(fb, b''); png(out / 'flow-4-settings.png', pixels(fb))
        lib.flow_key(54); lib.flow_key(54); lib.flow_key(51); lib.flow_draw(fb, b''); png(out / 'flow-5-about.png', pixels(fb))
        # #21: USB mode's option list (PLAY on the row), then the page with that row changed and not saved yet.
        lib.flow_key(52); lib.flow_key(53); lib.flow_key(51); lib.flow_options(0); lib.flow_key(54)
        lib.flow_draw(fb, b''); png(out / 'flow-6-options.png', pixels(fb))
        lib.flow_key(51); lib.flow_settings_rows(1, 1, 0); lib.flow_settings_marked(1 << 2)
        lib.flow_draw(fb, b''); png(out / 'flow-7-unsaved.png', pixels(fb))
        lib.flow_settings_marked(0); lib.flow_settings(3, 1, b''); lib.flow_settings_rows(1, 0, 0); lib.flow_key(53); lib.flow_key(53)
        lib.flow_draw(fb, b''); png(out / 'flow-8-memory-pending.png', pixels(fb))
        stage2_previews(tmp, out / 'stage2')
    print('\n'.join(str(p) for p in sorted(out.glob('**/*.png'))))


def stage2_previews(tmp, out):
    """Main stage 2 (ABI 2): the real stage-2 program (target/stage2_menu.cpp)
    run against a scripted stage 1 (tests/stage2_menu_bridge.cpp); each frame
    is what it presents through begin_frame/end_frame."""
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_xyf_stage2_menu import PLAY, PLUS, Stage2Program, build_stage2, press
    out.mkdir(parents=True, exist_ok=True)
    p = Stage2Program(build_stage2(tmp, 'preview'))
    p.run(lost=0); png(out / 'stage2-1-main.png', pixels(p.fb))
    p.run(press(PLUS) + press(PLAY) + press(PLUS), lost=6); png(out / 'stage2-2-library.png', pixels(p.fb))
    p.run(lost=0, note='Not available in this build.'); png(out / 'stage2-3-note.png', pixels(p.fb))
    q = Stage2Program(build_stage2(tmp, 'preview-empty'))
    q.run(lost=0, blob=None); png(out / 'stage2-4-no-library.png', pixels(q.fb))


if __name__ == '__main__':
    main()
