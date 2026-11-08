# LionTech Academy — Instructor guide

**Ansible Roles, Handlers, Variables & Galaxy · With Prof Prince**

## Outcome and industry scenario

Ask: “Imagine configuring 20,000 servers. How would you make the result consistent, change one setting safely, and prove the service works?” Today's smaller lab uses the same idea: describe the desired state, reuse it through roles, deploy one server at a time, and verify the result. A small classroom lab demonstrates the pattern; large fleets also require capacity planning, staged rollouts and monitoring.

Students finish able to explain a role, trace its dependencies, override defaults, understand a handler, deploy a templated website and install a locally built Galaxy collection.

## Before class — instructor checklist

- Unzip the project and follow controller setup in the README. Use a terminal in the project root.
- Run `bash scripts/test.sh` in the activated virtual environment.
- Prepare Ubuntu lab VMs. Replace example IPs and username, test SSH and sudo, and allow port 8080 from your own computer if browser access is wanted.
- Rehearse `ansible-playbook site.yml -K` on those VMs, then repeat it. Keep a terminal with the rendered page and a browser ready.
- Use a fresh VM snapshot for the live first deployment if you want students to see initial changes. Do not delete shared infrastructure to recreate a demonstration.
- Show the lab recap after each command. Read errors from the first failed task rather than guessing from the final summary.

## Suggested three-hour class

| Minutes | Demonstration | What students should explain |
|---|---|---|
| 0–15 | Welcome, scenario, static inventory, `ping` | Controller versus managed host; ad hoc command versus repeatable playbook |
| 15–35 | `01_facts_variables.yml` and `02_files_lab.yml` | A module's arguments, registered output and an unchanged rerun |
| 35–60 | Tour `site.yml` and `roles/` | Task boundaries, role defaults, files, templates and dependencies |
| 60–85 | Deploy `site.yml` on Ubuntu VMs | Package, user, template and service automation working together |
| 85–100 | Change message versus port; inspect handlers | Content updates need no Nginx reload; config changes do |
| 100–110 | Break and questions | Review terms with a student explanation |
| 110–130 | Overrides, tags, host limit and audit role | Reuse one codebase for several environments |
| 130–150 | Build/install collection and run lab 06 | Role versus collection; local artifact versus public Galaxy publishing |
| 150–165 | Lab 03 error recovery and lab 04 commands | Recovery does not mean ignoring an unexplained error |
| 165–180 | Student challenges and recap | Independent change, verification and explanation |

## 1. Reconnect ad hoc commands to playbooks

Say: “An ad hoc command is useful for a quick action. A playbook records a sequence so we can review and repeat it.”

```bash
ansible webservers -m ansible.builtin.ping
ansible webservers -m ansible.builtin.command -a 'uptime'
ansible-playbook playbooks/01_facts_variables.yml
```

Explain: `ansible` runs an ad hoc action; `webservers` selects an inventory group; `-m` chooses a module; `-a` supplies arguments. `ping` checks Ansible connectivity and Python execution, not ICMP network ping. The sample `command` ad hoc invocation may report changed because arbitrary commands do not inherently describe desired state.

Open `inventories/lab/hosts.ini`. `web1` is an inventory alias, `ansible_host` is the real address, `ansible_user` is the SSH login, and `[webservers]` is a group. Example IPs must be replaced. Then show group variables and the `web1` colour override.

## 2. Explain every core term

