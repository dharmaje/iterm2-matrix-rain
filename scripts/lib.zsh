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
