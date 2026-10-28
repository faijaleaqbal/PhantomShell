# PhantomShell hook for fish shell (>= 3.0)
# ==========================================
#
#   source /path/to/PhantomShell/shell/phantom.fish
#
# Add that line to your fish config (~/.config/fish/config.fish) to make it permanent:
#   echo "source /path/to/PhantomShell/shell/phantom.fish" >> ~/.config/fish/config.fish
#
# What it does
# ------------
# Hooks fish_postexec event. After every interactive command, if the exit status
# was non-zero, it asks haunt.py to whisper an explanation and a corrected command.
# The hook never executes anything, never changes your exit status, and stays
# silent when Ollama is asleep.
#
# Knobs (set them before sourcing, or export in config.fish):
# ----------------------------------------------------------
#   PHANTOM_HOME         repo directory (auto-detected from this file)
#   PHANTOM_PYTHON       interpreter to use (auto-detected: python3, python, py)
#   PHANTOM_SKIP         exit codes to ignore (default: "0 130 141")
#   PHANTOM_EXTRA_ARGS   extra haunt.py flags (default: "")
#   PHANTOM_NIGHT        1 = whisper after successful commands, 00:00-05:00
#   PHANTOM_NIGHT_CHANCE percent chance per command (default: 15)
#   PHANTOM_OFF          1 = mute the ghost entirely

# --- locate this file and repo root ------------------------------------------
if test -z "$PHANTOM_HOME"
    set -l _phantom_self (status filename)
    if test -n "$_phantom_self"
        set -l _phantom_dir (dirname (status filename))
        set -gx PHANTOM_HOME (builtin realpath "$_phantom_dir/..")
    else
        set -gx PHANTOM_HOME "$HOME/PhantomShell"
    end
end

# --- locate Python interpreter ------------------------------------------------
if test -z "$PHANTOM_PYTHON"
    for _phantom_candidate in python3 python py
        if command -q $_phantom_candidate
            set -g PHANTOM_PYTHON $_phantom_candidate
            break
        end
    end
end

# --- late-night idle whisper after successful commands ------------------------
function _phantom_night_roll
    test "$PHANTOM_NIGHT" = "1"; or return 0

    set -l hour (date +%H 2>/dev/null)
    test -n "$hour"; or return 0
    string match -qr '^[0-9]+$' -- "$hour"; or return 0
    test $hour -lt 5; or return 0

    set -l chance 15
    if test -n "$PHANTOM_NIGHT_CHANCE"; and string match -qr '^[0-9]+$' -- "$PHANTOM_NIGHT_CHANCE"
        set chance $PHANTOM_NIGHT_CHANCE
    end
    test $chance -gt 0; or return 0

    set -l roll (random 0 99)
    test $roll -lt $chance; or return 0

    set -l extra_args
    if test -n "$PHANTOM_EXTRA_ARGS"
        set extra_args (string split " " -- "$PHANTOM_EXTRA_ARGS")
    end

    env PHANTOM_SHELL=fish "$PHANTOM_PYTHON" "$PHANTOM_HOME/haunt.py" --night-whisper $extra_args 2>/dev/null
    return 0
end

# --- post-execution hook ------------------------------------------------------
function _phantom_haunt --on-event fish_postexec
    set -l exit_code $status
    set -l last_cmd $argv[1]

    # Re-entrancy and opt-out guards
    test "$PHANTOM_OFF" = "1"; and return $exit_code
    test -n "$PHANTOM_PYTHON"; or return $exit_code
    test -f "$PHANTOM_HOME/haunt.py"; or return $exit_code

    # Successful command? The ghost may only murmur, and only at night.
    if test $exit_code -eq 0
        _phantom_night_roll
        return 0
    end

    # Check ignored exit codes
    set -l skip_codes 0 130 141
    if test -n "$PHANTOM_SKIP"
        set skip_codes (string split " " -- "$PHANTOM_SKIP")
    end
    if contains -- $exit_code $skip_codes
        return $exit_code
    end

    # Ignore empty commands or calls to haunt.py itself
    if test -z "$last_cmd"; or string match -qr 'haunt\.py' -- "$last_cmd"
        return $exit_code
    end

    set -l extra_args
    if test -n "$PHANTOM_EXTRA_ARGS"
        set extra_args (string split " " -- "$PHANTOM_EXTRA_ARGS")
    end

    # Summon the ghost
    env PHANTOM_SHELL=fish "$PHANTOM_PYTHON" "$PHANTOM_HOME/haunt.py" "$last_cmd" "exit code $exit_code" $extra_args 2>/dev/null

    return $exit_code
end

