#!/usr/bin/env bash
# Claude Code hook for iterm2-matrix-rain.
# Reads the hook JSON on stdin, maps the event to a rain state and hands it
# to `matrix-rain event` in the background.  Prints nothing to stdout (hook
# stdout is fed to Claude) and always exits 0.
#
# Test without a daemon:
#   echo '{"hook_event_name":"Stop"}' | MATRIX_RAIN_HOOK_DRY_RUN=1 hooks/claude-code-hook.sh
#   (prints "state=done" to stderr)

input="$(cat 2>/dev/null)"

field() {  # field <name>: top-level string value from the JSON on stdin
  if [ "${MATRIX_RAIN_HOOK_NO_JQ:-0}" != "1" ] && command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r --arg k "$1" '.[$k] // empty' 2>/dev/null
  else
    printf '%s' "$input" | tr -d '\n' \
      | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p"
  fi
}

event="$(field hook_event_name)"
state=""
case "$event" in
  SessionStart) state=idle ;;
  UserPromptSubmit|PreToolUse|PostToolUse) state=working ;;
  PermissionRequest) state=waiting ;;
  Notification)
    case "$(field notification_type)" in
      permission_prompt|idle_prompt) state=waiting ;;
    esac ;;
  PostToolUseFailure|StopFailure) state=error ;;
  Stop|SubagentStop) state=done ;;
  SessionEnd) state=off ;;
esac

if [ "${MATRIX_RAIN_HOOK_DRY_RUN:-0}" = "1" ]; then
  [ -n "$state" ] && echo "state=$state" >&2
  exit 0
fi
[ -z "$state" ] && exit 0

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"
py="$repo/.venv/bin/python"
[ -x "$py" ] || exit 0
PYTHONPATH="$repo" nohup "$py" -m matrix_rain event "$state" >/dev/null 2>&1 </dev/null &
exit 0
