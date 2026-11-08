# Student lab — Reusable web deployment

**LionTech Academy · With Prof Prince**

## Objectives

- Organize automation into roles and explain their dependencies.
- Configure hosts using defaults, group variables, host variables and extra variables.
- Explain how changed tasks notify handlers.
- Build, install and run a role from an Ansible collection.
- Verify a service, inspect results and demonstrate unchanged reruns.

## Part A — No cloud server needed

1. Follow controller setup in the README. Activate `.venv` and stay in the project root.
2. Run `ansible-playbook -i inventories/local/hosts.ini playbooks/01_facts_variables.yml`. Identify a play variable, fact, registered result and derived variable.
3. Run lab 02 with the same local inventory. Open `/tmp/liontech_ansible_roles_lab/editable.conf`. Explain its environment line, colour and marked block. Run it again. Why is the second recap unchanged?
4. Run lab 07 twice. Find `RUNNING HANDLER` in the first run. Explain why the second unchanged run does not execute the handler.
5. Run lab 07 with `-e 'handler_message=My update'`. Read `application.conf` and `applied.conf` in `/tmp/liontech_ansible_handlers_lab`. Both should contain your message.
6. Build and install the collection using the README commands. Run lab 06. Locate `galaxy-banner.txt` and override `banner_message` with your name.
7. Run lab 03. Explain why its intentional failure is recorded as rescued, and identify which task always executes.
8. Run lab 04. Explain why a pipe requires a shell and why these read-only commands explicitly set `changed_when: false`.

## Part B — Ubuntu website project

1. Work on the disposable VMs provided by your instructor. Replace example inventory IPs and SSH username. Validate SSH access and the host fingerprint.
2. Run `ansible-inventory --graph` and `ansible webservers -m ansible.builtin.ping`.
3. Inspect `site.yml` and trace `web_content → web_server → baseline` dependencies. Predict their execution order.
4. Run `ansible-playbook site.yml -K` and inspect the recap. Do not continue past an unexplained failure.
5. Open the web page at the VM's address on port 8080, if your network rules allow it. The playbook also verifies the page from inside each VM.
6. Run the deployment again. Most tasks should be unchanged. Identify any legitimate package-cache refresh.
7. Change `class_message` through an extra variable. Verify the page changed. Explain why no configuration reload was needed.
8. Add a different `class_accent` for `web2` in its host variables. Deploy and compare the pages.
9. Change the port to 8081 using an extra variable. Observe the validation/reload handlers and verification tasks. Restore the default port by running without the override.
10. Run `playbooks/05_include_role.yml` against the VM inventory. Inspect the fetched deployment report and explain why this lab requires a prior deployment.

## Submit your evidence

- A short explanation of each role and the deployment order.
- One first-run recap and one unchanged repeat-run recap.
- The page showing your customized greeting.
- A handler-trigger example and an example that did not trigger a handler.
- The installed collection name and banner output.
- One workplace scenario where this pattern would replace repetitive manual work.

Never submit passwords, SSH private keys, access tokens or unredacted confidential configuration.
