#!/bin/zsh
# Turn on the camera motion tint and restart the daemon.
script_path="${0:A}"
source "${script_path:h}/lib.zsh"

usage() {
  cat <<USAGE
Usage: ${script_path:t} --run

Turns on camera motion: what moves in front of the Mac's camera is outlined in
gold in the rain.  Writes [camera] enabled = true to
~/.config/iterm2-matrix-rain/config.toml, then restarts the daemon.  The camera
opens only while a Matrix session is animating in the frontmost window, and
nothing is recorded.  Needs the camera packages: install.sh --camera.

Examples:
  ${script_path:t} --run
  alias matrix-camera-on='$script_path --run'
USAGE
}

[[ "$1" == "--run" ]] || { usage; exit 0; }
need_venv
"$MATRIX_RAIN" camera enable >/dev/null || die "matrix-rain camera enable failed."
print "Camera motion enabled."
restart_daemon