| Term | Plain explanation | Example in this project |
|---|---|---|
| Controller | Computer running Ansible | Your Linux/WSL terminal |
| Managed host | Machine Ansible configures | Ubuntu VM named `web1` |
| Inventory | List and grouping of target hosts | `inventories/lab/hosts.ini` |
| Playbook | YAML file containing one or more plays | `site.yml` |
| Play | Connects selected hosts to tasks and settings | `hosts: webservers`, `become: true` |
| Task | One named automation action | Create the website directory |
| Module | Component that performs the action | `ansible.builtin.file` |
| FQCN | Fully qualified collection name for content | `ansible.builtin.copy` |
| Role | Reusable directory of related automation | `roles/web_server` |
| Collection | Package that can contain roles, plugins and modules | `liontech.classroom` |
| Defaults | Easily overridden role variable values | `roles/web_content/defaults/main.yml` |
| Variable | Named input to automation | `class_port: 8080` |
| Fact | Information Ansible discovers about a host | `ansible_facts['distribution']` |
| Register | Save a task's returned result under a variable | `register: class_http` |
| Jinja expression | Inserts or calculates a value inside `{{ ... }}` | `{{ class_title }}` |
| Filter | Transforms a Jinja value | `class_slurp.content \| b64decode` |
| Handler | Special task notified by a changed task | Reload Nginx |
| Notify | Queues a handler after a changed result | `notify: Reload classroom nginx` |
| Listen | Gives handlers a shared notification topic | Both validate and reload listen to the same topic |
| Idempotence | Repeating desired-state automation leaves an already-correct state unchanged | Second file lab reports `changed=0` |
| Become | Run a task using privilege escalation | Install packages with sudo |
| Tag | Label used to select tasks | `--tags deploy,verify` |
| Condition | Rule deciding whether a task runs | `when: not ansible_check_mode` |
| Loop | Repeat an action for a list of items | Topics in lab 01 |
| Check mode | Simulate supported tasks without applying their changes | `--check` |
| Diff mode | Show supported before/after file changes | `--diff` |
| Dependency | Another role that runs before this role | `web_content` needs `web_server` |
| Static import | Reuse expanded during playbook parsing | Roles listed in the play's `roles` section |
| Dynamic include | Reuse loaded when execution reaches the task | `include_role` in lab 05 |

YAML uses indentation to express nesting. A dash starts a list item. A colon separates a key and value. Use spaces, not tabs. Quote strings beginning with a Jinja expression and quote file modes such as `'0644'`.

## 3. Read the role layout aloud

Open `roles/web_server`. Explain each folder: `tasks/main.yml` is the entry point; `handlers/main.yml` holds notified tasks; `defaults/main.yml` holds overridable values; `templates/` contains Jinja-rendered text; `meta/main.yml` describes the role and dependencies. The other role's `files/` folder contains assets copied without Jinja rendering. `vars/` is optional and usually has higher precedence than defaults; this project keeps classroom configuration in inventory and defaults rather than hiding it in role vars.

Trace the chain: `web_content` depends on `web_server`, which depends on `baseline`. Baseline prepares the host. Web server prepares Nginx. Web content supplies the page. Audit inspects evidence. This is useful when multiple teams or environments need consistent infrastructure components.

## 4. Walk through the complete deployment

1. Open `site.yml`: explain hosts, sudo, fact gathering, rolling execution (`serial: 1`) and precondition checks.
2. Open baseline tasks: install Nginx/curl, create a dedicated service group/user and website directory. `state: present` means ensure it exists; it does not request a new installation on every run.
3. Open the web-server template: Jinja inserts the chosen port and root. Show `template`'s source on the controller and destination on the managed host.
4. Open the HTML template and assets. `copy` moves unchanged content; `template` renders variables first. In this project a source directory ending in `/` copies its contents into the target assets directory.
5. Run `ansible-playbook site.yml -K`. Discuss `ok`, `changed`, `skipped`, `failed` and `rescued` from actual output.
6. Show validation: `wait_for` waits for the port; `uri` checks HTTP status and content. A running service is not sufficient proof that the desired page is delivered.
7. Open the page, then the fetched audit report. The loopback URL in `uri` refers to each managed VM because the task runs there.
8. Repeat the deployment. Ask students why most tasks are now `ok`. Mention that package-cache freshness can legitimately change after its cache timeout.

Business application: use the pattern for application rollout, standard configuration, environment-specific settings and an audit trail. It can reduce inconsistent manual work while still requiring review and validation.

## 5. Explain handlers with two contrasting changes

