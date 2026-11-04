# Désinstallation d'openAntennaValidation sous Windows.
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-Purge]
#   -Purge : supprime aussi les données (configuration, base, journaux).
# Python, l'API SDRplay et les pilotes WinUSB, partagés avec d'autres logiciels, sont conservés.
param([switch]$Purge)

$ErrorActionPreference = "Stop"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # Started from the Start menu: ask for the administrator rights.
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Purge) { $argList += "-Purge" }
    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs
    exit 0
}

$Prefix = Join-Path $env:ProgramFiles "open-antenna-validation"
$DataDir = Join-Path $env:ProgramData "open-antenna-validation"
$MenuDir = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\openAntennaValidation"

# Launcher loop first, so it does not restart the station, then Python itself.
foreach ($pattern in @("*start-station.ps1*", "*open_antenna_validation*serve*")) {
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                     Where-Object { $_.CommandLine -like $pattern -and $_.ProcessId -ne $PID })) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2
Unregister-ScheduledTask -TaskName "openAntennaValidation" -Confirm:$false -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName "openAntennaValidation" -ErrorAction SilentlyContinue
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $env:PUBLIC "Desktop\openAntennaValidation.lnk")
if (Test-Path $MenuDir) { Remove-Item -Recurse -Force $MenuDir }
if (Test-Path $Prefix) { Remove-Item -Recurse -Force $Prefix }

if ($Purge) {
    if (Test-Path $DataDir) { Remove-Item -Recurse -Force $DataDir }
    Write-Host "Station désinstallée, données supprimées."
} else {
    Write-Host "Station désinstallée. Données conservées dans $DataDir (-Purge pour les supprimer)."
}
Read-Host "Entrée pour fermer" | Out-Null
