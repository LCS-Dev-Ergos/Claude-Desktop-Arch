"""Exercise workflow state handling without network access or publication secrets."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class InspectionScriptTests(unittest.TestCase):
    """Check real shell orchestration for no-change, review, and failure outcomes."""

    def inspect(self, code):
        """Run the actual orchestration against a minimal controlled updater result."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            scripts = root / 'scripts'
            scripts.mkdir()
            for name in ('ci-upstream.sh', 'ci_policy.py'):
                shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
            (scripts / 'check-update.py').write_text(f'import sys\nprint("Controlled inspection result")\nsys.exit({code})\n')
            output, summary = root / 'output', root / 'summary'
            result = subprocess.run(['bash', 'scripts/ci-upstream.sh'], cwd=root, capture_output=True,
                                    env={**os.environ, 'GITHUB_OUTPUT': str(output),
                                         'GITHUB_STEP_SUMMARY': str(summary)}, text=True)
            return result, output.read_text() if output.exists() else '', summary.read_text() if summary.exists() else '', (root / '.ci-output/update').exists()

    def test_review_stays_manual_and_current_produces_no_candidate(self):
        """Exit four must report manual review without creating a publishable artifact."""
        for code, expected in ((3, 'current'), (4, 'review_required')):
            result, output, summary, candidate = self.inspect(code)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output, f'status={expected}\n')
            self.assertIn(expected, summary)
            self.assertFalse(candidate)

    def test_verification_failure_cannot_report_a_candidate(self):
        """Unexpected updater failure must fail CI and leave no success output."""
        result, output, summary, candidate = self.inspect(1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')
        self.assertEqual(summary, '')
        self.assertFalse(candidate)


if __name__ == '__main__':
    unittest.main()
