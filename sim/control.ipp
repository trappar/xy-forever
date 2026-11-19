// Included by runtime.cpp beside the board state it controls.
std::string command(const std::string& line, bool& quit, int client) {
    char verb[32] = {0}, name[32] = {0};
    long long value = 0;
    const int n = sscanf(line.c_str(), "%31s %31s %lld", verb, name, &value);
    if (n >= 1 && !strcmp(verb, "status")) return status_json();
    if (n >= 1 && !strcmp(verb, "quit")) { quit = true; return "{\"ok\": true}"; }
    if (n >= 1 && !strcmp(verb, "power")) {
        power_cycle(n < 2 || strcmp(name, "plain"), "control");
        return "{\"ok\": true}";
    }
    if (n == 2 && (!strcmp(verb, "advance") || !strcmp(verb, "jump"))) {   // advance|jump MS: sim/clock.cpp
        const long long ms = strtoll(name, 0, 10);
        if (ms <= 0) return "{\"ok\": false, \"error\": \"advance: MS > 0\"}";
        if (verb[0] == 'j') { board_jump(ms); return "{\"ok\": true}"; }
        board_advance(client, ms);
        return "";                                        // answered when the clock is there
    }
    if (n >= 1 && !strcmp(verb, "cut")) {                 // power cut now, then power on (PLAY)
        ++shared->cuts;
        power_cycle(true, "power cut");
        return "{\"ok\": true}";
    }
    if (n >= 1 && !strcmp(verb, "inject")) {              // inject CLASS OP ARG0 ARG2 [BODY-HEX] (hex; class 0: generic)
        unsigned cls = 0, op = 0, a0 = 0, a2 = 0;
        char hex[1100] = "";
        const int k = sscanf(line.c_str(), "inject %x %x %x %x %1099s", &cls, &op, &a0, &a2, hex);
        if (k < 4 || procs[0].fd < 0 || procs[0].state != "running")
            return "{\"ok\": false, \"error\": \"inject: CLASS OP ARG0 ARG2 [BODY-HEX], A running\"}";
        std::vector<uint8_t> body;
        for (size_t i = 0; k == 5 && hex[i] && hex[i + 1]; i += 2) {
            unsigned byte = 0;
            if (sscanf(hex + i, "%2x", &byte) != 1) return "{\"ok\": false, \"error\": \"inject: bad hex\"}";
            body.push_back(uint8_t(byte));
        }
        // The existing ARG2 test input is the write count: stock serializes it in RX+8,
        // reconstructed at request+0x18 by 889f8bca -> 889f87d4, never queue linkage +0xc.
        Message m = {message_magic, k_request, inject_bit | ++inject_count, cls, op, a0,
                     cls == cls_write ? a2 : 0, cls == cls_write ? 0 : a2, 0, 0,
                     uint32_t(body.size())};
        injected[m.tag] = {false, 0, 0, 0};
        log("inject: class %08x op %02x arg %08x (%zu body bytes)", cls, op, a0, body.size());
        forward(0, m, body);
        char text[64];
        snprintf(text, sizeof(text), "{\"ok\": true, \"tag\": %u}", m.tag & ~inject_bit);
        return text;
    }
    if (n == 2 && !strcmp(verb, "reset") && (!strcmp(name, "a") || !strcmp(name, "b"))) {
        const int k = name[0] == 'a' ? 0 : 1;
        if (procs[k].state != "running") return "{\"ok\": false, \"error\": \"processor not running\"}";
        log("control: reset %s alone", names[k]);         // reset a|b: no handler, no USB stop
        stop(k);
        resetting(k);
        return "{\"ok\": true}";
    }
    if (n >= 2 && !strcmp(verb, "swap")) {               // swap PATH: test hook (runtime.hpp)
        snprintf(shared->swap_path, sizeof(shared->swap_path), "%s", line.c_str() + 5);
        shared->swap_armed = 1;
        return "{\"ok\": true}";
    }
    if (n >= 2 && !strncmp(verb, "knob", 4)) return input_command(line, verb);    // sim/devices.cpp
    if (n >= 2 && !strcmp(verb, "realkey")) return input_command(line, verb);
    if (n == 2 && !strcmp(verb, "injected")) {            // injected TAG: A's answer, if any
        auto it = injected.find(inject_bit | uint32_t(strtoul(name, 0, 10)));
        if (it == injected.end()) return "{\"ok\": false, \"error\": \"no such injected request\"}";
        char text[160];
        snprintf(text, sizeof(text), "{\"ok\": true, \"done\": %d, \"result\": %d, \"error\": %u, \"body\": %u}",
                 it->second.done ? 1 : 0, int32_t(it->second.result), it->second.error, it->second.body);
        return text;
    }
    if (n == 3 && !strcmp(verb, "set")) {
        struct { const char* name; std::atomic<int32_t>* field; } fields[] = {
            {"a_silent", &shared->a_silent}, {"a_slow_ms", &shared->a_slow_ms}, {"drop_request", &shared->drop_request},
            {"drop_body", &shared->drop_body}, {"cable_pull", &shared->cable_pull}, {"cut_after", &shared->cut_after},
            {"fail_write", &shared->fail_write}, {"vanish_marker", &shared->vanish_marker},
            {"tfw_rpc_version", &shared->tfw_rpc_version}, {"a_otp_fail", &shared->a_otp_fail}, {"a_otp_preinit", &shared->a_otp_preinit},
            {"fail_mount", &shared->fail_mount}, {"otp_fail", &shared->otp_fail}, {"otp_preinit", &shared->otp_preinit},
            {"fail_select", &shared->fail_select}, {"statfs_override", &shared->statfs_override},
            {"statfs_block_bytes", &shared->statfs_block_bytes}, {"statfs_free_blocks", &shared->statfs_free_blocks}, {"statfs_fail", &shared->statfs_fail},
            {"fail_unmount", &shared->fail_unmount}, {"fail_open", &shared->fail_open}, {"full_path", &shared->full_path},
            {"a_boot_ms", &shared->a_boot_ms}, {"b_boot_ms", &shared->b_boot_ms}, {"a_redeliver", &shared->a_redeliver},
            {"a_stall_now", &shared->a_stall_now}, {"b_queue", &shared->b_queue}, {"cut_body", &shared->cut_body},
            {"boot_a_window_ms", &shared->boot_a_window_ms}, {"b_close_stuck", &shared->b_close_stuck},
            {"defer_save_boot", &shared->defer_save_boot}, {"a_stage_corrupt", &shared->a_stage_corrupt}, {"unmount_ms", &shared->unmount_ms}, {"mount_ms", &shared->mount_ms}, {"rw_mount_ms", &shared->rw_mount_ms}, {"needs_repair", &shared->needs_repair}, {"rename_fault", &shared->rename_fault},
            {"rename_fault_kind", &shared->rename_fault_kind}, {"knob_per_pass", &shared->knob_per_pass},
            {"knob_wrap", &shared->knob_wrap}, {"knob_identity_fail", &shared->knob_identity_fail}};
        for (auto& f : fields) if (!strcmp(f.name, name)) { *f.field = int32_t(value); return "{\"ok\": true}"; }
        if (!strcmp(name, "space")) { shared->space = value; return "{\"ok\": true}"; }
    }
    return "{\"ok\": false, \"error\": \"unknown command\"}";
}
