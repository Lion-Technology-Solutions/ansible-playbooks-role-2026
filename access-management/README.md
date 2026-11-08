# LionTech Academy — Named users & SSH authentication across 60 servers

**With Prof Prince · Paul, Elvis, John, George & Mercy**

This project consumes the inventory Terraform already generates. It does not create or rewrite your fleet inventory. Run every command below from the repository root so the project's `ansible.cfg` and role paths load.

## The scenario

Your Ubuntu instances initially accept `ubuntu`, while Amazon Linux instances initially accept `ec2-user`. Terraform inventory must already contain the correct bootstrap SSH username and key settings for each host. Ansible uses that existing access and sudo to create named accounts. Each person can then connect directly as their own account on every targeted host.

**The private key is never deployed to a remote server.** Each student's public key goes into that student's `~/.ssh/authorized_keys` on the servers. Their matching private key remains on their laptop/control node. Copying one private key onto all 60 servers would let a compromised server impersonate that user elsewhere. SSH authentication and sudo authorization are separate controls.

| Login | Initial example access | Key location on the server |
|---|---|---|
| `paul` | SSH, shared workspace, full passwordless sudo for this lab | `/home/paul/.ssh/authorized_keys` |
| `elvis` | SSH, shared workspace, full passwordless sudo for this lab | `/home/elvis/.ssh/authorized_keys` |
| `john` | SSH and shared workspace; no role-managed sudo grant | `/home/john/.ssh/authorized_keys` |
| `george` | SSH and shared workspace; no role-managed sudo grant | `/home/george/.ssh/authorized_keys` |
| `mercy` | SSH and shared workspace; no role-managed sudo grant | `/home/mercy/.ssh/authorized_keys` |

These names represent Paul, Elvis, John, George and Mercy using conventional lowercase Linux usernames. Edit `users.yml` to change the example policy. Paul and Elvis's `NOPASSWD: ALL` policy gives full root access; use it only where that level of administration is intended. Other users are not added to `sudo` or `wheel` by this role. For an existing account, unrelated group memberships or other sudo policies are preserved; `sudo_enabled: false` removes only this role's own grant and is not a blanket revocation of all access.

## 1. Install the controller dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-controller.txt
ansible-galaxy collection install -r access-management/requirements.yml -p vendor/collections
export TF_INVENTORY=/absolute/path/to/your/terraform-generated-inventory.ini
ansible-inventory -i "$TF_INVENTORY" --graph
ansible all -i "$TF_INVENTORY" --list-hosts
ansible all -i "$TF_INVENTORY" -m ansible.builtin.ping
```

Replace only the inventory path. Confirm the target list really is your intended 60 lab servers. Managed hosts need Python 3 and working authorized bootstrap sudo. The integration test targets Ubuntu 24.04; Amazon Linux uses the same RedHat-family account operations and the `sshd` service name, but needs a separate fleet canary run. Modern Ansible also needs a supported managed-node Python version; upgrade older Amazon Linux 2 Python or select an appropriate controller version before class.

The controller needs `ssh-keygen`; the managed hosts need the OpenSSH server already working. Confirm SSH host keys through a trusted source before connecting. Keep host-key checking enabled.

## 2. Collect one PUBLIC key from each person

On Paul's own laptop/control node:

```bash
ssh-keygen -t ed25519 -a 64 -f ~/.ssh/liontech_paul -C paul@liontech-classroom
```

Enter a passphrase. Keep `~/.ssh/liontech_paul` private. Copy **only** `~/.ssh/liontech_paul.pub` to `public_keys/paul.pub` on the Ansible controller. Elvis, John, George and Mercy do the same independently, supplying their own `.pub` files.

The `public_keys` directory contains instructions, not fake keys. Deployment stops if any supplied path/key is missing or invalid. No SSH private keys, account passwords or production addresses are committed. Student key files are ignored by Git, including public keys, to keep personal access material out of the classroom repository.

Open `access-management/users.yml`. Each entry supplies a name, public-key file and optional sudo policy. Key-file paths in this example derive from `playbook_dir`; when using a custom file outside the project, use absolute paths to its real public keys.

## 3. Validate and deploy the five-user file

```bash
ansible-playbook -i "$TF_INVENTORY" access-management/deploy_users.yml --syntax-check
ansible-playbook -i "$TF_INVENTORY" access-management/deploy_users.yml --list-hosts
ansible-playbook -i "$TF_INVENTORY" access-management/deploy_users.yml --limit YOUR_CANARY_ALIAS -K
ansible-playbook -i "$TF_INVENTORY" access-management/deploy_users.yml -K
ansible-playbook -i "$TF_INVENTORY" access-management/deploy_users.yml -K
```

Replace `YOUR_CANARY_ALIAS` with an actual alias from Terraform's generated inventory. `-K` asks for bootstrap sudo password; omit it if your EC2 bootstrap account already has passwordless sudo. Leave the inventory's bootstrap `ansible_user` alone during provisioning.

The playbook processes five hosts per batch by default and stops the rollout after a batch failure. To change the batch size, add `-e rollout_batch_size=10`. Five users across 60 hosts means up to 300 account instances, with the same per-person public key installed on each intended server. A changed-free repeat run demonstrates desired-state automation.

For your own list, copy and edit the variables file, then pass its absolute path:

```bash
cp access-management/users.yml access-management/users.local.yml
ansible-playbook -i "$TF_INVENTORY" access-management/deploy_users.yml -e users_file="$PWD/access-management/users.local.yml" -K
```

## 4. Or prompt for a single student

```bash
ansible-playbook -i "$TF_INVENTORY" access-management/deploy_user_prompt.yml -K
```

It asks for the lowercase username and absolute public `.pub` key path. This workflow grants normal student access, not sudo. For an existing administrator it also removes this role's own sudo grant; use the reviewed users file workflow when preserving administrator access. In noninteractive automation, prompt values can be supplied explicitly:

```bash
ansible-playbook -i "$TF_INVENTORY" access-management/deploy_user_prompt.yml \
  -e student_username=paul -e student_public_key_file="$PWD/public_keys/paul.pub" -K
