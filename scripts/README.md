# scripts

Small zsh helpers for everyday use.  Each one prints help and does nothing when run
without arguments; add `--run` to act.  They work from any folder, so you can alias them.

| Script | What it does |
|---|---|
| `enable-matrix-profile.zsh` | Applies the Matrix profile to the iTerm2 session you run it in, including inside tmux. |
| `enable-camera.zsh` | Writes `[camera] enabled = true` to your config and restarts the daemon. |
| `disable-camera.zsh` | Writes `[camera] enabled = false` to your config and restarts the daemon. |
| `lib.zsh` | Shared helpers the scripts source; not run directly. |
| `autolaunch.applescript` | Source of the iTerm2 AutoLaunch script that `install.sh --autolaunch` compiles and installs. |

The camera scripts need `install.sh --install`, and `enable-camera.zsh` also needs `install.sh --camera`.
They restart the daemon through the installed AutoLaunch script, so iTerm2 supplies the API cookie.

Sample aliases for `~/.zshrc`, with your clone's path:

```zsh
alias matrix-on='~/iterm2-matrix-rain/scripts/enable-matrix-profile.zsh --run'
alias matrix-camera-on='~/iterm2-matrix-rain/scripts/enable-camera.zsh --run'
alias matrix-camera-off='~/iterm2-matrix-rain/scripts/disable-camera.zsh --run'
```
