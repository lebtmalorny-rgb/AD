"""Optional integration check: real SDK + Ansible against loopback fake Keystone.

PVS_ANSIBLE_PLAYBOOK=/path/to/ansible-playbook /sdk/python -m unittest discover -s tests -v
No real cloud credentials are loaded or sent.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from test_rbac import Identity

ANSIBLE = os.environ.get('PVS_ANSIBLE_PLAYBOOK')


@unittest.skipUnless(ANSIBLE and importlib.util.find_spec('openstack'), 'set PVS_ANSIBLE_PLAYBOOK and install openstacksdk for HTTP integration')
class SdkHttpTests(unittest.TestCase):
    def test_role_lifecycle_through_ansible_and_sdk(self):
        api = Identity()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass

            def respond(self, code, data=None):
                body = json.dumps(data).encode() if data is not None else b''
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)

            def route(self):
                url = urlsplit(self.path)
                path = url.path.rstrip('/')
                requests.append((self.command, self.path))
                if self.command == 'GET' and path in ('', '/v3'):
                    return self.respond(200, {'version': {'id': 'v3.14', 'status': 'stable', 'links': [{'rel': 'self', 'href': endpoint + '/'}]}})
                match = re.fullmatch(r'/v3/(domains|groups|projects|roles)', path)
                if match and self.command == 'GET':
                    kind = match[1]
                    query = {key: value[0] for key, value in parse_qs(url.query).items()}
                    items = api._list(kind, **query)
                    # Keystone wire spelling differs from SDK Resource.is_enabled.
                    for item in items:
                        if 'is_enabled' in item:
                            item['enabled'] = item.pop('is_enabled')
                    return self.respond(200, {kind: items, 'links': {'next': None}})
                if path == '/v3/roles' and self.command == 'POST':
                    data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    return self.respond(201, {'role': api.create_role(data['role']['name'])})
                match = re.fullmatch(r'/v3/roles/([^/]+)/implies(?:/([^/]+))?', path)
                if match:
                    if self.command == 'GET' and not match[2]:
                        return self.respond(200, api.get(path[3:]).json())
                    if self.command == 'PUT' and match[2]:
                        try:
                            api.put(path[3:])
                        except RuntimeError:
                            return self.respond(400, {'error': {'message': 'InvalidImpliedRole', 'code': 400}})
                        return self.respond(201, {})
                match = re.fullmatch(r'/v3/projects/([^/]+)/groups/([^/]+)/roles/([^/]+)', path)
                if match:
                    args = match.groups()
                    if self.command == 'HEAD':
                        return self.respond(204 if api.validate_group_has_project_role(*args) else 404)
                    if self.command == 'PUT':
                        api.assign_project_role_to_group(*args)
                        return self.respond(204)
                    if self.command == 'DELETE':
                        api.unassign_project_role_from_group(*args)
                        return self.respond(204)
                self.respond(404, {'error': {'message': 'Unhandled test endpoint', 'code': 404}})

            do_GET = route
            do_POST = route
            do_PUT = route
            do_HEAD = route
            do_DELETE = route

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        endpoint = f'http://127.0.0.1:{server.server_port}/v3'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='pvs-rbac-http-') as temp:
                root = Path(temp)
                (root / 'ansible.cfg').write_text('[defaults]\n')
                role = Path(__file__).resolve().parents[1] / 'kolla/ansible/roles/pvs-rbac'
                (root / 'clouds.yaml').write_text(json.dumps({'clouds': {'kolla-admin': {
                    'auth_type': 'none', 'identity_endpoint_override': endpoint,
                    'identity_api_version': '3',
                }}}))
                (root / 'play.yml').write_text(json.dumps([{
                    'hosts': 'localhost', 'gather_facts': False,
                    'roles': [str(role)],
                }]))
                variables = dict(node_config=temp,
                                 pvs_rbac_enabled=False, pvs_vm_developer_policy_ready=False,
                                 pvs_admin_implied_role_ready=False)
                # Clear OS_* from the test process: never contact an operator's cloud.
                env = {k: v for k, v in os.environ.items() if not k.startswith('OS_')}
                env.update(ANSIBLE_LOCAL_TEMP=str(root / 'local'), ANSIBLE_REMOTE_TEMP=str(root / 'remote'),
                           ANSIBLE_NOCOLOR='1', ANSIBLE_CONFIG=str(root / 'ansible.cfg'))

                def run(check=False, expected=0):
                    (root / 'vars.json').write_text(json.dumps(variables))
                    cmd = [ANSIBLE, '-i', 'localhost,', '-c', 'local', str(root / 'play.yml'), '-e', '@' + str(root / 'vars.json')]
                    if check: cmd.append('--check')
                    result = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=60)
                    self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
                    return result.stdout

                run()
                self.assertEqual([], requests, 'Disabled role contacted Keystone')
                variables['pvs_rbac_enabled'] = True
                self.assertIn('prohibited_implied_role', run(expected=2))
                self.assertEqual([], api.writes)
                variables['pvs_admin_implied_role_ready'] = True
                api.prohibited_implied_roles.clear()  # Operator applied Keystone override.
                self.assertIn('policy', run(expected=2))
                self.assertEqual([], api.writes)
                variables['pvs_vm_developer_policy_ready'] = True
                self.assertIn('changed=1', run(check=True))
                self.assertEqual([], api.writes)
                self.assertIn('changed=1', run())
                self.assertEqual(7, len(api.writes))
                api.writes.clear()
                self.assertIn('changed=0', run())
                self.assertEqual([], api.writes)
                variables['pvs_keystone_assignments'] = [dict(role='vm_developer', state='absent')]
                variables['pvs_vm_developer_policy_ready'] = False
                self.assertIn('changed=1', run())
                self.assertEqual([('revoke', 'pvs', 'dev', 'role-vm_developer')], api.writes)
                self.assertIn(('pvs', 'virt', 'role-virtualization_admin'), api.grants)
                self.assertIn(('unrelated-project', 'unrelated-group', 'admin'), api.grants)
                self.assertIn('changed=0', run())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