First run the local handler lab twice. A notification is sent only when its task reports a change. Handlers usually execute at the end of a play section, rather than immediately. `meta: flush_handlers` explicitly runs pending handlers at that point. A handler notified several times in the same batch normally runs once; definition order controls execution, not notification order.

Then change only `class_message` using `-e`. The HTML changes; Nginx does not need reloading to serve a new static HTML file. Next override `class_port=8081`: the configuration template changes, notifying validation and reload. The first handler runs `nginx -t`; the second reloads the service. If validation fails, stop and correct the configuration. The previous backup from `template` can help inspect what changed; this project does not implement an automatic production rollback.

```bash
ansible-playbook site.yml -e 'class_message=Automation works' -K
ansible-playbook site.yml -e class_port=8081 -K
ansible-playbook site.yml -K
```

The last command restores the inventory's 8080 setting. Each port change needs a free port. Opening it in a browser also requires your network rules to allow that port.

## 6. Variables and precedence

Show the actual inputs: defaults in a role, group vars for the web-server group, host vars for a per-server accent and command-line extra vars. For these sources, defaults are overridden by inventory configuration, the specific host value overrides the group's same setting, and `-e` takes priority. Ansible has a larger precedence model; avoid claiming this short list covers every source.

`register` stores output from a task; `set_fact` creates a host-scoped variable during a run; gathered facts describe the host. They serve different purposes. Ask students to print one of each from lab 01. Do not put real passwords in examples or commit them to GitHub. Ansible Vault can encrypt sensitive variable files, but using an encrypted file also requires managing its decryption credential separately.

## 7. Galaxy exercise

Say: “A role organizes automation. A collection packages reusable content. Galaxy supplies tooling and a distribution ecosystem.” Build the collection, inspect the resulting tarball filename, install it to the project-specific collection path and run lab 06. Show how the FQCN identifies the installed role.

Explain that public publishing needs an appropriate namespace, release/version discipline and authentication. Building and installing a local archive is different from publishing to Galaxy or pushing source to GitHub.

## 8. Student challenges and answer direction

1. Change the page greeting without editing the template. **Use an extra variable or inventory variable; HTML changes, no reload.**
2. Give `web2` a different accent. **Add `inventories/lab/host_vars/web2.yml` with `class_accent`.**
3. Deploy only to `web1`. **Use `--limit web1`.**
4. Explain why `src` for `copy` is not normally a remote path. **Source defaults to the controller; `remote_src: true` changes that behaviour.**
5. Override the collection welcome message. **Run lab 06 with `-e 'banner_message=My first collection'`.**
6. Prove a handler is not run unnecessarily. **Run lab 07 twice unchanged and inspect the recap.**
7. Predict what happens if Nginx syntax validation fails. **The handler fails, reload does not proceed, and the configuration must be fixed before retrying.**
8. Explain the deliberate error in lab 03. **A failed block enters rescue; always runs afterward; rescued errors are visible in the recap.**

## Troubleshooting during class

| Symptom | Check and action |
|---|---|
| `UNREACHABLE` | IP, SSH user/key, firewall and verified host key; test SSH directly |
| `Missing sudo password` | Use `-K` or prepare authorized passwordless sudo |
| Role not found | Run from project root; inspect `ansible-config dump --only-changed` |
| Collection role not found | Build/install collection; confirm the project collection path and FQCN |
| No hosts matched | Inspect the inventory graph, host pattern and selected inventory |
| Local RPC startup error | Use the pinned controller version in a clean virtual environment |
| Port already in use | Identify the conflicting service or choose an unused high port |
| New configuration fails | Read `nginx -t` output; inspect the generated config and backup |
| Copy source missing | Source belongs on the controller unless `remote_src: true` |
| Fresh check-mode run fails | Missing packages/directories were only simulated; establish the lab first |
| Audit-only run fails | Deploy first; audit expects a website and deployment report |

Close by asking each student to explain one task, one variable source and one handler trigger in their own words.
