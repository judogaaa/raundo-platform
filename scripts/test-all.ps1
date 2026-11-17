<#
.SYNOPSIS
  Full local RAUNDO test gate (no BOT_TOKEN).

.DESCRIPTION
  Runs Ruff, compileall, pytest, Alembic upgrade/check on isolated DB, schema verify.

  Destructive DB ops are allowlisted: ONLY database name "raundo_local_suite"
  on container "raundo_local-postgres-1" published at host port 55432.

  Local report does NOT invent GitHub Actions status (default CiStatus=NOT_CHECKED).

.EXAMPLE
  .\scripts\test-all.ps1
  .\scripts\test-all.ps1 -CiStatus BLOCKED
#>
[CmdletBinding()]
param(
    [string]$PostgresHost = "127.0.0.1",
    [int]$PostgresPort = 55432,
    [string]$PostgresUser = "raundo",
    [string]$PostgresPassword = "raundo_local_only",
    [string]$AdminDb = "postgres",
    [string]$SuiteDb = "raundo_local_suite",
    [string]$ComposeProject = "raundo_local",
    [string]$PostgresContainer = "raundo_local-postgres-1",
    [ValidateSet("NOT_CHECKED", "PASS", "FAIL", "BLOCKED")]
    [string]$CiStatus = "NOT_CHECKED"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot
# Prefer this tree's src over an editable install that may point at another worktree.
$env:PYTHONPATH = (Join-Path $RepoRoot "src") + $(
    if ($env:PYTHONPATH) { [IO.Path]::PathSeparator + $env:PYTHONPATH } else { "" }
)

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$VenvRuff = Join-Path $RepoRoot ".venv\Scripts\ruff.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Missing .venv. Run: python -m venv .venv; .\.venv\Scripts\pip install -e `".[dev]`""
}

$Sha = (git rev-parse HEAD 2>$null)
if (-not $Sha) { $Sha = "UNKNOWN" }
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss K"
$ReportDir = Join-Path $RepoRoot "docs\test-reports"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$ReportPath = Join-Path $ReportDir "latest.md"

$Results = New-Object System.Collections.Generic.List[object]
$Failed = 0
$PytestPassed = $null
$PytestFailed = $null
$PytestErrors = @()
function Add-Step {
    param([string]$Name, [string]$Status, [string]$Detail = "")
    $Results.Add([pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail })
    $color = if ($Status -eq "PASS") { "Green" } elseif ($Status -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("[{0}] {1}" -f $Status, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host ("       {0}" -f $Detail) }
}

function Write-ReportAndExit {
    param([int]$Code)
    $Overall = if ($Failed -eq 0 -and $Code -eq 0) { "PASS" } else { "FAIL" }
    if ($Code -ne 0 -and $Overall -eq "PASS") { $Overall = "FAIL" }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("# RAUNDO local test report")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("- **Date:** $Stamp")
    [void]$sb.AppendLine("- **SHA:** ``$Sha``")
    [void]$sb.AppendLine("- **Overall local:** **$Overall**")
    [void]$sb.AppendLine("- **GitHub Actions (caller-supplied):** ``$CiStatus`` (script does not probe remote CI)")
    [void]$sb.AppendLine("- **BOT_TOKEN:** not used / not required")
    [void]$sb.AppendLine("- **Suite DB:** ``${PostgresHost}:${PostgresPort}/$SuiteDb`` (allowlisted DROP/CREATE only)")
    [void]$sb.AppendLine("- **Pytest:** passed=$PytestPassed failed=$PytestFailed")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("## Steps")
    [void]$sb.AppendLine("")
    foreach ($r in $Results) {
        $extra = ""
        if ($r.Detail) { $extra = " - $($r.Detail.Trim())" }
        [void]$sb.AppendLine("- **$($r.Name):** $($r.Status)$extra")
    }
    if ($PytestErrors.Count -gt 0) {
        [void]$sb.AppendLine("")
        [void]$sb.AppendLine("## Pytest failures")
        foreach ($e in $PytestErrors) { [void]$sb.AppendLine("- ``$e``") }
    }
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("## Policy")
    [void]$sb.AppendLine("- Every PR must run ``.\scripts\test-all.ps1`` before review/merge request.")
    [void]$sb.AppendLine("- DROP/CREATE allowed only for database ``raundo_local_suite`` on container ``raundo_local-postgres-1`` / host port 55432.")
    [void]$sb.AppendLine("- Record remote CI separately as PASS/FAIL/BLOCKED/NOT_CHECKED; never invent CI green in this script.")
    [void]$sb.AppendLine("- Do not disable GitHub Actions or bypass branch protection.")

    [System.IO.File]::WriteAllText($ReportPath, $sb.ToString(), [System.Text.UTF8Encoding]::new($false))
    $stampFile = Join-Path $ReportDir ("{0}-{1}.md" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $Overall.ToLower())
    Copy-Item $ReportPath $stampFile -Force

    Write-Host ""
    Write-Host "Report: $ReportPath" -ForegroundColor Cyan
    Write-Host ("OVERALL LOCAL: {0} (failed steps: {1})" -f $Overall, $Failed) -ForegroundColor $(if ($Overall -eq "PASS") { "Green" } else { "Red" })
    exit $Code
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action,
        [switch]$Fatal
    )
    try {
        $detail = & $Action
        Add-Step -Name $Name -Status "PASS" -Detail ($(if ($null -ne $detail) { "$detail" } else { "" }))
        return $true
    }
    catch {
        $script:Failed += 1
        Add-Step -Name $Name -Status "FAIL" -Detail $_.Exception.Message
        if ($Fatal) {
            Write-Host "FATAL: stopping further DB-dependent steps." -ForegroundColor Red
            Write-ReportAndExit -Code 1
        }
        return $false
    }
}

Write-Host "=== RAUNDO local test-all ===" -ForegroundColor Cyan
Write-Host "SHA: $Sha"
Write-Host ("DB:  {0}:{1}/{2}" -f $PostgresHost, $PostgresPort, $SuiteDb)
Write-Host ("CI status param: {0}" -f $CiStatus)
Write-Host ""

# --- Fail-closed: Docker ---
Invoke-Step -Fatal "Docker engine" {
    docker info 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop engine not available" }
    "ok"
} | Out-Null

# --- Fail-closed: allowlist + target identity BEFORE any DROP ---
Invoke-Step -Fatal "DB safety allowlist (suite name/container/port)" {
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $env:RAUNDO_GATE_SUITE_DB = $SuiteDb
        $env:RAUNDO_GATE_PG_CONTAINER = $PostgresContainer
        $env:RAUNDO_GATE_PG_PORT = "$PostgresPort"
        $guardPy = Join-Path $env:TEMP "raundo_gate_allowlist.py"
        @"
import os
from raundo.testing.db_safety import (
    assert_suite_db_drop_allowed,
    assert_postgres_container_allowed,
    assert_suite_host_port,
)
assert_suite_db_drop_allowed(os.environ["RAUNDO_GATE_SUITE_DB"])
assert_postgres_container_allowed(os.environ["RAUNDO_GATE_PG_CONTAINER"])
assert_suite_host_port(int(os.environ["RAUNDO_GATE_PG_PORT"]))
print("allowlist_ok")
"@ | Set-Content -Path $guardPy -Encoding ascii
        $out = & $VenvPython $guardPy 2>&1 | ForEach-Object { "$_" }
        if ($LASTEXITCODE -ne 0) { throw ($out -join "`n") }
        ($out -join " ").Trim()
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
} | Out-Null

