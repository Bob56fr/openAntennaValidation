# Lanceur de la station openAntennaValidation sous Windows (icône du bureau, décision D21).
# Démarre la station dans cette fenêtre, ouvre sa page, et la relance si elle s'arrête sur
# une erreur. Ctrl+C dans la fenêtre arrête la station proprement (SDR libérés).
param(
    [int]$Port = 8660,
    [switch]$NoBrowser
)

$Prefix = $PSScriptRoot
$env:OAV_HOME = Join-Path $env:ProgramData "open-antenna-validation"
$Python = Join-Path $Prefix "venv\Scripts\python.exe"
$Url = "http://localhost:$Port/"
$RestartDelayS = 10
$Host.UI.RawUI.WindowTitle = "openAntennaValidation - Ctrl+C pour arrêter la station"

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    # Already running (second click on the icon): just show the page.
    if (-not $NoBrowser) { Start-Process $Url }
    exit 0
}
if (-not $NoBrowser) {
    # Opens the page once it answers, without holding up the station.
    $wait = "for (`$i = 0; `$i -lt 60; `$i++) { try { Invoke-WebRequest '${Url}api/session' -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process '$Url'; break } catch { Start-Sleep 1 } }"
    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile", "-Command", $wait -WindowStyle Hidden
}

while ($true) {
    & $Python -m open_antenna_validation serve
    $code = $LASTEXITCODE
    if ($code -eq 0) { break }
    Write-Host ""
    Write-Host "La station s'est arrêtée (code $code) : relance dans $RestartDelayS s." -ForegroundColor Yellow
    Write-Host "Fermez cette fenêtre pour l'arrêter. Journaux : $env:OAV_HOME"
    Start-Sleep -Seconds $RestartDelayS
}
