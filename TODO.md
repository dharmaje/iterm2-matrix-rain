# TODO

- Verify the AutoLaunch script on a real iTerm2 restart: that iTerm2 runs a compiled `.scpt` from `Scripts/AutoLaunch` at start-up, and that the daemon exits 0 when iTerm2 quits (only a client-side close was tested).
- Verify the self-update against the real public remote once `origin` exists; so far it was tested only on throwaway local repositories.
- Confirm that macOS shows the notifications.  `osascript display notification` ran, but whether a banner appeared was not observed.
- Verify Python 3.10 (tomli path); only 3.14 and 3.15 were run.
