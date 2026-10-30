# iterm2-matrix-rain

A Matrix theme plus a soft animated rain behind iTerm2 text, which shifts with Claude Code hook events.
A Python daemon renders PNG frames and swaps the session background through the iTerm2 Python API.

## Commands

- `./install.sh --install --autolaunch`: preflight, `.venv`, profile and AutoLaunch script.  Add `--hooks` to print the hook block.
- `.venv/bin/matrix-rain daemon | event <state> | preview <out.png> | bench | selftest | update | version`
- Run `matrix-rain selftest` after every change; it needs no iTerm2.

## Safety rules

- Never set a solid-color or bright background frame, even in a test.  Only renderer output.
- Test only in the user's current session (`daemon --session <id>` or `event --tty <tty>`).  Restore its background afterward, then read the profile back to confirm `background_image_location` is empty.
- Show the user the hook block before you edit `~/.claude/settings.json`, and merge it rather than replace the file.
- Never commit `.venv/`, `local/`, `*.egg-info` or anything from `~/.cache/iterm2-matrix-rain`.

## What a push does

Nothing runs on push.  Installed clones fast-forward from `origin`'s default branch when their daemon starts, so `main` must always be releasable: selftest passing, `bench` under 100 ms per frame, README current.

## Load map

| Need to... | Read |
|---|---|
| install for a user, apply the profile, add hooks, start and verify | `.claude/skills/install-matrix-rain/SKILL.md` |
| change colors, speed, density, blend, fps, states, the profile name | `.claude/skills/customize-matrix-rain/SKILL.md` |
| rain missing or faint, wrong window, updates refused, stopping it | `.claude/skills/troubleshoot-matrix-rain/SKILL.md` |
| check a measured fact before relying on it | `MEMORY.md` |
