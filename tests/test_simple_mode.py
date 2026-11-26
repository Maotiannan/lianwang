"""No live configuration or business services are accessed by these tests."""
from pathlib import Path
from contextlib import nullcontext
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'macos'))
import simple_mode as mode
import backend as b


class SimpleModeTests(unittest.TestCase):
    def setUp(self):
        self.rules = [{'type':'DomainSuffix', 'proxy':'DIRECT'}, {'type':'Match', 'proxy':'Current'}]
        self.proxies = {'Current':{'type':'Selector','now':'Working'},
                        'Working':{'type':'AnyTLS'}, 'DIRECT':{'type':'Direct'}}

    def test_proxy_uses_current_clash_group(self):
        self.assertEqual(mode.route_choice('proxy', self.rules, self.proxies), 'Current')

    def test_changed_subscription_is_followed(self):
        self.rules[-1]['proxy'] = 'Replacement'
        self.proxies['Replacement'] = {'type':'Selector', 'now':'Working'}
        self.assertEqual(mode.route_choice('proxy', self.rules, self.proxies), 'Replacement')

    def test_proxy_does_not_silently_become_direct(self):
        self.proxies['Current']['now'] = 'DIRECT'
        with self.assertRaisesRegex(ValueError, '选好代理线路'):
            mode.route_choice('proxy', self.rules, self.proxies)

    def test_missing_and_circular_groups_are_rejected(self):
        for target in ('Missing', 'Current'):
            with self.subTest(target=target):
                self.proxies['Current']['now'] = target
                with self.assertRaises(ValueError):
                    mode.route_choice('proxy', self.rules, self.proxies)

    def test_load_balancer_must_not_include_direct(self):
        self.proxies['Current'] = {'type':'LoadBalance','all':['Working','DIRECT']}
        with self.assertRaises(ValueError):
            mode.route_choice('proxy', self.rules, self.proxies)

    def test_service_protection_still_rejects_simple_changes(self):
        with patch.object(b,'locked',return_value=nullcontext()), \
             patch.object(b,'read_json',return_value={'protectServices':True}), \
             patch.object(b,'update_cv',side_effect=AssertionError('unexpected routing change')):
            with self.assertRaisesRegex(b.Problem, '承载本机业务服务'):
                b.apply_route('/Applications/Docker.app','direct',simple=True)

    def test_direct_and_auto_do_not_need_a_proxy_selection(self):
        self.assertEqual(mode.route_choice('direct'), 'DIRECT')
        self.assertEqual(mode.route_choice('auto'), 'DEFAULT')

    def test_menu_excludes_services_and_engine_processes(self):
        self.assertTrue(mode.visible_app('/Applications/Example.app'))
        for path, protected in [('/usr/bin/curl',False),('/Applications/Docker.app',True),
                                ('/Applications/MClash.app',False),('/Applications/网络出口.app',False)]:
            self.assertFalse(mode.visible_app(path, protected))

    def test_existing_choices_are_shown_without_migration(self):
        self.assertEqual([mode.display_mode(v) for v in (None,'DIRECT','My old group')],
                         ['auto','direct','proxy'])

    def test_menu_snapshot_does_not_change_configuration(self):
        state = {'overrides':{'/Applications/Example.app':{'choice':'Current'},
                             '/Applications/Docker.app':{'choice':'DIRECT'}}, 'protectServices':True,
                 'policyProviderMode':True}
        with patch.object(b,'read_json',return_value=state), \
             patch.object(b,'clash',return_value={'mode':'rule'}), \
             patch.object(b,'mc',return_value={'state':'on'}), \
             patch.object(b.PAUSED.__class__,'exists',return_value=False), \
             patch.object(b,'save_json',side_effect=AssertionError('unexpected write')), \
             patch.object(b,'update_cv',side_effect=AssertionError('unexpected routing change')):
            value = b.menu_snapshot()
        self.assertEqual(value['apps'],[{'path':'/Applications/Example.app','mode':'proxy'}])
        self.assertTrue(value['ready'])


if __name__ == '__main__':
    unittest.main()
