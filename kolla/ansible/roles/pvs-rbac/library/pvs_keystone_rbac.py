#!/usr/bin/python
"""Reconcile the two PVS Keystone roles and explicit project group grants."""

INFERENCES = {
    'virtualization_admin': [('virtualization_admin', 'admin')],
    'vm_developer': [('vm_developer', 'member'), ('member', 'reader')],
}


def exactly_one(resources, label):
    resources = list(resources)
    if len(resources) != 1:
        raise ValueError(f'Expected exactly one {label}; found {len(resources)}')
    resource = resources[0]
    if resource.get('is_enabled', resource.get('enabled', True)) is False:
        raise ValueError(f'{label} is disabled')
    return resource


def reconcile(identity, config, check_mode=False):
    """Read/validate everything first, then apply only the planned operations."""
    assignments = config['assignments']
    seen = set()
    for item in assignments:
        name, state = item['role'], item.get('state', 'present')
        if name not in INFERENCES:
            raise ValueError(f'Unsupported Keystone role: {name}')
        if state not in ('present', 'absent'):
            raise ValueError(f'Invalid state: {state}')
        if name in seen:
            raise ValueError(f'Duplicate assignment for role: {name}')
        seen.add(name)
        if not config['groups'].get(name):
            raise ValueError(f'Missing AD group for role: {name}')
        if name == 'virtualization_admin' and state == 'present' and not config['admin_implied_role_ready']:
            raise ValueError('Confirm Keystone [assignment] prohibited_implied_role permits admin before granting virtualization_admin')
        if name == 'vm_developer' and state == 'present' and not config['developer_policy_ready']:
            raise ValueError('vm_developer requires confirmed API policy readiness')
    if not assignments:
        return dict(changed=False, operations=[])

    ad_domain = exactly_one(identity.domains(name=config['ad_domain']), 'AD domain')
    project_domain = exactly_one(identity.domains(name=config['project_domain']), 'project domain')
    project = exactly_one(identity.projects(name=config['project'], domain_id=project_domain['id']), 'project')
    groups = {
        item['role']: exactly_one(
            identity.groups(name=config['groups'][item['role']], domain_id=ad_domain['id']),
            f"group {config['groups'][item['role']]} in {config['ad_domain']}",
        ) for item in assignments
    }
    present = {x['role'] for x in assignments if x.get('state', 'present') == 'present'}
    edges = [edge for name in sorted(present) for edge in INFERENCES[name]]
    required = seen | {name for edge in edges for name in edge}
    roles = {}
    for name in sorted(required):
        # Role implications are global; a domain-specific namesake must not match.
        matches = [r for r in identity.roles(name=name) if not r.get('domain_id')]
        if len(matches) > 1:
            raise ValueError(f'Ambiguous global role: {name}')
        if not matches and name not in INFERENCES:
            raise ValueError(f'Required standard global role is missing: {name}')
        roles[name] = matches[0] if matches else None

    operations = [dict(action='create_role', role=name) for name in sorted(present) if roles[name] is None]
    cached_edges = {}
    for prior, implied in edges:
        if roles[prior] is None:
            existing = set()
        else:
            if prior not in cached_edges:
                response = identity.get(f"/roles/{roles[prior]['id']}/implies")
                response.raise_for_status()
                cached_edges[prior] = {r['id'] for r in response.json()['role_inference']['implies']}
            existing = cached_edges[prior]
        # Do not silently accept an already overprivileged custom PVS role.
        if prior in INFERENCES and existing - {roles[implied]['id']}:
            raise ValueError(f'Unexpected implications on role {prior}; review them before applying')
        if roles[implied]['id'] not in existing:
            operations.append(dict(action='imply', role=prior, implied=implied))

    for item in assignments:
        name, state = item['role'], item.get('state', 'present')
        assigned = roles[name] is not None and identity.validate_group_has_project_role(
            project['id'], groups[name]['id'], roles[name]['id'])
        if state == 'present' and not assigned:
            operations.append(dict(action='grant', role=name, group=groups[name]['id'], project=project['id']))
        elif state == 'absent' and assigned:
            operations.append(dict(action='revoke', role=name, group=groups[name]['id'], project=project['id']))

    if not check_mode:
        for operation in operations:
            name, action = operation['role'], operation['action']
            if action == 'create_role':
                roles[name] = identity.create_role(name=name)
            elif action == 'imply':
                response = identity.put(f"/roles/{roles[name]['id']}/implies/{roles[operation['implied']]['id']}")
                response.raise_for_status()
            elif action == 'grant':
                identity.assign_project_role_to_group(operation['project'], operation['group'], roles[name]['id'])
            else:
                identity.unassign_project_role_from_group(operation['project'], operation['group'], roles[name]['id'])
    return dict(changed=bool(operations), operations=operations)


def main():
    from ansible.module_utils.basic import AnsibleModule

    module = AnsibleModule(argument_spec=dict(
        cloud=dict(type='str', default='kolla-admin'),
        ad_domain=dict(type='str', required=True),
        project=dict(type='str', required=True),
        project_domain=dict(type='str', required=True),
        groups=dict(type='dict', required=True),
        assignments=dict(type='list', elements='dict', required=True, options=dict(
            role=dict(type='str', required=True, choices=list(INFERENCES)),
            state=dict(type='str', default='present', choices=['present', 'absent']),
        )),
        developer_policy_ready=dict(type='bool', default=False),
        admin_implied_role_ready=dict(type='bool', default=False),
    ), supports_check_mode=True)
    try:
        import openstack
        # Credentials/CA come from the generated clouds.yaml; never return them.
        with openstack.connect(cloud=module.params['cloud']) as connection:
            result = reconcile(connection.identity, module.params, module.check_mode)
    except ValueError as error:
        module.fail_json(msg=str(error))
    except ImportError:
        module.fail_json(msg='Install openstacksdk in the Python environment used by this localhost task')
    except Exception as error:
        module.fail_json(msg=f'Keystone request failed ({type(error).__name__}); check connectivity, CA, cloud credentials and API permissions. Earlier successful operations are not rolled back.')
    module.exit_json(**result)


if __name__ == '__main__':
    main()
