#!/usr/bin/env bash
# Open the text rain in a tmux split next to the pane this runs in.
set -euo pipefail

script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
REPO="$(dirname "$(dirname "$script_path")")"
MATRIX_RAIN="$REPO/.venv/bin/matrix-rain"
name="$(basename "$script_path")"

usage() {
  cat <<USAGE
Usage: $name --run [WIDTH] [-- matrix-rain term options]

Splits the current tmux window and runs the text rain in a new pane on the
right, WIDTH wide (default 30%).  The rain follows Claude Code events from this
tmux client, so run Claude Code in the pane you started from.  Press q in the
rain pane to close it.  Works on Linux and macOS; needs tmux and
install.sh --install.

Examples:
  $name --run
  $name --run 40%
  $name --run 30% -- --demo
  alias matrix-pane='$script_path --run'
USAGE
}

die() { echo "$*" >&2; exit 1; }

[[ "${1:-}" == "--run" ]] || { usage; exit 0; }
shift
width="30%"
if [[ $# -gt 0 && "$1" != "--" ]]; then width="$1"; shift; fi
[[ "${1:-}" == "--" ]] && shift
[[ "$width" =~ ^[0-9]+%?$ ]] || die "WIDTH must be a number of columns or a percentage, such as 30%."

[[ -x "$MATRIX_RAIN" ]] || die "matrix-rain is not installed; run $REPO/install.sh --install first."
command -v tmux >/dev/null 2>&1 || die "tmux is not installed.  Without tmux, run $MATRIX_RAIN run in another terminal tab."
[[ -n "${TMUX:-}" ]] || die "Not inside tmux.  Start tmux first, or run $MATRIX_RAIN run in another terminal tab."

# tmux runs the command through a shell, so quote each word.
cmd="$(printf '%q ' "$MATRIX_RAIN" term "$@")"
target=()
[[ -n "${TMUX_PANE:-}" ]] && target=(-t "$TMUX_PANE")
tmux split-window -h -d -l "$width" ${target[@]+"${target[@]}"} "$cmd"
echo "Rain pane opened.  Press q in it to close it."
