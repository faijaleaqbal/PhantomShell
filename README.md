# PhantomShell: The Haunted Terminal

> An offline CLI ghost that haunts your shell when a command fails.
> One eerie sentence, one exact fix — generated on your own machine.

PhantomShell watches your prompt. When a command exits non-zero, a digital ghost
stirs: ASCII apparition, a dim green whisper about your mistakes, and a clean,
copy-pasteable command that fixes the error. Everything runs locally through
[Ollama](https://ollama.com) — no API keys, no telemetry, no network.

Built for the **Ghost in the Machine** hackathon. Designed to survive a demo
with the Wi-Fi off.

```
       .-.
      (o o)
      | O \
      |   |
      |   |
      ^^^^^
  Somewhere a fan spins down, and something else spins up.

┌─  PHANTOM SHELL // the shell remembers  ────────────────────────────────────┐
│  You called into the dark with a word no binary answers to; the dark        │
│  called back with a typo.                                                   │
│  'gti' is not a command the machine knows. It suspects 'git'.               │
└─────────────────────────────────────────────────────────────────────────────┘
┌─  whispered fix  ───────────────────────────────────────────────────────────┐
│ git status                                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
  23:58  //  qwen2.5-coder:1.5b  //  0.4s
```

---

## Why it is not just a wrapper around a chatbot

| | |
|---|---|
| **Fully offline** | Talks only to `127.0.0.1:11434`. Proxies are explicitly bypassed so a local daemon never leaks through `HTTP_PROXY`. |
| **Small models** | Auto-detects `qwen2.5-coder:1.5b` or `llama3.2:1b`, keeps them warm for 30 minutes. Fast on CPU. |
| **Never executes anything** | It prints a suggested command. You decide whether to run it. |
| **Never breaks your shell** | Always exits `0`, bounded timeout, no tracebacks, restores your exit code, skips `Ctrl+C`, one haunt per history entry. |
| **Degrades in character** | If Ollama is asleep, a deterministic "omen engine" still fixes typos, missing Python modules, permissions, busy ports and bad paths. |
| **Night mode (opt-in)** | After midnight, the ghost may murmur a single dim line after a *successful* command. No model call, no interruption. |
| **Terminal-injection aware** | Strips ANSI/OSC escapes and bidi controls from error text, redacts common credential shapes, and truncates before anything is printed or sent to the model. |

---

## Quickstart (2 minutes)

**1. Install Ollama and one small model**

```bash
# macOS / Linux / Windows: https://ollama.com/download
ollama pull qwen2.5-coder:1.5b     # best at shell syntax (recommended)
# or: ollama pull llama3.2:1b      # chattier, slightly better prose
```

Ollama usually runs as a background service. If not:

```bash
ollama serve
```

**2. Install the one Python dependency**

```bash
cd PhantomShell
python -m pip install -r requirements.txt
```

**3. Wake the ghost**

```bash
# Works even with Ollama stopped (pre-baked omen engine):
python haunt.py --demo --offline

# Live, local model:
python haunt.py "gti status" "gti: command not found"
python haunt.py "python app.py" "ModuleNotFoundError: No module named 'yaml'"
python haunt.py "npm run dev" "Error: listen EADDRINUSE: address already in use :::3000"
```

> First inference after a cold start can take 5–15 s on CPU while the model
> loads. Warm it once with `ollama run qwen2.5-coder:1.5b "hi"` before a demo,
> or raise the timeout (`--timeout 30`). After that, `keep_alive` keeps it hot.

---

## Shell integration

The hooks live in [`shell/`](shell/). They are small, dependency-free, and
return your original exit status so your existing prompt/theme keeps working.

### a) Bash or Zsh

Add one line to `~/.bashrc` (bash) or `~/.zshrc` (zsh):

```bash
source "$HOME/PhantomShell/shell/phantom.bash"
```

Reload with `source ~/.bashrc` (or open a new terminal), then fail something:

```bash
gti status
```

Knobs (set before sourcing):

```bash
PHANTOM_HOME="$HOME/PhantomShell"     # auto-detected, usually unnecessary
PHANTOM_PYTHON=python3                # interpreter, auto-detected
PHANTOM_SKIP="0 130 141"              # exit codes to ignore (Ctrl+C, SIGPIPE)
PHANTOM_EXTRA_ARGS="--timeout 20"     # extra haunt.py flags
PHANTOM_OFF=1                         # mute the ghost for this session
```

If you prefer a minimal inline hook instead of the file:

```bash
# bash: ~/.bashrc
_phantom_haunt() {
    local code=$?
    [ "$code" -eq 0 ] && return 0
    local cmd
    cmd=$(HISTTIMEFORMAT= builtin history 1)
    cmd="${cmd#"${cmd%%[![:space:]]*}"}"   # trim leading spaces
    cmd="${cmd#"${cmd%%[!0-9]*}"}"         # drop history number
    cmd="${cmd#\*}"
    cmd="${cmd#"${cmd%%[![:space:]]*}"}"
    command python3 "$HOME/PhantomShell/haunt.py" "$cmd" "exit code $code"
    return "$code"
}
PROMPT_COMMAND="_phantom_haunt${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
```

```zsh
# zsh: ~/.zshrc
_phantom_haunt() {
    local code=$?
    (( code == 0 )) && return 0
    command python3 "$HOME/PhantomShell/haunt.py" "$(fc -ln -1)" "exit code $code"
    return $code
}
precmd_functions+=(_phantom_haunt)
```

**Why the hook passes only the exit code by default:** a shell cannot read the
stderr of a process it already reaped. The ghost still gets the failed command
and the exit status, which handles the majority of cases. If you want real error
text, opt in before sourcing:

```bash
PHANTOM_CAPTURE_STDERR=1
```

That routes the shell's stderr through `tee` into a temp log for the prompt to
read. It is off by default because it touches every command's stderr.

### b) PowerShell (Windows PowerShell 5.1+ / PowerShell 7+)

Add one line to your profile — find it with `$PROFILE`, then:

```powershell
. "$HOME\PhantomShell\shell\phantom.ps1"
```

Reload with `. $PROFILE`, then:

```powershell
gti status
```

If scripts are blocked:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Minimal inline version, if you prefer:

```powershell
$global:__PhantomPrompt = $function:prompt
function global:prompt {
    $ok = $?; $code = $LASTEXITCODE
    if (-not $ok) {
        $env:PHANTOM_LAST_COMMAND = (Get-History -Count 1).CommandLine
        $env:PHANTOM_LAST_ERROR = if ($Error[0]) { $Error[0].Exception.Message } else { "exit code $code" }
        python "$HOME\PhantomShell\haunt.py" 2>$null
    }
    $global:LASTEXITCODE = $code
    & $global:__PhantomPrompt
}
```

The bundled hook is more careful than the snippet above: it de-duplicates empty
prompts, skips `Ctrl+C`, reads `PHANTOM_EXTRA_ARGS` live, and passes evidence
through environment variables so quotes and Unicode survive Windows argument
parsing.

### Late-night murmurs (optional)

The ghost can also acknowledge *successful* commands between 00:00 and 05:00 —
one dim line, no model call, no ASCII art:

```bash
PHANTOM_NIGHT=1          # enable (default: off)
PHANTOM_NIGHT_CHANCE=15  # percent chance per command (default: 15)
```

```powershell
$env:PHANTOM_NIGHT = '1'
$env:PHANTOM_NIGHT_CHANCE = '15'
```

It is deliberately quiet and probabilistic: the terminal should feel watched,
not heckled. The clock check lives in `haunt.py --night-whisper`, which prints
nothing outside the witching hours.

---

## CLI reference

```
python haunt.py "<failed command>" "<error output or exit code>" [options]
```

| Flag | What it does |
|---|---|
| `--demo` | Use a built-in failing scenario. Great for recordings. |
| `--offline` | Never call Ollama; use the omen engine. |
| `--night-whisper` | Print one idle line if it is late (used by the hooks). |
| `--fix-only` | Print **only** the corrected command (for clipboard/pipes). |
| `--model NAME` | Model name, or `auto` (default) to pick an installed small model. |
| `--host URL` | Ollama base URL (default `http://127.0.0.1:11434`). |
| `--timeout SEC` | How long to wait for the ghost (default `10`). |
| `--no-art` | Skip the ASCII apparition. |
| `--no-flicker` | Disable the shimmer animation. |
| `--no-color` | Disable colour (also honours `NO_COLOR`). |
| `--seed N` | Reproducible art/wording, for demos and screenshots. |
| `--version` | Print the version. |

Examples:

```bash
# Copy the fix to the clipboard instead of reading it
python haunt.py "gti status" "command not found" --offline --fix-only | clip      # Windows
python haunt.py "gti status" "command not found" --offline --fix-only | pbcopy    # macOS
python haunt.py "gti status" "command not found" --offline --fix-only | xclip    # Linux

# Record a deterministic demo
python haunt.py --demo --offline --seed 7
```

`--fix-only` is meant for **reading or copying**, not for `eval`. The ghost is a
very small model; treat its suggestions as advice, not authority.

---

## How it works

```
 command fails
      │
      ▼
 shell hook ── last command + exit code (+ stderr if opted in)
      │
      ▼
 haunt.py ── sanitize ANSI/OSC • redact secrets • truncate
      │
      ├── Ollama reachable? ──yes──► POST /api/generate (local, tiny model)
      │                                  │
      │                                  ▼
      │                             parse one-sentence whisper + fenced fix
      │
      └── no / timeout / bad JSON ──► deterministic omen engine + pre-baked whisper
                                         │
                                         ▼
                                   rich render (art, panels, meta)
```

Fallback chain:

1. **Explicit model** or **auto-detected** installed model (`/api/tags`).
2. Any Ollama failure — refused connection, timeout, missing model, empty
   response, malformed JSON — becomes a friendly reason plus the offline omen.
3. Anything unexpected is swallowed by `main()`; at worst you get one dim line,
   never a traceback in your prompt. Set `PHANTOM_DEBUG=1` while developing.

### The offline omen engine

Small and deliberately unglamorous, but it makes the demo work with the network
off:

- `gti status` → `git status` (fuzzy match against common commands)
- `No module named 'yaml'` → `python3 -m pip install pyyaml` (import→PyPI map)
- `Permission denied` → `sudo …`, with special cases for Docker and SSH keys
- `EADDRINUSE :::3000` → `lsof -i :3000`
- `Cannot find path …` / `No such file or directory` → list the parent directory
- `exit code 130` → "The spell was interrupted", no invented fix
- anything else → an honest "the bones are silent", no hallucinated command

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PHANTOM_MODEL` | `auto` | Model name, or auto-detect. |
| `PHANTOM_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama base URL. |
| `PHANTOM_TIMEOUT` | `10` | Seconds to wait; also `--timeout`. |
| `PHANTOM_KEEP_ALIVE` | `30m` | How long Ollama keeps the model loaded. |
| `PHANTOM_SEED` | unset | Reproducible art/wording. |
| `PHANTOM_SHELL` | auto | Shell name used in the prompt (`bash`, `zsh`, `powershell`). |
| `PHANTOM_OFF` | `0` | `1` mutes the hook. |
| `PHANTOM_SKIP` | `0 130 141` | Exit codes the bash/zsh hook ignores. |
| `PHANTOM_EXTRA_ARGS` | empty | Extra flags for the hook to pass. |
| `PHANTOM_CAPTURE_STDERR` | `0` | `1` enables stderr capture in bash/zsh. |
| `PHANTOM_NIGHT` | `0` | `1` enables late-night murmurs after success. |
| `PHANTOM_NIGHT_CHANCE` | `15` | Percentage chance per successful command. |
| `PHANTOM_DEBUG` | `0` | `1` shows tracebacks instead of the shroud message. |
| `NO_COLOR` | unset | Standard colour opt-out, honoured by `rich`. |

---

## Safety and privacy

- **Local only.** Requests go to the configured Ollama host. The default client
  has proxy handling disabled, so `HTTP_PROXY` cannot redirect a localhost call.
- **No execution.** Nothing the model writes is ever run. There is no `eval`,
  no `subprocess`, no shell interpolation of model output.
- **Sanitised input and output.** CSI/OSC escape sequences, C0/C1 controls and
  bidi overrides are stripped; the text is tail-truncated (compilers put the
  useful part last) before printing or sending.
- **Secret redaction.** Bearer tokens, `sk-…`, `ghp_…`, AWS access key IDs and
  `password=…`-style pairs are masked before the prompt leaves the process.
  This is best-effort, not a guarantee — review what your errors contain.
- **Injection aware.** Command and error text are wrapped in `<command>` /
  `<error>` tags, and the system prompt tells the model to treat them as
  evidence, never instructions.
- **Shell friendly.** Exit status `0` always, bounded timeout, errors contained.
  The reading shell's `$?` / `$LASTEXITCODE` is restored afterwards.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No ghost answers at http://127.0.0.1:11434` | Start Ollama: `ollama serve` (or launch the desktop app). |
| `the model '…' is not installed` | `ollama pull qwen2.5-coder:1.5b` (or `llama3.2:1b`). |
| First answer takes 10+ s | Cold model load. Warm it: `ollama run qwen2.5-coder:1.5b "hi"`, or `--timeout 30`. |
| Ghost never appears | Check the hook is sourced (`echo $PROMPT_COMMAND` / `$PROFILE`), your command really failed, and the code is not in `PHANTOM_SKIP`. |
| Ghost appears twice | Both the file hook and an inline snippet are active. Keep one. |
| Garbled art / boxes | Use Windows Terminal, or run with `--no-art`. |
| PowerShell profile ignored | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then `. $PROFILE`. |
| Colours look wrong | `--no-color`, or set `NO_COLOR=1`. |
| Shell hangs on failure | Lower `--timeout`; make sure the host is local (`127.0.0.1`, not a LAN IP). |

---

## Tests

No Ollama, no network, no TTY required — a fake daemon is part of the suite:

```bash
python -m unittest discover -s tests -v
```

41 tests cover escape-sequence stripping, secret redaction, truncation, reply
parsing, every offline omen rule, the Ollama client (success, 404 model,
refused connection), prompt construction, night whispers, and the CLI/fallback
paths.

---

## Project layout

```
PhantomShell/
├── haunt.py              # the ghost: CLI, Ollama client, omen engine, renderer
├── shell/
│   ├── phantom.bash      # bash + zsh hook (PROMPT_COMMAND / precmd)
│   └── phantom.ps1       # PowerShell 5.1+ / 7+ prompt wrapper
├── tests/
│   └── test_haunt.py     # offline unit + integration tests
├── requirements.txt      # rich
├── LICENSE               # MIT
└── README.md
```

---

## Demo script (60 seconds)

1. `python haunt.py --demo --offline` — instant, Wi-Fi off, still in character.
2. `python haunt.py --demo` — same failure, now with the local model's whisper.
3. In a real shell: `gti status` → the hook suggests `git status`.
4. `python haunt.py "python app.py" "ModuleNotFoundError: No module named 'yaml'" --fix-only`
   → copy-paste `python3 -m pip install pyyaml`.
5. Kill Ollama (`Ctrl+C` in its terminal) and fail something again → the omen
   engine takes over instead of crashing the prompt.

---

## Limitations

- 1–1.5B models are fast and charming, not omniscient. Cross-check destructive
  suggestions (`rm`, `sudo`, `kill`) before running them.
- Default bash hooks do not see stderr (shell limitation); enable
  `PHANTOM_CAPTURE_STDERR=1` if you want the full error text.
- Offline omen rules are intentionally conservative; unknown failures get an
  honest "no fix" rather than a hallucinated command.
- `rich` renders best in a modern terminal. Legacy `cmd.exe` may fall back to
  plain output.

## License

MIT — see [LICENSE](LICENSE).

---

*The machine remembers. It is not angry. It is just disappointed.*
