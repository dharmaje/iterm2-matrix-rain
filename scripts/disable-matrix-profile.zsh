#!/bin/zsh
# Switch the iTerm2 session this script runs in back to the default profile.
script_path="${0:A}"
source "${script_path:h}/lib.zsh"

usage() {
  cat <<USAGE
Usage: ${script_path:t} --run [profile]

Switches the iTerm2 session you run it from back to iTerm2's default profile (the
one starred in Settings > Profiles), or to the profile you name.  The daemon
notices within a few seconds and removes the rain from that session.  Works
inside tmux.

Examples:
  ${script_path:t} --run
  ${script_path:t} --run "Default"
  alias matrix-off='$script_path --run'
USAGE
}

[[ "$1" == "--run" ]] || { usage; exit 0; }
read -r sid my_tty <<< "$(current_session)" || exit 1
target="${2:-$(default_profile)}"
[[ -n "$target" ]] || die "Could not find iTerm2's default profile; name one: ${script_path:t} --run \"Default\""
[[ "$target" != "Matrix" ]] || die "The default profile is Matrix; name another: ${script_path:t} --run \"Default\""

"$IT2" profile apply "$target" --session "$sid" || die "it2 could not apply the profile \"$target\"."
print "Profile \"$target\" applied to this session ($my_tty)."
