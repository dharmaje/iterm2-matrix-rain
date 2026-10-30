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
[[ -x "$IT2" ]] || die "it2 not found at $IT2; iTerm2 3.5 or newer is required."

if [[ -n "$TMUX" ]]; then
  my_tty="$(tmux display -p '#{client_tty}')"
else
  my_tty="$(tty)"
fi
sid="$("$IT2" session list | tr -d '\\' | awk -F'\t' -v t="$my_tty" '$5 == t {print $1; exit}')"
[[ -n "$sid" ]] || die "No iTerm2 session found for $my_tty; run this from an iTerm2 window."

"$IT2" profile apply Matrix --session "$sid" || die "it2 could not apply the Matrix profile; is it installed (install.sh --install)?"
print "Matrix profile applied to this session ($my_tty)."
