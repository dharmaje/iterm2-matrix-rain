#!/usr/bin/env bash
# iterm2-matrix-rain installer.  Run with no arguments for help.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
ITERM_APP="${MATRIX_RAIN_ITERM_APP:-/Applications/iTerm.app}"
PLISTBUDDY="${MATRIX_RAIN_PLISTBUDDY:-/usr/libexec/PlistBuddy}"
BIN_LINK="$HOME/.local/bin/matrix-rain"
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

On Linux there is no iTerm2: the rain runs full-screen in any terminal instead
(matrix-rain run).  --install checks python3 $MIN_PYTHON+ and its venv module, then also
links ~/.local/bin/matrix-rain to .venv.  --autolaunch and --camera are macOS only and
say so.  --uninstall removes that link, the cache folder and .venv.

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

# The OS, as uname -s prints it.  MATRIX_RAIN_UNAME overrides it (selftest).
os_name() { if [ -n "${MATRIX_RAIN_UNAME:-}" ]; then echo "$MATRIX_RAIN_UNAME"; else uname -s; fi; }
is_darwin() { [ "$(os_name)" = "Darwin" ]; }

# os_release KEY: one value from /etc/os-release (MATRIX_RAIN_OS_RELEASE
# overrides the path), quotes stripped.  Prints nothing when it is missing.
os_release() {
  local f="${MATRIX_RAIN_OS_RELEASE:-/etc/os-release}"
  [ -r "$f" ] || return 0
  sed -n -E "s/^$1=[\"']?([^\"']*)[\"']?[[:space:]]*\$/\\1/p" "$f" | head -n 1
}

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
# exits non-zero with one line naming the first problem.  macOS checks for
# iTerm2; anything else gets the terminal (text) rain and its shorter list.
preflight() {
  if is_darwin; then preflight_darwin; else preflight_linux; fi
}

preflight_darwin() {
  ROWS=()
  local problem="" found py pyver api

  if [ "$(os_name)" = "Darwin" ]; then
    row "macOS" "any" "$(sw_vers -productVersion 2>/dev/null || echo yes)" "OK"
  else
    row "macOS" "any" "$(uname -s)" "MISSING"; problem="${problem:-this needs macOS}"
  fi

  found="$("$PLISTBUDDY" -c 'Print :CFBundleShortVersionString' "$ITERM_APP/Contents/Info.plist" 2>/dev/null || true)"
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

# preflight_linux: python3 and its venv module are required; the rest is
# information.  There is no iTerm2 here, so nothing iTerm2 or macOS is checked.
preflight_linux() {
  ROWS=()
  local problem="" py pyver name ver colors

  name="$(os_release NAME)"; ver="$(os_release VERSION_ID)"
  row "OS" "any" "${name:-$(os_name)}${ver:+ $ver}" "OK"

  py="${PYTHON:-python3}"
  pyver="$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
  if [ -z "$pyver" ]; then
    row "python3" "$MIN_PYTHON+" "not found" "MISSING"; problem="${problem:-install Python $MIN_PYTHON or newer (sudo apt install python3), or set PYTHON=}"
  elif ver_ge "$pyver" "$MIN_PYTHON"; then
    row "python3" "$MIN_PYTHON+" "$pyver" "OK"
  else
    row "python3" "$MIN_PYTHON+" "$pyver" "UPGRADE"; problem="${problem:-install Python $MIN_PYTHON or newer, or point PYTHON= at one}"
  fi

  # Debian and Ubuntu split venv and ensurepip out of python3; venv needs both.
  if [ -z "$pyver" ]; then
    row "python3 venv module" "any" "-" "MISSING"
  elif "$py" -c 'import ensurepip, venv' 2>/dev/null; then
    row "python3 venv module" "any" "yes" "OK"
  else
    row "python3 venv module" "any" "not found" "MISSING"
    problem="${problem:-install the python3 venv module (sudo apt install python3-venv), then rerun}"
  fi

  case "${COLORTERM:-}" in
    truecolor|24bit) colors="truecolor" ;;
    *) colors="256-color" ;;
  esac
  row "terminal colors (info)" "256+" "$colors" "OK"

  if command -v jq >/dev/null 2>&1; then
    row "jq (optional)" "any" "$(jq --version 2>/dev/null || echo yes)" "OK"
  else
    row "jq (optional)" "any" "not found" "OK (hook uses sed)"
  fi

  if command -v tmux >/dev/null 2>&1; then
    row "tmux (optional)" "any" "$(tmux -V 2>/dev/null | sed 's/^tmux //' || echo yes)" "OK"
  else
    row "tmux (optional)" "any" "not found" "OK (for a split pane)"
  fi

  if command -v notify-send >/dev/null 2>&1; then
    row "notify-send (optional)" "any" "yes" "OK"
  else
    row "notify-send (optional)" "any" "not found" "OK (no notifications)"
  fi

  case ":$PATH:" in
    *":$HOME/.local/bin:"*) row "~/.local/bin on PATH" "optional" "yes" "OK" ;;
    *) row "~/.local/bin on PATH" "optional" "no" "OK (add it, or use .venv/bin)" ;;
  esac

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
  if is_darwin; then
    mkdir -p "$PROFILE_DIR"
    backup_profile
    cp "$REPO/profiles/Matrix.json" "$PROFILE_DST"
  else
    link_command
  fi
  chmod +x "$HOOK"
  local st
  st="$("$VENV/bin/matrix-rain" selftest 2>&1)" || { echo "$st" | grep -v '^PASS' >&2; die "installed, but the self-test failed (see above)"; }
  echo "Installed.  Self-test: $(echo "$st" | tail -1)"
  is_darwin || { next_steps_linux; return 0; }
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

