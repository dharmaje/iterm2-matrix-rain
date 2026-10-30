-- iterm2-matrix-rain AutoLaunch script.
-- install.sh --autolaunch replaces __REPO__ with the clone's path, compiles this
-- file with osacompile and installs it in
-- ~/Library/Application Support/iTerm2/Scripts/AutoLaunch/, which iTerm2 runs at start-up.
-- iTerm2 hands out the API cookie, so the daemon connects without a permission prompt.

set repoPath to "__REPO__"
set logPath to (POSIX path of (path to home folder)) & "Library/Logs/iterm2-matrix-rain.log"

-- Do nothing when a daemon is already running.
try
	do shell script "/usr/bin/pgrep -f 'matrix[-_]rain daemon' >/dev/null"
	return "already running"
end try

tell application "iTerm2" to set creds to request cookie and key for app named "iterm2-matrix-rain"

set savedDelims to AppleScript's text item delimiters
set AppleScript's text item delimiters to " "
set apiCookie to text item 1 of creds
set apiKey to text item 2 of creds
set AppleScript's text item delimiters to savedDelims

-- Only the python command is backgrounded, with every stream redirected, so
-- do shell script returns at once instead of waiting on the daemon.
set logDir to (POSIX path of (path to home folder)) & "Library/Logs"
do shell script "mkdir -p " & quoted form of logDir & "; cd " & quoted form of repoPath & " || exit 1; " & ¬
	"ITERM2_COOKIE=" & quoted form of apiCookie & ¬
	" ITERM2_KEY=" & quoted form of apiKey & ¬
	" PYTHONPATH=" & quoted form of repoPath & ¬
	" nohup " & quoted form of (repoPath & "/.venv/bin/python") & " -m matrix_rain daemon" & ¬
	" > " & quoted form of logPath & " 2>&1 < /dev/null &"
return "started"
