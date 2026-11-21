"""Test030 menu boot (staged link: catalog blob + chosen library entry): layout, decoder,
buffers and protocol rules; no device execution."""
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
import xyf_build as builder
from xy_forever.census import (FILE_BOOT_BYTES, FILE_BOOT_FIELDS, FILE_BOOT_LIBRARY_BYTES, FILE_BOOT_MAGIC,
                                   FILE_BOOT_MENU_BYTES, FILE_BOOT_MENU_FIELDS, file_boot,
                                   BOOT_TIMING_FIELDS, BOOT_TIMING_MAGIC, BOOT_TIMING_BYTES,
                                   boot_timing_summary, boot_timing,
                                   BOOT_DETAIL_FIELDS, BOOT_DETAIL_MAGIC, BOOT_DETAIL_BYTES)

XYF = (ROOT / 'target/xyf_boot.hpp').read_text()
SOURCE = unit_text(ROOT / 'target/file_boot.cpp')
SESSION = (ROOT / 'target/peer_services.cpp').read_text()
SESSION_H = (ROOT / 'target/peer_services.hpp').read_text()
KEY_UI = (ROOT / 'target/key_ui.cpp').read_text()
FILE_BOOT_H = (ROOT / 'target/file_boot.hpp').read_text()


def constant(text, name):
    return int(re.search(r'\b' + name + r' = (0x[0-9a-f]+|\d+)u?[;,]', text).group(1), 0)


def body(text, start):
    begin = text.index(start)
    return text[begin:text.index('\n}\n', begin)]


