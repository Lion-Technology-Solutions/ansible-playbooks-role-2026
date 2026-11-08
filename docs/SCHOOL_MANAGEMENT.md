# LionTech School Management — Ubuntu deployment

This project adds `school_management.yml` and the reusable `roles/school_management` role to the existing Ansible classroom repository. It installs Flask and Waitress from Ubuntu apt repositories, creates a restricted system user, deploys a systemd service, and verifies HTTP plus database health on every Ubuntu host. Existing website and access-management labs remain separate.

## What the application does

- Public LionTech student portal with a **Project completed — WhatsApp** button.
- Opens `https://wa.me/16473818836?text=Project+completed`; the student taps **Send** in WhatsApp. The app never sends messages automatically or verifies completion.
- LionTech Academy blue/gold branding, website `https://www.liontechacademy.com`, phone `+1 (647) 381-8836`.
- Admin login; add/delete students and courses, enroll/unenroll students, and display counts.
- SQLite persistence across service restarts and repeated Ansible runs. Each host has its **own independent database**; this is not a synchronized cluster.
- Random per-host admin credentials, CSRF protection, escaped HTML and parameterized SQL.

Use synthetic records for this teaching project. The default HTTP listener is for a trusted classroom network. Public use requires a TLS reverse proxy, `school_secure_cookie: true`, login rate limiting and an appropriate backup/access policy before storing personal student data. This role does not install TLS or modify cloud security groups/UFW.

## 1. Prepare the Ubuntu controller

From your existing checkout:

```bash
git pull --ff-only origin main
sudo apt update
sudo apt install -y python3-venv python3-pip git
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-controller.txt
```

The pinned controller requires Python 3.12+ (Ubuntu 24.04 includes it). Ubuntu 22.04 can be a managed host but its default Python 3.10 cannot run this pinned controller. Managed hosts need SSH, Python 3, sudo, systemd and apt access. Ubuntu 22.04/24.04 are the intended lab targets.

## 2. Use the inventory Terraform already generated

Do **not** replace your Terraform-generated inventory with the repository's example IPs. The play targets `all`, gathers facts, deploys to Ubuntu and skips non-Ubuntu hosts. Preserve the connection username and private-key settings Terraform provided, including any `ec2-user` settings for other operating systems.

Replace `/path/to/terraform-generated-inventory.ini` below with your actual generated inventory path:

```bash
ansible-inventory -i /path/to/terraform-generated-inventory.ini --graph
ansible all -i /path/to/terraform-generated-inventory.ini -m ansible.builtin.ping
ansible-playbook -i /path/to/terraform-generated-inventory.ini school_management.yml --syntax-check
ansible-playbook -i /path/to/terraform-generated-inventory.ini school_management.yml --list-hosts
ansible-playbook -i /path/to/terraform-generated-inventory.ini school_management.yml
```

Add `-K` if sudo requires a password. Verify SSH host fingerprints normally before deploying. Use `--limit HOSTNAME` for one host first if desired. Repeat the deployment to check idempotence; apt cache refresh can report a change after an hour. A fresh-host `--check` is not a substitute for installation/runtime verification.

Port **8081** avoids the existing classroom website on 8080. Permit 8081 from your own address in the host/cloud firewall, then visit:

```text
http://YOUR_UBUNTU_HOST_IP:8081
```

The role checks `/health` locally on each host; this does not prove your computer can reach the cloud firewall.

## 3. Sign in as administrator

The student portal and WhatsApp button do not need a login. For school records choose **Administration**, use username `admin`, and retrieve that host's generated password over SSH:

```bash
sudo python3 -c 'import json; print(json.load(open("/etc/liontech-school/secrets.json"))["password"])'
```

Credentials are created once and retained on repeated deployment. Password/session secret live in a root-owned file readable by the service group. Do not commit that file or student databases. To rotate the password, edit its `password` field on the host, then restart `liontech-school`; rotate `secret_key` too to invalidate existing sessions.

## 4. Override branding or port

The defaults are in `roles/school_management/defaults/main.yml`. Set variables in your inventory's group vars, or an external vars file:

```yaml
school_port: 8081
school_brand: LionTech Academy
school_phone: '+1 (647) 381-8836'
school_whatsapp: '16473818836'  # International digits only, no + or spaces
school_website: https://www.liontechacademy.com
school_completion_message: Project completed
school_secure_cookie: false  # Set true when accessed through HTTPS
```

```bash
ansible-playbook -i /path/to/terraform-generated-inventory.ini school_management.yml -e @school-vars.yml
```

## 5. Troubleshoot and back up

On each managed Ubuntu host:

```bash
sudo systemctl status liontech-school
sudo journalctl -u liontech-school -n 60 --no-pager
curl http://127.0.0.1:8081/health
```

Application: `/opt/liontech-school`. Database: `/var/lib/liontech-school/school.sqlite3`. Configuration: `/etc/liontech-school`. For a consistent SQLite snapshot, stop the service, copy the database to your protected backup location, then start it again. Redeployment preserves data and credentials.

## Validation

Run the application functional checks on your controller:

```bash
source .venv/bin/activate
pip install Flask==3.1.2 waitress==3.0.2
python scripts/test_school_management.py
ansible-playbook school_management.yml --syntax-check
```

Functional tests use a temporary SQLite database and check login, CSRF, student/course registration, enrollment, deletion, persistence across clients, escaped output, health and the exact WhatsApp destination/message. GitHub Actions also deploys the actual role twice on Ubuntu 24.04 and checks the repeat recap for `changed=0`.

References: [Ansible roles](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_reuse_roles.html), [systemd service module](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/systemd_service_module.html), [Flask with Waitress](https://flask.palletsprojects.com/en/stable/deploying/waitress/).
