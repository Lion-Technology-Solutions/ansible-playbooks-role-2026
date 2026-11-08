# Public keys only

Place each student's public key here as `paul.pub`, `elvis.pub`, `john.pub`, `george.pub` and `mercy.pub`. Actual key files are ignored by Git. No placeholder keys are supplied: deployment intentionally stops until valid public keys exist.

Each student generates their own key on their laptop/control node:

```bash
ssh-keygen -t ed25519 -a 64 -f ~/.ssh/liontech_paul -C paul@liontech-classroom
```

Use a passphrase and load the private key into your local SSH agent. Give the instructor **only** `~/.ssh/liontech_paul.pub`. The private file `~/.ssh/liontech_paul` stays with Paul. Do not copy it to the servers or GitHub. Repeat independently for other students.
