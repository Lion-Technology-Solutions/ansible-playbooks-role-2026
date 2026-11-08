#!/usr/bin/env python3
"""Exercise the real Ansible preflight without changing any Linux accounts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

with tempfile.TemporaryDirectory(prefix='liontech-access-inputs-') as directory:
    base = Path(directory)
    users = []
    for name in ['paul', 'elvis', 'john', 'george', 'mercy']:
        key = base / name
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
        users.append({'name': name, 'public_key_file': str(key) + '.pub', 'sudo_enabled': False})
    playbook = base / 'preflight.yml'
    playbook.write_text(json.dumps([{'name': 'Validate inputs only', 'hosts': 'localhost',
                                    'gather_facts': False, 'tasks': [{'ansible.builtin.include_role':
                                    {'name': 'managed_accounts', 'tasks_from': 'validate'}}]}]))

    def check(label, payload, valid):
        variables = base / 'input.yml'
        variables.write_text(json.dumps({'managed_ssh_users': payload}))
        result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local',
                                 str(playbook), '-e', '@' + str(variables)], text=True, capture_output=True)
        assert (result.returncode == 0) == valid, label + '\n' + result.stdout + result.stderr
        print(label + ': PASS')

    check('Five valid public-key inputs', users, True)
    check('Duplicate username rejected', [users[0], users[0]], False)
    check('Root account rejected', [dict(users[0], name='root')], False)
    check('Bootstrap account rejected', [dict(users[0], name='ubuntu')], False)
    check('Invalid Linux username rejected', [dict(users[0], name='Paul;whoami')], False)
    check('Private-key path rejected', [dict(users[0], public_key_file=str(base / 'paul'))], False)
    check('Missing public key rejected', [dict(users[0], public_key_file=str(base / 'missing.pub'))], False)
    invalid = base / 'invalid.pub'
    invalid.write_text('ssh-ed25519 definitely_not_valid_base64\n')
    check('Malformed key rejected', [dict(users[0], public_key_file=str(invalid))], False)
    check('String boolean rejected', [dict(users[0], sudo_enabled='true')], False)
    check('Inconsistent sudo settings rejected', [dict(users[0], sudo_nopasswd=True)], False)
