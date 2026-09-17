import copy
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'kolla/ansible/roles/pvs-rbac/library/pvs_keystone_rbac.py'


def load_module():
    spec = importlib.util.spec_from_file_location('pvs_keystone_rbac', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Identity:
    def __init__(self):
        self.data = {
            'domains': [dict(id='ad', name='AD', is_enabled=True), dict(id='default', name='Default', is_enabled=True)],
            'projects': [dict(id='pvs', name='PVS', domain_id='default', is_enabled=True)],
            'groups': [dict(id='virt', name='PVS_VirtAdmins', domain_id='ad'), dict(id='dev', name='PVS_VMDevelopers', domain_id='ad')],
            'roles': [dict(id=n, name=n, domain_id=None) for n in ('admin', 'member', 'reader')],
        }
        self.edges = set()
        self.prohibited_implied_roles = {'admin'}
        self.grants = {('unrelated-project', 'unrelated-group', 'admin')}
        self.writes = []

    def _list(self, kind, **query):
        return [copy.deepcopy(x) for x in self.data[kind] if all(x.get(k) == v for k, v in query.items())]

    def domains(self, **q): return self._list('domains', **q)
    def projects(self, **q): return self._list('projects', **q)
    def groups(self, **q): return self._list('groups', **q)
    def roles(self, **q): return self._list('roles', **q)

    def create_role(self, name):
        result = dict(id='role-' + name, name=name, domain_id=None)
        self.data['roles'].append(result)
        self.writes.append(('create', name))
        return result

    def get(self, path):
        prior = path.split('/')[2]
        return Response({'role_inference': {'implies': [{'id': target} for source, target in self.edges if source == prior]}})

    def put(self, path):
        _, _, prior, _, implied = path.split('/')
        implied_name = next(r['name'] for r in self.data['roles'] if r['id'] == implied)
        if implied_name in self.prohibited_implied_roles:
            raise RuntimeError('InvalidImpliedRole: prohibited_implied_role')
        self.edges.add((prior, implied))
        self.writes.append(('imply', prior, implied))
        return Response({})

    def validate_group_has_project_role(self, project, group, role):
        return (project, group, role) in self.grants

    def assign_project_role_to_group(self, project, group, role):
        self.grants.add((project, group, role))
        self.writes.append(('grant', project, group, role))

    def unassign_project_role_from_group(self, project, group, role):
        self.grants.remove((project, group, role))
        self.writes.append(('revoke', project, group, role))


class RbacTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MODULE.exists():
            raise AssertionError('The post-deploy RBAC module has not been implemented')
        cls.module = load_module()

    def setUp(self):
        self.api = Identity()
        self.api.prohibited_implied_roles.clear()  # Represents configured Keystone.
        self.config = dict(
            ad_domain='AD', project='PVS', project_domain='Default',
            groups=dict(virtualization_admin='PVS_VirtAdmins', vm_developer='PVS_VMDevelopers', security_admin='PVS_SecurityAudit'),
            assignments=[dict(role='virtualization_admin', state='present'), dict(role='vm_developer', state='present')],
            developer_policy_ready=True,
            admin_implied_role_ready=True,
        )

    def run_rbac(self, check=False):
        return self.module.reconcile(self.api, self.config, check)

    def assert_preflight_error(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            self.run_rbac()
        self.assertEqual([], self.api.writes)

    def test_create_and_repeat(self):
        self.assertTrue(self.run_rbac()['changed'])
        self.assertIn(('pvs', 'virt', 'role-virtualization_admin'), self.api.grants)
        self.assertIn(('pvs', 'dev', 'role-vm_developer'), self.api.grants)
        self.assertEqual({('role-virtualization_admin', 'admin'), ('role-vm_developer', 'member'), ('member', 'reader')}, self.api.edges)
        self.api.writes.clear()
        self.assertFalse(self.run_rbac()['changed'])
        self.assertEqual([], self.api.writes)
        self.assertIn(('unrelated-project', 'unrelated-group', 'admin'), self.api.grants)

    def test_check_has_no_writes_and_predicts_apply(self):
        planned = self.run_rbac(check=True)
        self.assertTrue(planned['changed'])
        self.assertEqual([], self.api.writes)
        self.assertEqual(planned['operations'], self.run_rbac()['operations'])
        self.assertFalse(self.run_rbac(check=True)['changed'])

    def test_explicit_revoke_preserves_roles_and_implications(self):
        self.run_rbac()
        edges = self.api.edges.copy()
        self.config['assignments'][1]['state'] = 'absent'
        self.config['developer_policy_ready'] = False
        self.api.writes.clear()
        self.assertTrue(self.run_rbac()['changed'])
        self.assertEqual([('revoke', 'pvs', 'dev', 'role-vm_developer')], self.api.writes)
        self.assertEqual(edges, self.api.edges)
        self.assertFalse(self.run_rbac()['changed'])

    def test_absent_missing_role_does_not_create(self):
        self.config['assignments'] = [dict(role='vm_developer', state='absent')]
        self.config['developer_policy_ready'] = False
        self.assertFalse(self.run_rbac()['changed'])
        self.assertEqual([], self.api.writes)

    def test_omitted_assignment_is_not_revoked(self):
        self.run_rbac()
        self.config['assignments'] = self.config['assignments'][:1]
        self.assertFalse(self.run_rbac()['changed'])
        self.assertIn(('pvs', 'dev', 'role-vm_developer'), self.api.grants)

    def test_gate_blocks_all_writes(self):
        self.config['developer_policy_ready'] = False
        self.assert_preflight_error('policy')

    def test_admin_inference_gate_blocks_all_writes(self):
        self.config['admin_implied_role_ready'] = False
        self.assert_preflight_error('prohibited_implied_role')

    def test_admin_revoke_needs_no_readiness_acknowledgement(self):
        self.run_rbac()
        self.config['assignments'] = [dict(role='virtualization_admin', state='absent')]
        self.config['admin_implied_role_ready'] = False
        self.api.writes.clear()
        self.run_rbac()
        self.assertEqual([('revoke', 'pvs', 'virt', 'role-virtualization_admin')], self.api.writes)

    def test_wrong_ack_does_not_bypass_keystone_prohibition(self):
        self.api.prohibited_implied_roles = {'admin'}
        with self.assertRaisesRegex(RuntimeError, 'InvalidImpliedRole'):
            self.run_rbac()
        self.assertNotIn(('pvs', 'virt', 'role-virtualization_admin'), self.api.grants)

    def test_unknown_role(self):
        self.config['assignments'].append(dict(role='security_admin', state='present'))
        self.assert_preflight_error('role')

    def test_unknown_state(self):
        self.config['assignments'][0]['state'] = 'presnet'
        self.assert_preflight_error('state')

    def test_duplicate_assignment(self):
        self.config['assignments'].append(self.config['assignments'][0])
        self.assert_preflight_error('Duplicate')

    def test_missing_group(self):
        self.api.data['groups'].pop()
        self.assert_preflight_error('group')

    def test_duplicate_group(self):
        self.api.data['groups'].append(dict(id='other', name='PVS_VMDevelopers', domain_id='ad'))
        self.assert_preflight_error('group')

    def test_same_name_in_other_domain_not_selected(self):
        self.api.data['groups'].insert(0, dict(id='wrong', name='PVS_VMDevelopers', domain_id='default'))
        self.api.data['projects'].insert(0, dict(id='wrong-project', name='PVS', domain_id='ad', is_enabled=True))
        self.run_rbac()
        self.assertIn(('pvs', 'dev', 'role-vm_developer'), self.api.grants)
        self.assertFalse(any('wrong' in str(x) for x in self.api.writes))

    def test_missing_parent_role(self):
        self.api.data['roles'].pop()
        self.assert_preflight_error('reader')

    def test_domain_role_cannot_substitute_global_role(self):
        self.api.data['roles'][0]['domain_id'] = 'ad'
        self.assert_preflight_error('admin')

    def test_disabled_project(self):
        self.api.data['projects'][0]['is_enabled'] = False
        self.assert_preflight_error('disabled')

    def test_disabled_domain(self):
        self.api.data['domains'][0]['is_enabled'] = False
        self.assert_preflight_error('disabled')

    def test_extra_implication_is_not_silently_accepted(self):
        self.api.data['roles'].append(dict(id='custom', name='vm_developer', domain_id=None))
        self.api.edges.add(('custom', 'admin'))
        self.assert_preflight_error('Unexpected')

    def test_empty_assignments_no_api_writes(self):
        self.config['assignments'] = []
        self.assertFalse(self.run_rbac()['changed'])

    def test_api_read_failure_does_not_mutate(self):
        def denied(**kwargs):
            raise RuntimeError('Forbidden')
        self.api.roles = denied
        with self.assertRaisesRegex(RuntimeError, 'Forbidden'):
            self.run_rbac()
        self.assertEqual([], self.api.writes)


if __name__ == '__main__':
    unittest.main()
