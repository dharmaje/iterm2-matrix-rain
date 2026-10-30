#!/usr/bin/env bash
# iterm2-matrix-rain installer.  Run with no arguments for help.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
ITERM_APP="${MATRIX_RAIN_ITERM_APP:-/Applications/iTerm.app}"
PROFILE_DIR="$HOME/Library/Application Support/iTerm2/DynamicProfiles"
PROFILE_DST="$PROFILE_DIR/iterm2-matrix-rain.json"
AUTOLAUNCH_DIR="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
AUTOLAUNCH="$AUTOLAUNCH_DIR/iterm2-matrix-rain.scpt"
OLD_LABEL="us.epel.iterm2-matrix-rain"
OLD_PLIST="$HOME/Library/LaunchAgents/$OLD_LABEL.plist"
LOG="$HOME/Library/Logs/iterm2-matrix-rain.log"
HOOK="$REPO/hooks/claude-code-hook.sh"
MIN_ITERM2="3.5"
MIN_PYTHON="3.10"
ASSUME_YES=0

usage() {
  cat <<USAGE
Usage: ./install.sh <option> [<option> ...]

  --install        Check dependencies, then create .venv, install requirements and the
                   matrix-rain command, and copy the Matrix Dynamic Profile into iTerm2.
  --autolaunch     Install an iTerm2 AutoLaunch script that starts the daemon each time
                   iTerm2 starts (it fetches the API cookie from iTerm2 for the daemon).
  --hooks          Print the JSON to merge into ~/.claude/settings.json (edits nothing).
  --uninstall      Stop the daemon and remove the AutoLaunch script (and any LaunchAgent
                   from older versions), the Dynamic Profile, the cache folder and .venv.
                   Your Claude Code hooks are left for you to remove.
  --camera         Install the optional camera packages (requirements-camera.txt) into
                   .venv for the camera motion tint, then print how to enable it.
  --yes            Answer yes to upgrade prompts (for non-interactive runs).

Before changing anything, --install and --autolaunch check macOS, iTerm2 $MIN_ITERM2+,
python3 $MIN_PYTHON+, osacompile/osascript and that iTerm2's Python API is on, and print a
table.  A missing or too-old dependency stops the install.  Upgrading an already
installed Python package asks first; answering no stops with nothing changed.

Set PYTHON=/path/to/python3 to choose the interpreter.

Examples:
  ./install.sh --install
  ./install.sh --install --autolaunch
  ./install.sh --install --yes
  ./install.sh --camera
  ./install.sh --hooks
  ./install.sh --uninstall
USAGE
}

die() { echo "install.sh: $*" >&2; exit 1; }

# The Matrix profile is rewritable, so iTerm2 saves Settings edits into the
# installed file.  Keep a copy of an edited file before replacing it.  The copy
# goes outside DynamicProfiles: iTerm2 loads every file there, and a second
# profile with the same Guid would clash.
PROFILE_BACKUPS="$HOME/.cache/iterm2-matrix-rain/profile-backups"
backup_profile() {
  [[ -f "$PROFILE_DST" ]] || return 0
  cmp -s "$PROFILE_DST" "$REPO/profiles/Matrix.json" && return 0
  mkdir -p "$PROFILE_BACKUPS"
  local stamp b n=1
  stamp="$(date +%Y%m%d-%H%M%S)"; b="$PROFILE_BACKUPS/iterm2-matrix-rain-$stamp.json"
  while [[ -e "$b" ]]; do b="$PROFILE_BACKUPS/iterm2-matrix-rain-$stamp-$n.json"; n=$((n + 1)); done
  cp -p "$PROFILE_DST" "$b"
  echo "Your edited Matrix profile was saved to $b"
}

