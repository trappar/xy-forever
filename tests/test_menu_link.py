"""menu_link (the USB service's command link) and the file boot link it rides on: Result layout,
decoder, link timing and wiring; no device execution. The Test024 held session is gone."""
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.census import (MENU_LINK_BYTES, MENU_LINK_FIELDS, MENU_LINK_MAGIC, PEER_DIAGNOSTIC_FIELDS,
                                   PEER_SNAPSHOT_WORDS, menu_link)

HEADER = (ROOT / 'target/menu_link.hpp').read_text()
SOURCE = (ROOT / 'target/menu_link.cpp').read_text()
SESSION = (ROOT / 'target/peer_services.cpp').read_text()
FILE_BOOT = (ROOT / 'target/file_boot.cpp').read_text()


def constant(name, text=HEADER):
    return int(re.search(r'\b' + name + r' = (0x[0-9a-f]+|\d+)u?[,;]', text).group(1), 0)


def block(**values):
    words = [0] * (MENU_LINK_BYTES // 4)
    words[0] = MENU_LINK_MAGIC
    for name, value in values.items():
        words[MENU_LINK_FIELDS.index(name)] = value
    return words


class MenuLinkTest(unittest.TestCase):
    def test_result_layout_matches_header(self):
        body = HEADER[HEADER.index('struct Result {'):HEADER.index('};', HEADER.index('struct Result {'))]
        names = [n.strip() for decl in re.findall(r'uint32_t ([^;]+);', body) for n in decl.split(',')]
        self.assertEqual(names, MENU_LINK_FIELDS)
        self.assertIn('peer_services::Diagnostic diagnostic;', body)
        self.assertIn('24 * 4 + sizeof(peer_services::Diagnostic)', HEADER)
        self.assertEqual(MENU_LINK_BYTES, 4 * (24 + len(PEER_DIAGNOSTIC_FIELDS) + 2 * PEER_SNAPSHOT_WORDS))

    def test_decoder_reports_both_roles(self):
        b = block(version=1, role=1, begin_result=1, final_result=4, safe_handoff=1, play=1, link_ms=540,
                  keepalives=40, echoes=42, commands=2, answers=2)
        a = block(version=1, role=0, begin_result=1, final_result=4, safe_handoff=1, activity=45,
                  commands_seen=2, last_command=0x434d0207, last_argument=0x434d0207, max_gap_ms=260)
        for words, role in ((b, 1), (a, 0)):
            dump = struct.pack('<%dI' % len(words), *words)
            out = menu_link([dict(address=0x8ab05000, bytes=len(dump), offset=0)], dump)
            self.assertEqual((out['role'], out['begin_result'], out['final_result']), (role, 'waiting', 'ready'))
        self.assertEqual((out['commands_seen'], out['last_command'], out['last_command_item']), (2, '0x434d0207', 2))
        self.assertIsNone(menu_link([dict(address=0, bytes=16, offset=0)], bytes(16)))

    def test_link_timing_is_bounded_with_margin(self):
        # The file boot's link session (xyf_boot) carries the keepalives and every USB command.
        idle = constant('role0_idle_ms', (ROOT / 'target/file_boot.hpp').read_text())
        keepalive, answer = constant('keepalive_ms', FILE_BOOT), constant('answer_ms', FILE_BOOT)
        self.assertLessEqual(constant('handshake_ms', FILE_BOOT), 3000)
        self.assertLessEqual(idle, 3000)                        # a's cost if b never comes
        self.assertLessEqual(keepalive * 4, idle)               # several keepalives per idle window
        self.assertLess(2 * answer + keepalive, idle)           # b declares loss before a gives up

    def test_tags_match_session_and_decoder(self):
        self.assertIn('command_tag = 0x434du', (ROOT / 'target/peer_services.hpp').read_text())
        self.assertEqual(constant('keepalive_tag', FILE_BOOT), 0x4b41)
        self.assertNotEqual(constant('keepalive_tag', FILE_BOOT), 0x434d)

    def test_usb_commands_ride_the_file_boot_session(self):
        self.assertIn('bool tick() { return xyf_boot::tick(); }', SOURCE)
        self.assertIn('bool usb_command_value(uint32_t value) { return xyf_boot::link_value(value); }', SOURCE)
        for banned in ('freeze(', 'start_receiving', 'admit(', 'hold(', 'begin('):
            self.assertNotIn(banned, SOURCE)

    def test_session_end_always_closes(self):
        # end() marks an undrained/unreleased session failed but still closes it.
        end = SESSION[SESSION.index('Result Stock121PeerSession::end() {'):]
        end = end[:end.index('\n}\n')]
        drain = [l for l in end.splitlines() if 'Result::drain' in l]
        self.assertEqual(len(drain), 1)
        self.assertNotIn('return', drain[0])
        self.assertLess(end.index('Result::drain'), end.index('stock<0x889f562eu'))

    def test_role0_activity_reads_only_the_stock_rx_header(self):
        observe = SESSION[SESSION.index('void Stock121PeerSession::observe(U32 now) {'):]
        observe = observe[:observe.index('\n}\n')]
        self.assertEqual(set(re.findall(r'0x[0-9a-f]{8}u', observe)), {'0x11b000c0u'})
        self.assertIn('first[i]!=second[i]', observe)            # torn DMA reads rejected
        poll = SESSION[SESSION.index('Result Stock121PeerSession::poll(U32 now) {'):]
        idle = poll[poll.index('if((hold_||watch_)&&!role()&&!agreed_){'):]
        idle = idle[:idle.index('\n    }\n')]
        # observe, then (Test027) a served storage request counts as activity, then the idle check.
        order = [idle.index(s) for s in ('observe(now);', 'if(served_activity_){served_activity_=false;last_activity_=now;}',
                                          'if(now-last_activity_>=idle_ms_)return fail(Result::timeout);')]
        self.assertEqual(order, sorted(order))
        self.assertIn('if(hold_&&role()&&!diagnostic_.link_initialized&&diagnostic_.elapsed_ms>=handshake_ms_)'
                      'return fail(Result::timeout);', poll)

    def test_key_ui_runs_in_file_boot_session_only(self):
        # --xyf-mtp (removed) was the only mode whose menu used the menu-link session.
        ui = (ROOT / 'target/key_ui.cpp').read_text()
        self.assertNotIn('menu_link', ui)
        self.assertNotIn('KEY_UI_MENU_LINK', ui)
        for text in re.findall(r'"([^"]*)"', SOURCE):
            self.assertLess(len(text), 58, text)                # one console line (60 columns)

    def test_no_pre_copy_hook(self):
        # The Test024 held session ran as a hook before file boot; v3 has only file boot's session.
        self.assertNotIn('xy_forever_menu_link', (ROOT / 'target/cold.cpp').read_text())
        self.assertNotIn('xy_forever_menu_link', SOURCE)


if __name__ == '__main__':
    unittest.main()
