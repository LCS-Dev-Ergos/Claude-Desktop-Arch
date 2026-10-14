"""Regression checks for review gates and failure-safe update writes."""
import importlib.util
import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'scripts'


class UpdateTests(unittest.TestCase):
    """Ensure changed dependencies and failed generation cannot silently publish updates."""

    def setUp(self):
        """Load the update script without invoking its command-line entry point."""
        path = SCRIPTS / 'check-update.py'
        self.assertTrue(path.exists(), 'updater is not implemented')
        sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location('check_update', path)
        self.u = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.u)

    def test_dependency_change_requires_review(self):
        """A newly required library must not be treated as an ordinary version bump."""
        old = {'dependencies': {'Depends': 'gtk'}, 'electron': '44.4.3', 'resources': ['app.asar'], 'native_modules': []}
        new = {**old, 'dependencies': {'Depends': 'gtk, libnew'}}
        self.assertTrue(self.u.review_reasons(old, new))
        self.assertFalse(self.u.review_reasons(old, {**old, 'electron': '44.6.0'}))
        self.assertTrue(self.u.review_reasons(old, {**old, 'electron': '45.0.0'}))

    def test_updated_recipe_keeps_license_checksum(self):
        """Future updates must preserve the license source and its matching checksum."""
        root = SCRIPTS.parent
        manifest = json.loads((root / 'upstream.json').read_text())
        rendered = self.u.render_pkgbuild(root, manifest['package'], manifest)
        with tempfile.TemporaryDirectory() as tmp:
            recipe = pathlib.Path(tmp) / 'PKGBUILD'
            recipe.write_text(rendered)
            result = subprocess.run(
                ['bash', '-c', 'source "$1"; printf "%s\\n" "${#source[@]}" "${#sha256sums[@]}"; '
                 'for index in "${!source[@]}"; do if [[ ${source[$index]} == LICENSE.md ]]; then '
                 'printf "%s\\n" "${sha256sums[$index]-missing}"; fi; done',
                 'check-license', str(recipe)], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(),
                         ['6', '6', hashlib.sha256((root / 'LICENSE.md').read_bytes()).hexdigest()])

    def test_failed_srcinfo_generation_preserves_files(self):
        """Do not commit PKGBUILD changes before metadata generation succeeds."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name in ['PKGBUILD', 'upstream.json', '.SRCINFO']:
                (root / name).write_text('original ' + name)
            with mock.patch.object(self.u, 'generate_srcinfo', side_effect=ValueError('generation failed')):
                with self.assertRaises(ValueError):
                    self.u.commit_update(root, 'replacement', {'schema': 1})
            for name in ['PKGBUILD', 'upstream.json', '.SRCINFO']:
                self.assertEqual((root / name).read_text(), 'original ' + name)

    def test_write_failure_rolls_back_previous_replacements(self):
        """A failed second write must restore the first file and retain the backup."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name in ['PKGBUILD', 'upstream.json', '.SRCINFO']:
                (root / name).write_text('original ' + name)
            real_replace = self.u.os.replace

            def fail_second(source, destination):
                """Simulate a filesystem failure only at the manifest commit boundary."""
                if pathlib.Path(destination) == root / 'upstream.json':
                    raise OSError('simulated write failure')
                return real_replace(source, destination)

            with mock.patch.object(self.u, 'generate_srcinfo', return_value='generated'), mock.patch.object(self.u.os, 'replace', side_effect=fail_second):
                with self.assertRaises(OSError):
                    self.u.commit_update(root, 'replacement', {'schema': 1})
            for name in ['PKGBUILD', 'upstream.json', '.SRCINFO']:
                self.assertEqual((root / name).read_text(), 'original ' + name)


if __name__ == '__main__':
    unittest.main()
