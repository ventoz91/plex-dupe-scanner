# TODO

## Active bug: `--apply torrent` hangs after printing output header

### Symptom
```
Running script on server...
── Output ───────────────────────────────────────────
            ← hangs here indefinitely, no lines appear
```

### What we've tried
1. **First fix**: replaced sequential `stdout.read()` + `stderr.read()` with a
   background thread draining stderr + streaming stdout line by line.
   → Fixed the stdout/stderr buffer deadlock, but the hang persists.

### What the hang tells us now
With streaming in place, the "── Output" header prints the moment we start
iterating stdout. If nothing follows, the remote process is either:
- Not producing any output at all (first `rm` hasn't started), OR
- The channel is open but the remote process is stalled before the first command

### Leading theories (investigate in this order)

1. **sudo waiting for something we're not sending**
   `sudo -S` reads the password from stdin, but some server configs show a
   "lecture" message or additional prompt that consumes our script bytes before
   bash sees them. Try adding a second `\n` after the password, or switching to
   `sudo -S -k` (force re-auth, skip cached credentials).

2. **`requiretty` in sudoers**
   Some distros require a real TTY for sudo. If `/etc/sudoers` has `requiretty`,
   `sudo -S` without a PTY will silently hang or fail.
   Fix: pass `get_pty=True` to `exec_command`, or add `NOPASSWD` + `!requiretty`
   for the admin user in sudoers.

3. **First rm target is on a slow/hung filesystem**
   If the torrent directory is on NFS, a stalled drive, or a docker volume,
   `rm` can block indefinitely waiting on the filesystem. No code fix for this —
   would need to investigate on the server directly.

4. **Script bytes corrupted in transit**
   The exec_script string passes through stdin after the sudo password. If any
   byte sequence triggers sudo to re-prompt or bash to wait for more input, the
   whole thing stalls. Test by adding `echo HELLO` as the very first line of the
   generated script (manually, before `set -u`) and see if it appears in output.

### Suggested next steps

- [ ] **Quick test**: Manually edit a torrent cleanup script, add `echo HELLO` as
      the first line after `HasBeenChecked=true`, run `--apply torrent`. If
      "HELLO" appears in output, sudo + bash are executing fine and the hang is in
      the first `rm`. If nothing appears, the issue is pre-execution (sudo config).

- [ ] **Check sudoers**: SSH into the server and run `sudo -l` — look for
      `requiretty`. If present, either add `Defaults !requiretty` for the user or
      switch to `get_pty=True` in `exec_command`.

- [ ] **PTY approach**: Change `client.exec_command(remote_cmd, timeout=None)` to
      `client.exec_command(remote_cmd, timeout=None, get_pty=True)`. PTY merges
      stderr into stdout (so drop the stderr thread) and satisfies `requiretty`.
      Downside: PTY output includes terminal control codes that need stripping.

- [ ] **NOPASSWD alternative**: Add to sudoers on server:
      `admin ALL=(ALL) NOPASSWD: /bin/bash`
      Then switch remote_cmd to `sudo -n bash -s` (non-interactive, no password).
      Cleanest solution if the server is trusted.

---

## Backlog / nice to have

- [ ] `--review` should also open the junk report when `scan_junk: true`
      (currently only opens purge + torrent scripts)
- [ ] Dry-run mode doesn't print junk stats if junk scan hasn't run yet —
      add a note in output suggesting `scan_junk: true` when it's not set
- [ ] Consider a `--scan-only` flag that runs the scan and prints reports
      without writing scripts (different from dry-run which skips reports entirely)
