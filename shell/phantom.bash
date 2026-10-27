# PhantomShell hook for bash and zsh
# ===================================
#
#   source /path/to/PhantomShell/shell/phantom.bash
#
# What it does
# ------------
# After every interactive command, if the exit status was non-zero, it asks
# haunt.py to whisper an explanation and a corrected command. The hook never
# executes anything, never changes your exit status, and stays silent when
# Ollama is asleep.
#
# Knobs (set them before sourcing, e.g. in ~/.bashrc)
# ---------------------------------------------------
#   PHANTOM_HOME           repo directory (auto-detected from this file)
#   PHANTOM_PYTHON         interpreter to use (auto-detected)
#   PHANTOM_SKIP           exit codes to ignore     (default: "0 130 141")
#   PHANTOM_EXTRA_ARGS     extra haunt.py flags     (default: "")
#   PHANTOM_CAPTURE_STDERR 1 = capture real stderr  (default: 0, see below)
#   PHANTOM_NIGHT          1 = whisper after successful commands, 00:00-05:00
#   PHANTOM_NIGHT_CHANCE   percent chance per command (default: 15)
#   PHANTOM_OFF            1 = mute the ghost entirely
#
# Optional stderr capture
# -----------------------
# A shell cannot read the stderr of a command it already ran, so by default the
# ghost receives "exit code N" plus the command line. On bash/zsh you can opt in
# to real error text. It routes the shell's stderr through `tee` into a temp log,
# which is a small, mostly harmless cost - but it is opt-in because it touches
# every command's stderr:
#
#   PHANTOM_CAPTURE_STDERR=1
#
# Unset/remove it if any tool behaves oddly with redirected stderr.

# --- locate this file and the repo -------------------------------------------
if [ -n "${ZSH_VERSION:-}" ]; then
    _phantom_self="${(%):-%x}"
elif [ -n "${BASH_SOURCE[0]:-}" ]; then
    _phantom_self="${BASH_SOURCE[0]}"
else
    _phantom_self="$0"
fi

if [ -z "${PHANTOM_HOME:-}" ] && [ -n "${_phantom_self:-}" ]; then
    PHANTOM_HOME="$(cd "$(dirname "$_phantom_self")/.." 2>/dev/null && pwd)"
fi
export PHANTOM_HOME

# --- locate a Python 3 interpreter -------------------------------------------
if [ -z "${PHANTOM_PYTHON:-}" ]; then
    for _phantom_candidate in python3 python py; do
        if command -v "$_phantom_candidate" >/dev/null 2>&1; then
            PHANTOM_PYTHON="$_phantom_candidate"
            break
        fi
    done
fi

# --- optional stderr capture (opt-in) ----------------------------------------
if [ "${PHANTOM_CAPTURE_STDERR:-0}" = "1" ] && [ -z "${PHANTOM_ERR_LOG:-}" ]; then
    PHANTOM_ERR_LOG="${TMPDIR:-/tmp}/phantom-stderr-$$.log"
    : >"$PHANTOM_ERR_LOG" 2>/dev/null
    # Keep the file open for the life of the shell; re-truncated after each prompt.
    exec 2> >(tee -a "$PHANTOM_ERR_LOG" >&2)
fi

# --- the hook -----------------------------------------------------------------
# Late-night idle whisper after successful commands (opt-in via PHANTOM_NIGHT=1).
_phantom_night_roll() {
    [ "${PHANTOM_NIGHT:-0}" = "1" ] || return 0

    local hour
    hour=$(date +%H 2>/dev/null) || return 0
    hour="${hour#0}"
    [ -n "$hour" ] || hour=0
    case "$hour" in '' | *[!0-9]*) return 0 ;; esac
    [ "$hour" -lt 5 ] || return 0

    local chance="${PHANTOM_NIGHT_CHANCE:-15}"
    case "$chance" in '' | *[!0-9]*) chance=15 ;; esac
    [ "$chance" -gt 0 ] || return 0
    [ $((RANDOM % 100)) -lt "$chance" ] || return 0

    # shellcheck disable=SC2086  # PHANTOM_EXTRA_ARGS is intentionally word-split
    PHANTOM_SHELL="${_PHANTOM_SHELL:-bash}" \
        "$PHANTOM_PYTHON" "$PHANTOM_HOME/haunt.py" --night-whisper ${PHANTOM_EXTRA_ARGS:-} 2>/dev/null
    return 0
}

