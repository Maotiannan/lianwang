"""Routing updates must never fall back to disconnecting a shared engine."""
import copy
from pathlib import Path
import sys
import unittest
from contextlib import nullcontext

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'macos'))
import workspace as w
import backend as b
from test_subscription_switch import transform, fixture
from unittest.mock import patch


class Gateway:
    def __init__(self):
        self.events = []
        self.ready = True
        self.accept = True
        self.capture_revision = 7
        self.revision = 'r1'
        self.changed = True
        self.document = {'entrances': [
            {'id': 'unrelated', 'kind': 'appRouting', 'enabled': False},
            {'id': w.ENTRANCE, 'kind': 'appRouting', 'enabled': True}], 'rules': [],
            'workspaces': [{'id': w.WORKSPACE, 'entranceIDsUpdate': [w.ENTRANCE]}]}

    def __call__(self, method, params=None, **kwargs):
        self.events.append(method)
        if method == 'configuration.snapshot':
            return {'document': copy.deepcopy(self.document), 'configurationRevision': self.revision,
                    'currentWorkspaceID': w.WORKSPACE, 'unifiedConfigurationEnabled': True,
                    'nodes': {'items': [], 'hasMore': False}}
        if method == 'configuration.plan':
            return {'valid': True, 'changed': self.changed}
        if method == 'core.status':
            return {'connected': True, 'controller': 'ready' if self.ready else 'degraded',
                    'state': 'running', 'startedAt': 'same-session'}
        if method == 'appRouting.status':
            return {'state': 'on', 'enabled': True, 'dnsEnabled': False, 'revision': self.capture_revision}
        if method == 'configuration.apply':
            assert params['expectedRevision'] == self.revision
            assert next(e['enabled'] for e in params['document']['entrances'] if e['id']==w.ENTRANCE)
            self.document = copy.deepcopy(params['document'])
            self.revision += 'x'
            return {'configurationRevision': self.revision}
        if method == 'appRouting.setEnabled':
            assert params == {'enabled': True}
            if self.accept:
                self.capture_revision += 1
                self.document['entrances'][0]['enabled'] = True
            return {}
        raise AssertionError('Disruptive or unexpected operation: ' + method)


