#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist artifacts/tests
ansible-galaxy collection build collection_src/ansible_collections/liontech/classroom --output-path dist --force
ansible-galaxy collection install -r requirements.yml -p vendor/collections --force
ansible-inventory -i inventories/lab/hosts.ini --list > artifacts/tests/inventory.json
ansible-playbook site.yml --syntax-check
for playbook in playbooks/*.yml; do
  ansible-playbook "$playbook" --syntax-check
done
for number in 01 02 03 04 06 07; do
  for playbook in playbooks/"${number}"_*.yml; do
    ansible-playbook -i inventories/local/hosts.ini "$playbook" > "artifacts/tests/${number}-first.log"
    ansible-playbook -i inventories/local/hosts.ini "$playbook" > "artifacts/tests/${number}-repeat.log"
    python3 - "artifacts/tests/${number}-repeat.log" <<'PY'
import pathlib, re, sys
log = pathlib.Path(sys.argv[1]).read_text()
recap = log.split('PLAY RECAP')[-1]
assert re.search(r'changed=0\b', recap), recap
assert re.search(r'failed=0\b', recap), recap
print(sys.argv[1] + ': repeat run passed (changed=0, failed=0)')
PY
  done
done
echo 'Syntax, local execution and repeat-run checks passed. Ubuntu VM deployment remains a separate lab check.'
