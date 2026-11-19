"""Freestanding module groups shared by the product builder. No stock-call policy."""
TFW_CHECK_MODULES = ('tfw_check', 'tfw', 'tfw_signature', 'p224', 'aes')
MOD_JOB_MODULES = ('mod_job', 'mod_link', 'mod_elf', 'mod_numbers', 'mod_patch', 'mod_content')
MODS_PAGE_MODULES = ('xyf_mods', 'xyf_mods_view', 'xyf_mods_io')
USB_STORAGE_MODULES = ('usb_proto', 'xyf_store', 'xyf_state', 'xyf_state_write', 'xyf_filelist', 'xyf_manifest', 'xyf_recognise')
GC_MODULES = ('inflate', 'xyf_stage2', 'xyf_store', 'xyf_info', 'xyf_state', 'xyf_state_write',
              'xyf_library', 'xyf_cold_library', 'xyf_catalog', 'xyf_crc32', 'ui_pages', 'ui_gfx', 'ui_screens',
              'xyf_menu', 'xyf_menu_view', 'xyf_manifest', 'xyf_recognise', 'usb_proto', 'otp_keys', 'tfw_device_check', 'xyf_filelist') + TFW_CHECK_MODULES + MOD_JOB_MODULES + \
    MODS_PAGE_MODULES


def compile_tfw(cpp, source):
    for name in TFW_CHECK_MODULES:
        cpp(name, source / ('native/' + name + '.cpp'))
    for name in ('otp_keys', 'tfw_device_check'):
        cpp(name, source / ('target/' + name + '.cpp'))
