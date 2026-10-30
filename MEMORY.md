# MEMORY

Durable, measured facts.  Re-check a fact before relying on it after a major iTerm2 release.

- **2026-10-30, iTerm2 3.7.6:** an animated GIF background shows only its first frame.  That is why a daemon swaps PNG frames.
- **2026-10-30:** one background swap through the Python API costs about 10 ms.  At 6 fps, iTerm2's CPU rose from about 6% to about 23%, roughly 15 to 20 points.  A frame renders and encodes in about 8 ms at 1200x586.
- **2026-10-30:** iTerm2's `Blend` shows more of the background image as it rises.  0.3 is barely visible behind text and 0.6 is the default.
- **2026-10-30:** a process started by launchd hangs in the iterm2 module's AppleScript cookie request, because it has no Automation permission.  iTerm2's AutoLaunch folder supplies the cookie instead, and the daemon has a 15 s connect watchdog.
- **2026-10-30:** inside tmux, `$ITERM_SESSION_ID` belongs to whichever shell started the tmux server and can name another window.  Events therefore use the tmux client tty (`#{client_tty}`), which the daemon maps to the session's `tty` variable.
- **2026-10-30:** iTerm2 may cache a background image by path, so each session writes a ring of three file names (`frame0..2.png`).
