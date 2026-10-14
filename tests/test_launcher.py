"""Exercise launcher argument rejection before any application executes."""
import os
import pathlib
import subprocess
import tempfile
import unittest

LAUNCHER = pathlib.Path(__file__).resolve().parents[1] / 'claude-desktop.sh'


class LauncherTests(unittest.TestCase):
    """Verify user-controlled input cannot bypass launch security policy."""

    def test_unsafe_cli_flags_are_rejected(self):
        """A dangerous option must fail before runtime resolution."""
        self.assertTrue(LAUNCHER.exists(), 'launcher is not implemented')
        for flag in ['--no-sandbox', '--no-sandbox=true', '--disable-setuid-sandbox', '--disable-web-security', '--remote-debugging-port=9222', '--inspect=9229', '--js-flags=--expose-gc']:
            with self.subTest(flag=flag):
                result = subprocess.run(['bash', str(LAUNCHER), flag], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn('Unsupported option', result.stderr)

    def test_unsafe_config_and_shell_text_are_rejected(self):
        """Configuration must neither disable the sandbox nor execute shell text."""
        self.assertTrue(LAUNCHER.exists(), 'launcher is not implemented')
        with tempfile.TemporaryDirectory() as tmp:
            config = pathlib.Path(tmp) / 'claude-desktop-flags.conf'
            marker = pathlib.Path(tmp) / 'executed'
            for line in ['--no-sandbox', f'$(touch {marker})']:
                config.write_text(line + '\n')
                result = subprocess.run(['bash', str(LAUNCHER)], env={**os.environ, 'XDG_CONFIG_HOME': tmp}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
