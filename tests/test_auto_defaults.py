"""Synthetic fixtures only; no live application or service changes."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch
import copy
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'macos'))
import backend as b


class AutoDefaultsTests(unittest.TestCase):
    def fixture(self,stack,overrides):
        saved={'overrides':overrides,'appliedOverrides':copy.deepcopy(overrides),
               'policyProviderMode':True,
               'protectServices':True,'serviceChecks':[],'systemPolicy':{'host':'127.0.0.1','port':7897}}
        directory=Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.object(b,'STATE',directory/'settings.json'))
        stack.enter_context(patch.object(b,'JOURNAL',directory/'pending.json'))
        stack.enter_context(patch.object(b,'PAUSED',directory/'paused'))
        b.save_json(b.STATE,saved)
        stack.enter_context(patch.object(b,'locked',return_value=nullcontext()))
        stack.enter_context(patch.object(b,'clash',return_value={'mode':'rule'}))
        stack.enter_context(patch.object(b,'mc',side_effect=lambda method,*a,**k:
            {'state':'on'} if method=='appRouting.status' else {'currentWorkspaceID':b.workspace.WORKSPACE}))
        stack.enter_context(patch.object(b.protection,'check_services'))
        events=[]
        update=stack.enter_context(patch.object(b,'update_cv',side_effect=lambda *a,**k:events.append('clash')))
        apply=stack.enter_context(patch.object(b.workspace,'apply',side_effect=lambda *a:events.append('transport')))
        return saved,update,apply,events

    def test_all_current_apps_return_to_auto_in_one_transaction(self):
        overrides={'/Applications/A.app':{'choice':'Current','followSubscription':True},
                   '/Applications/B.app':{'choice':'DIRECT'}}
        with ExitStack() as stack:
            old,update,apply,events=self.fixture(stack,overrides)
            result=b.reset_all_auto()
            self.assertEqual(result['resetCount'],2)
            new=b.read_json(b.STATE)
            self.assertEqual(new['overrides'],{})
            self.assertEqual(new['appliedOverrides'],{})
            self.assertEqual(new['systemPolicy'],old['systemPolicy'])
            self.assertTrue(new['protectServices'])
            self.assertFalse(b.JOURNAL.exists())
            update.assert_called_once();apply.assert_called_once()
            self.assertEqual(events,['transport','clash'])

    def test_already_automatic_does_not_reload_or_change_services(self):
        with ExitStack() as stack:
            old,update,apply,events=self.fixture(stack,{})
            self.assertEqual(b.reset_all_auto()['resetCount'],0)
            update.assert_not_called();apply.assert_not_called()
            b.protection.check_services.assert_not_called()

    def test_failure_keeps_previous_choices_and_recovers_transaction(self):
        with ExitStack() as stack:
            old,update,apply,events=self.fixture(stack,{'/Applications/A.app':{'choice':'DIRECT'}})
            update.side_effect=RuntimeError('synthetic failure')
            recovery=stack.enter_context(patch.object(b,'recover'))
            with self.assertRaisesRegex(RuntimeError,'synthetic failure'):b.reset_all_auto()
            self.assertEqual(b.read_json(b.STATE)['overrides'],old['overrides'])
            recovery.assert_called_once()

    def test_manual_choices_are_not_reset_by_menu_read(self):
        with ExitStack() as stack:
            old,update,apply,events=self.fixture(stack,{'/Applications/A.app':{'choice':'DIRECT'}})
            result=b.menu_snapshot()
            self.assertEqual(result['apps'],[{'path':'/Applications/A.app','mode':'direct'}])
            self.assertEqual(b.read_json(b.STATE)['overrides'],old['overrides'])
            update.assert_not_called();apply.assert_not_called()


if __name__=='__main__':unittest.main()
