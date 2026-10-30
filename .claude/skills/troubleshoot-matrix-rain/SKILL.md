---
name: troubleshoot-matrix-rain
description: Troubleshoot iterm2-matrix-rain. Use when the rain does not animate, is too faint, reacts in the wrong window or not at all under tmux, the daemon will not connect or hangs from launchd, a notification says iTerm2 is newer than built for, the log says an update was refused, or the user wants to stop it and restore the background.
---

# Troubleshoot

Start with `~/Library/Logs/iterm2-matrix-rain.log` and `pgrep -fl 'matrix[-_]rain daemon'`.

| Symptom | Check and fix |
|---|---|
| Nothing animates | Is a daemon running?  Does the log show `animating <id>`?  The session's profile must be named `Matrix` (the `[daemon] profile` setting), and the Python API must be on.  Restart with the AutoLaunch script (see the install skill). |
| Rain too faint | Raise `[daemon] blend` (higher shows more image) or `brightness` per state; see the customize skill. |
| Wrong window or no reaction under tmux | Run `matrix-rain event working -v` in that pane.  It should print the tmux client tty, and that tty should appear in the log's `animating` line.  `$ITERM_SESSION_ID` inside tmux is stale by design and is ignored. |
| `could not connect to iTerm2 within 15 s` | The daemon was started outside iTerm2 with no cookie, for example by launchd.  Use the AutoLaunch script, or run the daemon from an iTerm2 terminal.  Do not use a LaunchAgent. |
| Notification: iTerm2 newer than built for | Informational only; it posts once per new major.minor.  Run `matrix-rain version`, then `matrix-rain update`. |
| `update: refuse, working tree has local changes` (or diverged, or not on the branch) | Updates only fast-forward a clean clone on the default branch.  Ask the user before stashing or committing their changes, or set `[update] auto = false`. |
| Stop and restore | Run `kill <pid>` (SIGTERM); the daemon restores the background and removes its socket.  Confirm by reading the session profile back.  `./install.sh --uninstall` removes everything it installed except the hooks. |
