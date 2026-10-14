"""Verify data-only update proposals before any GitHub write token is used."""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProposalTests(unittest.TestCase):
    """Reject altered recipe code and compatibility changes in an automated PR."""

    def setUp(self):
        """Load the trusted proposal validator independently from candidate files."""
        path = ROOT / 'scripts/open-update-pr.py'
        self.assertTrue(path.exists(), 'PR publisher is not implemented')
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location('update_pr', path)
        self.proposal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.proposal)

    def candidate(self, directory):
        """Generate a synthetic next-version candidate through the real recipe renderer."""
        updater = self.proposal.load_updater(ROOT)
        baseline = json.loads((ROOT / 'upstream.json').read_text())
        entry = baseline['package']
        entry['Version'] = str(int(entry['Version'].split('.')[0]) + 1) + '.0.0'
        entry['Filename'] = f"pool/main/c/claude-desktop/claude-desktop_{entry['Version']}_amd64.deb"
        (directory / 'upstream.json').write_text(updater.json_text(baseline))
        recipe = updater.render_pkgbuild(ROOT, entry, baseline)
        (directory / 'PKGBUILD').write_text(recipe)
        (directory / '.SRCINFO').write_text(updater.generate_srcinfo(ROOT, recipe, baseline))
        (directory / 'report.md').write_text('Synthetic authenticated-candidate test report.\n')

    def test_recipe_injection_is_rejected(self):
        """Downloaded proposal files cannot add commands under a write-token job."""
        with tempfile.TemporaryDirectory() as tmp:
            candidate = pathlib.Path(tmp)
            self.candidate(candidate)
            self.proposal.validate_candidate(ROOT, candidate)
            with (candidate / 'PKGBUILD').open('a') as recipe:
                recipe.write('\ntouch /tmp/should-never-run\n')
            with self.assertRaises(ValueError):
                self.proposal.validate_candidate(ROOT, candidate)

    def test_compatibility_and_extra_files_are_rejected(self):
        """A candidate requiring manual review cannot be converted into an ordinary PR."""
        with tempfile.TemporaryDirectory() as tmp:
            candidate = pathlib.Path(tmp)
            self.candidate(candidate)
            manifest = json.loads((candidate / 'upstream.json').read_text())
            manifest['inspection']['resources'].append('new-native-component')
            (candidate / 'upstream.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                self.proposal.validate_candidate(ROOT, candidate)
            self.candidate(candidate)
            (candidate / 'unexpected').write_text('unreviewed')
            with self.assertRaises(ValueError):
                self.proposal.validate_candidate(ROOT, candidate)


if __name__ == '__main__':
    unittest.main()
