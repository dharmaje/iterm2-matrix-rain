# Shared helpers for the iterm2-matrix-rain zsh scripts.  Sourced, not run.

REPO="${${(%):-%x}:A:h:h}"
MATRIX_RAIN="$REPO/.venv/bin/matrix-rain"
IT2="/Applications/iTerm.app/Contents/Resources/utilities/it2"
AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm2-matrix-rain.scpt"
LOG="$HOME/Library/Logs/iterm2-matrix-rain.log"

die() { print -u2 -- "$*"; exit 1; }

need_venv() {
  [[ -x "$MATRIX_RAIN" ]] || die "matrix-rain is not installed; run $REPO/install.sh --install first."
}

# Stop the daemon (it restores backgrounds on exit) and start it again the
# way iTerm2 does at launch, so it gets an API cookie without a prompt.
restart_daemon() {
  pkill -f 'matrix[-_]rain daemon' 2>/dev/null
  local i
  for i in {1..20}; do pgrep -f 'matrix[-_]rain daemon' >/dev/null || break; sleep 0.25; done
  if [[ -f "$AUTOLAUNCH" ]]; then
    osascript "$AUTOLAUNCH" >/dev/null || die "Could not start the daemon; see $LOG."
    print "Daemon restarted.  Log: $LOG"
  else
    print "No AutoLaunch script installed.  Start the daemon from an iTerm2 window:"
    print "  $MATRIX_RAIN daemon &"
  fi
}

# Print the iTerm2 session id for the terminal this script runs in.  Inside
# tmux, $ITERM_SESSION_ID can be stale, so match the tmux client's tty instead.
current_session() {
  [[ -x "$IT2" ]] || die "it2 not found at $IT2; iTerm2 3.5 or newer is required."
  local my_tty sid
  if [[ -n "$TMUX" ]]; then my_tty="$(tmux display -p '#{client_tty}')"; else my_tty="$(tty)"; fi
  sid="$("$IT2" session list | tr -d '\\' | awk -F'\t' -v t="$my_tty" '$5 == t {print $1; exit}')"
  [[ -n "$sid" ]] || die "No iTerm2 session found for $my_tty; run this from an iTerm2 window."
  print -r -- "$sid $my_tty"
}

# Print the name of iTerm2's default profile (the one marked with a star).
default_profile() {
  local guid
  guid="$(defaults read com.googlecode.iterm2 "Default Bookmark Guid" 2>/dev/null)"
  [[ -n "$guid" ]] && "$IT2" profile list | awk -F'\t' -v g="$guid" '$1 == g {print $2; exit}'
}
