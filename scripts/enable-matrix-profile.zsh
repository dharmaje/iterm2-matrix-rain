#!/bin/zsh
# Switch the iTerm2 session this script runs in to the Matrix profile.
script_path="${0:A}"
source "${script_path:h}/lib.zsh"

usage() {
  cat <<USAGE
Usage: ${script_path:t} --run

Switches the iTerm2 session you run it from to the Matrix profile: Matrix colors,
the status bar, and the rain (the daemon animates sessions on that profile).
Works inside tmux by finding the iTerm2 session that shows this tmux client.

Examples:
  ${script_path:t} --run
  alias matrix-on='$script_path --run'
USAGE
}

[[ "$1" == "--run" ]] || { usage; exit 0; }
read -r sid my_tty <<< "$(current_session)" || exit 1

"$IT2" profile apply Matrix --session "$sid" || die "it2 could not apply the Matrix profile; is it installed (install.sh --install)?"
print "Matrix profile applied to this session ($my_tty)."
