"""Require lint tool failures to propagate across the chroot wrapper."""
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class NamcapExecutionTests(unittest.TestCase):
    """Test the invocation boundary rather than interpreting a simulated lint log."""

    def test_namcap_process_failure_is_not_hidden(self):
        """A crashed lint process must fail even when no E diagnostic was emitted."""
        helper = ROOT / 'scripts/ci-namcap.sh'
        self.assertTrue(helper.is_file(), 'Checked Namcap wrapper is not implemented')
        with tempfile.TemporaryDirectory() as tmp:
            stage = pathlib.Path(tmp)
            (stage / 'test.pkg.tar.zst').touch()
            tool = stage / 'arch-nspawn'
            tool.write_text('#!/bin/sh\nprintf "Controlled lint tool failure\\n" >&2\nexit 41\n')
            tool.chmod(0o755)
            result = subprocess.run(['bash', str(helper), str(stage / 'chroot'), str(stage)],
                                    env={**os.environ, 'PATH': str(stage) + os.pathsep + os.environ['PATH']},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 41, result.stderr)
            self.assertIn('Controlled lint tool failure', result.stderr)


if __name__ == '__main__':
    unittest.main()
