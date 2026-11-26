"""Regression tests use synthetic subscriptions; no live routing or services."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch, Mock
import copy
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'macos'))
import backend as b


def transform(config, entries):
    script = (b.RESOURCE / 'routing.js').read_text()
    script += '\nconst [c,e]=JSON.parse(require("fs").readFileSync(0,"utf8")); process.stdout.write(JSON.stringify(applyNetworkEgress(c,e)));'
    result = subprocess.run(['node', '-e', script],
                            input=json.dumps([config, entries]), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def fixture(name='Old'):
    return {'mode':'rule', 'tun':{'enable':False}, 'dns':{'enable':False},
            'proxies':[{'name':'Node','type':'socks5','server':'127.0.0.1','port':9999}],
            'proxy-groups':[{'name':name,'type':'select','proxies':['Node']}],
            'rules':['DOMAIN,local.test,DIRECT','MATCH,'+name]}


def state():
    return {'token':'synthetic-only', 'targets':{'Old':{'key':'stable','port':29991}},
            'overrides':{'/Applications/Test.app':{'choice':'Old','executables':['/test'], 'signingIDs':[]}},
            'systemPolicy':{'host':'127.0.0.1','port':7897,'blocks':[]},
            'policyVersion':4, 'routingVersion':b.ROUTING_VERSION, 'configGeneration':1, 'serviceChecks':[{'label':'fixture','url':'http://invalid.test'}]}


class SubscriptionSwitchTests(unittest.TestCase):
    def test_existing_saved_proxy_follows_replacement_without_changing_relay(self):
        entries=b.entries(state())
        old=transform(fixture(),entries)
        new=transform(fixture('Replacement'),entries)
        self.assertTrue(new['rules'][0].endswith(',Replacement'))
        self.assertIn('IN-NAME,net-egress-menu-stable,Replacement',new['rules'])
        self.assertEqual(old['listeners'],new['listeners'])
        self.assertEqual(new['rules'][-2:],fixture('Replacement')['rules'])
        self.assertEqual(new['tun'],{'enable':False})
        self.assertEqual(new['dns'],{'enable':False})

    def test_hook_handles_successive_switches_without_backend_sync(self):
        entries=b.entries(state())
        for name in ['Replacement','Third','Old']:
            with self.subTest(name=name):
                result=transform(fixture(name),entries)
                self.assertTrue(result['rules'][0].endswith(','+name))
                self.assertEqual(transform(result,entries),result)

    def test_direct_and_explicit_fixed_targets_keep_their_meaning(self):
        entries=[{'choice':'DIRECT','pattern':'direct-app'},
                 {'choice':'Node','pattern':'fixed-app','followSubscription':False}]
        result=transform(fixture('Replacement'),entries)
        self.assertTrue(result['rules'][0].endswith(',DIRECT'))
        self.assertTrue(result['rules'][1].endswith(',Node'))

    def test_invalid_proxy_default_does_not_change_automatic_or_direct_traffic(self):
        config=fixture();config['rules']=['MATCH,DIRECT']
        result=transform(config,b.entries(state()))
        self.assertTrue(result['rules'][0].endswith(',REJECT'))
        self.assertEqual(result['rules'][-1],'MATCH,DIRECT')
        self.assertEqual(transform(result,[])['rules'],['MATCH,DIRECT'])

    def sync_fixture(self, stack, saved, generation=1):
        saved['policyProviderMode'] = True
        tmp=Path(stack.enter_context(tempfile.TemporaryDirectory()))
        config=tmp/'config';config.touch()
        stack.enter_context(patch.object(b,'CONFIG',config))
        stack.enter_context(patch.object(b,'JOURNAL',tmp/'pending.json'))
        stack.enter_context(patch.object(b,'PAUSED',tmp/'paused'))
        saved['configGeneration']=config.stat().st_mtime_ns if generation==1 else 0
        stack.enter_context(patch.object(b,'locked',return_value=nullcontext()))
        stack.enter_context(patch.object(b,'read_json',return_value=copy.deepcopy(saved)))
        stack.enter_context(patch.object(b,'save_json',side_effect=lambda p,s:p.write_text('{}') if p==b.JOURNAL else None))
        stack.enter_context(patch.object(b,'clash',return_value={'mixed-port':7897,'mode':'rule'}))
        stack.enter_context(patch.object(b.system_route,'endpoint',return_value=saved['systemPolicy']))
        stack.enter_context(patch.object(b,'yaml_config',return_value=fixture('Replacement')))
        stack.enter_context(patch.object(b.system_route,'prepare'))
        stack.enter_context(patch.object(b.workspace,'live_session',return_value=('fixture',1)))
        mc=stack.enter_context(patch.object(b,'mc',side_effect=lambda method,*a,**k:
            {'connected':True} if method=='core.status' else {'currentWorkspaceID':b.workspace.WORKSPACE}))
        update=stack.enter_context(patch.object(b,'update_cv'))
        apply=stack.enter_context(patch.object(b.workspace,'apply'))
        return mc,update,apply

    def test_unchanged_sync_does_not_probe_or_reload(self):
        with ExitStack() as stack:
            mc,update,apply=self.sync_fixture(stack,state())
            stack.enter_context(patch.object(b.protection,'check_services',side_effect=AssertionError('unnecessary probe')))
            self.assertEqual(b.sync_system(),{'ok':True})
            update.assert_not_called();apply.assert_not_called()

    def test_reconnect_is_not_blocked_by_preexisting_proxy_outage(self):
        with ExitStack() as stack:
            mc,update,apply=self.sync_fixture(stack,state())
            events=[]
            mc.side_effect=lambda method,*a,**k: events.append(method) or ({'connected':False} if method=='core.status' else {})
            stack.enter_context(patch.object(b.protection,'check_services',side_effect=lambda checks: events.append('health')))
            b.sync_system()
            self.assertLess(events.index('core.connect'),events.index('health'))

    def test_subscription_sync_can_repair_while_public_probe_is_unavailable(self):
        with ExitStack() as stack:
            mc,update,apply=self.sync_fixture(stack,state(),generation=2)
            stack.enter_context(patch.object(b.protection,'reachable_services',return_value=[],create=True))
            def health(checks):
                if checks:raise RuntimeError('existing upstream outage')
            stack.enter_context(patch.object(b.protection,'check_services',side_effect=health))
            result=b.sync_system()
            self.assertTrue(result['changed']);update.assert_called_once();apply.assert_not_called()
            self.assertNotIn('core.connect',[c.args[0] for c in mc.call_args_list])

    def test_old_install_upgrades_even_if_subscription_file_has_not_changed(self):
        saved=state();saved.pop('routingVersion')
        with ExitStack() as stack:
            mc,update,apply=self.sync_fixture(stack,saved)
            stack.enter_context(patch.object(b.protection,'reachable_services',return_value=[],create=True))
            stack.enter_context(patch.object(b.protection,'check_services'))
            result=b.sync_system()
            self.assertTrue(result.get('changed'));update.assert_called_once()
            self.assertEqual(update.call_args.args[0]['routingVersion'],3)
            apply.assert_not_called()

    def test_real_transport_change_is_applied(self):
        with ExitStack() as stack:
            mc,update,apply=self.sync_fixture(stack,state(),generation=2)
            b.system_route.prepare.side_effect=lambda new,*a:new['systemPolicy'].update(port=29992)
            stack.enter_context(patch.object(b.protection,'reachable_services',return_value=[]))
            stack.enter_context(patch.object(b.protection,'check_services'))
            b.sync_system()
            apply.assert_called_once()

    def test_service_regression_still_rolls_back(self):
        saved=state()
        with ExitStack() as stack:
            mc,update,apply=self.sync_fixture(stack,saved,generation=2)
            stack.enter_context(patch.object(b.protection,'reachable_services',return_value=saved['serviceChecks']))
            stack.enter_context(patch.object(b.protection,'check_services',side_effect=RuntimeError('new regression')))
            recover=stack.enter_context(patch.object(b,'recover'))
            with self.assertRaisesRegex(RuntimeError,'new regression'):b.sync_system()
            recover.assert_called_once()

    def test_new_proxy_choice_reuses_existing_automatic_relay(self):
        saved=state()
        with ExitStack() as stack:
            mc,update,apply=self.sync_fixture(stack,saved)
            mc.side_effect=lambda method,*a,**k: {'state':'on'} if method=='appRouting.status' else {'currentWorkspaceID':b.workspace.WORKSPACE}
            def api(path,*a,**k):
                if path=='/configs':return {'mode':'rule'}
                if path=='/rules':return {'rules':[{'type':'Match','proxy':'Replacement'}]}
                if path=='/proxies':return {'proxies':{'Replacement':{'type':'Selector','now':'Node'},'Node':{'type':'Socks5'}}}
                raise AssertionError(path)
            stack.enter_context(patch.object(b,'clash',side_effect=api))
            stack.enter_context(patch.object(b.protection,'check_services'))
            stack.enter_context(patch.object(b,'inspect_target',side_effect=lambda path,choice:{'choice':choice}))
            stack.enter_context(patch.object(b,'import_relays',side_effect=AssertionError('unnecessary new relay')))
            b.apply_route('/Applications/New.app','proxy',simple=True)
            new=update.call_args.args[0]
            self.assertEqual(new['overrides']['/Applications/New.app'],{'choice':'Old','followSubscription':True})

    def test_missing_subscription_proxy_has_visible_warning_but_allows_recovery(self):
        saved=state()
        saved['policyProviderMode']=True
        with ExitStack() as stack:
            mc,update,apply=self.sync_fixture(stack,saved)
            mc.return_value={'state':'on'};mc.side_effect=None
            def api(path,*a,**k):
                return {'/configs':{'mode':'rule'},'/rules':{'rules':[{'type':'Match','proxy':'DIRECT'}]},
                        '/proxies':{'proxies':{'DIRECT':{'type':'Direct'}}}}[path]
            stack.enter_context(patch.object(b,'clash',side_effect=api))
            result=b.menu_snapshot()
            self.assertTrue(result['ready'])
            self.assertIn('恢复自动',result['warning'])
            update.assert_not_called();apply.assert_not_called()


if __name__=='__main__':unittest.main()
