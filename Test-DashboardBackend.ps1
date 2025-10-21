# ===========================================================
# Test-DashboardBackend.ps1
# Full backend functional health test for Crypto Futures Dashboard
# ===========================================================

# === CONFIG ===
$BackendUrl = "http://localhost:5000"
$RepoRoot   = "E:\Trading\crypto-futures-dashboard"
$DbPath     = "$RepoRoot\backend\src\futuresboard\futures.db"
$ContinuityJson = "$RepoRoot\docs\continuity_state.json"
$LogPath    = "$RepoRoot\backend\logs\app.log"

# === COLOR HELPERS ===
function Write-Ok($msg)    { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Step($msg)  { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }

# === 1) HEALTH ENDPOINT ===
Write-Step "Checking backend /health endpoint..."
try {
    $health = (curl "$BackendUrl/health" | ConvertFrom-Json)
    if ($health.status -eq "healthy") {
        Write-Ok "Backend responded: $($health.status) (v$($health.version))"
        Write-Ok "Continuity Phase: $($health.continuity.phase)"
        Write-Ok "UptimePct: $($health.continuity.uptimePct)%"
    } else {
        Write-Warn "Backend responded but not healthy: $($health.status)"
    }
}
catch {
    Write-Err "Health endpoint failed: $_"
}

# === 2) METRICS ENDPOINT ===
Write-Step "Fetching /api/metrics..."
try {
    $metrics = curl "$BackendUrl/api/metrics?tf=5m" | ConvertFrom-Json
    if ($metrics -and $metrics.Count -gt 0) {
        $first = $metrics[0]
        Write-Ok "Metrics loaded: $($metrics.Count) entries."
        Write-Ok "Sample: $($first.symbol) -> LS=$($first.global_ls_5m) OI=$($first.oi_abs_usd)"
    } else {
        Write-Warn "Metrics endpoint returned empty."
    }
}
catch {
    Write-Err "Metrics endpoint error: $_"
}

# === 3) HISTORY ENDPOINT ===
Write-Step "Testing /api/metrics/BTCUSDT/history..."
try {
    $hist = curl "$BackendUrl/api/metrics/BTCUSDT/history?tf=5m&limit=10" | ConvertFrom-Json
    if ($hist -and $hist.Count -gt 0) {
        Write-Ok "History endpoint OK (records: $($hist.Count))"
    } else {
        Write-Warn "History endpoint empty."
    }
}
catch {
    Write-Err "History endpoint error: $_"
}

# === 4) CONTINUITY JSON ===
Write-Step "Validating docs/continuity_state.json..."
try {
    if (Test-Path $ContinuityJson) {
        $state = Get-Content $ContinuityJson | ConvertFrom-Json
        Write-Ok "Continuity timestamp: $($state.timestamp)"
        Write-Ok "Backend state: $($state.backend)"
        Write-Ok "UptimePct: $($state.uptimePct)%"
    } else {
        Write-Warn "Continuity JSON not found at $ContinuityJson"
    }
}
catch {
    Write-Err "Error reading continuity_state.json: $_"
}

# === 5) DATABASE ROW COUNT ===
Write-Step "Checking metrics table record count..."
try {
    $sqliteExe = "sqlite3.exe"
    if (-not (Get-Command $sqliteExe -ErrorAction SilentlyContinue)) {
        $sqliteExe = "$env:ProgramFiles\SQLite\sqlite3.exe"
    }
    if (Test-Path $DbPath -and (Test-Path $sqliteExe)) {
        $count = & $sqliteExe $DbPath "SELECT COUNT(*) FROM metrics;"
        if ($count -gt 0) {
            Write-Ok "Database OK - $count metrics rows"
        } else {
            Write-Warn "Database found but 0 rows in metrics."
        }
    } else {
        Write-Warn "sqlite3.exe or DB not found; skipping."
    }
}
catch {
    Write-Err "Database check failed: $_"
}

# === 6) LOG HEALTH ===
Write-Step "Scanning backend logs..."
try {
    if (Test-Path $LogPath) {
        $recent = Get-Content $LogPath -Tail 10
        Write-Ok "Recent log entries:"
        $recent | ForEach-Object { Write-Host "   $_" }
    } else {
        Write-Warn "Log file not found: $LogPath"
    }
}
catch {
    Write-Err "Error reading logs: $_"
}

Write-Host ""
Write-Host "[END] Test suite completed." -ForegroundColor Cyan
exit 0
