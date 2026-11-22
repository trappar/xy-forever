// Included by runtime.cpp inside its board namespace.
std::string status_json() {
    char text[2304], clock[512];
    snprintf(
        clock, sizeof(clock),
        "\"held\": %d, \"steps\": %u, \"work\": [%u, %u], \"idle\": [%d, %d], \"flight\": [%d, %d, %d, %d], \"pids\": [%d, %d], \"pid\": %d, \"package_gc\": %u, \"a_otp_fail\": %d, \"a_otp_inits\": %d, \"a_otp_reads\": %d, \"mount_calls\": %d, \"stage_executor_hits\": %d, \"raw_boot_changes\": %d",
        shared->usb_held.load(), shared->steps.load(), shared->work[0].load(), shared->work[1].load(),
        shared->idle_work[0] == shared->work[0] && shared->idle_steps[0] == shared->steps,
        shared->idle_work[1] == shared->work[1] && shared->idle_steps[1] == shared->steps, shared->inflight[0].load(),
        shared->inflight[1].load(), shared->outflight[0].load(), shared->outflight[1].load(), procs[0].pid,
        procs[1].pid, getpid(), shared->package_gc.load(), shared->a_otp_fail.load(), shared->a_otp_inits.load(),
        shared->a_otp_reads.load(), shared->mount_calls.load(), shared->stage_executor_hits.load(),
        shared->raw_boot_changes.load());
    snprintf(
        text, sizeof(text),
        "{\"a\": \"%s\", \"b\": \"%s\", \"a_boots\": %d, \"b_boots\": %d, \"power_ons\": %d, \"listening\": %d, "
        "\"storage_ops\": %d, \"cuts\": %d, \"a_silent\": %d, \"a_slow_ms\": %d, \"drop_request\": %d, "
        "\"drop_body\": %d, \"cable_pull\": %d, \"cut_after\": %d, \"fail_write\": %d, \"space\": %lld, "
        "\"partition\": %d, \"boot_writes\": %d, \"rw_open\": %d, \"unclean_mounts\": %d, "
        "\"to_a\": %u, \"to_b\": %u, \"otp_fields\": %d, \"otp_fail\": %d, \"otp_inits\": %d, \"otp_reads\": %d, "
        "\"a_bootloader\": %d, \"b_bootloader\": %d, \"clock_ms\": %lld, "
        "\"b_sent_ms\": %lld, \"a_reset_ms\": %lld, \"b_reset_ms\": %lld, \"outstanding\": %zu, \"a_delayed\": %zu, "
        "\"odd_selects\": %d, \"b_queue\": %d, \"b_queued\": %d, \"cut_body\": %d, "
        "\"boot_a_window_ms\": %d, \"b_waits_a\": %d, \"b_close_stuck\": %d, \"rw_mounts\": %d, "
        "\"stage2_opens\": %d, \"stage2_calls\": %d, \"a_silenced\": %d, \"a_clock_reads\": %u, \"b_clock_reads\": %u, \"needs_repair\": %d, %s}",
        procs[0].state.c_str(), procs[1].state.c_str(), procs[0].boots, procs[1].boots, power_ons,
        shared->b_listening.load(), shared->storage_ops.load(), shared->cuts.load(), shared->a_silent.load(),
        shared->a_slow_ms.load(), shared->drop_request.load(), shared->drop_body.load(), shared->cable_pull.load(),
        shared->cut_after.load(), shared->fail_write.load(), (long long)shared->space.load(), shared->partition.load(),
        shared->boot_writes.load(), shared->rw_open.load(), shared->unclean_mounts.load(), relayed[0], relayed[1],
        otp_fields(), shared->otp_fail.load(), shared->otp_inits.load(), shared->otp_reads.load(),
        shared->in_bootloader[0].load(), shared->in_bootloader[1].load(), (long long)board_ms(), (long long)b_sent_ms,
        (long long)reset_ms[0], (long long)reset_ms[1], outstanding.size(), to_a.size(), shared->odd_selects.load(),
        shared->b_queue.load(), shared->b_queued.load(), shared->cut_body.load(), shared->boot_a_window_ms.load(),
        shared->b_waits_a.load(), shared->b_close_stuck.load(), shared->rw_mounts.load(), shared->stage2_opens.load(),
        shared->stage2_calls.load(), shared->a_silenced.load(), shared->clock_reads[0].load(),
        shared->clock_reads[1].load(), shared->needs_repair.load(), clock);
    return text;
}
