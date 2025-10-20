# Health Sentinel v1.0 – Crypto Futures Dashboard
# Checks backend /health endpoint and logs result every 5 min.

$repo      = "E:\Trading\crypto-futures-dashboard"
$logFile   = Join-Path $repo "docs\health_sentinel.log"
$healthUrl = "http://localhost:5000/health"

if (!(Test-Path (Split-Path $logFile))) { New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null }

function Write-HealthLog($msg,[string]$color="White") {
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $entry = "$timestamp  $msg"
    Add-Content -Path $logFile -Value $entry
    Write-Host $entry -ForegroundColor $color
}

while ($true) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200 -and $response.Content -match '"status":"healthy"') {
            Write-HealthLog "✅ Backend Healthy" "Green"
        } else {
            Write-HealthLog "⚠️ Backend Unhealthy – Code $($response.StatusCode)" "Yellow"
            [console]::beep(900,200)
        }
    } catch {
        Write-HealthLog "❌ Backend Down: $($_.Exception.Message)" "Red"
        [console]::beep(600,400)
    }
    Start-Sleep -Seconds 300   # check every 5 min
}
