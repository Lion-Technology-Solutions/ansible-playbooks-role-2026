# Modules demonstrated — explanation and workplace application

Every name below has the `ansible.builtin` prefix. The file path identifies runnable code. Use `ansible-doc ansible.builtin.MODULE` for full options and platform requirements; options differ between modules.

| Module | Key arguments / return value | Explanation and workplace use | Example file |
|---|---|---|---|
| `ping` | No arguments; returns `pong` | Validate Ansible connectivity before rollout | `playbooks/01_facts_variables.yml` |
| `setup` | Fact gathering; invoked automatically | Discover OS and platform so automation can choose appropriate behaviour | `site.yml` with `gather_facts: true` |
| `debug` | `msg` or `var` | Explain a result or inspect nonsecret values | Labs 01–04; audit role |
| `command` | `argv`; registered `stdout`, `rc` | Execute a program without shell expansion; validate Nginx syntax | Lab 04; web-server handlers |
| `shell` | Command text including a pipe | Use shell features when needed; do not interpolate untrusted input | Lab 04 |
| `set_fact` | Named values | Derive a host-scoped runtime value | Lab 01 |
| `file` | `path`, `state`, `owner`, `mode` | Ensure directories and permissions exist | Baseline; lab 02 |
| `copy` | `src` or `content`, `dest`, `mode` | Distribute files or fixed configuration | Web content; labs 02/07 |
| `lineinfile` | `regexp`, `line`, `path` | Manage one known setting without replacing the entire file | Lab 02 |
| `replace` | `regexp`, `replace`, `path` | Replace a controlled pattern; design the result to remain stable on rerun | Lab 02 |
| `blockinfile` | `block`, `path`, managed markers | Maintain a specific section inside a larger file | Lab 02 |
| `stat` | `path`; `stat.exists`, `stat.checksum` | Verify file presence and inspect metadata | Audit; labs 02/07 |
| `slurp` | `src`; base64 `content` | Read a small remote text file for inspection | Lab 02 |
| `find` | `paths`, `patterns`; `matched`, `files` | Locate matching files for auditing or later processing | Lab 02 |
| `assert` | `that`, optional `fail_msg` | Fail early if preconditions or verification rules are false | Site; labs 02/07 |
| `fetch` | Remote `src`, controller `dest`, `flat` | Gather reports from hosts back to the controller | Audit; lab 02 |
| `include_vars` | Variable file | Load configuration at runtime | Lab 03 |
| `include_tasks` | Task file; condition on include | Dynamically reuse a task sequence | Lab 03 |
| `fail` | `msg` | Stop on a known error or demonstrate recovery | Lab 03 deliberately rescued |
| `include_role` | `name` | Dynamically invoke reusable role tasks | Lab 05 |
| `apt` | `update_cache`, `cache_valid_time` | Refresh Ubuntu package metadata only when needed | Baseline |
| `package` | `name`, `state: present` | Ensure required packages are installed | Baseline |
| `group` | `name`, `system` | Create a dedicated service group | Baseline |
| `user` | `name`, `group`, `shell`, `create_home` | Create an application account with controlled access | Baseline |
| `template` | Jinja `src`, remote `dest` | Generate environment-specific application configuration | Web server; web content |
| `service` | `name`, `state`, `enabled` | Start, enable or reload the application service | Web server and handlers |
| `meta` | `flush_handlers` | Control play execution and run queued handlers before verification | Site; lab 07 |
| `wait_for` | `host`, `port`, `timeout` | Wait until a service port is reachable | Site |
| `uri` | `url`, `status_code`, `return_content` | Verify actual HTTP behaviour, rather than just process state | Site |

## Read an example task

```yaml
- name: Ensure Nginx is running
  ansible.builtin.service:
    name: nginx
    state: started
    enabled: true
```

The first `name` describes the task to a person. The `name` nested under `service` identifies the operating-system service. `state: started` means ensure it is running. `enabled: true` means configure startup at boot. These are separate settings.

```yaml
- name: Verify the page
  ansible.builtin.uri:
    url: 'http://127.0.0.1:{{ class_port }}/'
    status_code: 200
    return_content: true
  register: class_http
```

`200` is the expected HTTP success status. `return_content` asks for the response body; `register` saves the result so a later assertion can inspect it. The module executes on the managed host unless delegation/local connection changes that behaviour.

## Features that are not modules

`hosts`, `become`, `vars`, `register`, `notify`, `listen`, `when`, `loop`, `tags`, `retries`, `until`, `serial`, `block`, `rescue` and `always` are playbook keywords or structural features. Jinja filters such as `b64decode`, `default`, `int` and `bool` transform values. Neither keywords nor filters should be presented as modules.

Prefer a desired-state module over a raw command when one exists. `changed_when: false` is appropriate for genuinely read-only inspection commands here; adding it to a command that changes a system would hide real changes, not make the command idempotent.
