"""Native metadata precedence through host admission, rebuild and package indexing."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from xy_forever import moddev, modindex, modkit, modpatch, modsend, native_json, package_records as R, package_store
from xy_forever import usb_proto as P
from test_modkit import synthetic_kit, finish, CATALOG_BYTES

ROOT = Path(__file__).resolve().parents[1]


class PackageRecords(unittest.TestCase):
    def test_shared_selection_and_semantic_admission(self):
        for case in json.loads((ROOT / 'tests/package-records.json').read_text()):
            with self.subTest(case=case['name']):
                files = {name: text.encode() for name, text in case['files'].items()}
                name = R.record_name(iter(files))
                self.assertEqual(name, case['record'])
                before = dict(files)
                if case['fields'] is None:
                    with self.assertRaises(ValueError):
                        R.parse(files[name], name)
                else:
                    self.assertEqual(R.parse(files[name], name), case['fields'])
                self.assertEqual(files, before)
        self.assertIsNone(R.record_name(iter(())))

    def test_integrity_precedes_native_semantics_and_covers_selected_file(self):
        # A checked legacy record never authorizes an unchecked native override.
        legacy = b'type=mod\nname=Old\nmachine=synth\ngeneration=v1\n'
        files = {'info.txt': legacy}
        files['files.sha256'] = package_store.list_text(files)
        files['manifest.json'] = b'{'
        with self.assertRaisesRegex(package_store.StoreError, 'exactly'):
            package_store.validate(files)
        files['files.sha256'] = package_store.list_text({p: b for p, b in files.items() if p != 'files.sha256'})
        files['manifest.json'] = b'wrong hash and invalid JSON'
        with self.assertRaisesRegex(package_store.StoreError, 'SHA-256'):
            package_store.validate(files)
        files['files.sha256'] = package_store.list_text({p: b for p, b in files.items() if p != 'files.sha256'})
        with self.assertRaises(native_json.RecordError):
            package_store.validate(files)

    def test_moddev_entry_stamp_falls_back_only_on_absence(self):
        native = native_json.package_document(dict(type='firmware', name='New', sha256='a' * 64))
        for response, expected in (((P.ids.S_NOT_FOUND, b''), 'b' * 64), ((0, native), 'a' * 64),
                                   ((0, b'{'), None), ((P.ids.S_STORAGE, b''), None)):
            with self.subTest(response=response):
                calls = []
                def read(path, **_):
                    calls.append(path)
                    return response if path.endswith('manifest.json') else (0, ('sha256=%s\n' % ('b' * 64)).encode())
                device = SimpleNamespace(read_all=read)
                if expected is None:
                    with self.assertRaises(modsend.Refused):
                        moddev.entry_stamp(device, 'entry')
                else:
                    self.assertEqual(moddev.entry_stamp(device, 'entry'), expected)
                self.assertEqual(len(calls), 2 if response[0] == P.ids.S_NOT_FOUND else 1)

    def test_modkit_modsend_index_and_dev_read_the_same_native_set(self):
        with tempfile.TemporaryDirectory(prefix='xyf-native-packages-') as scratch:
            work = Path(scratch)
            kit = work / 'kit.json'
            kit.write_text(json.dumps(synthetic_kit()))
            root = Path(modkit.split_kit(kit, work / 'packages', finish=finish)['root'])
            expected = modkit.rebuild_kit(root, ['alpha', 'beta'], ['cap', 'flt'], CATALOG_BYTES)
            names = [p.name for p in root.iterdir() if p.is_dir()]
            for name in names:
                folder = root / name
                fields = R.read_values(folder)
                (folder / 'manifest.json').write_bytes(native_json.package_document(fields))
                (folder / 'info.txt').unlink()
                (folder / 'files.sha256').write_text(modkit.files_sha256_text(folder))
            profile, generation, packages = modsend.local_set(root)
            self.assertEqual(set(packages), set(names))
            self.assertTrue(all(files[-2][0] == 'manifest.json' and files[-1][0] == 'files.sha256'
                                for files in packages.values()))
            self.assertEqual(modkit.rebuild_kit(root, ['alpha', 'beta'], ['cap', 'flt'], CATALOG_BYTES), expected)
            self.assertEqual(modpatch.info_values(root / 'base/info.txt')['profile'], profile)
            self.assertEqual(moddev.resolve(root / 'alpha'), (root, ['alpha']))
            index_dir = work / 'index'
            modindex.build(root, index_dir)
            fetched = work / 'fetched'
            modindex.fetch(index_dir, fetched, out=lambda *_: None)
            self.assertEqual(modsend.local_set(fetched)[:2], (profile, generation))
            self.assertEqual({p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()},
                             {p.relative_to(fetched).as_posix(): p.read_bytes() for p in fetched.rglob('*') if p.is_file()})
            # A present newer schema refuses through user-facing command boundaries.
            base_record = root / 'base/manifest.json'
            good_base = base_record.read_bytes()
            base_record.write_bytes(good_base.replace(b'"schema_version":1', b'"schema_version":2'))
            with self.assertRaisesRegex(modsend.Refused, 'manifest.json: unsupported JSON schema version'):
                modsend.local_set(root)
            base_record.write_bytes(good_base)
            alpha_record = root / 'alpha/manifest.json'
            good_alpha = alpha_record.read_bytes()
            alpha_record.write_bytes(b'{')
            with self.assertRaisesRegex(modsend.Refused, 'manifest.json'):
                moddev.resolve(root / 'alpha')
            alpha_record.write_bytes(good_alpha)
            # Both metadata files exist but only legacy is listed: do not publish.
            folder = root / 'alpha'
            (folder / 'info.txt').write_bytes(b'type=mod\nname=Old\nmachine=synth\ngeneration=v1\n')
            old_json = (folder / 'manifest.json').read_bytes()
            (folder / 'manifest.json').unlink()
            (folder / 'files.sha256').write_text(modkit.files_sha256_text(folder))
            (folder / 'manifest.json').write_bytes(old_json)
            with self.assertRaisesRegex(modsend.Refused, 'does not list manifest.json'):
                modsend.local_set(root)
            with self.assertRaisesRegex(modkit.KitError, 'files differ'):
                modkit.read_package(folder, 'mod')


if __name__ == '__main__':
    unittest.main()
