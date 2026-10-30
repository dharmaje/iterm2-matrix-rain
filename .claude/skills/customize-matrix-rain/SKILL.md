---
name: customize-matrix-rain
description: Customize iterm2-matrix-rain. Use when asked to change rain colors, palette, tint, speed, density, brightness, blend, fps, transition time, glyph size or font, to preview a state as a PNG, to add a new state or map another Claude Code hook event to a state, or to change the opt-in profile name in ~/.config/iterm2-matrix-rain/config.toml.
---

# Customize

All settings live in `matrix_rain/defaults.toml`.  Users override them in `~/.config/iterm2-matrix-rain/config.toml`, which needs only the keys that change.  The daemon reads that file at start, so restart it after edits.

1. **Tune a state.**  Each `[states.<name>]` has `background`, `tail` and `head` as RGB, plus `speed` (cells per second), `density` (share of columns active), `brightness` and `mutation`.  Keep changes subtle: a near-black background and heads below about 240 in every channel.
2. **Preview before applying.**  Run `.venv/bin/matrix-rain preview /tmp/rain-<state>.png --state <state>` and read the PNG.  Show it to the user, then write the config.
3. **Window-level settings.**  `[daemon] blend` controls how much image shows through; higher shows more rain, and 0.6 is the default.  `fps` sets the focused-window rate, `idle_interval` the background rate, `transition` the easing time, and `profile` the opt-in profile name.  When `profile` changes, the user's sessions must use a profile with the new name.
4. **Glyphs.**  `[render]` sets `cell_width`, `cell_height`, `font_size`, `glow`, `mirror` and the `fonts` fallback list.  Run `matrix-rain bench` afterward; a frame should stay well under 100 ms.
5. **Add a state.**  Add `[states.<new>]` to `defaults.toml` (transient states also need `hold` and `next`).  Add the name to `STATES` in `matrix_rain/config.py`, and add it to `TRANSIENT` in `matrix_rain/state.py` if it holds.
6. **Map a hook event.**  Edit the `case` in `hooks/claude-code-hook.sh`, then add a row to `HOOK_CASES` in `matrix_rain/selftest.py`.  If Claude Code does not yet call the hook for that event, add it to the `--hooks` block in `install.sh`.
7. Run `matrix-rain selftest` and update the README state table.
