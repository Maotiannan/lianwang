"""A process can be running before its automation endpoint is ready."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'macos'))
import engine


class EngineStartupTests(unittest.TestCase):
    def test_waits_for_endpoint_without_letting_cli_launch_another_app(self):
        attempts = []

        def execute(args, **kwargs):
            if args[0] == str(engine.LAUNCHER):
                return subprocess.CompletedProcess(args, 0)
            self.assertEqual(args[1], 'core.status')
            self.assertIn('--no-launch', args)
            attempts.append(args)
            return subprocess.CompletedProcess(args, 0 if len(attempts) >= 3 else 1)

        with patch.object(engine.subprocess, 'run', side_effect=execute), \
                patch('time.sleep'):
            engine.ensure_running()
        self.assertEqual(len(attempts), 3, 'Launch completion is not endpoint readiness')

    def test_failed_launcher_does_not_fall_back_to_vendor_autolaunch(self):
        with patch.object(engine.subprocess, 'run', side_effect=OSError('unavailable')) as run:
            with self.assertRaises(OSError):
                engine.ensure_running()
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
