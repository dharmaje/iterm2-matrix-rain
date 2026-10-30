# MEMORY

Durable, measured facts.  Re-check a fact before relying on it after a major iTerm2 release.

- **2026-10-30, iTerm2 3.7.6:** an animated GIF background shows only its first frame.  That is why a daemon swaps PNG frames.
- **2026-10-30:** one background swap through the Python API costs about 10 ms.  At 6 fps, iTerm2's CPU rose from about 6% to about 23%, roughly 15 to 20 points.  A frame renders and encodes in about 8 ms at 1200x586.
- **2026-10-30:** iTerm2's `Blend` shows more of the background image as it rises.  0.3 is barely visible behind text and 0.6 is the default.
- **2026-10-30:** a process started by launchd hangs in the iterm2 module's AppleScript cookie request, because it has no Automation permission.  iTerm2's AutoLaunch folder supplies the cookie instead, and the daemon has a 15 s connect watchdog.
- **2026-10-30:** inside tmux, `$ITERM_SESSION_ID` belongs to whichever shell started the tmux server and can name another window.  Events therefore use the tmux client tty (`#{client_tty}`), which the daemon maps to the session's `tty` variable.
- **2026-10-30:** iTerm2 may cache a background image by path, so each session writes a ring of three file names (`frame0..2.png`).
- **2026-10-30:** with the camera motion tint on, a 1200x586 frame takes about 12 ms (about 8 ms without), including about 0.6 ms of 320x240 motion processing.  Measured with the synthetic source; the real camera was not opened while building it.
- **2026-10-30:** with the camera off, or with zero motion heat, the renderer takes the old code path and its frames are byte-identical to the frames from before the camera feature.  A selftest guards this.
- **2026-10-30, live camera test:** the default camera delivered 30 fps despite a 10 fps setting, until the device's frame duration was set; frames are now also dropped down to `fps`.  For about 3 s after opening, raw motion read 81%, 73% and 23% while the background settled, so a 1.5 s `warmup` gate holds the tint.  A person moving a little gave 1 to 6% motion, peaking at 12.5%, at threshold 24.
- **2026-10-30:** pyobjc 13.0 ships Python 3.15 wheels for AVFoundation, Quartz, CoreMedia and libdispatch.  The camera reads plane 0 of a 420f buffer, which is full-range luma, so no color conversion is needed.
- **2026-10-30:** a session that the daemon has written per-session settings to (its background image and blend) no longer follows later edits to the Matrix Dynamic Profile.  Re-apply the profile (`it2 profile apply Matrix --session <id>`) to pick up changes such as the status bar.
- **2026-10-30:** a script's status bar component cannot be pre-placed in a profile's `Status Bar Layout` in a verifiable way, so users drag it in.  iTerm2 calls the click handler as RPC `__us_epel_iterm2_matrix_rain_camera__on_click(session_id:)`, which `iterm2.async_invoke_function` can call for testing.  Status bar position is global, not per profile.
- **2026-10-30:** a motion mask taken only against the slow background leaves a ghost where someone just stood.  Gating it by a dilated frame-to-frame difference removes that.  Pillow rank filters cost O(k²), so the outline's close, open and erode run at 160x120 (about 1.2 ms per camera frame).
