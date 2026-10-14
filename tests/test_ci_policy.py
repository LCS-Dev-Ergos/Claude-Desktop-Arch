"""Exercise CI trust boundaries and publication failure paths without external credentials."""
import importlib.util
import json
import pathlib
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PolicyTests(unittest.TestCase):
    """Reject unsafe snapshots, lint findings, deployment settings, and version transitions."""

    def setUp(self):
        """Load the policy implementation only after its entry point exists."""
        path = ROOT / 'scripts/ci_policy.py'
        self.assertTrue(path.is_file(), 'CI policy is not implemented')
        spec = importlib.util.spec_from_file_location('ci_policy', path)
        self.policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.policy)

    def test_upstream_exit_codes_preserve_manual_review(self):
        """Compatibility and no-change results must not be treated as update candidates."""
        self.assertEqual(self.policy.update_status(0), 'candidate')
        self.assertEqual(self.policy.update_status(3), 'current')
        self.assertEqual(self.policy.update_status(4), 'review_required')
        for code in (1, 2, 5, -1):
            with self.assertRaises(ValueError):
                self.policy.update_status(code)

    def test_new_namcap_error_fails(self):
        """Only the exact reviewed optional-link diagnostics may pass the lint gate."""
        known = 'claude-desktop-local E: Symlink (usr/libexec/virtiofsd) points to non-existing ../lib/virtiofsd\n'
        self.policy.check_namcap(known)
        self.policy.check_namcap('claude-desktop-local W: ELF file lacks FULL RELRO\n')
        for line in ('pkg E: Dependency libnew detected and not included',
                     'pkg E: Symlink (etc/secret) points to non-existing ../../secret'):
            with self.assertRaises(ValueError):
                self.policy.check_namcap(known + line)

    def test_environment_requires_reviewers_and_only_main(self):
        """Creating an unprotected environment must never unlock the publish job."""
        config = {'protection_rules': [{'type': 'required_reviewers', 'reviewers': [{'type': 'User'}]}],
                  'deployment_branch_policy': {'custom_branch_policies': True, 'protected_branches': False}}
        branches = {'branch_policies': [{'name': 'main', 'type': 'branch'}]}
        self.policy.check_environment(config, branches)
        for bad in ({}, {**config, 'protection_rules': []}):
            with self.assertRaises(ValueError):
                self.policy.check_environment(bad, branches)
        for bad in ({'branch_policies': []}, {'branch_policies': [{'name': '*', 'type': 'branch'}]},
                    {'branch_policies': [{'name': 'main', 'type': 'tag'}]}):
            with self.assertRaises(ValueError):
                self.policy.check_environment(config, bad)

    def test_snapshot_rejects_tampering_and_symlinks(self):
        """Publication must copy checksum-matching regular sources rather than arbitrary files."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name in self.policy.PUBLISH_FILES:
                shutil.copyfile(ROOT / name, root / name)
            snapshot = self.policy.snapshot(root)
            self.assertEqual(snapshot['version'], json.loads((ROOT / 'upstream.json').read_text())['package']['Version'])
            (root / 'LICENSE.md').write_text('tampered')
            with self.assertRaises(ValueError):
                self.policy.snapshot(root)
            (root / 'LICENSE.md').unlink()
            (root / 'LICENSE.md').symlink_to(ROOT / 'LICENSE.md')
            with self.assertRaises(ValueError):
                self.policy.snapshot(root)

    def test_snapshot_rejects_recovery_marker(self):
        """An interrupted updater transaction must block release preparation."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / '.update-transaction.json').write_text('{}')
            with self.assertRaises(ValueError):
                self.policy.snapshot(root)

    def test_downgrade_and_changed_same_version_are_rejected(self):
        """A successful old build cannot silently replace a newer AUR release."""
        self.policy.check_transition((2, 4, 0, 1), (2, 3, 0, 2), False)
        self.policy.check_transition((2, 4, 0, 1), (2, 4, 0, 1), True)
        for new, old, identical in (((2, 3, 0, 2), (2, 4, 0, 1), False),
                                    ((2, 4, 0, 1), (2, 4, 0, 1), False)):
            with self.assertRaises(ValueError):
                self.policy.check_transition(new, old, identical)


if __name__ == '__main__':
    unittest.main()
