#!/bin/zsh
# Put the repo's Matrix profile back, or restore one of your saved copies.
script_path="${0:A}"
source "${script_path:h}/lib.zsh"

DP_FILE="$HOME/Library/Application Support/iTerm2/DynamicProfiles/iterm2-matrix-rain.json"
BACKUPS="$HOME/.cache/iterm2-matrix-rain/profile-backups"

usage() {
  cat <<USAGE
Usage: ${script_path:t} --run [saved-copy.json]

Replaces the installed Matrix profile with the repo's version, after saving the
current file to $BACKUPS.  Use it when an edit made in iTerm2 Settings breaks
the profile.  Give a saved copy instead to restore it.  iTerm2 reloads the file
within a few seconds; run enable-matrix-profile.zsh --run in an open window to
apply it there.

Saved copies:
$(ls -1t "$BACKUPS" 2>/dev/null | sed 's/^/  /' | head -10)

Examples:
  ${script_path:t} --run
  ${script_path:t} --run "$BACKUPS/iterm2-matrix-rain-20261030-101500.json"
USAGE
}

[[ "$1" == "--run" ]] || { usage; exit 0; }
src="${2:-$REPO/profiles/Matrix.json}"
[[ -f "$src" ]] || die "No such file: $src"
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$src" 2>/dev/null || die "Not valid JSON: $src"

mkdir -p "$BACKUPS" "${DP_FILE:h}"
staged="$(mktemp)" && cp "$src" "$staged" || die "Could not read $src"
if [[ -f "$DP_FILE" ]] && ! cmp -s "$DP_FILE" "$staged"; then
  stamp="$(date +%Y%m%d-%H%M%S)"; saved="$BACKUPS/iterm2-matrix-rain-$stamp.json"; n=1
  while [[ -e "$saved" ]]; do saved="$BACKUPS/iterm2-matrix-rain-$stamp-$n.json"; (( n++ )); done
  cp -p "$DP_FILE" "$saved"
  print "Saved the current profile to $saved"
fi
cp "$staged" "$DP_FILE" && rm -f "${staged:?}"
print "Installed ${src:t} as the Matrix profile."
