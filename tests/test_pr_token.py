"""Verify the manual token test cannot change package metadata or run on PR refs."""
import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TokenTestTests(unittest.TestCase):
    """Exercise trusted context checks and an actual disposable Git transaction."""

    def setUp(self):
        """Load the token-test implementation without performing network operations."""
        path = ROOT / 'scripts/test-pr-token.py'
        self.assertTrue(path.is_file(), 'Manual PR token test is not implemented')
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location('pr_token_test', path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def context(self, sha):
        """Construct non-secret workflow metadata for a manual main invocation."""
        return {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_SHA': sha, 'GITHUB_RUN_ID': '12345',
                'GITHUB_REPOSITORY': 'example/packaging', 'GH_TOKEN': 'test-only-placeholder'}

    def test_only_manual_main_and_exact_sha_are_allowed(self):
        """A scheduled run, pull-request ref, or stale checkout must fail before publication."""
        sha = 'a' * 40
        self.module.validate_context(self.context(sha), sha)
        for field, value in (('GITHUB_EVENT_NAME', 'schedule'), ('GITHUB_REF', 'refs/pull/1/merge'),
                             ('GITHUB_SHA', 'b' * 40), ('GITHUB_RUN_ID', '../main'), ('GH_TOKEN', '')):
            env = {**self.context(sha), field: value}
            with self.assertRaises(ValueError):
                self.module.validate_context(env, sha)

    def test_commit_changes_only_test_document(self):
        """The smoke branch must retain every package source byte and the version/release."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name in self.module.PUBLISH_FILES:
                shutil.copyfile(ROOT / name, root / name)
            subprocess.run(['git', 'init', '--initial-branch=main', str(root)], check=True, capture_output=True)
            self.module.git(root, 'add', '.')
            self.module.git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                            '-c', 'commit.gpgsign=false', 'commit', '-m', 'Fixture')
            before = self.module.git(root, 'rev-parse', 'HEAD').strip()
            sources = {name: (root / name).read_bytes() for name in self.module.PUBLISH_FILES}
            branch, document = self.module.prepare_commit(root, self.context(before))
            self.assertTrue(branch.startswith('automation/pr-token-smoke-'))
            self.assertIn('not an upstream release', document.read_text())
            changed = self.module.git(root, 'diff', '--name-only', before, 'HEAD').splitlines()
            self.assertEqual(changed, ['docs/Upstream-PR-Token-Test.md'])
            self.assertEqual(sources, {name: (root / name).read_bytes() for name in self.module.PUBLISH_FILES})

    def test_existing_document_is_not_overwritten(self):
        """The smoke test must preserve an existing document instead of replacing it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'docs').mkdir()
            document = root / 'docs/Upstream-PR-Token-Test.md'
            document.write_text('Existing content\n')
            with self.assertRaises(ValueError):
                self.module.write_document(root, '2.0.0', self.context('a' * 40))
            self.assertEqual(document.read_text(), 'Existing content\n')


if __name__ == '__main__':
    unittest.main()
