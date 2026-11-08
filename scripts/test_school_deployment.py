#!/usr/bin/env python3
"""Credential-output integration check for a disposable, already deployed Ubuntu runner.

Capture requested password output for assertions, without copying it into public CI logs.
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
credentials = json.loads(subprocess.check_output(
    ['sudo', 'cat', '/etc/liontech-school/secrets.json'], text=True))
command = ['ansible-playbook', '-i', 'localhost,', '-c', 'local', 'school_management.yml']
for flags, shows_password in [([], True), (['-e', 'school_show_admin_password=false'], False), (['--check'], False)]:
    result = subprocess.run(command + flags, cwd=ROOT, text=True, capture_output=True)
    # Do not attach captured output on failure; it contains the requested password.
    assert result.returncode == 0, 'Deployment credential-output check failed; inspect the role without exposing credentials.'
    assert (credentials['password'] in result.stdout) == shows_password, 'Unexpected password display behavior'
    assert credentials['secret_key'] not in result.stdout, 'Session signing key must never be displayed'
    assert re.search(r'changed=0\b', result.stdout.split('PLAY RECAP')[-1]), 'Unchanged deployment must be idempotent'
    if shows_password:
        assert 'Admin username: admin' in result.stdout
        assert 'Deployment successful on localhost' in result.stdout
print('Password displayed after successful deployment; hidden when disabled and in check mode. Session key stays private.')
