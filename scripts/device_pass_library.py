"""Library metadata read and legacy scratch-entry check for the device pass."""
from xy_forever import package_records
from xy_forever.xyf_entry import manifest_text


def change(self, scratch_info, scratch_id, fail):
    before = self.state['window1']
    label = 'Pass %s' % (self.folder.name[-24:] if self.folder else 'plan')
    local = self.artifact('scratch-info.txt')
    local.write_text(manifest_text(dict(name=label, version='0')))
    self.ok(['put-file', local, scratch_info, '--yes-write'])
    usb, server = self.settled(before, 'library-change')
    if server['b_window'] != 3:
        raise fail('op 9 answered %s (3 = it closed the open window)' % server['b_window'])
    if usb.get('crash_restarts') != self.state['crashes1']:
        raise fail('crash restarts %s -> %s: the device restarted' % (self.state['crashes1'],
                                                                     usb.get('crash_restarts')))
    entry = [i for i in self.menu_catalog('catalog.bin')['items'] if i['id'] == scratch_id]
    if not entry or entry[0]['name'] != label:
        raise fail('menu catalog: %s is %s, expected the name %r' % (
            scratch_id, entry[0]['name'] if entry else 'missing', label))
    return 'menu shows %s = %r without a restart (removed again by scratch-cleanup)' % (scratch_id, label)


def read_metadata(self, folder, artifact, extra, names, parse_info, fail):
    candidates = (package_records.NATIVE, package_records.LEGACY) if names is None else (
        package_records.record_name(names),)
    for name in candidates:
        if name is None:
            break
        path = folder + '/' + name
        data = self.read(path, artifact + '-' + name, *extra)
        if data is None:
            if names is not None:
                raise fail('%s not readable' % path)
            continue
        try:
            return parse_info(package_records.text_view(data, name))
        except ValueError as error:
            raise fail('%s: %s' % (path, error)) from error
    return {}
