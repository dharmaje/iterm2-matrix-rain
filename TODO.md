# TODO

- Confirm that macOS shows the notifications.  `osascript display notification` ran, but whether a banner appeared was not observed.
- Verify Python 3.10 (tomli path); only 3.14 and 3.15 were run.
- Run `./install.sh --install --autolaunch` on a real Mac after the Linux branch was added to `install.sh`.  The Darwin preflight was only run on Linux with stubbed `uname`, `sw_vers`, PlistBuddy, `defaults` and `osacompile`.
- Confirm that GNOME shows the `notify-send` banner for an iTerm2 or update notice; it is only reached by `matrix-rain update` on Linux.
- Measure the text rain's bytes per frame and CPU in Ptyxis itself (live), and over SSH.
