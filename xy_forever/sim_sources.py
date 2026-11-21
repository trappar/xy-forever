"""Source groups built by the device simulator."""

TARGET_SOURCES = ('usb_service', 'usb_proto', 'storage_server', 'file_engine', 'file_client', 'menu_link', 'file_boot',
                  'cold_storage', 'retained_lookup', 'cold_heap', 'peer_services', 'xyf_library', 'xyf_cold_library',
                  'xyf_manifest', 'xyf_package_record', 'xyf_recognise', 'xyf_filelist', 'xyf_catalog', 'xyf_state', 'xyf_state_write', 'xyf_store', 'xyf_info', 'xyf_crc32',
                  'boot_services', 'otp_keys', 'tfw_device_check', 'tfw_import_source', 'xyf_stage2', 'stage2_main',
                  'xyf_menu', 'xyf_mods', 'xyf_mods_io',                 # the menu's state and the Mods page
                  'ui_pages', 'ui_gfx',                                   # the bar's scale() (nothing is drawn)
                  'knob_input',                                           # the knob reader
                  'mtp_session')                                          # the MTP session (XYF_MTP_SESSION)
NATIVE_SOURCES = ('ldr', 'hash', 'json', 'trace_slot', 'trace_slot_zero', 'memory', 'peer_boot', 'inflate',
                  'tfw_check', 'tfw', 'tfw_signature', 'p224', 'aes',
                  'mod_job', 'mod_link', 'mod_elf', 'mod_numbers', 'mod_patch', 'mod_content',   # the job on B
                  'mtp_responder')                                                                  # the MTP responder
SIM_SOURCES = ('clock', 'runtime', 'link', 'stock', 'devices', 'boot', 'stage2')
# Files whose device-only (__bfin__) part the simulator hosts. boot_services.cpp is not one:
# its device part reads the OTP key and stays out.
DEVICE_PARTS = ('usb_service.cpp', 'usb_service.hpp', 'storage_server.cpp', 'storage_server.hpp', 'file_client.cpp',
                'file_client.hpp', 'peer_services.cpp',
                'cold_storage.cpp', 'retained_lookup.cpp', 'cold_heap.cpp', 'xyf_clock.hpp', 'otp_keys.cpp', 'tfw_device_check.cpp',
                'tfw_import_source.cpp',
                'xyf_mods_io.cpp', 'xyf_mods_io.hpp', 'mtp_session.cpp')