# ver_ge FOUND NEEDED: true when FOUND >= NEEDED.  Compares dotted numbers and
# ignores anything after them ("3.7.6beta1" is 3.7.6).
ver_ge() {
  local a b
  a="$(printf '%s' "$1" | sed -E 's/^[^0-9]*([0-9]+(\.[0-9]+)*).*/\1/')"
  b="$(printf '%s' "$2" | sed -E 's/^[^0-9]*([0-9]+(\.[0-9]+)*).*/\1/')"
  [[ "$a" =~ ^[0-9] ]] || return 1
  awk -v a="$a" -v b="$b" 'BEGIN {
    n = split(a, x, "."); m = split(b, y, ".");
    k = (n > m) ? n : m;
    for (i = 1; i <= k; i++) { xi = (i <= n) ? x[i] + 0 : 0; yi = (i <= m) ? y[i] + 0 : 0;
      if (xi > yi) exit 0; if (xi < yi) exit 1 }
    exit 0 }'
}

ROWS=()
row() { ROWS+=("$1|$2|$3|$4"); }
print_rows() {
  printf '%-30s %-12s %-14s %s\n' "Dependency" "Required" "Found" "Status"
  local r IFS='|'
  for r in "${ROWS[@]}"; do
    set -- $r
    printf '%-30s %-12s %-14s %s\n' "$1" "$2" "$3" "$4"
  done
}

# preflight: check everything before writing anything.  Prints the table and
# exits non-zero with one line naming the first problem.
preflight() {
  ROWS=()
  local problem="" found py pyver api

  if [ "$(uname -s)" = "Darwin" ]; then
    row "macOS" "any" "$(sw_vers -productVersion 2>/dev/null || echo yes)" "OK"
  else
    row "macOS" "any" "$(uname -s)" "MISSING"; problem="${problem:-this needs macOS}"
  fi

  found="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$ITERM_APP/Contents/Info.plist" 2>/dev/null || true)"
  if [ -z "$found" ]; then
    row "iTerm2" "$MIN_ITERM2+" "not found" "MISSING"; problem="${problem:-install iTerm2 $MIN_ITERM2 or newer in /Applications (https://iterm2.com)}"
  elif ver_ge "$found" "$MIN_ITERM2"; then
    row "iTerm2" "$MIN_ITERM2+" "$found" "OK"
  else
    row "iTerm2" "$MIN_ITERM2+" "$found" "UPGRADE"; problem="${problem:-upgrade iTerm2 to $MIN_ITERM2 or newer (iTerm2 > Check for Updates)}"
  fi

  py="${PYTHON:-python3}"
  pyver="$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
  if [ -z "$pyver" ]; then
    row "python3" "$MIN_PYTHON+" "not found" "MISSING"; problem="${problem:-install Python $MIN_PYTHON or newer (python.org or Homebrew), or set PYTHON=}"
  elif ver_ge "$pyver" "$MIN_PYTHON"; then
    row "python3" "$MIN_PYTHON+" "$pyver" "OK"
  else
    row "python3" "$MIN_PYTHON+" "$pyver" "UPGRADE"; problem="${problem:-install Python $MIN_PYTHON or newer, or point PYTHON= at one}"
  fi

  local tool
  for tool in osacompile osascript; do
    if command -v "$tool" >/dev/null 2>&1; then row "$tool" "any" "yes" "OK"
    else row "$tool" "any" "not found" "MISSING"; problem="${problem:-$tool is missing; it ships with macOS}"; fi
  done

  api="$(defaults read com.googlecode.iterm2 EnableAPIServer 2>/dev/null || true)"
  if [ "$api" = "1" ]; then
    row "iTerm2 Python API" "enabled" "enabled" "OK"
  else
    row "iTerm2 Python API" "enabled" "off" "MISSING"
    problem="${problem:-turn on iTerm2 > Settings > General > Magic > Enable Python API, then rerun}"
  fi

  if command -v jq >/dev/null 2>&1; then
    row "jq (optional)" "any" "$(jq --version 2>/dev/null || echo yes)" "OK"
  else
    row "jq (optional)" "any" "not found" "OK (hook uses sed)"
  fi

  if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c 'import AVFoundation' 2>/dev/null; then
    row "camera pkgs (optional)" "pyobjc" "installed" "OK"
  else
    row "camera pkgs (optional)" "pyobjc" "not installed" "OK (--camera adds)"
  fi

  print_rows
  [ -z "$problem" ] || die "$problem"
}

