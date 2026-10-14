"""Test the publication transaction with disposable local Git repositories only."""
import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    """Prove idempotence, source allowlisting, and rejection of remote surprises."""

    def setUp(self):
        """Load the publisher without invoking its credential-bearing entry point."""
        path = ROOT / 'scripts/publish-aur.py'
        self.assertTrue(path.is_file(), 'AUR publisher is not implemented')
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location('publish_aur', path)
        self.publisher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.publisher)

    def test_offline_publish_is_idempotent_and_does_not_execute_recipe(self):
        """Only allowlisted bytes reach AUR; a recipe's shell text is never executed."""
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            source = work / 'source'
            source.mkdir()
            for name in self.publisher.PUBLISH_FILES:
                shutil.copyfile(ROOT / name, source / name)
            marker = work / 'recipe-executed'
            with (source / 'PKGBUILD').open('a') as recipe:
                recipe.write(f'\ntouch {marker}\n')
            remote = work / 'aur.git'
            subprocess.run(['git', 'init', '--bare', '--initial-branch=master', str(remote)], check=True, capture_output=True)
            changed = self.publisher.sync_package(source, str(remote), work / 'first', 'Test Publisher', 'test@example.invalid')
            self.assertTrue(changed)
            self.assertFalse(marker.exists())
            names = subprocess.check_output(['git', '--git-dir', str(remote), 'ls-tree', '--name-only', 'master'], text=True).splitlines()
            self.assertEqual(sorted(names), sorted(self.publisher.PUBLISH_FILES))
            self.assertFalse(self.publisher.sync_package(source, str(remote), work / 'second', 'Test Publisher', 'test@example.invalid'))
            with (source / 'PKGBUILD').open('a') as recipe:
                recipe.write('# changed same-version recipe\n')
            with self.assertRaises(ValueError):
                self.publisher.sync_package(source, str(remote), work / 'third', 'Test Publisher', 'test@example.invalid')

    def test_known_hosts_must_pin_only_the_aur_host(self):
        """Wildcard, alternate-host, and certificate-authority trust must be rejected."""
        for text in ('', '* ssh-ed25519 AAAA', 'example.org ssh-ed25519 AAAA',
                     '@cert-authority aur.archlinux.org ssh-ed25519 AAAA'):
            with self.assertRaises(ValueError):
                self.publisher.check_known_hosts(text)

    def test_unexpected_remote_file_is_preserved_and_rejected(self):
        """A pre-existing AUR file must not be deleted by an automated migration."""
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            source = work / 'source'
            source.mkdir()
            for name in self.publisher.PUBLISH_FILES:
                shutil.copyfile(ROOT / name, source / name)
            remote = work / 'aur.git'
            subprocess.run(['git', 'init', '--bare', '--initial-branch=master', str(remote)], check=True, capture_output=True)
            self.publisher.sync_package(source, str(remote), work / 'first', 'Test Publisher', 'test@example.invalid')
            clone = work / 'first'
            (clone / 'unexpected.install').write_text('Unreviewed remote script\n')
            self.publisher.git(clone, 'add', 'unexpected.install')
            self.publisher.git(clone, 'commit', '-m', 'Add unexpected file')
            self.publisher.git(clone, 'push', 'origin', 'master')
            before = self.publisher.git(clone, 'rev-parse', 'HEAD').stdout
            with self.assertRaisesRegex(ValueError, 'Unexpected AUR file'):
                self.publisher.sync_package(source, str(remote), work / 'second', 'Test Publisher', 'test@example.invalid')
            after = subprocess.check_output(['git', '--git-dir', str(remote), 'rev-parse', 'master'])
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
