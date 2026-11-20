#include "xyf_clock.hpp"
#include "xyf_state_write.hpp"
#include "usb_service.hpp"
#include "usb_spans.hpp"
#if defined(XYF_MENU_ON_FAILURE)
#include "file_boot.hpp"
#endif
#if defined(XYF_SERVICE_SCREEN)
#include "key_ui.hpp"
#endif
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
#include "xyf_boot.hpp"
#include "xyf_menu.hpp"                     // xyf::menu::storage_ready (the start sweep, #3)
#endif
#include "xyf_crc32.hpp"
#include "knob_input.hpp"      // the knob reader's counters (diag.status block KNB1)
#if defined(XYF_MTP_SESSION)
#include "mtp_session.hpp"          // the MTP session (product build and simulator)
#endif
#if defined(XYF_MTP_USB)
#include "mtp_usb.hpp"              // the composite MTP USB driver (product build and xyf-mtp-usb)
#include "mtp_usb_spans.hpp"
#include "mtp_spans.hpp"
#include "mtp_usb_gate.hpp"         // which check refuses a probe start, and why
#endif
#if defined(XYF_MTP_SESSION) || defined(XYF_MTP_USB)
#include "unlisted_op.hpp"          // what 0x61 and 0x62 answer to a request none of their ops takes
#endif
// XY Forever USB service (see usb_service.hpp). Stock addresses: the
// authenticated 1.2.1 bootloader, mapped offline (docs/hardware/usb.md, Boot partition and USB).
namespace xy_forever { namespace usb_service {
#if defined(__bfin__)
// Initialized data, not BSS: the cold link has no zero-initialization.
Result result __attribute__((section(".data"))) = {magic};
#endif

uint32_t identity() {
    uint32_t bits = 0;
    for (uint32_t i = 0; i != xyf::usb_spans::count; ++i) {
        const uint32_t* s = xyf::usb_spans::table[i];
        const volatile uint8_t* p = reinterpret_cast<const volatile uint8_t*>(s[0]);
        if (xyf::crc::update_volatile(0, p, s[1]) != s[2]) bits |= 1u << (i & 31u);
    }
    return bits;
}

bool has_descriptor(const uint8_t* data, uint32_t size) {
    for (uint32_t i = 0; i + 8 <= size; i += 4)
        if (xyf::usbp::get32(data + i) == descriptor_magic && xyf::usbp::get32(data + i + 4) == 1) return true;
    return false;
}

bool accept_image(bool uninstall, bool descriptor, uint32_t size, const uint8_t* sha256) {
    if (!uninstall) return descriptor;
    if (size != stock_os_bytes || !sha256) return false;
    uint32_t d = 0;
    for (uint32_t i = 0; i != 32; ++i) d |= uint32_t(sha256[i] ^ stock_os_sha256[i]);
    return !d;
}

bool header_matches(const uint8_t* sector, uint32_t size, uint32_t crc) {
    return xyf::usbp::get32(sector) >= 2 && xyf::usbp::get32(sector + 4) == size &&
           xyf::usbp::get32(sector + 8) == crc && sector[12] != 0 && sector[13] <= 1;
}
} }

#if defined(__bfin__)
#include "menu_link.hpp"
#include "storage_server.hpp"
#include "file_client.hpp"
#include "boot_partition_probe.hpp"
#include "../native/hash.hpp"
#include "../native/ldr.hpp"
#include "../native/mod_job.hpp"
#include "xyf_mods_store.hpp"        // mods.apply: the store's version (#124)
#include "cold_layout.hpp"
#include "stack_mark.hpp"         // live stack high-water mark in STATUS
#include "xyf_store.hpp"
#include "xyf_stage2.hpp"
#include "xyf_blob.hpp"
#include "xyf_blob_gc.hpp"
#include "xyf_save_log.hpp"           // #27: the save-time log
#if defined(XYF_CONTENT_SWITCH)
#include "xyf_job_journal.hpp"
#include "xyf_content_job.hpp"
#include "xyf_config.hpp"           // the saved configuration (the config job)
#include "xyf_settings.hpp"         // #21: settings.txt (through the config job), the confirm gate
#endif
#if defined(XYF_TFW_CHECK)
#include "a_install_stage.hpp"
#include "tfw_device_check.hpp"
#include "tfw_role_probe.hpp"
#include "../native/tfw_check.hpp"
#endif
extern "C" { extern const xyf_core::Policy* volatile xy_forever_cold_policy; }   // cold.cpp
namespace {
bool boot_probe_active();
bool rehearsal_active();
#include "usb_service_transport.ipp"        // stock wrappers, transport, PUT state, install log, HELLO, STATUS
#include "usb_service_stage2.ipp"           // the stage 2 loader
#include "usb_service_identity.ipp"         // hello version= and image=, the menu's labels
#include "usb_service_files.ipp"            // READ_FILE, SCREEN, PEEK, the MTP self-test, PUT, SET_DEFAULT, LIBRARY_BOOT
#if defined(XYF_USB_FILE_WRITE)
#include "usb_service_file_write.ipp"       // FILE_DELETE/RMDIR/MKDIR/RENAME/COPY/LIST/WRITE
#include "usb_service_stream.ipp"           // FILE_WRITE_BEGIN/DATA/END (#4)
#include "usb_service_storage_io.ipp"
#include "usb_service_mod_job.ipp"          // the mod assembly job on B
#include "usb_service_save_log.ipp"         // the save-time log
#include "usb_service_content.ipp"          // the content switch and config jobs
#include "usb_service_recovery.ipp"         // durable content intent, menu/service startup only
#include "usb_service_mtp_session.ipp"      // the MTP session (with XYF_SIM it leaves this namespace for one declaration)
#include "usb_service_mods_apply.ipp"       // MODS_APPLY
}
namespace xy_forever { namespace usb_service {      // the Mods page's hooks (usb_service.hpp)
#include "usb_service_mods_page.ipp"        // the Mods page's hooks
#include "usb_service_settings.ipp"         // settings.txt, the Settings page's rows (#21)
} }
namespace {
#include "usb_service_reports.ipp"
#include "usb_service_package_session.ipp"
#include "usb_service_job_status.ipp"       // JOB_STATUS, JOB_CANCEL
#include "usb_service_boot_probe.ipp"       // #7: read-only boot-partition diagnostic
#else
bool boot_probe_active() { return false; }
#endif

#include "usb_service_a_stage.ipp"         // product RAM-only A admission rehearsal
#include "usb_service_install.ipp"          // TFW_CHECK, INSTALL
}

namespace xy_forever { namespace usb_service {
#include "usb_service_entry.ipp"            // the public entry points
#include "usb_service_composite.ipp"        // serial + MTP as one composite device
#include "usb_service_crash_trigger.ipp"    // diag.crash (test build xyf-crash-trigger only, #102)
#include "usb_service_trust.ipp"            // #65: Ask before USB control
#include "usb_service_home.ipp"             // the home loop
} }
#endif
