# LionTech Academy | Ansible Roles & Galaxy

**School management project:** [Deploy the LionTech school app to all Ubuntu hosts](docs/SCHOOL_MANAGEMENT.md). Includes a reusable role, student self-registration and portals, project submissions and admin-published results, student/course/enrollment records, an admin login displayed after successful deployment, and a student WhatsApp **Project completed** button. Run `ansible-playbook -i /path/to/your/terraform-inventory.ini school_management.yml`.

**New class project:** [Deploy five named users and verify SSH access across your Terraform-managed fleet](access-management/README.md). Includes Paul, Elvis, John, George and Mercy, optional sudo, prompted input, public-key authentication and local SSH client configuration.

**With Prof Prince · Complete instructor demonstration and student lab project**

Build a reusable Nginx website deployment, understand role dependencies and handlers, then build and install your own Galaxy collection. Six local practice labs work without cloud servers. The full deployment targets disposable Ubuntu VMs.

## Start here

1. Read [the instructor guide](docs/INSTRUCTOR_GUIDE.md) for the teaching sequence and explanations.
2. Follow the setup below and run the local practice labs.
3. Prepare your Ubuntu VM inventory and run `site.yml`.
4. Review [the module examples](docs/MODULE_GUIDE.md) and the complete installed builtin-module catalogue.

The project demonstrates a practical selection of modules. Ansible has many additional collection-specific modules; it is not possible to demonstrate every module for every platform in one project. `docs/BUILTIN_MODULE_CATALOGUE.md` lists the builtin modules in the pinned controller version, including modules outside this lesson.

## 1. Controller setup

Use Linux, macOS, or Ubuntu in WSL on Windows. Run these commands from the project root. Python 3.12 is the tested controller interpreter.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-controller.txt
ansible --version
ansible-config dump --only-changed
```

No cloud credentials or Galaxy account is needed for the local labs. Keep the terminal in this directory so `ansible.cfg` loads. GitHub Actions runs the local checks on push once this repository is published.

## 2. Galaxy: build, install, and consume

```bash
mkdir -p dist
ansible-galaxy collection build collection_src/ansible_collections/liontech/classroom --output-path dist --force
ansible-galaxy collection install -r requirements.yml -p vendor/collections --force
ansible-galaxy collection list -p vendor/collections
ansible-playbook -i inventories/local/hosts.ini playbooks/06_galaxy_collection.yml
```

Expected: `/tmp/liontech_ansible_roles_lab/galaxy-banner.txt` contains the welcome message. `liontech.classroom.banner` means namespace `liontech`, collection `classroom`, role `banner`. The local tarball demonstrates the Galaxy distribution workflow; **nothing is uploaded to public Galaxy**. The collection source is in `collection_src/ansible_collections/liontech/classroom`.

To discover external content, demonstrate `ansible-galaxy role search nginx` and browse https://galaxy.ansible.com/. Explain that external collections may need Python libraries, APIs, credentials and compatible platform versions before installing them. A standard external requirements entry uses `name: namespace.collection` plus a reviewed `version`. Do not replace a tested classroom dependency with an unreviewed role during the live class.

Scaffolding commands (use unused destination names):

```bash
ansible-galaxy role init roles/demo_role
ansible-galaxy collection init demo.learning --init-path /tmp
```

## 3. Run the local labs

```bash
ansible-playbook -i inventories/local/hosts.ini playbooks/01_facts_variables.yml
ansible-playbook -i inventories/local/hosts.ini playbooks/02_files_lab.yml
ansible-playbook -i inventories/local/hosts.ini playbooks/03_control_flow.yml
ansible-playbook -i inventories/local/hosts.ini playbooks/04_commands.yml
ansible-playbook -i inventories/local/hosts.ini playbooks/07_handlers_lab.yml
ansible-playbook -i inventories/local/hosts.ini playbooks/07_handlers_lab.yml
ansible-playbook -i inventories/local/hosts.ini playbooks/07_handlers_lab.yml -e 'handler_message=Updated'
```

Lab 03 deliberately fails a task and rescues it: recap should show `rescued=1`, `failed=0`. The second unchanged handler run should show `changed=0` and no handler execution. The changed message triggers the handler again. These examples use dedicated `/tmp/liontech_*` directories, not real application configuration.

## 4. Deploy to Ubuntu VMs

Prepare one or two disposable Ubuntu 22.04 or 24.04 VMs with SSH access, Python 3, sudo privileges, package-repository access and no conflicting service on TCP 8080. Replace the documentation-only IP addresses and SSH username in `inventories/lab/hosts.ini`. Connect once with `ssh ubuntu@YOUR_IP`, verify the host fingerprint, and configure your SSH key. Do not disable host-key checking.

```bash
ansible-inventory --graph
ansible webservers -m ansible.builtin.ping
ansible-playbook site.yml --syntax-check
ansible-playbook site.yml --list-hosts
ansible-playbook site.yml --list-tasks
ansible-playbook site.yml --check --diff
ansible-playbook site.yml -K
ansible-playbook site.yml -K
ansible-playbook playbooks/05_include_role.yml -K
```

`-K` prompts for the managed-host sudo password; omit it when the lab user has configured passwordless sudo. A fresh-host check run may fail because package installation is only simulated, leaving Nginx unavailable. Run a real deployment before relying on check mode for drift review. Check mode is not a full runtime test. `--diff` may reveal file contents, so do not use it with secrets.

Expected deployment: packages installed, dedicated service user created, HTML rendered under `/var/www/liontech_class`, Nginx serves it on port 8080, config changes notify validation/reload handlers, and the page is checked with `uri` on each VM. Fetched deployment reports appear in `artifacts/`. The second deployment should normally report no changes; package-cache refresh can change after one hour. Open `http://YOUR_VM_IP:8080` if your VM firewall permits access from your computer. The playbook does not change your cloud firewall.

