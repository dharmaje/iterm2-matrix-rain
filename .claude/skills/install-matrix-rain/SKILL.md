---
name: install-matrix-rain
description: Install iterm2-matrix-rain for the user, on macOS with iTerm2 or on Linux (text rain in any terminal). Use when asked to install, set up or enable the Matrix rain, run install.sh, apply the Matrix profile to this iTerm2 session, add the Claude Code hooks to ~/.claude/settings.json, start the daemon with the AutoLaunch script, start the text rain in another tab or a tmux pane, or check that the rain works.
---

# Install

Run `.venv/bin/matrix-rain platform` (or `uname -s` before installing).  On Linux, or macOS outside iTerm2, follow **Linux and other terminals** below; steps 1 to 6 are for macOS with iTerm2.

1. **Preflight and install.**  Run `./install.sh --install --autolaunch` from the repo root and show the user the dependency table.  If it stops, relay its one-line reason; it usually means turning on iTerm2 > Settings > General > Magic > Enable Python API.  On an `Upgrade? [y/N]` prompt, ask the user; pass `--yes` only after they agree.
2. **Find the user's session.**  Inside tmux, run `tmux display -p '#{client_tty}'`, then find that tty in `/Applications/iTerm.app/Contents/Resources/utilities/it2 session list`; the first column is the session id.  Outside tmux, the session id is the part of `$ITERM_SESSION_ID` after the colon.
3. **Apply the profile to that session only.**  Run `it2 profile apply Matrix --session <id>` (full path as above).  Never omit `--session`, because the default is whichever session is active.
4. **Hooks.**  Run `./install.sh --hooks`, show the block, and merge it only after the user agrees.  Back up first with `cp ~/.claude/settings.json ~/.claude/settings.json.bak`.  Then append each event's entries with jq and keep existing hooks, for example:
   ```bash
   ./install.sh --hooks 2>/dev/null > /tmp/mr-hooks.json
   jq -s '.[0] as $a | .[1] as $b | $a * {hooks: ($a.hooks // {} | to_entries + ($b.hooks | to_entries) | group_by(.key) | map({key: .[0].key, value: (map(.value) | add)}) | from_entries)}' \
     ~/.claude/settings.json /tmp/mr-hooks.json > /tmp/mr-settings.json && mv /tmp/mr-settings.json ~/.claude/settings.json
   ```
   Check the file with `jq . ~/.claude/settings.json`.  Hooks apply to new Claude Code sessions.
5. **Start.**  Run `osascript ~/Library/Application\ Support/iTerm2/Scripts/AutoLaunch/iterm2-matrix-rain.scpt`.  It returns at once and does nothing if a daemon is already running.
6. **Verify.**
   - `~/Library/Logs/iterm2-matrix-rain.log` shows `animating <id>` for the user's session within a few seconds.
   - `.venv/bin/matrix-rain event waiting -v` reports `delivered=True`, and the log shows `<id> waiting`.
   - Read the profile back through the Python API (`session.async_get_profile()`) to confirm the name is Matrix and a frame path is set.

To stop it, `kill` the daemon pid; it restores the background on SIGTERM.

## Linux and other terminals

1. **Preflight and install.**  Run `./install.sh --install` and show the user the table.  The only hard requirements are python3 3.10+ and its venv module; a stop usually means `sudo apt install python3-venv`, which the user runs, not you.  It links `~/.local/bin/matrix-rain`.  `--autolaunch` and `--camera` print "macOS only" and change nothing.
2. **Hooks.**  The same as step 4 above; the hook block is identical on both systems.
3. **Start.**  The text rain fills a terminal, so it needs its own tab, window or tmux pane.  Never start it in the terminal Claude Code runs in.  Tell the user to run `matrix-rain run` in a second tab, or inside tmux run `scripts/rain-pane.sh --run`.  To try it yourself, use a private tmux server: `tmux -L mrtest new-session -d -x 120 -y 30 'matrix-rain term --demo'`, then `tmux -L mrtest capture-pane -p` and `tmux -L mrtest kill-server`.
4. **Verify.**  With the rain running, `matrix-rain event waiting -v` reports `delivered=True` and the rain turns amber within a second; `echo '{"hook_event_name":"Stop"}' | hooks/claude-code-hook.sh` turns it cyan, then idle about 4 s later.  `q` quits and restores the terminal.
