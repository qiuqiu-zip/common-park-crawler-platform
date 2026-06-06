param(
    [switch]$Full,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
Set-Location $RepoRoot

$env:PYTHONIOENCODING = "utf-8"

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return "py"
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return "python"
    }
    throw "Python was not found. Install Python or add it to PATH."
}

function Invoke-Step {
    param(
        [string]$Name,
        [string[]]$Command,
        [int[]]$SuccessCodes = @(0)
    )

    Write-Host ""
    Write-Host "==> $Name"
    $exe = $Command[0]
    $args = @()
    if ($Command.Count -gt 1) {
        $args = $Command[1..($Command.Count - 1)]
    }
    & $exe @args
    $code = $LASTEXITCODE
    if ($null -eq $code) {
        $code = 0
    }
    if ($SuccessCodes -notcontains $code) {
        throw "$Name failed with exit code $code"
    }
}

function Invoke-DbOrmScan {
    $rg = Get-Command rg -ErrorAction SilentlyContinue
    if (-not $rg) {
        throw "ripgrep (rg) is required for the DB/ORM scan. Install rg or run the documented scan manually."
    }

    $pattern = "\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b"
    Write-Host ""
    Write-Host "==> DB/ORM scan"
    & rg -n -i $pattern src pyproject.toml
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        throw "DB/ORM scan found matches; runtime must remain no-database."
    }
    if ($code -ne 1) {
        throw "DB/ORM scan failed with exit code $code"
    }
    Write-Host "No DB/ORM matches found."
}

function Invoke-GitIgnoreCheck {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "git was not found; skipping git ignore checks."
        return
    }

    foreach ($path in @(".codex/gpt_review_result.md", "test-output/", "data/", ".pytest_cache/")) {
        Write-Host ""
        Write-Host "==> git check-ignore $path"
        & git check-ignore --quiet $path
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            throw "Expected '$path' to be ignored by git."
        }
        Write-Host "$path is ignored."
    }
}

$Python = Get-PythonCommand

if ($SkipInstall) {
    $srcPath = Join-Path $RepoRoot "src"
    $existing = $env:PYTHONPATH
    if ([string]::IsNullOrWhiteSpace($existing)) {
        $env:PYTHONPATH = $srcPath
    } elseif ($existing -notlike "*$srcPath*") {
        $env:PYTHONPATH = "$srcPath;$existing"
    }
    Write-Host "Skipping editable install; PYTHONPATH includes src."
} else {
    Invoke-Step "Install editable package" @($Python, "-m", "pip", "install", "-e", ".[api,dev]")
}

if ((Get-Command crawler-platform -ErrorAction SilentlyContinue) -and -not $SkipInstall) {
    Invoke-Step "CLI console script help" @("crawler-platform", "--help")
} else {
    Invoke-Step "CLI module help" @($Python, "-m", "crawler_platform.cli", "--help")
}

Invoke-Step "Compile source, tests, and scripts" @($Python, "-m", "compileall", "-q", "src", "tests", "scripts")
Invoke-Step "Pytest" @($Python, "-m", "pytest", "-q", "-p", "no:cacheprovider")
Invoke-Step "Quality gate quick" @($Python, "scripts/quality_gate.py", "--quick", "--json-report", "./test-output/delivery/quality-quick.json")
Invoke-Step "Test matrix quick" @($Python, "scripts/run_test_matrix.py", "--quick", "--json-report", "./test-output/delivery/matrix-quick.json")

if ($Full) {
    Invoke-Step "Quality gate full" @($Python, "scripts/quality_gate.py", "--full", "--json-report", "./test-output/delivery/quality-full.json")
    Invoke-Step "Test matrix full" @($Python, "scripts/run_test_matrix.py", "--full", "--json-report", "./test-output/delivery/matrix-full.json")
}

Invoke-DbOrmScan
Invoke-GitIgnoreCheck

Write-Host ""
Write-Host "Delivery verification passed."