# link_command: ~/.local/bin/matrix-rain -> .venv/bin/matrix-rain, so the
# command works from any folder.  It replaces only a dangling link or one into
# some clone's .venv, never a file or another install's link (pipx, uv tool).
link_command() {
  local target="$VENV/bin/matrix-rain" cur
  if [ -L "$BIN_LINK" ]; then
    cur="$(readlink "$BIN_LINK")"
    if [ -e "$BIN_LINK" ] && [ "$cur" != "$target" ]; then
      case "$cur" in
        */.venv/bin/matrix-rain) ;;
        *) echo "Left $BIN_LINK alone: it links to $cur.  Run $target instead." >&2; return 0 ;;
      esac
    fi
  elif [ -e "$BIN_LINK" ]; then
    echo "Left $BIN_LINK alone: it is not a link.  Run $target instead." >&2
    return 0
  fi
  mkdir -p "$(dirname "$BIN_LINK")"
  ln -sfn "$target" "$BIN_LINK"
  echo "Linked $BIN_LINK -> $target"
}

next_steps_linux() {
  # Plain matrix-rain only when it is on PATH and is this clone's link.
  local cmd="$VENV/bin/matrix-rain"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) [ "$(readlink "$BIN_LINK" 2>/dev/null)" = "$cmd" ] && cmd="matrix-rain" ;;
  esac
  cat <<NEXT

Next steps (there is no iTerm2 here, so the rain runs as text in a terminal):
  1. Start it in a second terminal tab or window:  $cmd run
     (q quits; keys 1-6 show each state.  $cmd platform says what it detected.)
  2. Or beside Claude Code in a tmux split:  $REPO/scripts/rain-pane.sh --run
  3. Add the Claude Code hooks:  ./install.sh --hooks
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
  is_darwin || { echo "--camera is macOS only (it uses AVFoundation); skipped."; return 0; }
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
  is_darwin || { echo "--autolaunch is macOS only (iTerm2 AutoLaunch); skipped.  Start the rain with: matrix-rain run"; return 0; }
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
  is_darwin || { uninstall_linux; return 0; }
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

# uninstall_linux: stop any rain, then remove the command link (only when it
# points into this clone's .venv), the cache folder and .venv.
uninstall_linux() {
  pkill -TERM -f 'matrix[-_]rain (daemon|term|run)' 2>/dev/null || true
  if [ -L "$BIN_LINK" ] && [ "$(readlink "$BIN_LINK")" = "$VENV/bin/matrix-rain" ]; then
    rm -f "$BIN_LINK"
    echo "Removed $BIN_LINK"
  fi
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
    --install) need_preflight=1 ;;
    --autolaunch|--camera) is_darwin && need_preflight=1 ;;
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