Invoke-Step -Fatal "RAUNDO Postgres container healthy + port binding" {
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $st = docker inspect -f "{{.State.Health.Status}}" $PostgresContainer 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "container missing; start compose project $ComposeProject with port remap to $PostgresPort"
        }
        if ("$st".Trim() -ne "healthy") { throw "status=$st (need healthy)" }

        # Confirm published host port matches expected suite port (fail closed).
        $bindings = docker inspect -f '{{json .HostConfig.PortBindings}}' $PostgresContainer 2>&1
        if ($LASTEXITCODE -ne 0) { throw "cannot read PortBindings" }
        $portKey = '5432/tcp'
        if ("$bindings" -notmatch [regex]::Escape('"' + $PostgresPort + '"') -and `
            "$bindings" -notmatch [regex]::Escape(":$PostgresPort")) {
            # PortBindings JSON uses HostPort field
            if ("$bindings" -notmatch ('"HostPort"\s*:\s*"' + $PostgresPort + '"')) {
                throw "container $PostgresContainer is not published on host port $PostgresPort; bindings=$bindings"
            }
        }

        $img = docker inspect -f "{{.Config.Image}}" $PostgresContainer 2>&1
        if ("$img" -notmatch "postgres") { throw "unexpected image: $img" }
        "healthy image=$img hostPort=$PostgresPort"
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
} | Out-Null

Invoke-Step -Fatal "Recreate isolated suite database (allowlisted only)" {
    # Re-check allowlist immediately before DROP (defense in depth).
    $env:RAUNDO_GATE_SUITE_DB = $SuiteDb
    & $VenvPython -c "import os; from raundo.testing.db_safety import assert_suite_db_drop_allowed; assert_suite_db_drop_allowed(os.environ['RAUNDO_GATE_SUITE_DB'])"
    if ($LASTEXITCODE -ne 0) { throw "pre-DROP allowlist refused $SuiteDb" }

    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $dropSql = @"
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '$SuiteDb' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $SuiteDb;
"@
        $dropOut = $dropSql | docker exec -i $PostgresContainer psql -U $PostgresUser -d $AdminDb -v ON_ERROR_STOP=1 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "failed dropping ${SuiteDb}: $dropOut" }
        $createOut = "CREATE DATABASE $SuiteDb OWNER $PostgresUser;" |
            docker exec -i $PostgresContainer psql -U $PostgresUser -d $AdminDb -v ON_ERROR_STOP=1 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "failed creating ${SuiteDb}: $createOut" }
        $exists = docker exec $PostgresContainer psql -U $PostgresUser -d $AdminDb -tAc "SELECT 1 FROM pg_database WHERE datname='$SuiteDb'" 2>&1
        if ("$exists".Trim() -ne "1") { throw "database $SuiteDb missing after create" }
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
    "created $SuiteDb"
} | Out-Null

$env:DATABASE_URL = "postgresql+asyncpg://${PostgresUser}:${PostgresPassword}@${PostgresHost}:${PostgresPort}/${SuiteDb}"
$env:BOT_TOKEN = ""
# The suite database was just recreated by this script, so PostgreSQL integration
# tests must actually run here. Removing the flag silently skipped 60+ tests and
# reported a green gate without DB coverage.
$env:RAUNDO_POSTGRES_SUITE_TESTS = "1"

# The suite DB is brand new after DROP/CREATE: apply the real schema first,
# otherwise PostgreSQL integration tests fail on missing tables.
Invoke-Step "Alembic upgrade head (fresh suite DB)" {
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPython -m alembic upgrade head 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed with exit $LASTEXITCODE" }
        $cur = (& $VenvPython -m alembic current 2>&1 | ForEach-Object { "$_" }) -join "`n"
        if ($cur -notmatch "head") { throw "not at head: $cur" }
        "ok"
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
} | Out-Null

