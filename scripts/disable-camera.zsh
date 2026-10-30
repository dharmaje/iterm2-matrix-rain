#!/bin/zsh
# Turn off the camera motion tint and restart the daemon.
script_path="${0:A}"
source "${script_path:h}/lib.zsh"

usage() {
  cat <<USAGE
Usage: ${script_path:t} --run

Turns off camera motion: writes [camera] enabled = false to
~/.config/iterm2-matrix-rain/config.toml and restarts the daemon, which closes
the camera.  The rain keeps running.

Examples:
  ${script_path:t} --run
  alias matrix-camera-off='$script_path --run'
USAGE
}

[[ "$1" == "--run" ]] || { usage; exit 0; }
need_venv
"$MATRIX_RAIN" camera disable >/dev/null || die "matrix-rain camera disable failed."
print "Camera motion disabled."
restart_daemon
