# ===========================================================
# Test-DashboardBackend.ps1
# Backend functional health test for Crypto Futures Dashboard
# Works with TimescaleDB (Docker) + Quart/SocketIO backend
# ===========================================================

# === CONFIG ===
$BackendUrl     = "http://localhost:5000"
$RepoRoot       = "E:\Trading\crypto-futures-dashboard"
$ContinuityJson = "$RepoRoot\docs\continuity_state.json"
$LogPath        = "$RepoRoot\backend\logs\app.log"
$DbContainer    = "futures-db"
$DbUser         = "postgres"
$DbName         = "futures"

# === COLOR HELPERS ===
function Write-Ok($msg)    { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "[ERR]  $msg" -ForegroundColor Red }
function Write-Step($msg)  { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }

# -----------------------------------------------------------
# 1) HEALTH ENDPOINT
# -----------------------------------------------------------
Write-Step "Checking backend /health endpoint..."
try {
    $health = (Invoke-RestMethod "$BackendUrl/health")
    if ($health.status -eq "healthy") {
        Write-Ok "Backend is healthy (v$($health.version))"
        Write-Ok "Continuity Phase: $($health.continuity.phase)"
        Write-Ok "Uptime: $($health.continuity.uptimePct)%"
    } else {
        Write-Warn "Backend responded but not healthy: $($health.status)"
    }
}
catch {
    Write-Err "Failed to reach /health endpoint: $_"
}

# -----------------------------------------------------------
# 2) METRICS ENDPOINT
# -----------------------------------------------------------
Write-Step "Fetching recent metrics..."
try {
    $metrics = Invoke-RestMethod "$BackendUrl/api/metrics/history?limit=5"
    if ($metrics -and $metrics.data -and $metrics.data.Count -gt 0) {
        $first = $metrics.data[0]
        Write-Ok "Loaded $($metrics.data.Count) metrics rows"
        Write-Ok "Sample: $($first.symbol) @ $($first.updated_at)"
    } else {
        Write-Warn "Metrics endpoint returned empty."
    }
}
catch {
    Write-Err "Metrics endpoint error: $_"
}

# -----------------------------------------------------------
# 3) QUANT SUMMARY ENDPOINT
# -----------------------------------------------------------
Write-Step "Checking /api/quant/summary..."
try {
    $quant = Invoke-RestMethod "$BackendUrl/api/quant/summary"
    if ($quant -and $quant.data -and $quant.data.Count -gt 0) {
        Write-Ok "Quant summary OK ($($quant.data.Count) symbols)"
        foreach ($row in $quant.data) {
            Write-Host ("   " + $row.symbol + " -> bias=" + $row.bias + " score=" + $row.confluence_score)
        }
    } else {
        Write-Warn "Quant summary returned empty."
    }
}
catch {
    Write-Err "Quant summary check failed: $_"
}

# -----------------------------------------------------------
# 4) CONTINUITY JSON
# -----------------------------------------------------------
Write-Step "Validating continuity_state.json..."
try {
    if (Test-Path $ContinuityJson) {
        $state = Get-Content $ContinuityJson | ConvertFrom-Json
        Write-Ok "Continuity timestamp: $($state.timestamp)"
        Write-Ok "Backend: $($state.backend)"
        Write-Ok "Uptime: $($state.uptimePct)%"
    } else {
        Write-Warn "Continuity JSON not found at $ContinuityJson"
    }
}
catch {
    Write-Err "Error reading continuity JSON: $_"
}

# -----------------------------------------------------------
# 5) DATABASE RECORD COUNT (TimescaleDB)
# -----------------------------------------------------------
Write-Step "Checking TimescaleDB row count (via Docker)..."
try {
    $cmd = "SELECT COUNT(*) FROM metrics;"
    $raw = docker exec -i $DbContainer psql -U $DbUser -d $DbName -t -c "$cmd" 2>$null
    $count = ($raw | Out-String).Trim() -replace '[^0-9]', ''
    if ($count -and [int]$count -gt 0) {
        Write-Ok "TimescaleDB OK - $count records in metrics table"
    } else {
        Write-Warn "TimescaleDB connected but returned 0 rows"
    }
}
catch {
    Write-Err "Failed to query TimescaleDB: $_"
}

# -----------------------------------------------------------
# 6) RECENT LOGS
# -----------------------------------------------------------
Write-Step "Checking recent backend logs..."
try {
    if (Test-Path $LogPath) {
        $recent = Get-Content $LogPath -Tail 12
        Write-Ok "Recent log entries:"
        $recent | ForEach-Object { Write-Host ("   " + $_) }
    } else {
        Write-Warn "No log file found at $LogPath"
    }
}
catch {
    Write-Err "Error reading log file: $_"
}

Write-Host ""
Write-Host "[DONE] All tests complete." -ForegroundColor Cyan
exit 0
