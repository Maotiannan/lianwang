from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'macos'))
import backend as b


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.mocks = ExitStack()
        self.addCleanup(self.mocks.close)
        root = Path(self.mocks.enter_context(tempfile.TemporaryDirectory()))
        for name, path in {'STATE':root/'settings.json', 'APP_DATA':root, 'JOURNAL':root/'pending.json'}.items():
            self.mocks.enter_context(patch.object(b, name, path))
        self.mc = self.mocks.enter_context(patch.object(b, 'mc'))
        self.clash = self.mocks.enter_context(patch.object(b, 'clash'))

    def test_menu_and_sync_are_inert_before_initial_setup(self):
        self.assertEqual(b.menu_snapshot()['status'], 'first-run')
        self.assertTrue(b.sync_system()['setupPending'])
        self.mc.assert_not_called()
        self.clash.assert_not_called()
        self.assertFalse(b.STATE.exists())

    def test_running_global_clash_is_rejected_before_any_setup_mutations(self):
        self.clash.return_value = {'mode':'global'}
        with self.assertRaises(b.Problem):
            b.setup()
        self.assertFalse(b.STATE.exists())
        self.mc.assert_not_called()

    def test_completed_setup_is_safe_to_repeat(self):
        state = {'systemPolicy':{'port':23456}, 'policyProviderMode':True}
        b.save_json(b.STATE,state)
        self.mc.side_effect = [{'currentWorkspaceID':b.workspace.WORKSPACE}, {'state':'on','enabled':True}]
        self.assertEqual(b.setup()['state'],'on')
        self.assertEqual(b.read_json(b.STATE),state)
        self.clash.assert_not_called()
        self.assertEqual([c.args[0] for c in self.mc.call_args_list],['configuration.snapshot','appRouting.status'])

    def test_new_install_protects_services_by_default(self):
        state=b.new_state()
        self.assertTrue(state['protectServices'])
        self.assertEqual(state['overrides'],{})