class XyfBootTest(unittest.TestCase):
    def test_timing_wire_layout_and_validity(self):
        block = XYF[XYF.index('struct TimingInfo {'):XYF.index('};', XYF.index('struct TimingInfo {'))]
        names = [n.strip() for decl in re.findall(r'uint32_t ([^;]+);', block) for n in decl.split(',')]
        self.assertEqual(names, BOOT_TIMING_FIELDS)
        words = [0] * len(names)
        values = dict(magic=BOOT_TIMING_MAGIC, version=1, valid=0x1f | 128,
                      a_mount_start_ms=12, a_mount_ms=5100, a_scan_ms=4,
                      a_read_ms=700, a_crc_ms=45, a_admit_ms=0, a_ready_ms=5116, b_check_ms=6200)
        for name, value in values.items():
            words[names.index(name)] = value
        encoded = struct.pack('<16I', *words)
        decoded = boot_timing_summary(encoded)
        self.assertEqual(decoded['a_mount_ms'], 5100)
        self.assertEqual(decoded['a_admit_ms'], 0)       # completed within timer resolution
        self.assertIsNone(decoded['a_sha_ms'])           # not observed at CHECK time
        self.assertIsNone(decoded['b_receive_ms'])
        self.assertEqual(boot_timing([dict(offset=3, bytes=len(encoded))], b'xxx' + encoded), decoded)
        self.assertIsNone(boot_timing_summary(encoded[:-4]))
        self.assertIsNone(boot_timing_summary(struct.pack('<I', 0) + encoded[4:]))
        self.assertIsNone(boot_timing_summary(encoded[:4] + struct.pack('<I', 2) + encoded[8:]))
        # The same block is saved with the version-5 dry-run result, not lost on reboot.
        prefix = [0] * ((FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES + FILE_BOOT_MENU_BYTES) // 4)
        prefix[FILE_BOOT_FIELDS.index('magic')] = FILE_BOOT_MAGIC
        prefix[FILE_BOOT_FIELDS.index('version')] = 5
        prefix[FILE_BOOT_FIELDS.index('role')] = 1
        result = struct.pack('<%dI' % len(prefix), *prefix) + encoded
        out = file_boot([dict(address=0, bytes=len(result), offset=0)], result)
        self.assertEqual(out['timing'], decoded)
        self.assertEqual(BOOT_TIMING_BYTES, 64)

    def test_transfer_detail_layout_and_version_6_result(self):
        block = XYF[XYF.index('struct TimingDetail {'):XYF.index('};', XYF.index('struct TimingDetail {'))]
        names = [n.strip() for decl in re.findall(r'uint32_t ([^;]+);', block) for n in decl.split(',')]
        self.assertEqual(names, BOOT_DETAIL_FIELDS)
        self.assertEqual(BOOT_DETAIL_BYTES, 32)
        timing = struct.pack('<16I', BOOT_TIMING_MAGIC, 1, 256 | 512, *([0] * 9), 0, 3500, 930, 0)
        detail = struct.pack('<8I', BOOT_DETAIL_MAGIC, 1, 900, 2500, 450, 120, 420, 300)
        prefix = [0] * ((FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES + FILE_BOOT_MENU_BYTES) // 4)
        prefix[FILE_BOOT_FIELDS.index('magic')] = FILE_BOOT_MAGIC
        prefix[FILE_BOOT_FIELDS.index('version')] = 6
        prefix[FILE_BOOT_FIELDS.index('role')] = 1
        result = struct.pack('<%dI' % len(prefix), *prefix) + timing + detail
        out = file_boot([dict(address=0, bytes=len(result), offset=0)], result)
        self.assertEqual(out['timing']['b_receive_ms'], 3500)
        self.assertEqual(out['detail']['b_metadata_ms'], 900)
        self.assertEqual(out['detail']['b_admit_hash_ms'], 420)
        self.assertEqual(out['detail']['b_release_ms'], 300)
        # The product result is version 6 and B stamps every stage of the transfer loop.
        self.assertIn('r.version = 6;', SOURCE)
        for field in BOOT_DETAIL_FIELDS[2:]:
            self.assertIn('d.' + field + ' =', SOURCE)

    def test_timing_status_distinguishes_current_and_previous_boot(self):
        import xyf_host
        words = [BOOT_TIMING_MAGIC, 1] + [0] * 14
        encoded = struct.pack('<16I', *words)
        status = struct.pack('<II', BOOT_TIMING_MAGIC, len(encoded)) + encoded
        decoded = xyf_host.decode_status(status)
        self.assertIsNone(decoded['dry_run_file_boot'])
        self.assertIsNone(decoded['boot_timing']['a_mount_ms'])
        self.assertIsNone(decoded['boot_timing']['a_mount_start_ms'])
        self.assertIsNone(decoded['boot_timing']['a_ready_ms'])

    def test_menu_info_layout_matches_decoder(self):
        block = XYF[XYF.index('struct MenuInfo {'):XYF.index('};', XYF.index('struct MenuInfo {'))]
        names = [n.strip() for decl in re.findall(r'uint32_t ([^;]+);', block) for n in decl.split(',')]
        self.assertEqual(names, FILE_BOOT_MENU_FIELDS)
        self.assertEqual(FILE_BOOT_MENU_BYTES, 4 * len(names))
        # Menu info follows the library info, both only in their modes.
        result = FILE_BOOT_H[FILE_BOOT_H.index('struct Result {'):]
        self.assertLess(result.index('LibraryInfo library;'), result.index('xyf_boot::MenuInfo menu;'))

    def test_decoder_reads_menu_block(self):
        words = [0] * ((FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES + FILE_BOOT_MENU_BYTES) // 4)
        for name, value in dict(magic=FILE_BOOT_MAGIC, version=3, role=1, outcome=1, committed=1,
                                storage_ms=648, hash_ms=105).items():
            words[FILE_BOOT_FIELDS.index(name)] = value
        menu = [0] * 16
        values = dict(play=1, catalog_ok=1, catalog_entries=3, choice=2, choice_entry=1, commands=2,
                      last_command=0x434d2105, blobs=1)
        for name, value in values.items():
            menu[FILE_BOOT_MENU_FIELDS.index(name)] = value
        at = (FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES) // 4
        words[at:at + 16] = menu
        block = struct.pack('<%dI' % len(words), *words)
        out = file_boot([dict(address=0x8ab06000, bytes=len(block), offset=0)], block)
        self.assertEqual(out['outcome'], 'file')
        # b (role 1): the storage/hash time words report how long a took to answer commands.
        self.assertEqual((out['first_answer_ms'], out['longest_answer_ms']), (648, 105))
        ask = body(SOURCE, 'bool b_ask(Stock121PeerSession& session, U32 value, U32 wait_ms) {')
        self.assertIn('if (!b_lost && !result.storage_ms) result.storage_ms = now();', ask)
        self.assertIn('if (waited > result.hash_ms) result.hash_ms = waited;', ask)
        self.assertEqual((out['menu']['choice'], out['menu']['choice_entry'], out['menu']['last_item']),
                         ('entry', 1, '0x21'))
        self.assertIn('library', out)
        self.assertNotIn('reserved', out['menu'])

    def test_buffers_are_disjoint_and_inside_the_free_gap(self):
        scratch = (0x8b800000, 0x8b810000)
        staging = (0x8b820000, 0x8c000000)
        buffers = [(constant(XYF, 'catalog_buffer'), constant(XYF, 'catalog_capacity')),
                   (constant(XYF, 'library_buffer'), constant(XYF, 'scan_buffer') - constant(XYF, 'library_buffer')),
                   (constant(XYF, 'scan_buffer'), constant(XYF, 'scan_capacity'))]
        for start, size in buffers:
            self.assertTrue(scratch[1] <= start and start + size <= staging[0], hex(start))
            self.assertEqual(start % 32, 0)
        spans = sorted((s, s + n) for s, n in buffers)
        for (a, b), (c, d) in zip(spans, spans[1:]):
            self.assertLessEqual(b, c)
        self.assertIn('static_assert(xyf_boot::scan_capacity >= xyf::library::scratch_needed', SOURCE)
        self.assertIn('static_assert(xyf::catalog::max_bytes <= xyf_boot::catalog_capacity', SOURCE)

    def test_staged_session_rules(self):
        poll = body(SESSION, 'Result Stock121PeerSession::poll(U32 now) {')
        # Role0: a restored blob returns the server to waiting; the session stays held.
        self.assertIn('if(staged_&&blob_&&server_.phase()==xyf_core::peer::Phase::release){', poll)
        self.assertIn('server_.begin();frozen_size_=0;blob_=false;++blobs_;', poll)
        # Role0: a release without a restored image agrees on embedded (never a commit).
        self.assertIn('if(declined()||(staged_&&server_.phase()!=xyf_core::peer::Phase::release)){', poll)
        # Role0: close waits until the caller unmounted (storage nests inside the session).
        self.assertLess(poll.index('if(staged_&&!role()&&defer_close_)return Result::waiting;'),
                        poll.index('checked=check_drained(diagnostic_.after);\n    if(checked==Result::waiting)'))
        # Role1: a blob is admitted by its own CRC/SHA and ends after restore (no release 7).
        self.assertIn('client_.admit(client_.digest())', poll)
        self.assertIn('client_.stop();transferring_=false;blob_=false;blob_done_=true;++blobs_;', poll)
        # Role1: no commands while a transfer is in flight.
        command = body(SESSION, 'Result Stock121PeerSession::command(U32 value) {')
        self.assertIn('||transferring_)return Result::unavailable;', command)
        refuse = body(SESSION, 'void Stock121PeerSession::refuse() {') if 'void Stock121PeerSession::refuse() {\n' in SESSION \
            else SESSION[SESSION.index('void Stock121PeerSession::refuse()'):][:120]
        self.assertIn('staged_&&!role()&&!agreed_', refuse)
        self.assertIn('Result freeze_blob(const Byte* data, U32 size);', SESSION_H)

    def test_owner_flow_unmounts_before_freezing_and_refuses_on_any_failure(self):
        owner = body(SOURCE, 'bool role0_menu_boot(U32& bytes, U32& crc) {')
        self.assertLess(owner.index('session.begin('), owner.index('storage.begin_fresh('))
        boot = owner[owner.index('// Boot: pick, read'):]
        self.assertLess(boot.index('storage.end()'), boot.index('admit(reinterpret_cast<Byte*>(staging)'))
        self.assertLess(boot.index('admit(reinterpret_cast<Byte*>(staging)'), boot.index('session.freeze_image('))
        self.assertIn('if (!serve) { session.refuse();', boot)
        self.assertIn('xyf::cold_library::matches(selection, bytes, crc)', boot)
        loop = owner[owner.index('while (running(status)) {'):]
        self.assertLess(loop.index('r.storage_end = U32(storage.end())'), loop.index('session.allow_close();'))
        self.assertIn("lib.state.once[0] = 0;", owner)               # no 'once' while read-only

    def test_client_flow(self):
        client = body(SOURCE, 'bool role1_menu_boot(U32& bytes) {')
        # Test 059: the product's menu-on-failure path comes first (#if); --xyf-boot keeps this one.
        client = client[client.index('#else\n    if (!b_lost && r.menu.play) {'):]
        self.assertLess(client.index('xyf_boot::item_catalog'), client.index('xy_forever_key_ui();'))
        self.assertLess(client.index('xy_forever_key_ui();'), client.index('xyf_boot::item_boot_default'))
        self.assertIn('if (r.admit_reason != reason_ok) break;            // not admitted: never release', client)
        self.assertIn('xyf::catalog::decode(', client)

    def test_commands_are_requests_that_the_storage_processor_answers(self):
        # Diagnosis 075: b's op-10 command was echoed by a's stock ISR while a was still mounting,
        # and b's next request header replaced it before a's poll sampled it. Every command of the
        # staged session is now a request that waits in a's pool slot and that a's loop answers.
        send = body(SOURCE, 'bool b_send(Stock121PeerSession& session, U32 item, U32 wait_ms = xyf_boot::command_ms) {')
        self.assertIn('return b_ask(session, Stock121PeerSession::command_tag << 16 | (item & 0xffu) << 8 |', send)
        self.assertIn('wait_ms);', send)
        ask = body(SOURCE, 'bool b_ask(Stock121PeerSession& session, U32 value, U32 wait_ms) {')
        order = [ask.index(t) for t in ('if (!b_settle(session)) return false;', 'session.ask(value)',
                                        'while (session.busy() && !b_lost && now() - start < wait_ms)',
                                        'if (session.busy() || session.echoes() != before + 1) b_lost = 1;')]
        self.assertEqual(order, sorted(order))
        self.assertNotIn('session.command(', ask)
        # The only op-10 command left in the file boot is the keepalive (v3 and the older modes).
        self.assertEqual(re.findall(r'session(?:\.|->)command\(([^)]*)', SOURCE), ['keepalive_tag << 16 | (++b_sequence & 0xffffu'])
        # keepalives are plain op-10 commands again (the probe is gone).
        tick = body(SOURCE, 'bool tick() {')
        self.assertNotIn('session->ask(', tick)
        self.assertNotIn('probe_item', SOURCE + SESSION)
        self.assertNotIn('probe_ms', SOURCE)
        self.assertNotIn('settle_ms', SOURCE + XYF)                  # no "quiet so a sees the header" wait
        # b waits long enough for a's slowest mount seen (5.1 s), and a's own idle rule is not shorter.
        self.assertGreaterEqual(constant(XYF, 'command_ms'), 10000)
        link = body(SOURCE, 'bool link_value(uint32_t value) {')
        self.assertIn('b_ask(*session, value, b_patience_ms ? b_patience_ms : xyf_boot::command_ms)', link)

    def test_storage_processor_takes_commands_before_any_other_request_use(self):
        poll = body(SESSION, 'Result Stock121PeerSession::poll(U32 now) {')
        take = poll.index('if(staged_&&!agreed_&&!pending_){')
        block = poll[take:poll.index('if(handshake_only_&&!agreed_){', take)]
        # Exact pool slot, the stock select request type, no flags, tagged argument; then the stock take.
        for text in ('request_pool_slot(driver_,request)', 'word(request)==0x11907694u',
                     '!*reinterpret_cast<volatile unsigned short*>(request+0x1c)',
                     'command_request(byte(request+5),word(request+8),word(request+0x18))',
                     'word(primary_+4)=0;pending_=request;', 'const U32 value=word(request+8);last_activity_=now;',
                     '++commands_seen_;last_command_=last_argument_=value;',
                     'respond(command_ack(value));', 'return Result::waiting;'):
            self.assertIn(text, block)
        # F1: b's bootloader (b restarted alone) is held before any command or request use.
        follow = poll.index('boot_request(word(queued),byte(queued+5),word(queued+8),word(queued+0x18))')
        self.assertLess(follow, take)
        self.assertLess(poll.index('if(peer_restart_&&staged_&&!agreed_)return Result::waiting;'), take)
        # Before the RPC hook (a command must never reach the storage server) and the image protocol.
        self.assertLess(take, poll.index('if(!agreed_&&staged_&&rpc_&&serve_'))
        self.assertLess(take, poll.index('pending_=queued;'))
        # One command per poll: the caller handles it before the next poll can take another.
        owner = body(SOURCE, 'bool role0_menu_boot(U32& bytes, U32& crc) {')
        loop = owner[owner.index('while (running(status)) {'):]
        self.assertLess(loop.index('status = session.poll(now());'), loop.index('handled = session.commands_seen();'))
        # b: the answer must be a's own (never the transfer client's result handling).
        ask = body(SESSION, 'Result Stock121PeerSession::ask(U32 value) {')
        self.assertIn('!staged_||!role()||!link_ready()||active_||agreed_||declined_||transferring_', ask)
        self.assertIn('expected_echo_=command_ack(value);asking_=true;return submit(request);', ask)
        done = poll[poll.index('if(asking_){'):]
        self.assertLess(done.index('if(result!=expected_echo_)return fail(Result::protocol);'), done.index('++echoes_;'))
        self.assertLess(poll.index('if(asking_){'), poll.index('client_.accept(result'))

    def test_storage_processor_lets_its_answer_leave_before_it_resets(self):
        # poll() only queues the answer and the stock reset disables interrupts at once. Without
        # this wait b never gets the answer to a reboot command and waits command_ms.
        owner = body(SOURCE, 'bool role0_menu_boot(U32& bytes, U32& crc) {')
        resets = [m.start() for m in re.finditer(r'usb_service::reset_self\(\);', owner)]
        # a follows b (nothing to answer: not a command handler) through a_follow, which closes
        # storage first, only when b's bootloader asks (F1): a b silent past the idle window ends the
        # session without a restart. No guard, no alone rule.
        follow = body(SOURCE, 'void a_follow(cold_storage::Stock121ReadOnlySession& storage) {')
        self.assertLess(follow.index('a_close_files(storage);'), follow.index('usb_service::reset_self();'))
        self.assertIn('if (session.follow_now(now())) a_follow(storage);', owner)
        self.assertNotIn('a_alone_ms', SOURCE)
        self.assertNotIn('a_guarded', SOURCE)
        self.assertEqual(owner.count('a_follow(storage);'), 1)
        self.assertEqual(len(resets), 1)                             # the reboot item (v3)
        for at in resets:
            handler = owner[owner.rindex('if (item == ', 0, at):at]
            self.assertIn('a_answer_out(session);', handler)
            first = min(handler.index(t) for t in ('storage_server::finish(', 'storage.end()') if t in handler)
            self.assertLess(handler.index('a_answer_out(session);'), first)          # before the unmount
        wait = body(SOURCE, 'void a_answer_out(Stock121PeerSession& session) {')
        self.assertIn('while (session.answering() && now() - start < xyf_boot::answer_out_ms) {}', wait)
        self.assertIn('while (now() - quiet < xyf_boot::answer_quiet_ms) {}', wait)
        answering = body(SESSION, 'bool Stock121PeerSession::answering() const {')
        self.assertIn('if(!opened_||role())return false;', answering)
        self.assertIn('word(driver_+0x30)||word(driver_+0x1c4)||word(driver_+0x1c8)||byte(driver_+0x28)', answering)
        # b resets 100 ms after the answer: a's whole wait must stay well inside that.
        self.assertLess(constant(XYF, 'answer_out_ms') + constant(XYF, 'answer_quiet_ms'), 100)
        # v3: home start may use the link, so the session must be published first.
        client = body(SOURCE, 'bool role1_menu_boot(U32& bytes) {')
        home = client[client.index('b_session = &session;'):]
        self.assertLess(home.index('b_session = &session;'), home.index('usb_service::home_start();'))

    def test_client_items(self):
        items = [constant(XYF, n) for n in ('item_catalog', 'item_boot_default', 'item_boot_entry')]
        self.assertEqual(len(set(items)), 3)
        self.assertLessEqual(constant(XYF, 'item_boot_entry') + 16, 0x100)     # entry index fits the item byte

    def test_menu_uses_catalog_and_reports_choice(self):
        self.assertIn('xyf::menu::start(app, xy_forever::xyf_boot::catalog(), true);', KEY_UI)
        self.assertIn('xy_forever::xyf_boot::choose(leave - 2);', KEY_UI)       # library.boot (no library page)
        self.assertIn('xy_forever::xyf_boot::choose_default();', KEY_UI)
        # #3: a lost link ends the menu only without USB (lost_ends_menu); with USB the live menu stays and its
        # status line shows the lost link at once.
        self.assertEqual(KEY_UI.count('if (lost_ends_menu()) { r.exit_reason = exit_link; break; }'), 1)
        self.assertNotIn('link_lost()) { r.exit_reason = exit_link; break; }', KEY_UI)
        self.assertIn('if (lost && !shown_lost) { shown_lost = true; status = " "; dirty = 1; }', KEY_UI)
        display = (ROOT / 'target/key_ui_display.ipp').read_text()
        self.assertIn('return xy_forever::xyf_boot::link_lost() && !xy_forever::usb_service::home_usb();', display)

    def test_link_close_with_a_request_still_out(self):
        """#3: session.end() with a request still queued: link_close waits for it at most close_ms, marks the
        link lost before end() (no later request can claim the slot), and passes only when the hardware is off
        (the stock master close removes the link interrupts: nothing completes the request later)."""
        api = (ROOT / 'target/file_boot_b_api.ipp').read_text()
        body = api[api.index('bool link_close(bool wait) {'):]
        body = body[:body.index('\n}\n')]
        order = ['while (wait && session->busy() && now() - start < close_ms) {', 'b_lost = 1;',
                 'result.end_result = U32(session->end());', 'peer_services::check_quiet(s) == peer_services::Result::ok']
        at = [body.index(o) for o in order]
        self.assertEqual(at, sorted(at))
        self.assertEqual(constant(XYF, 'close_ms'), 500)

    def test_builder_mode(self):
        self.assertTrue(builder.features('xyf-product')['file_boot'])     # the menu boot is part of file boot
        self.assertIn('target/xyf_boot.hpp', builder.own_sources())


if __name__ == '__main__':
    unittest.main()