```

## 5. Authenticate as Paul on every host

On a computer holding Paul's private key, load it into the local SSH agent:

```bash
ssh-add ~/.ssh/liontech_paul
ansible-playbook -i "$TF_INVENTORY" access-management/verify_user_login.yml \
  -e login_username=paul -e login_private_key_file="$HOME/.ssh/liontech_paul" \
  -e expected_passwordless_sudo=true
```

If you do not have an agent session, start one with `ssh-agent bash` first, then run `ssh-add`. Do not give Paul's private key to classmates or put it on a server. Each student runs this verification from their own control node with their own key. The admin does not need to collect private keys to provision accounts.

The verification play overrides the inventory's login identity in memory; it does not edit Terraform output. It runs `ping`, `id -un`, checks the identity, writes a user-owned file into `/srv/liontech/team` and reports `sudo -n true`. For John, George or Mercy, set their username/key path and `-e expected_passwordless_sudo=false`. That checks **passwordless** sudo; a failure does not prove the absence of every possible password-based privilege.

Do not pass a competing `-e ansible_user=ubuntu` or an extra-variable bootstrap private-key value during verification: extra variables outrank the play's settings. The computer must have network access to each target, and agent-unlocked private keys are needed for passphrase-protected keys.

One direct manual check is:

```bash
ssh -i ~/.ssh/liontech_paul paul@SERVER_IP
whoami
id
sudo -n whoami
```

The last command should print `root` for the example Paul/Elvis policies. `whoami` before sudo must print the named account, not `ubuntu` or `ec2-user`.

## 6. Optional local SSH client configuration

```bash
ansible-playbook -i "$TF_INVENTORY" access-management/render_ssh_config.yml \
  -e login_username=paul -e login_private_key_file="$HOME/.ssh/liontech_paul"
