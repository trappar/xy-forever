// Part of usb_service.cpp, included there at this point: not compiled alone.
#if defined(XYF_CRASH_TRIGGER) && defined(XYF_USB_FILE_WRITE)
// diag.crash (0x63; dev, test build xyf-crash-trigger only, never the product; not in the published spec, so in
// no build's cmds) (#102): one chosen fault on B while the menu has the screen, to see the crash screen
// (key_ui_crash.ipp) on real faults. Request: u8 kind: 1 EXCPT, 2 misaligned reads, 3 undefined instruction,
// 4 read of unmapped memory (scripts/crash_trigger.py). Any other payload, none included: unsupported.
// Refused busy while A's read-write window may be open: a write's window B has not closed yet (writes_due), its
// close still out (closing), the last close lost on the link (mounted stale), a job, MTP traffic or an upload
// under way. Refused state when the vector is not armed. Else the ok reply goes out (50 ms, as REBOOT) and the
// fault happens here, in the menu loop: no storage, no reset; B halts on the crash screen until a power cycle.
const uint8_t t_crash = 0x63;
void crash_command(uint8_t seq, const uint8_t* payload, U32 length) {
    const U32 kind = length == 1 ? payload[0] : 0;
    U32 code = P::s_ok;
    if (kind < 1 || kind > 4) code = P::s_unsupported;
    else if (writes_due || closing || mounted == file_client::mounted_stale || job_running() XYF_OR_MTP_BUSY ||
             (put_expected && result.put_received != put_expected)) code = P::s_busy;
    else if (!key_ui::crash_armed_now()) code = P::s_state;
    status_only(t_crash, seq, code);
    if (code != P::s_ok) return;
    const U32 t = now();
    while (now() - t < 50u) {}
    key_ui::crash_fault(kind);
}
#endif
