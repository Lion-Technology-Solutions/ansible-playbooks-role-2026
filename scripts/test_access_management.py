#!/usr/bin/env python3
"""Isolated, root-only Linux integration test. Never points at the fleet inventory."""
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)


def run(args, **kwargs):
    result = subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)
    return result.stdout


def assert_clean_repeat(output):
    recap = output.split('PLAY RECAP')[-1]
    assert re.search(r'changed=0\b', recap), recap
    assert re.search(r'failed=0\b', recap), recap


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run only in a disposable Linux test environment as root (sudo -E).')
    for binary in ['ansible-playbook', 'ssh-keygen', 'ssh', 'sshd', 'visudo']:
        assert shutil.which(binary), f'Missing prerequisite: {binary}'
    # Unique account names avoid touching the instructor's actual accounts.
    prefix = 'lt' + str(os.getpid())
    names = [prefix + n for n in ['paul', 'elvis', 'john', 'george', 'mercy']]
    for name in names:
        try:
            pwd.getpwnam(name)
        except KeyError:
            continue
        raise AssertionError(f'Test account already exists: {name}')
    base = Path(tempfile.mkdtemp(prefix='liontech-access-test-'))
    base.chmod(0o755)
    group = prefix + 'team'
    daemon = None
    # Close each SSH connection after its task so cleanup never leaves test-user sessions.
    os.environ['ANSIBLE_SSH_ARGS'] = '-C -o ControlMaster=no -o ControlPersist=no'
    try:
        (base / 'homes').mkdir(mode=0o755)
        (base / 'empty-skeleton').mkdir(mode=0o755)
        (base / 'keys').mkdir(mode=0o700)
        Path('/run/sshd').mkdir(mode=0o755, exist_ok=True)
        host_key = base / 'keys/host'
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(host_key)])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        config = base / 'sshd_config'
        config.write_text(
            f'Port {port}\nListenAddress 127.0.0.1\nHostKey {host_key}\n'
            f'PidFile {base}/sshd.pid\nAuthorizedKeysFile .ssh/authorized_keys\n'
            'PasswordAuthentication no\nKbdInteractiveAuthentication no\n'
            'UsePAM yes\nPermitRootLogin no\nStrictModes yes\nSubsystem sftp internal-sftp\n'
            'PubkeyAuthentication no\n'  # The SSH role must enable it correctly.
        )
        users = []
        for i, name in enumerate(names):
            key = base / 'keys' / name
            # Temporary unencrypted test keys: never leave this test directory or enter Git.
            run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)])
            users.append({'name': name, 'public_key_file': str(key) + '.pub',
                          'sudo_enabled': i < 2, 'sudo_nopasswd': i < 2})
        users_file = base / 'users.yml'
        users_file.write_text(json.dumps({'managed_ssh_users': users}))
        settings = {
            'users_file': str(users_file),
            'ssh_accounts_home_base': str(base / 'homes'),
            'ssh_accounts_skeleton': str(base / 'empty-skeleton'),
            'ssh_team_group': group,
            'ssh_team_workspace': str(base / 'team'),
            'ssh_daemon_config': str(config),
            'ssh_daemon_include_directory': str(base / 'sshd_config.d'),
            'ssh_daemon_fragment_validation': f'/usr/sbin/sshd -t -h {host_key} -f %s',
            'ssh_daemon_reload_enabled': False,
        }
        settings_file = base / 'settings.yml'
        settings_file.write_text(json.dumps(settings))
        local_inventory = base / 'local.ini'
        local_inventory.write_text('testhost ansible_connection=local\n')
        deploy = ['ansible-playbook', '-i', str(local_inventory),
                  'access-management/deploy_users.yml', '-e', '@' + str(settings_file)]
        run(deploy)
        assert_clean_repeat(run(deploy))
        print('Five accounts, public keys, workspace, sudoers and SSH configuration: PASS; unchanged repeat: PASS')
        prompt = ['ansible-playbook', '-i', str(local_inventory), 'access-management/deploy_user_prompt.yml',
                  '-e', '@' + str(settings_file), '-e', json.dumps({'student_username': names[-1],
                  'student_public_key_file': users[-1]['public_key_file']})]
        assert_clean_repeat(run(prompt))
        print('Single-user prompt workflow with supplied noninteractive answers: PASS')
        # Verify exact file permissions, public-key presence and absence of copied private keys.
        for user in users:
            home = base / 'homes' / user['name']
            assert home.stat().st_mode & 0o777 == 0o700
            assert (home / '.ssh').stat().st_mode & 0o777 == 0o700
            authorized = home / '.ssh/authorized_keys'
            assert authorized.stat().st_mode & 0o777 == 0o600
            assert Path(user['public_key_file']).read_text().strip() in authorized.read_text()
            private_header = re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----')
            for candidate in home.rglob('*'):
                if candidate.is_file():
                    with candidate.open('rb') as stream:
                        header = stream.read(4096).decode(errors='ignore').lstrip()
                    assert not private_header.match(header), f'Private key found at {candidate}'
        # Build known_hosts from our own generated host key, not unverified ssh-keyscan output.
        known = base / 'known_hosts'
        public = Path(str(host_key) + '.pub').read_text().split()
        known.write_text(f'[127.0.0.1]:{port} {public[0]} {public[1]}\n')
        daemon = subprocess.Popen(['/usr/sbin/sshd', '-D', '-f', str(config), '-E', str(base / 'sshd.log')])
        # Poll the local socket rather than assuming the daemon started immediately.
        for _ in range(50):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    break
            except OSError:
                import time
                time.sleep(0.1)
        assert daemon.poll() is None, (base / 'sshd.log').read_text()
        remote_inventory = base / 'remote.ini'
        # Deliberately invalid bootstrap user: verification play must override inventory identity.
        remote_inventory.write_text(f'testhost ansible_host=127.0.0.1 ansible_port={port} ansible_user=wrong_bootstrap\n')
        for i, name in enumerate(names):
            verification = {
                'login_username': name,
                'login_private_key_file': str(base / 'keys' / name),
                'team_workspace': str(base / 'team'),
                'expected_passwordless_sudo': i < 2,
                'ansible_ssh_common_args': f'-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known} -o IdentitiesOnly=yes',
            }
            args = ['ansible-playbook', '-i', str(remote_inventory),
                    'access-management/verify_user_login.yml', '-e', json.dumps(verification)]
            run(args)
            assert_clean_repeat(run(args))
            print(f'{name}: real SSH identity, shared write, sudo expectation, repeat run: PASS')
        # A different private key must not authenticate as Paul.
        bad = subprocess.run(['ssh', '-F', '/dev/null', '-p', str(port),
                              '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                              '-o', f'UserKnownHostsFile={known}', '-o', 'IdentitiesOnly=yes',
                              '-o', 'IdentityAgent=none', '-i', str(base / 'keys' / names[2]),
                              f'{names[0]}@127.0.0.1', 'true'], capture_output=True)
        assert bad.returncode != 0, 'Incorrect key unexpectedly authenticated'
        print('Wrong-user private key rejected: PASS')
        # Revoke a privilege that this role previously granted, then prove it is gone.
        users[0]['sudo_enabled'] = False
        users[0]['sudo_nopasswd'] = False
        users_file.write_text(json.dumps({'managed_ssh_users': users}))
        run(deploy)
        assert not Path('/etc/sudoers.d/90-liontech-user-' + names[0]).exists()
        assert_clean_repeat(run(deploy))
        print('Role-owned sudo policy revocation and stable rerun: PASS')
        # Reject bootstrap/system identities before any deployment action.
        users_file.write_text(json.dumps({'managed_ssh_users': [{'name': 'root', 'public_key_file': users[0]['public_key_file']}]}))
        rejected = subprocess.run(deploy, text=True, capture_output=True)
        assert rejected.returncode != 0
        assert 'Validate usernames' in rejected.stdout
        print('Unsafe root username rejected: PASS')
    except subprocess.CalledProcessError as exc:
        print(exc.stdout)
        print(exc.stderr)
        if (base / 'sshd.log').exists():
            print((base / 'sshd.log').read_text())
        raise
    finally:
        if daemon:
            daemon.send_signal(signal.SIGTERM)
            daemon.wait(timeout=10)
        for name in names:
            Path('/etc/sudoers.d/90-liontech-user-' + name).unlink(missing_ok=True)
            subprocess.run(['userdel', name], capture_output=True)
            subprocess.run(['groupdel', name], capture_output=True)
        subprocess.run(['groupdel', group], capture_output=True)
        shutil.rmtree(base)


if __name__ == '__main__':
    main()
