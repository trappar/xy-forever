"""Freestanding module groups shared by the product builder. No stock-call policy."""
TFW_CHECK_MODULES = ('tfw_check', 'tfw', 'tfw_signature', 'p224', 'aes')
JSON_MODULES = ('json',)
TFW_TARGET_MODULES = ('otp_keys', 'tfw_device_check', 'tfw_import_source')
MOD_JOB_MODULES = ('mod_job', 'mod_link', 'mod_elf', 'mod_numbers', 'mod_patch', 'mod_content')
MODS_PAGE_MODULES = ('xyf_mods', 'xyf_mods_view', 'xyf_mods_io')
USB_STORAGE_MODULES = ('usb_proto', 'xyf_store', 'xyf_state', 'xyf_state_write', 'xyf_filelist', 'xyf_manifest', 'xyf_recognise')
GC_MODULES = ('inflate', 'xyf_stage2', 'xyf_store', 'xyf_info', 'xyf_state', 'xyf_state_write',
              'xyf_library', 'xyf_cold_library', 'xyf_catalog', 'xyf_crc32', 'ui_pages', 'ui_gfx', 'ui_screens',
              'xyf_menu', 'xyf_menu_view', 'xyf_manifest', 'xyf_package_record', 'xyf_recognise', 'usb_proto', 'xyf_filelist') + JSON_MODULES + TFW_TARGET_MODULES + TFW_CHECK_MODULES + MOD_JOB_MODULES + \
    MODS_PAGE_MODULES


def compile_tfw(cpp, source):
    for name in TFW_CHECK_MODULES:
        cpp(name, source / ('native/' + name + '.cpp'))
    for name in TFW_TARGET_MODULES:
        cpp(name, source / ('target/' + name + '.cpp'))


def own_sources(source, suffixes):
    """Build fingerprints include pinned vendor code and its provenance/license."""
    files = [p for d in ('target', 'native', 'xy_forever') for p in (source / d).iterdir()
             if p.is_file() and p.suffix in suffixes]
    files += [source / 'xyf_build.py', source / 'toolchains.json']
    files += [p for p in (source / 'third_party').rglob('*') if p.is_file()]
    return sorted(str(p.relative_to(source)) for p in files)