confirm() {  # confirm "question": returns 0 on yes
  [ "$ASSUME_YES" = "1" ] && return 0
  local ans=""
  if ! { read -r -p "$1 [y/N] " ans </dev/tty; } 2>/dev/null; then
    echo "(no terminal to answer the prompt)" >&2
    return 1
  fi
  [[ "$ans" =~ ^[Yy]([Ee][Ss])?$ ]]
}

# package_plan: what pip would change in the venv, one line each:
#   install <name> <to>   or   upgrade <name> <from> <to>
package_plan() {  # package_plan [requirements file]
  local report req="${1:-$REPO/requirements.txt}"
  report="$("$VENV/bin/python" -m pip install --dry-run --quiet --disable-pip-version-check \
    --report - -r "$req" 2>/dev/null)" || return 1
  REPORT="$report" "$VENV/bin/python" - <<'PY'
import json, os
from importlib import metadata
data = json.loads(os.environ["REPORT"] or "{}")
for item in data.get("install", []):
    name = item["metadata"]["name"]
    to = item["metadata"]["version"]
    try:
        have = metadata.version(name)
    except metadata.PackageNotFoundError:
        print("install", name, to)
        continue
    if have != to:
        print("upgrade", name, have, to)
PY
}

do_install() {
  local py="${PYTHON:-python3}" plan upgrades
  if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import sys' 2>/dev/null; then
    echo "Creating $VENV with $("$py" --version)"
    rm -rf "${VENV:?}"
    "$py" -m venv "$VENV"
  fi
  confirm_packages "$REPO/requirements.txt"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$REPO/requirements.txt"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --no-deps -e "$REPO"
  mkdir -p "$PROFILE_DIR"
  backup_profile
  cp "$REPO/profiles/Matrix.json" "$PROFILE_DST"
  chmod +x "$HOOK"
  local st
  st="$("$VENV/bin/matrix-rain" selftest 2>&1)" || { echo "$st" | grep -v '^PASS' >&2; die "installed, but the self-test failed (see above)"; }
  echo "Installed.  Self-test: $(echo "$st" | tail -1)"
  cat <<NEXT

Next steps:
  1. Use the Matrix profile: Settings > Profiles > Matrix > Other Actions > Set as Default,
     or open a window with it (Profiles menu).
  2. Start the daemon with iTerm2:  ./install.sh --autolaunch
     (or by hand from an iTerm2 terminal:  $VENV/bin/matrix-rain daemon).
  3. Add the Claude Code hooks:  ./install.sh --hooks
  4. Optional: camera motion tint:  ./install.sh --camera
NEXT
}