_phantom_haunt() {
    local exit_code=$?             # must be the very first statement
    local last_cmd=""
    local evidence=""

    # Re-entrancy and opt-out guards.
    [ "${_PHANTOM_BUSY:-0}" = "1" ] && return "$exit_code"
    [ "${PHANTOM_OFF:-0}" = "1" ] && return "$exit_code"
    [ -t 1 ] || return "$exit_code"
    [ -n "${PHANTOM_PYTHON:-}" ] || return "$exit_code"
    [ -f "${PHANTOM_HOME:-/nonexistent}/haunt.py" ] || return "$exit_code"

    # One haunt per history entry: pressing Enter on an empty line should not
    # summon the ghost again.
    if [ -n "${HISTCMD:-}" ] && [ "${HISTCMD}" = "${_PHANTOM_LAST_HIST:-}" ]; then
        return "$exit_code"
    fi
    _PHANTOM_LAST_HIST="${HISTCMD:-}"

    # Successful command? The ghost may only murmur, and only at night.
    if [ "$exit_code" -eq 0 ]; then
        _phantom_night_roll
        return 0
    fi

    case " ${PHANTOM_SKIP:-0 130 141} " in
        *" $exit_code "*) return "$exit_code" ;;
    esac

    # Recover the command that failed. bash's `history 1` and zsh's `fc -ln -1`
    # both report the command that just finished at prompt time; bash's
    # `fc -ln -1` lags one entry, so the two shells need different tools.
    if [ -n "${ZSH_VERSION:-}" ]; then
        last_cmd=$(fc -ln -1 2>/dev/null || true)
    else
        last_cmd=$(HISTTIMEFORMAT= builtin history 1 2>/dev/null || true)
        # Strip the history entry number ("  42  cmd" or "  42* cmd").
        last_cmd="${last_cmd#"${last_cmd%%[![:space:]]*}"}"
        last_cmd="${last_cmd#"${last_cmd%%[!0-9]*}"}"
        last_cmd="${last_cmd#\*}"
    fi
    last_cmd="${last_cmd#"${last_cmd%%[![:space:]]*}"}"
    case "$last_cmd" in
        "" | *haunt.py*) return "$exit_code" ;;
    esac

    # Prefer captured stderr, truncated to the tail (compilers put the useful
    # part last). haunt.py sanitizes and re-truncates anyway.
    if [ -n "${PHANTOM_ERR_LOG:-}" ] && [ -s "$PHANTOM_ERR_LOG" ]; then
        evidence=$(tail -n 20 "$PHANTOM_ERR_LOG" 2>/dev/null || true)
        : >"$PHANTOM_ERR_LOG" 2>/dev/null || true
    fi
    [ -n "$evidence" ] || evidence="exit code $exit_code"

    _PHANTOM_BUSY=1
    # shellcheck disable=SC2086  # PHANTOM_EXTRA_ARGS is intentionally word-split
    PHANTOM_SHELL="${_PHANTOM_SHELL:-bash}" \
        "$PHANTOM_PYTHON" "$PHANTOM_HOME/haunt.py" "$last_cmd" "$evidence" \
        ${PHANTOM_EXTRA_ARGS:-} 2>/dev/null
    _PHANTOM_BUSY=0

    # Give the original status back to the rest of PROMPT_COMMAND / precmd.
    return "$exit_code"
}

# --- register with the shell --------------------------------------------------
if [ -n "${ZSH_VERSION:-}" ]; then
    _PHANTOM_SHELL="zsh"
    if typeset -f add-zsh-hook >/dev/null 2>&1; then
        add-zsh-hook precmd _phantom_haunt
    else
        precmd_functions+=(_phantom_haunt)
    fi
elif [ -n "${BASH_VERSION:-}" ]; then
    _PHANTOM_SHELL="bash"
    # Prepend; the hook returns the original exit status for later entries.
    PROMPT_COMMAND="_phantom_haunt${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
fi

unset _phantom_self _phantom_candidate
