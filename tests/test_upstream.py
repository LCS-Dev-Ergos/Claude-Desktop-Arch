"""Regression checks for authenticated metadata and hostile archive inputs."""
import datetime as dt
import hashlib
import importlib.util
import io
import pathlib
import tarfile
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'upstream.py'


class UpstreamTests(unittest.TestCase):
    """Exercise security boundaries without downloading application assets."""

    def setUp(self):
        """Load the implementation only after it exists."""
        self.assertTrue(SCRIPT.exists(), 'upstream verifier is not implemented')
        spec = importlib.util.spec_from_file_location('upstream', SCRIPT)
        self.u = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.u)

    def release(self, date='Fri, 09 Oct 2026 20:31:56 +0000', until='Fri, 16 Oct 2026 20:31:56 +0000'):
        """Return independently specified APT release metadata."""
        return f'Date: {date}\nValid-Until: {until}\nSHA256:\n {"a" * 64} 3 main/binary-amd64/Packages\n'

    def test_signature_tampering_is_rejected(self):
        """Changes to signed repository metadata must fail authentication."""
        key = SCRIPT.parent / 'keys/anthropic.asc'
        signed = pathlib.Path(__file__).parent / 'fixtures/InRelease'
        plaintext = self.u.verify_release(signed, key)
        self.assertIn('Origin: Anthropic', plaintext)
        with tempfile.TemporaryDirectory() as tmp:
            altered = pathlib.Path(tmp) / 'InRelease'
            altered.write_text(signed.read_text().replace('Origin: Anthropic', 'Origin: Attacker'))
            with self.assertRaises(ValueError):
                self.u.verify_release(altered, key)
        with self.assertRaises(ValueError):
            self.u.verify_release(signed, key, '0' * 40)

    def test_expired_release_is_rejected(self):
        """Removing expiry enforcement must allow this test to fail."""
        now = dt.datetime(2026, 10, 17, tzinfo=dt.timezone.utc)
        with self.assertRaises(ValueError):
            self.u.parse_release(self.release(), now)

    def test_future_release_is_rejected(self):
        """Reject metadata dated later than the permitted clock skew."""
        now = dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc)
        with self.assertRaises(ValueError):
            self.u.parse_release(self.release(), now)

    def test_valid_release_exposes_authenticated_index_hash(self):
        """Verify that the exact index path determines its expected digest."""
        now = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
        self.assertEqual(self.u.parse_release(self.release(), now)['main/binary-amd64/Packages'], ('a' * 64, 3))

    def test_duplicate_fields_are_rejected(self):
        """Reject ambiguous package identity instead of taking the last field."""
        with self.assertRaises(ValueError):
            self.u.parse_control('Package: claude-desktop\nPackage: other\n')

    def test_package_selection_rejects_escaping_filename(self):
        """Prevent trusted-looking index fields from selecting external paths."""
        entry = 'Package: claude-desktop\nVersion: 2.3.4\nArchitecture: amd64\nFilename: pool/main/c/claude-desktop/../../other.deb\nSize: 3\nSHA256: ' + 'a' * 64 + '\n'
        with self.assertRaises(ValueError):
            self.u.select_package(entry, 'amd64')

    def test_other_package_is_not_selected(self):
        """A newer unrelated package must not determine the Claude version."""
        other = 'Package: other\nVersion: 9.0.0\nArchitecture: amd64\n\n'
        claude = 'Package: claude-desktop\nVersion: 2.3.4\nArchitecture: amd64\nFilename: pool/main/c/claude-desktop/claude-desktop_2.3.4_amd64.deb\nSize: 3\nSHA256: ' + 'a' * 64 + '\n'
        self.assertEqual(self.u.select_package(other + claude, 'amd64')['Version'], '2.3.4')

    def test_wrong_asset_hash_is_rejected(self):
        """A matching byte count must not bypass the digest check."""
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / 'asset'; p.write_bytes(b'abc')
            with self.assertRaises(ValueError):
                self.u.verify_file(p, 3, 'a' * 64)
            self.u.verify_file(p, 3, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_archive_traversal_and_links_are_rejected(self):
        """Prevent extraction outside staging through files or symbolic links."""
        for name, link in [('../escape', None), ('/escape', None), ('usr/link', '../../../escape')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                p = pathlib.Path(tmp) / 'data.tar'
                with tarfile.open(p, 'w') as archive:
                    item = tarfile.TarInfo(name)
                    if link:
                        item.type = tarfile.SYMTYPE; item.linkname = link
                    else:
                        item.size = 1
                    archive.addfile(item, None if link else io.BytesIO(b'x'))
                with self.assertRaises(ValueError):
                    self.u.validate_tar(p)

    def test_archive_device_is_rejected(self):
        """No device node from a source archive may be materialized."""
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / 'data.tar'
            with tarfile.open(p, 'w') as archive:
                item = tarfile.TarInfo('usr/device'); item.type = tarfile.CHRTYPE
                archive.addfile(item)
            with self.assertRaises(ValueError):
                self.u.validate_tar(p)


if __name__ == '__main__':
    unittest.main()