ssh -F artifacts/ssh_config_paul liontech-YOUR_INVENTORY_ALIAS
```

This generates one local file containing a `User paul` and an `IdentityFile` reference for all Terraform hosts. It copies no key material. It does not overwrite `~/.ssh/config`; the explicit `-F` chooses this file. For a bastion, supply `-e client_proxy_jump=YOUR_AUTHORIZED_BASTION_ALIAS`. The helper does not translate arbitrary inventory SSH options into OpenSSH directives; preserve any other required network settings manually.

## What the roles do

`managed_accounts` validates names and public keys before mutations, protects root/bootstrap accounts and unexpected existing homes, creates groups/users, installs public keys, sets home/SSH file permissions, writes validated per-user sudoers rules and creates the shared workspace. It preserves additional authorized keys with `exclusive: false`.

`ssh_public_key_auth` validates the existing daemon config, writes a `PubkeyAuthentication yes` fragment, includes it in global scope at the beginning of the main config, validates the assembled file and notifies validation/reload handlers. The handler uses `ssh` on Debian-family hosts and `sshd` on RedHat-family hosts. It does not disable password authentication, change root login, remove MFA requirements or rewrite existing AllowUsers/AllowGroups policies. Such policies may still restrict students; the real login verification is essential. Review restrictions before deployment rather than removing them indiscriminately.

For systems where SSH config is managed elsewhere, use `-e configure_ssh_public_key_auth=false` to provision accounts and keys only. The existing SSH policy must already permit these users and public-key authentication. Do not disable the default reload during a real running-server deployment; the test-only override is used solely to prepare an isolated test daemon before it starts.

## Terms to teach as you run it

| Term | Meaning | Classroom demonstration |
|---|---|---|
| Authentication | Proving an identity | Matching private-key proof to an installed public key |
| Authorization | What that identity may do | Team workspace versus sudo |
| Public key | Key material a server may store to recognize its owner | `authorized_keys` entry |
| Private key | Secret that proves possession; stays with its owner | Local `IdentityFile` |
| Bootstrap account | Existing account used to prepare the machine | `ubuntu` / `ec2-user` |
| Role | Reusable automation with tasks, defaults and templates | `managed_accounts` |
| Loop | Repeat a task for all user entries | Five users, then all target hosts |
| Handler | Action triggered only by a changed task | Validate and reload SSH |
| Variable | Input controlling the same reusable code | User list and sudo switches |
| `register` | Store a module result for a later check | Real username and sudo return code |
| `0700` | Only the owner can access a directory | Private home and `.ssh` |
| `0600` | Only the owner reads/writes the file | `authorized_keys` |
| `0440` | Owner/group read, no write | Sudoers policy |
| `2770` | Owner/group access; new entries inherit the directory group | Team workspace; individual file modes still control file sharing |
| `serial` | Number of hosts processed per rollout batch | Five at a time across the 60 hosts |
| Idempotence | Running again leaves an already-correct state unchanged | Repeat provisioning and inspect recap |

## Suggested 90-minute demonstration

1. **0–10:** Explain authentication versus authorization. Count intended hosts in Terraform's inventory.
2. **10–25:** Generate a passphrase-protected key; identify `.pub` versus private file. Collect only the public key.
3. **25–40:** Read the users file and role. Predict which accounts can sudo.
4. **40–55:** Provision a canary; inspect permissions and validate a real named-user login.
5. **55–65:** Roll out across the intended 60 hosts in batches. Repeat for idempotence.
6. **65–75:** Run the prompted single-user workflow with a separate student username.
7. **75–85:** Generate a local SSH config; let each student verify with their own key.
8. **85–90:** Change an owned sudo policy, rerun, and explain the result. Do not remove existing operational access as an unreviewed classroom exercise.

## Troubleshooting

| Failure | What to inspect |
|---|---|
| Bootstrap unreachable | Terraform host address, bootstrap username/key, SSH/network reachability and host key |
| Invalid public key | Correct `.pub` file, absolute controller path, `ssh-keygen -lf path.pub` |
| Permission denied as Paul | Correct private key on client, agent loaded, installed public key, account spelling and home permissions |
| Key accepted but login restricted | SSH Match/AllowUsers/AllowGroups, PAM access rules, MFA, account expiry and SSH logs |
| SSH validator failed | Read `sshd -t` output and inspect the config; no reload should proceed |
| Sudo fails for an administrator | Correct role-owned sudoers file, `visudo -c`, and intended password policy |
| Normal user still has sudo | Preexisting group memberships or unrelated sudo rules preserved by this role |
| Shared file not readable by others | Directory setgid controls group inheritance; file modes and umask still matter |
| Check mode on fresh hosts fails | User/directory creation was simulated; a subsequent task cannot necessarily use nonexistent accounts |

## Validation and scope

The integration test uses temporary uniquely named users, generated temporary test keys and a separate SSH daemon on a loopback high port. It does not contact Terraform's hosts or modify the running system SSH daemon's configuration. It tests five accounts, public-key login, wrong-key rejection, permissions, shared writes, sudo policy changes and unchanged reruns. It cleans up the temporary users and keys afterward. Run it only on a disposable Linux runner with root privileges and OpenSSH server installed:

```bash
sudo env "PATH=$PATH" python3 scripts/test_access_management.py
```

Tests do not prove your 60 servers are reachable or that their existing SSH/MFA policies allow the accounts. Keep your bootstrap session until canary logins succeed.

Official references: [user](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/user_module.html), [authorized_key](https://docs.ansible.com/projects/ansible/latest/collections/ansible/posix/authorized_key_module.html), [blockinfile](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/blockinfile_module.html).