# confirm_packages FILE: show what pip would install or upgrade from FILE and
# ask before any upgrade.  Declining exits with nothing changed.
confirm_packages() {
  local plan upgrades
  plan="$(package_plan "$1")" || die "could not resolve $(basename "$1") with pip (network down?)"
  ROWS=()
  local kind name a b
  while read -r kind name a b; do
    [ -n "$kind" ] || continue
    if [ "$kind" = "upgrade" ]; then row "$name" "$b" "$a" "UPGRADE"; else row "$name" "$a" "not installed" "INSTALL"; fi
  done <<<"$plan"
  if [ ${#ROWS[@]} -gt 0 ]; then
    echo
    print_rows
  fi
  upgrades="$(grep -c '^upgrade ' <<<"$plan" || true)"
  if [ "${upgrades:-0}" -gt 0 ]; then
    echo
    while read -r kind name a b; do
      [ "$kind" = "upgrade" ] && echo "  $name $a -> $b"
    done <<<"$plan"
    confirm "Upgrade?" || die "upgrade declined; nothing was changed.  Rerun and answer y, or pass --yes."
  fi
}

do_camera() {
  [ -x "$VENV/bin/python" ] || die "run ./install.sh --install first"
  confirm_packages "$REPO/requirements-camera.txt"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$REPO/requirements-camera.txt"
  "$VENV/bin/python" -c 'import AVFoundation, Quartz, CoreMedia, libdispatch' \
    || die "camera packages installed but do not import"
  cat <<CAM
Camera packages installed.  The camera stays off until you enable it:
  $VENV/bin/matrix-rain camera enable     (then restart the daemon)
  $VENV/bin/matrix-rain camera test       (10 s live check; macOS asks for camera access)
  $VENV/bin/matrix-rain camera disable
CAM
}

do_hooks() {
  local cmd="$HOOK"
  cat <<JSON
{
  "hooks": {
    "SessionStart":       [{ "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "UserPromptSubmit":   [{ "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "PreToolUse":         [{ "matcher": "*", "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "PostToolUse":        [{ "matcher": "*", "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "PostToolUseFailure": [{ "matcher": "*", "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "PermissionRequest":  [{ "matcher": "*", "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "Notification":       [{ "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "Stop":               [{ "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "StopFailure":        [{ "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "SubagentStop":       [{ "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }],
    "SessionEnd":         [{ "hooks": [{ "type": "command", "command": "$cmd", "timeout": 5 }] }]
  }
}
JSON
  echo "Merge these entries into ~/.claude/settings.json next to any hooks you already have." >&2
}

do_autolaunch() {
  [ -x "$VENV/bin/python" ] || { echo "Run ./install.sh --install first" >&2; exit 1; }
  local tmp
  tmp="$(mktemp -d)"
  case "$REPO" in
    *'"'*|*\\*) echo "The clone path must not contain a double quote or backslash: $REPO" >&2; exit 1 ;;
  esac
  local repo_esc
  repo_esc="$(printf '%s' "$REPO" | sed -e 's/[&|]/\\&/g')"
  sed "s|__REPO__|$repo_esc|" "$REPO/scripts/autolaunch.applescript" > "$tmp/autolaunch.applescript"
  mkdir -p "$AUTOLAUNCH_DIR"
  osacompile -o "$AUTOLAUNCH" "$tmp/autolaunch.applescript"
  rm -rf "${tmp:?}"
  echo "Installed $AUTOLAUNCH"
  echo "It runs each time iTerm2 starts.  To start the daemon now without restarting iTerm2:"
  echo "  osascript \"$AUTOLAUNCH\""
  echo "Log: $LOG"
}

do_uninstall() {
  if [ -f "$AUTOLAUNCH" ]; then
    rm -f "$AUTOLAUNCH"
    echo "Removed AutoLaunch script"
  fi
  launchctl bootout "gui/$(id -u)/$OLD_LABEL" 2>/dev/null || true
  if [ -f "$OLD_PLIST" ]; then
    rm -f "$OLD_PLIST"
    echo "Removed old LaunchAgent"
  fi
  pkill -TERM -f 'matrix[-_]rain daemon' 2>/dev/null || true
  rm -f "$PROFILE_DST" && echo "Removed Dynamic Profile"
  rm -rf "${HOME:?}/.cache/iterm2-matrix-rain"
  rm -rf "${VENV:?}" && echo "Removed .venv"
  echo "Remove the claude-code-hook.sh entries from ~/.claude/settings.json yourself."
}

# Sourcing with MATRIX_RAIN_INSTALL_LIB=1 loads the functions only (selftest).
if [ "${MATRIX_RAIN_INSTALL_LIB:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

[ $# -eq 0 ] && { usage; exit 0; }
need_preflight=0
for arg in "$@"; do
  case "$arg" in
    --install|--autolaunch|--camera) need_preflight=1 ;;
    --hooks|--uninstall) ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage; exit 2 ;;
  esac
done
[ "$need_preflight" = "1" ] && preflight
for arg in "$@"; do
  case "$arg" in
    --install) do_install ;;
  esac
done
for arg in "$@"; do
  case "$arg" in
    --camera) do_camera ;;
  esac
done
for arg in "$@"; do
  case "$arg" in
    --autolaunch) do_autolaunch ;;
    --hooks) do_hooks ;;
    --uninstall) do_uninstall ;;
  esac
done