class LiveRoutesTests(unittest.TestCase):
    def test_mihomo_canonical_rule_names_are_equivalent(self):
        self.assertTrue(b.same_loaded_rule('IP-CIDR6,fe80::/10,DIRECT,no-resolve',
                        {'type':'IPCIDR','payload':'fe80::/10','proxy':'DIRECT'}))
        self.assertTrue(b.same_loaded_rule('GEOIP,CN,DIRECT',
                        {'type':'GeoIP','payload':'cn','proxy':'DIRECT'}))
        self.assertFalse(b.same_loaded_rule('GEOIP,CN,DIRECT',
                         {'type':'GeoIP','payload':'cn','proxy':'Other'}))
        self.assertFalse(b.same_loaded_rule('IP-CIDR6,fe80::/10,DIRECT',
                         {'type':'IPCIDR','payload':'fe80::/16','proxy':'DIRECT'}))

    def test_pending_upgrade_leaves_runtime_untouched(self):
        saved = {'overrides': {'/Applications/A.app': {'choice': 'DIRECT'}}}
        with patch.object(b, 'locked', return_value=nullcontext()), \
             patch.object(b, 'read_json', return_value=saved), \
             patch.object(b.JOURNAL.__class__, 'exists', return_value=False), \
             patch.object(b, 'clash', side_effect=AssertionError('unexpected Clash access')), \
             patch.object(b, 'mc', side_effect=AssertionError('unexpected engine access')), \
             patch.object(b, 'save_json', side_effect=AssertionError('unexpected state write')):
            self.assertEqual(b.sync_system(), {'ok': True, 'upgradePending': True})
            with self.assertRaisesRegex(b.Problem, '初始化'):
                b.apply_route('/Applications/A.app', 'DEFAULT')
            with self.assertRaisesRegex(b.Problem, '初始化'):
                b.reset_all_auto()

    def apply(self, gateway):
        with patch.object(w, 'config_for', side_effect=lambda snap,*a: {
                'document': dict(snap['document'], rules=[{'id':'changed-app'}]),
                'expectedRevision': snap['configurationRevision']}):
            return w.apply(gateway, {'overrides': {}})

    def test_one_app_update_keeps_shared_session(self):
        call = Gateway()
        self.apply(call)
        self.assertIn('appRouting.setEnabled', call.events)
        self.assertNotIn('configuration.workspace.activate', call.events)
        self.assertEqual(call.capture_revision, 8)
        self.assertTrue(call.document['entrances'][0]['enabled'])
        self.assertFalse(call.document['entrances'][1]['enabled'])

    def test_unready_engine_refuses_before_any_mutation(self):
        call = Gateway(); call.ready = False
        with self.assertRaisesRegex(ValueError, '就绪|连接'):
            self.apply(call)
        self.assertNotIn('configuration.apply', call.events)
        self.assertNotIn('appRouting.setEnabled', call.events)

    def test_retired_relay_keeps_existing_tcp_and_udp_listener(self):
        before = {'targets': {'Old': {'key': 'stable', 'port': 29991}}, 'token': 'synthetic-only',
                  'overrides': {'/Applications/A.app': {'choice': 'Old', 'followSubscription': True}}}
        after = copy.deepcopy(before); after['overrides'] = {}
        b.retain_relay_choices(after, before)
        old = transform(fixture(), b.entries(before))
        new = transform(fixture(), b.entries(after))
        self.assertEqual(old['listeners'], new['listeners'])
        self.assertFalse(any('EgressMenu' in rule for rule in new['rules']))
        self.assertIn('IN-NAME,net-egress-menu-stable,Old', new['rules'])

    def test_rejected_live_update_restores_metadata_without_restart(self):
        call = Gateway(); call.accept = False
        before = copy.deepcopy(call.document)
        with self.assertRaisesRegex(ValueError, '确认|生效|更新'):
            self.apply(call)
        self.assertTrue(next(e['enabled'] for e in call.document['entrances'] if e['id']==w.ENTRANCE))
        self.assertEqual(call.document['workspaces'][0]['entranceIDsUpdate'], [w.ENTRANCE])
        self.assertNotIn('configuration.workspace.activate', call.events)

    def test_unchanged_rules_do_not_touch_live_session(self):
        call = Gateway(); call.changed = False
        self.apply(call)
        self.assertNotIn('configuration.apply', call.events)
        self.assertNotIn('appRouting.setEnabled', call.events)

    def test_app_choices_keep_the_shared_clash_configuration_identical(self):
        state = {'targets': {'Old': {'key':'proxy','port':29991,'kind':'group'},
                            'DIRECT': {'key':'direct','port':29992}},
                 'token':'synthetic-only', 'systemPolicy':{'host':'127.0.0.1','port':7897},
                 'overrides':{}}
        b.prepare_policy(state)
        baseline = transform(fixture(), b.entries(state))
        for choice in ['Old','DIRECT',None]:
            state['overrides'] = {} if choice is None else {'/Applications/A.app': {'choice':choice}}
            changed = transform(fixture(), b.entries(state))
            # Only the cold-start fallback changes; shared rules, listeners,
            # URLs and every other setting stay identical.
            for name, provider in changed['rule-providers'].items():
                self.assertEqual(provider['type'], 'http')
                self.assertNotIn('path', provider)
                self.assertEqual(provider['proxy'], 'DIRECT')
                self.assertEqual(provider['interval'], 0)
                provider['payload'] = baseline['rule-providers'][name]['payload']
            self.assertEqual(baseline, changed)

    def test_subscription_replacement_preserves_dynamic_rule_files(self):
        state = {'targets': {'Old': {'key':'proxy','port':29991,'kind':'group'},
                            'DIRECT': {'key':'direct','port':29992}},
                 'token':'synthetic-only', 'overrides':{}}
        b.prepare_policy(state)
        old = transform(fixture(), b.entries(state))
        new = transform(fixture('Replacement'), b.entries(state))
        self.assertEqual(old['rule-providers'],new['rule-providers'])
        self.assertEqual(old['listeners'],new['listeners'])
        self.assertIn('RULE-SET,net-egress-menu-policy-proxy,Replacement', new['rules'])


if __name__ == '__main__':
    unittest.main()