## 5. Running options and variable overrides

```bash
ansible-playbook site.yml --limit web1 -K
ansible-playbook site.yml --list-tags
ansible-playbook site.yml --tags deploy,verify -K
ansible-playbook site.yml --tags audit -K
ansible-playbook site.yml -e 'class_environment=staging' -K
ansible-playbook site.yml -e @examples/override.yml -K
ansible-playbook site.yml -v -K
```

Audit-only runs require a prior deployment. Tags can skip prerequisites; do not run a role fragment on a fresh host. `serial: 1` updates one host at a time. Role dependencies run `baseline → web_server → web_content`, followed by `audit`. The VM role dependency demonstration is static; `05_include_role.yml` shows a dynamic include.

## 6. Validate the project

```bash
bash scripts/test.sh
```

The script builds and installs the collection, validates the inventory and syntax of all playbooks, runs the six local labs twice, and checks `changed=0` and `failed=0` on repeat. It **does not run the privileged Nginx deployment**. See `docs/VALIDATION.md` for the checks actually completed in the preparation environment.

## Project map

| Path | Purpose |
|---|---|
| `site.yml` | Complete Ubuntu website deployment |
| `inventories/lab` | Static inventory, group and per-host variables |
| `roles/baseline` | Packages, group, user, directory |
| `roles/web_server` | Nginx template, service, validation/reload handlers |
| `roles/web_content` | HTML template, copied assets, deployment report |
| `roles/audit` | File checksum, debug output, fetched report |
| `playbooks/01`–`07` | Focused demonstrations and local labs |
| `collection_src` | Buildable collection containing a reusable banner role |
| `scripts/test.sh` | Repeatable validation |
| `docs` | Instructor guide, module explanations, exercises, validation |

## GitHub publishing

The project repository is https://github.com/Lion-Technology-Solutions/ansible-playbooks-role-2026. To work from GitHub, clone it and follow the controller setup above. The commands below are for initializing an empty repository from an unzipped copy; for an existing repository, clone it first and commit changes there. Set your own Git identity before committing:

```bash
git init -b main
git add .
git commit -m "Add LionTech Ansible Roles and Galaxy classroom project"
git remote add origin https://github.com/Lion-Technology-Solutions/ansible-playbooks-role-2026.git
git push -u origin main
```

Authenticate through your normal GitHub credential flow. Never put a token in a repository file. For a nonempty repository, clone it first and integrate these files rather than forcing a push.

## Official references

- Roles: https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_reuse_roles.html
- Handlers: https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_handlers.html
- Variables: https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_variables.html
- Galaxy CLI: https://docs.ansible.com/projects/ansible/latest/cli/ansible-galaxy.html
- Builtin collection: https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/index.html
