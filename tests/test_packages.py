import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from xy_forever.packages import Limits, PackageError, inspect_package, stage_package

OS = 'firmware/blackfin/bin/os.ldr'
BACKPLANE = 'firmware/backplane/bin/backplane.cyacd'
BLE = 'firmware/ble/bin/ble.bin'


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, rows, name='package.zip'):
        path = self.root / name
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for key, value in rows:
                    archive.writestr(key, value)
        return path

    def test_official_layout_excludes_all_bootloader_components(self):
        path = self.archive(
            [
                (OS, b'synthetic OS'),
                ('firmware/blackfin/bin/bootloader.ldr', b'never copied'),
                ('firmware/blackfin/bin/bootloader.version', b'1000.0.23'),
                ('firmware/ble/bin/nordic_bootloader_0.bin', b'never copied either'),
                ('firmware/ble/bin/nordic_bootloader_1.bin', b'nor this'),
                ('firmware/blackfin/content/custom_bootloader.bin', b'excluded even in content'),
                ('firmware/blackfin/content/display/test.svg', b'svg'),
                ('firmware/firmware.version', b'1.2.5'),
            ]
        )
        output = self.root / 'stage'
        plan = stage_package(path, output)
        self.assertEqual(len([m for m in plan.members if m.role == 'excluded-bootloader']), 5)
        self.assertEqual((output / 'payload/os.ldr').read_bytes(), b'synthetic OS')
        self.assertEqual((output / 'content/display/test.svg').read_bytes(), b'svg')
        self.assertFalse((output / 'os.ldr').exists())
        self.assertFalse(any('bootloader' in p.name for p in output.rglob('*')))

    def test_no_existing_output_overwrite(self):
        archive = self.archive([(OS, b'os')])
        output = self.root / 'stage'
        output.mkdir()
        (output / 'user-data').write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            stage_package(archive, output)
        self.assertEqual((output / 'user-data').read_bytes(), b'preserve')

    def test_path_traversal_aliases_and_unknown_firmware_rejected(self):
        bad_names = [
            '../os.ldr',
            '/os.ldr',
            'firmware/../os.ldr',
            'firmware//bad',
            'firmware/blackfin/content/../../os.ldr',
            'C:/os.ldr',
            'firmware\\blackfin\\bin\\os.ldr',
            'firmware/blackfin/content/a.',
            'firmware/blackfin/content/a ',
            'firmware/unknown.bin',
            'firmware/blackfin/bin/OS.LDR',
            'firmware/blackfin/content/a\x01b',
        ]
        for index, bad in enumerate(bad_names):
            with self.subTest(name=bad):
                path = self.archive([(OS, b'os'), (bad, b'bad')], f'bad{index}.zip')
                with self.assertRaises(PackageError):
                    inspect_package(path)

    def test_duplicate_and_case_alias_rejected(self):
        for index, rows in enumerate(
            [
                [(OS, b'a'), (OS, b'b')],
                [(OS, b'a'), ('firmware/blackfin/content/Foo', b'1'), ('firmware/blackfin/content/foo', b'2')],
            ]
        ):
            with self.assertRaises(PackageError):
                inspect_package(self.archive(rows, f'duplicate{index}.zip'))

    def test_file_directory_conflict_rejected(self):
        path = self.archive(
            [(OS, b'os'), ('firmware/blackfin/content/a', b'a'), ('firmware/blackfin/content/a/b', b'b')]
        )
        with self.assertRaises(PackageError):
            inspect_package(path)

    def test_symlink_member_rejected(self):
        link = zipfile.ZipInfo('firmware/blackfin/content/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(PackageError):
            inspect_package(self.archive([(OS, b'os'), (link, b'/os.ldr')]))

    def test_bounds_enforced_before_extraction(self):
        path = self.archive([(OS, b'o' * 100), ('firmware/firmware.version', b'v')])
        for limits in [Limits(member_bytes=99), Limits(total_bytes=100), Limits(members=1), Limits(archive_bytes=10)]:
            with self.subTest(limits=limits), self.assertRaises(PackageError):
                inspect_package(path, limits=limits)

    def test_missing_os_rejected(self):
        with self.assertRaises(PackageError):
            inspect_package(self.archive([('firmware/firmware.version', b'1.2.5')]))

    def test_crc_error_even_for_excluded_bootloader_rejected(self):
        path = self.root / 'crc.zip'
        marker = b'unique bootloader marker'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(OS, b'os')
            archive.writestr('firmware/blackfin/bin/bootloader.ldr', marker)
        data = bytearray(path.read_bytes())
        data[data.index(marker)] ^= 1
        path.write_bytes(data)
        with self.assertRaises(PackageError):
            inspect_package(path)


if __name__ == '__main__':
    unittest.main()