Invoke-Step "Ruff" {
    $prevNoColor = $env:NO_COLOR
    $env:NO_COLOR = "1"
    try {
        # Out-Host: keep console output, do not capture into step Detail
        & $VenvRuff check . | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "ruff failed with exit $LASTEXITCODE" }
    }
    finally {
        if ($null -eq $prevNoColor) { Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue }
        else { $env:NO_COLOR = $prevNoColor }
    }
    "ok"
} | Out-Null

Invoke-Step "compileall src" {
    & $VenvPython -m compileall -q src
    if ($LASTEXITCODE -ne 0) { throw "compileall failed with exit $LASTEXITCODE" }
    "ok"
} | Out-Null

Invoke-Step "pytest (full suite, incl. feed_update + security)" {
    $outFile = Join-Path $env:TEMP "raundo-pytest-out.txt"
    & $VenvPython -m pytest -q --tb=line 2>&1 | Tee-Object -FilePath $outFile | Out-Host
    $code = $LASTEXITCODE
    $text = Get-Content $outFile -Raw -ErrorAction SilentlyContinue
    if ($text -match "(\d+) passed") { $script:PytestPassed = [int]$Matches[1] }
    if ($text -match "(\d+) failed") { $script:PytestFailed = [int]$Matches[1] }
    elseif ($code -eq 0) { $script:PytestFailed = 0 }
    if ($text) {
        $script:PytestErrors = @(
            [regex]::Matches($text, "(?m)^FAILED .+$") | ForEach-Object { $_.Value }
        )
    }
    if ($code -ne 0) { throw "pytest exit $code" }
    "passed=$script:PytestPassed failed=$script:PytestFailed"
} | Out-Null

