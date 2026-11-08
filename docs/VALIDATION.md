# Validation record

Prepared on November 8, 2026. Tested controller: Linux, Python 3.12, `ansible-core 2.20.4`.

| Check | Result |
|---|---|
| Build `liontech.classroom` version 1.0.0 | Passed |
| Install built artifact with `requirements.yml` | Passed |
| Parse static VM inventory | Passed |
| Syntax check `site.yml` and all seven demonstration playbooks | Passed |
| Execute local facts/variables lab | Passed |
| Execute local file-modules lab and assertions | Passed |
| Execute local control-flow/recovery lab | Passed; intentional failure rescued |
| Execute command/shell examples | Passed |
| Execute installed collection role | Passed |
| Execute local notification/handler lab | Passed; notified handler ran |
| Repeat each of the six local labs | Passed; every recap had `changed=0`, `failed=0` |
| Privileged website deployment on Ubuntu VMs | Not executed here; requires your lab VMs |
| Dynamic audit-role example against deployed VMs | Syntax checked; not executed here |
| GitHub Actions hosted run | Not executed in the preparation environment; inspect the repository Actions tab after publishing |

The preparation environment hit a local RPC startup error with Ansible Core 2.21.6. The project therefore pins the successfully tested 2.20.4 release. Reproduce using the clean virtual-environment instructions in the README.

Run `bash scripts/test.sh` to reproduce the completed checks. The script writes detailed logs under ignored `artifacts/tests/`. A successful local run does not prove SSH access, sudo privileges, Ubuntu package downloads, system service behaviour or your VM firewall configuration. Use the separate VM deployment instructions to verify those before class.
