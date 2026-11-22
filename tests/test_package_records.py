"""Native metadata precedence through host admission, rebuild and package indexing."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_modkit import CATALOG_BYTES, finish, synthetic_kit

from xy_forever import config_records, moddev, modindex, modkit, modpatch, modsend, native_json, package_store
from xy_forever import package_records as R
from xy_forever import usb_proto as P

ROOT = Path(__file__).resolve().parents[1]


class PackageRecords(unittest.TestCase):
    def test_saved_config_precedence_and_errors(self):
        native = native_json.config_document(dict(base='stock', mods='alpha,missing', safe='1'))
        legacy = b'base=other\nmods=beta\nsafe=1\n'
        for files, want in (
            ({'config.json': native}, ['alpha', 'missing']),
            ({'config.json': native, 'config.json.old': b'{', 'config.txt': legacy}, ['alpha', 'missing']),
            ({'config.json.part': b'{', 'config.json.old': native, 'config.txt': legacy}, ['alpha', 'missing']),
            ({'config.json.part': b'{', 'config.old': legacy}, ['beta']),
            ({'config.json.part': b'{'}, None),
            ({'config.json': b'{', 'config.txt': legacy}, modsend.Refused),
            ({'config.json.old': None, 'config.txt': legacy}, modsend.Refused),
            ({'config.txt': b'format=2\n', 'config.old': legacy}, modsend.Refused),
        ):
            with self.subTest(names=list(files)):
                reads = []

                def read(path, **_):
                    name = path.rsplit('/', 1)[1]
                    reads.append(name)
                    data = files[name]
                    return (P.ids.S_NOT_FOUND, b'') if data is None else (0, data)

                dev = SimpleNamespace(
                    list_dir=lambda _: (
                        0,
                        [(2 if data is None else 0, 0, name.encode()) for name, data in files.items()],
                    ),
                    read_all=read,
                )
                if want is modsend.Refused:
                    with self.assertRaisesRegex(modsend.Refused, r'config\.(json|txt)'):
                        moddev.config(dev)
                elif want is None:
                    self.assertIsNone(moddev.config(dev))
                else:
                    config = moddev.config(dev)
                    self.assertEqual((config['mods'], config['safe']), (want, '1'))
                name = next((name for name in config_records.NAMES if name in files), None)
                self.assertEqual(reads, [name] if name else [])
                if name and want is not modsend.Refused:
                    self.assertEqual(config_records.read(dev)[:2], (name, files[name]))

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
        native = native_json.package_document(
            dict(type='firmware', name='New', bytes=1, crc32='00000000', sha256='a' * 64)
        )
        for response, expected in (
            ((P.ids.S_NOT_FOUND, b''), 'b' * 64),
            ((0, native), 'a' * 64),
            ((0, b'{'), None),
            ((P.ids.S_STORAGE, b''), None),
        ):
            with self.subTest(response=response):
                calls = []

                def read(path, **_):
                    calls.append(path)
                    return response if path.endswith('manifest.json') else (0, ('sha256=%s\n' % ('b' * 64)).encode())

                device = SimpleNamespace(read_all=read, list_dir=lambda _: (0, [(0, 64, b'info.txt')]))
                if expected is None:
                    with self.assertRaises(modsend.Refused):
                        moddev.entry_stamp(device, 'entry')
                else:
                    self.assertEqual(moddev.entry_stamp(device, 'entry'), expected)
                self.assertEqual(len(calls), 2 if response[0] == P.ids.S_NOT_FOUND else 1)
        link = SimpleNamespace(
            read_all=lambda *_args, **_kw: (P.ids.S_NOT_FOUND, b''),
            list_dir=lambda _: (0, [(2, 0, b'manifest.json'), (0, 64, b'info.txt')]),
        )
        with self.assertRaisesRegex(modsend.Refused, 'manifest.json'):
            moddev.entry_stamp(link, 'entry')

    def test_modkit_modsend_index_and_dev_read_the_same_native_set(self):
        with tempfile.TemporaryDirectory(prefix='xyf-native-packages-') as scratch:
            work = Path(scratch)
            kit = work / 'kit.json'
            kit.write_text(json.dumps(synthetic_kit()))
            root = Path(modkit.split_kit(kit, work / 'packages', finish=finish)['root'])
            expected = modkit.rebuild_kit(root, ['alpha', 'beta'], ['cap', 'flt'], CATALOG_BYTES)
            names = [p.name for p in root.iterdir() if p.is_dir()]
            for name in names:
                self.assertEqual(R.local_name(root / name), R.NATIVE)
                self.assertFalse((root / name / R.LEGACY).exists())
            profile, generation, packages = modsend.local_set(root)
            self.assertEqual(set(packages), set(names))
            self.assertTrue(
                all(files[-2][0] == 'manifest.json' and files[-1][0] == 'files.sha256' for files in packages.values())
            )
            self.assertEqual(modkit.rebuild_kit(root, ['alpha', 'beta'], ['cap', 'flt'], CATALOG_BYTES), expected)
            self.assertEqual(modpatch.info_values(root / 'base' / R.NATIVE)['profile'], profile)
            self.assertEqual(moddev.resolve(root / 'alpha'), (root, ['alpha']))
            index_dir = work / 'index'
            modindex.build(root, index_dir)
            fetched = work / 'fetched'
            modindex.fetch(index_dir, fetched, out=lambda *_: None)
            self.assertEqual(modsend.local_set(fetched)[:2], (profile, generation))
            self.assertEqual(
                {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()},
                {p.relative_to(fetched).as_posix(): p.read_bytes() for p in fetched.rglob('*') if p.is_file()},
            )
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