Invoke-Step "pytest security/ownership subset" {
    & $VenvPython -m pytest -q --tb=line `
        tests/test_telegram_wiring.py `
        tests/test_config.py `
        tests/test_organizations.py `
        tests/test_startup_service.py `
        tests/test_investor_service.py `
        tests/test_test_all_db_safety.py `
        tests/test_feed_admin_and_wizards.py::test_feed_admin_unauthorized_denied `
        tests/test_feed_admin_and_wizards.py::test_feed_admin_authorized_reaches_handler
    if ($LASTEXITCODE -ne 0) { throw "security subset failed with exit $LASTEXITCODE" }
    "ok"
} | Out-Null

Invoke-Step "pytest feed_update integration files" {
    & $VenvPython -m pytest -q --tb=line `
        tests/test_feed_admin_and_wizards.py `
        tests/test_dispatcher_feed_update.py
    if ($LASTEXITCODE -ne 0) { throw "feed_update tests failed with exit $LASTEXITCODE" }
    "ok"
} | Out-Null

Invoke-Step "Alembic upgrade head" {
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPython -c "from raundo.config import get_settings; get_settings.cache_clear()"
        & $VenvPython -m alembic upgrade head 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed with exit $LASTEXITCODE" }
        $cur = (& $VenvPython -m alembic current 2>&1 | ForEach-Object { "$_" }) -join "`n"
        if ($cur -notmatch "head") { throw "not at head: $cur" }
        (($cur -split "`n") | Where-Object { $_ -match "2026|head" } | Select-Object -First 1).Trim()
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
} | Out-Null

Invoke-Step "Alembic check" {
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $chkLines = @(& $VenvPython -m alembic check 2>&1 | ForEach-Object { "$_" })
        $chk = $chkLines -join "`n"
        Write-Host $chk
        if ($LASTEXITCODE -ne 0) { throw "alembic check failed with exit $LASTEXITCODE" }
        if ($chk -notmatch "No new upgrade operations detected") {
            throw "unexpected alembic check output"
        }
        "models match DB"
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
} | Out-Null

Invoke-Step "pytest premium migration (postgres suite only)" {
    $env:RAUNDO_POSTGRES_SUITE_TESTS = "1"
    & $VenvPython -m pytest -q --tb=short tests/test_premium_migration.py
    if ($LASTEXITCODE -ne 0) { throw "premium migration tests failed with exit $LASTEXITCODE" }
    "ok"
} | Out-Null

Invoke-Step "Alembic still at head after migration tests" {
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $cur = (& $VenvPython -m alembic current 2>&1 | ForEach-Object { "$_" }) -join "`n"
        Write-Host $cur
        $expected = (& $VenvPython -m alembic heads 2>&1 | ForEach-Object { "$_" }) -join "`n"
        $m = $expected | Select-String -Pattern "^(\S+)\s*\(head\)$"
        if (-not $m) { throw "cannot resolve alembic head from: $expected" }
        if ($m.Count -ne 1) { throw "expected exactly one alembic head, got $($m.Count): $expected" }
        $headId = $m[0].Matches[0].Groups[1].Value
        if ($cur -notmatch [regex]::Escape($headId)) { throw "expected head $headId after migration tests: $cur" }
        "head ok"
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
} | Out-Null

Invoke-Step "Schema constraints (orgs, media, reviews)" {
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $sqlPath = Join-Path $RepoRoot "scripts\verify_schema.sql"
        Get-Content $sqlPath -Raw |
            docker exec -i $PostgresContainer psql -U $PostgresUser -d $SuiteDb -v ON_ERROR_STOP=1 2>&1 |
            ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "schema verify failed with exit $LASTEXITCODE" }
        "verify_schema.sql OK"
    }
    finally {
        $ErrorActionPreference = $prevEa
    }
} | Out-Null

if ($Failed -eq 0) {
    Write-ReportAndExit -Code 0
}
Write-ReportAndExit -Code 1
