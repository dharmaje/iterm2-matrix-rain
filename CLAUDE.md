# iterm2-matrix-rain

A Matrix theme plus a soft animated rain behind iTerm2 text, which shifts with Claude Code hook events.
A Python daemon renders PNG frames and swaps the session background through the iTerm2 Python API.
On Linux, or any other terminal, `matrix-rain term` draws the same simulation (`sim.py`) full-screen as colored glyphs instead; `matrix-rain run` picks the backend (`platform.py`).

## Commands

- `./install.sh --install --autolaunch`: preflight, `.venv`, profile and AutoLaunch script.  Add `--hooks` to print the hook block.
- Linux: `./install.sh --install` checks python3 and its venv module, then links `~/.local/bin/matrix-rain`; `--autolaunch` and `--camera` say macOS only.
- `.venv/bin/matrix-rain run | term | platform | daemon | event <state> | preview <out.png> | bench [--term] | selftest | update | version | camera enable|disable|status|test`
- `scripts/rain-pane.sh --run` opens the text rain in a tmux split.
- Optional camera motion tint (gold outline, burst on new motion): `./install.sh --camera`; off unless enabled.  The daemon also registers a clickable "Matrix rain camera" status bar toggle.
- Run `matrix-rain selftest` after every change; it needs no iTerm2, and it must pass on macOS and Linux (macOS-only checks print `SKIP`).
- Test `term` in a real pty or a private tmux server (`tmux -L <name>`), never by taking over the user's terminal.

## Safety rules

- Never set a solid-color or bright background frame, even in a test.  Only renderer output.
- Test only in the user's current session (`daemon --session <id>` or `event --tty <tty>`).  Restore its background afterward, then read the profile back to confirm `background_image_location` is empty.
- Show the user the hook block before you edit `~/.claude/settings.json`, and merge it rather than replace the file.
- Never open the camera without the user's go-ahead (`camera test`, or a daemon with `[camera] enabled = true`): it prompts and lights the camera.  Test with `--motion-demo`.
- Never commit `.venv/`, `local/`, `*.egg-info` or anything from `~/.cache/iterm2-matrix-rain`.

## What a push does

Nothing runs on push.  Installed clones fast-forward from `origin`'s default branch when their daemon starts, so `main` must always be releasable: selftest passing, `bench` under 100 ms per frame, README current.

## Load map

| Need to... | Read |
|---|---|
| install for a user (macOS or Linux), apply the profile, add hooks, start and verify | `.claude/skills/install-matrix-rain/SKILL.md` |
| change colors, speed, density, blend, fps, states, the profile name, camera motion tint, text rain settings | `.claude/skills/customize-matrix-rain/SKILL.md` |
| rain missing or faint, wrong window, updates refused, camera not reacting, stopping it, text rain on Linux | `.claude/skills/troubleshoot-matrix-rain/SKILL.md` |
| run the text rain on Linux or another terminal, follow modes, tmux pane, colors | `README.md` § Linux and other terminals, `matrix_rain/term.py` |
| change what `run` picks, or the OS checks in `install.sh` | `matrix_rain/platform.py`, `preflight_linux` / `preflight_darwin` in `install.sh` |
| check a measured fact before relying on it | `MEMORY.md` |
