# PhantomShell hook for PowerShell (Windows PowerShell 5.1+ and PowerShell 7+)
# ==========================================================================
#
#   . "$HOME\PhantomShell\shell\phantom.ps1"
#
# Add that line to your profile ($PROFILE) to make it permanent.
#
# The hook wraps the existing prompt function. It captures $? / $LASTEXITCODE
# before anything else runs, sends the failed command to haunt.py, restores the
# original exit code, and then draws your normal prompt.
#
# Knobs (environment variables):
#   PHANTOM_HOME        repo directory (auto-detected)
#   PHANTOM_OFF         1 = mute the ghost entirely
#   PHANTOM_SKIP        extra exit codes to ignore, comma separated
#   PHANTOM_EXTRA_ARGS  extra haunt.py flags, e.g. "--model llama3.2:1b"
#   PHANTOM_NIGHT       1 = whisper after successful commands, 00:00-05:00
#   PHANTOM_NIGHT_CHANCE percent chance per command (default: 15)
#
# The command and error are passed through environment variables instead of the
# command line, so quotes and Unicode in error text survive intact.

if (-not $env:PHANTOM_HOME) {
    $candidate = Split-Path -Parent $PSScriptRoot
    if (Test-Path (Join-Path $candidate 'haunt.py')) {
        $env:PHANTOM_HOME = $candidate
    }
    else {
        $env:PHANTOM_HOME = Join-Path $HOME 'PhantomShell'
    }
}

$global:__PhantomScript = Join-Path $env:PHANTOM_HOME 'haunt.py'

if (-not $global:__PhantomPython) {
    foreach ($name in @('python', 'python3', 'py')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) {
            $global:__PhantomPython = $found.Source
            break
        }
    }
}

if (-not $global:__PhantomPython -or -not (Test-Path $global:__PhantomScript)) {
    Write-Warning "PhantomShell: could not find python or haunt.py. Set `$env:PHANTOM_HOME and retry."
    return
}

# Python writes UTF-8; make the console decode it that way.
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }

if (-not $global:__PhantomPromptInstalled) {
    if (-not $global:__PhantomOriginalPrompt) {
        $existing = $function:prompt
        if ($existing) {
            $global:__PhantomOriginalPrompt = $existing
        }
        else {
            $global:__PhantomOriginalPrompt = { "PS $($executionContext.SessionState.Path.CurrentLocation)> " }
        }
    }

    function global:prompt {
        # Capture state FIRST. Every command below clobbers it.
        $phantomOk = $?
        $phantomCode = $global:LASTEXITCODE
        $phantomError = $null

        if (-not $phantomOk) {
            # Ctrl+C is noise, not a mystery.
            $skipCodes = @(130, -1073741510)
            if ($env:PHANTOM_SKIP) {
                $skipCodes += ($env:PHANTOM_SKIP -split ',' | ForEach-Object { [int]$_.Trim() })
            }

            if ($phantomCode -notin $skipCodes -and $env:PHANTOM_OFF -ne '1') {
                if ($global:Error.Count -gt 0 -and $global:Error[0].Exception) {
                    $phantomError = $global:Error[0].Exception.Message
                }
                if (-not $phantomError -and $null -ne $phantomCode -and $phantomCode -ne 0) {
                    $phantomError = "exit code $phantomCode"
                }
                if (-not $phantomError) { $phantomError = 'exit code 1' }

                $historyEntry = $null
                try { $historyEntry = Get-History -Count 1 -ErrorAction Stop } catch { }

                $lastCmd = ''
                $historyId = $null
                if ($historyEntry) {
                    $lastCmd = $historyEntry.CommandLine
                    $historyId = $historyEntry.Id
                }

                # Pressing Enter on an empty prompt must not summon the ghost twice.
                if ($historyId -and $historyId -eq $global:__PhantomLastHistoryId) {
                    $lastCmd = ''
                }

                if ($lastCmd -and $lastCmd -notmatch 'haunt\.py') {
                    # Keep the environment block well under the Windows limit.
                    if ($phantomError.Length -gt 2000) {
                        $phantomError = $phantomError.Substring($phantomError.Length - 2000)
                    }

                    if ($historyId) { $global:__PhantomLastHistoryId = $historyId }

                    $env:PHANTOM_LAST_COMMAND = $lastCmd
                    $env:PHANTOM_LAST_ERROR = $phantomError
                    $env:PHANTOM_SHELL = 'powershell'
                    try {
                        # Read at call time so users can change flags mid-session.
                        $phantomArgs = @()
                        if ($env:PHANTOM_EXTRA_ARGS) {
                            $phantomArgs = @($env:PHANTOM_EXTRA_ARGS -split '\s+' | Where-Object { $_ })
                        }
                        & $global:__PhantomPython $global:__PhantomScript @phantomArgs 2>$null
                    }
                    catch { }
                    finally {
                        Remove-Item Env:PHANTOM_LAST_COMMAND -ErrorAction SilentlyContinue
                        Remove-Item Env:PHANTOM_LAST_ERROR -ErrorAction SilentlyContinue
                    }
                }
            }
        }
        elseif ($env:PHANTOM_NIGHT -eq '1') {
            # A quiet murmur after a successful late-night command. The history
            # id check keeps empty prompts from rolling the dice again.
            $historyEntry = $null
            try { $historyEntry = Get-History -Count 1 -ErrorAction Stop } catch { }

            $historyId = $null
            if ($historyEntry) { $historyId = $historyEntry.Id }

            if (-not ($historyId -and $historyId -eq $global:__PhantomLastHistoryId)) {
                if ($historyId) { $global:__PhantomLastHistoryId = $historyId }

                $nightChance = 15
                if ($env:PHANTOM_NIGHT_CHANCE -match '^\d+$') {
                    $nightChance = [int]$env:PHANTOM_NIGHT_CHANCE
                }

                if ((Get-Date).Hour -lt 5 -and (Get-Random -Maximum 100) -lt $nightChance) {
                    try {
                        $phantomArgs = @()
                        if ($env:PHANTOM_EXTRA_ARGS) {
                            $phantomArgs = @($env:PHANTOM_EXTRA_ARGS -split '\s+' | Where-Object { $_ })
                        }
                        & $global:__PhantomPython $global:__PhantomScript @phantomArgs --night-whisper 2>$null
                    }
                    catch { }
                }
            }
        }

        # Give the original status back, then draw the real prompt.
        $global:LASTEXITCODE = $phantomCode
        & $global:__PhantomOriginalPrompt
    }

    $global:__PhantomPromptInstalled = $true
}
