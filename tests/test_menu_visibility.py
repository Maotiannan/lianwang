"""Recover the exact merged menu group observed after macOS refreshed it."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'macos'))
import menu_visibility


class MenuVisibilityTests(unittest.TestCase):
    def test_merged_disabled_group_is_repaired_without_touching_other_apps(self):
        main = {'bundle': {'_0': 'local.network-egress.menu'}}
        helper = {'bundle': {'_0': 'one.leaper.mclash'}}
        other = {'bundle': {'_0': 'other.app'}}
        before = [other, {'location': other, 'isAllowed': False, 'extra': 7}, main,
                  {'location': main, 'menuItemLocations': [helper, main], 'isAllowed': False}]
        after = menu_visibility.helper_menu_hidden(before)
        self.assertEqual(after[:2], before[:2])
        self.assertTrue(after[3]['isAllowed'])
        self.assertEqual(after[3]['menuItemLocations'], [main])
        self.assertFalse(after[5]['isAllowed'])
        self.assertEqual(after[5]['menuItemLocations'], [helper])
        self.assertFalse(before[3]['isAllowed'])

    def test_fresh_or_missing_parent_record_is_created(self):
        after = menu_visibility.helper_menu_hidden([])
        by_bundle = {after[n]['bundle']['_0']: after[n+1] for n in range(0, len(after), 2)}
        self.assertTrue(by_bundle['local.network-egress.menu']['isAllowed'])
        self.assertFalse(by_bundle['one.leaper.mclash']['isAllowed'])

    def test_unchanged_preferences_do_not_restart_menu_bar(self):
        from unittest.mock import patch
        from tempfile import TemporaryDirectory
        import plistlib
        values = menu_visibility.helper_menu_hidden([])
        data = plistlib.dumps({'trackedApplications': plistlib.dumps(values)})
        with TemporaryDirectory() as directory, patch.object(menu_visibility, 'run', return_value=data) as run:
            result = menu_visibility.repair(lock_path=Path(directory)/'lock')
        self.assertFalse(result['changed'])
        self.assertEqual(run.call_count, 1)

    def test_unknown_schema_is_rejected_before_writing_preferences(self):
        with self.assertRaises(ValueError):
            menu_visibility.helper_menu_hidden([{'unexpected': 'value'}])

    def test_unavailable_preferences_never_write_or_restart_system_agents(self):
        from unittest.mock import patch
        from tempfile import TemporaryDirectory
        import plistlib
        with TemporaryDirectory() as directory, \
                patch.object(menu_visibility, 'run', return_value=plistlib.dumps({})) as run, \
                patch.object(menu_visibility.os, 'kill') as kill:
            with self.assertRaises(PermissionError):
                menu_visibility.repair(lock_path=Path(directory)/'lock')
        self.assertEqual(run.call_count, 1)
        kill.assert_not_called()

    def test_repair_refreshes_only_display_agents_and_preserves_other_preferences(self):
        from unittest.mock import patch
        from tempfile import TemporaryDirectory
        import plistlib
        import signal
        document = {'trackedApplications': plistlib.dumps([]), 'keep': 'unchanged'}
        rows = ('101 /System/Library/CoreServices/ControlCenter.app/Contents/MacOS/ControlCenter\n'
                '102 /System/Library/CoreServices/MenuBarAgent.app/Contents/MacOS/MenuBarAgent\n'
                '103 /Applications/联网.app/Contents/Helpers/MClash.app/Contents/MacOS/MClash\n'
                '104 /Applications/联网.app/Contents/MacOS/NetworkEgress\n').encode()
        with TemporaryDirectory() as directory, \
                patch.object(menu_visibility, 'run', side_effect=[plistlib.dumps(document), b'', rows]) as run, \
                patch.object(menu_visibility.os, 'kill') as kill:
            result = menu_visibility.repair(lock_path=Path(directory)/'lock')
        self.assertTrue(result['changed'])
        self.assertEqual(plistlib.loads(run.call_args_list[1].kwargs['data'])['keep'], 'unchanged')
        self.assertEqual(kill.call_args_list, [((101, signal.SIGTERM),), ((102, signal.SIGTERM),)])


if __name__ == '__main__':
    unittest.main()
