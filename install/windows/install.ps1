# Installation d'openAntennaValidation - Windows 10/11 64 bits (décision D21).
#
# Lancer install.cmd (il demande les droits d'administrateur), ou, dans un PowerShell
# administrateur :
#   powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 [options]
#
#   -Sdr LISTE      SDR de la station, séparés par des virgules : sdrplay, rtlsdr, airspyhf
#                   (le premier est le SDR principal). Par défaut : ceux de la configuration
#                   existante, sinon sdrplay.
#   -Import DOSSIER reprend une station existante (config.toml et oav.sqlite3 copiés depuis
#                   une autre machine, par exemple /var/lib/open-antenna-validation).
#   -Port N         port de la page web (8660 par défaut).
#   -AutoStart      lance la station à l'ouverture de session de l'utilisateur.
#   -User NOM       utilisateur Windows qui fait tourner la station (par défaut : celui de la
#                   session ouverte).
#
# Pas de service Windows : la station tourne dans la session de l'utilisateur, dans une
# fenêtre réduite, et se relance seule si elle s'arrête sur une erreur. Debian reste la
# plateforme de référence pour les campagnes longues.
# Idempotent : relancer le script met à jour la station après une mise à jour du dépôt.

[CmdletBinding()]
param(
    [string]$Sdr = "",
    [string]$Import = "",
    [int]$Port = 8660,
    [switch]$AutoStart,
    [string]$User = ""
)

$ErrorActionPreference = "Stop"
$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Prefix = Join-Path $env:ProgramFiles "open-antenna-validation"
$DataDir = Join-Path $env:ProgramData "open-antenna-validation"
$LibDir = Join-Path $Prefix "lib"
$Downloads = Join-Path $Prefix "downloads"
$Venv = Join-Path $Prefix "venv"
$Log = Join-Path $env:ProgramData "oav-install.log"
$MenuDir = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\openAntennaValidation"
$TaskName = "openAntennaValidation"
$FirewallRule = "openAntennaValidation"
$SdrplayUrl = "https://www.sdrplay.com/downloads/"
$SdrplayService = "SDRplayAPIService"

# Vendor binaries. RTL-SDR Blog and Zadig: pinned release, SHA-256 checked. Airspy publishes
# its Windows library only inside the SDR# package, which changes without notice: its DLLs
# are checked by their Authenticode signature (signer AIRSPY) instead.
$RtlsdrZip = @{
    Url    = "https://github.com/rtlsdrblog/rtl-sdr-blog/releases/download/V1.4.0/Release.zip"
    Sha256 = "7EF33F1304647F65E5E0FDE43637A73D54F076E91E651A3CECC4F55A17FD9815"
    Name   = "rtl-sdr-blog-V1.4.0.zip"
}
$Zadig = @{
    Url    = "https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe"
    Sha256 = "4ECAA95DF3DA3621486A043AEF8B3050B8BAFE7C901402871E816229EF82039B"
    Name   = "zadig-2.9.exe"
}
$AirspyZip = @{
    Url    = "https://airspy.com/downloads/sdrsharp-x64.zip"
    Name   = "sdrsharp-x64.zip"
    Signer = "O=AIRSPY"
    Files  = @("airspyhf.dll", "libusb-1.0.dll", "pthreadVC2.dll")
}
# USB identifiers: RTL2832U dongles, Airspy HF+ family.
$RtlUsb = @("VID_0BDA&PID_2838", "VID_0BDA&PID_2832")
$AirspyUsb = @("VID_03EB&PID_800C")

function Step([string]$Text) { Write-Host ""; Write-Host ("=== {0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Text) }
function Ok([string]$Text) { Write-Host "OK : $Text" }
function Warn([string]$Text) { Write-Host "ATTENTION : $Text" -ForegroundColor Yellow }
function Fail([string]$Text, [int]$Code = 1) {
    Write-Host "ERREUR : $Text" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    exit $Code
}
# Native commands do not throw: check their exit code.
function Run([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { Fail "$What (code $LASTEXITCODE)." }
}

# Output of a native command whose failure is expected (stderr ignored): PowerShell 5.1 turns
# redirected stderr into a terminating error under "Stop".
function Try-Native([scriptblock]$Command) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & $Command 2>$null } catch { $out = $null } finally { $ErrorActionPreference = $saved }
    if ($LASTEXITCODE -ne 0) { return $null }
    return $out
}

function Get-Verified([hashtable]$Item) {
    New-Item -ItemType Directory -Force $Downloads | Out-Null
    $path = Join-Path $Downloads $Item.Name
    if (Test-Path $path) {
        if (-not $Item.Sha256 -or (Get-FileHash $path -Algorithm SHA256).Hash -eq $Item.Sha256) { return $path }
        Remove-Item $path -Force
    }
    Write-Host "Téléchargement de $($Item.Url)"
    $tmp = "$path.part"
    Invoke-WebRequest -Uri $Item.Url -OutFile $tmp -UseBasicParsing
    if ($Item.Sha256) {
        $hash = (Get-FileHash $tmp -Algorithm SHA256).Hash
        if ($hash -ne $Item.Sha256) {
            Remove-Item $tmp -Force
            Fail "empreinte SHA-256 de $($Item.Name) inattendue ($hash) : fichier corrompu ou modifié."
        }
        Ok "$($Item.Name), empreinte SHA-256 vérifiée"
    }
    Move-Item $tmp $path -Force
    return $path
}

# USB devices of the given identifiers, with the Windows driver bound to them.
function Get-UsbDevices([string[]]$Ids) {
    $found = @()
    foreach ($dev in @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue)) {
        $id = [string]$dev.InstanceId
        foreach ($usb in $Ids) {
            # Composite dongles: the receiver is interface 0.
            if ($id -like "USB\$usb\*" -or $id -like "USB\$usb&MI_00\*") {
                $service = (Get-PnpDeviceProperty -InstanceId $id -KeyName DEVPKEY_Device_Service -ErrorAction SilentlyContinue).Data
                $found += [pscustomobject]@{ Name = $dev.FriendlyName; Id = $id; Service = [string]$service }
            }
        }
    }
    return $found
}

function New-Shortcut([string]$Path, [string]$Target, [string]$Arguments, [string]$Description,
                      [string]$Icon = "", [int]$WindowStyle = 1) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $Target
    $link.Arguments = $Arguments
    $link.Description = $Description
    $link.WorkingDirectory = $Prefix
    $link.WindowStyle = $WindowStyle
    if ($Icon) { $link.IconLocation = $Icon }
    $link.Save()
}

# Processes of an installed station: the launcher loop first, so it does not restart the
# service process, then Python itself.
function Stop-Station {
    $stopped = $false
    foreach ($pattern in @("*start-station.ps1*", "*open_antenna_validation*serve*")) {
        foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                         Where-Object { $_.CommandLine -like $pattern -and $_.ProcessId -ne $PID })) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
    }
    if ($stopped) { Start-Sleep -Seconds 2; Write-Host "Station arrêtée pendant la mise à jour" }
}

New-Item -ItemType Directory -Force $DataDir | Out-Null
Start-Transcript -Path $Log -Append | Out-Null

Step "1. Contrôle du système et des droits"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "lancer ce script en administrateur (install.cmd le fait)."
}
$os = Get-CimInstance Win32_OperatingSystem
if ([Environment]::OSVersion.Version.Major -lt 10 -or -not [Environment]::Is64BitOperatingSystem) {
    Fail "Windows 10 ou 11 64 bits requis ($($os.Caption))."
}
if ($Import -and -not (Test-Path (Join-Path $Import "config.toml"))) { Fail "$Import\config.toml introuvable." }
if (-not $User) {
    # The elevated account can differ from the one of the open session.
    $User = (Get-CimInstance Win32_ComputerSystem).UserName
    if (-not $User) { $User = "$env:USERDOMAIN\$env:USERNAME" }
}
try { $UserSid = (New-Object Security.Principal.NTAccount($User)).Translate([Security.Principal.SecurityIdentifier]).Value }
catch { Fail "utilisateur Windows inconnu : $User (option -User)." }
Ok "$($os.Caption), station lancée par $User"

Step "2. Python"
$Python = ""
foreach ($v in @("3.13", "3.12", "3.11")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = Try-Native { & py "-$v" -c "import sys; print(sys.executable)" }
        if ($exe) { $Python = ([string]$exe).Trim(); break }
    }
}
if (-not $Python) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "Python 3.11 ou plus récent introuvable : installez-le depuis https://www.python.org/downloads/windows/ (pour tous les utilisateurs, avec le lanceur py), puis relancez." 3
    }
    Write-Host "Installation de Python 3.12 (winget, pour tous les utilisateurs)"
    Run "installation de Python par winget" { winget install --id Python.Python.3.12 --exact --scope machine --silent --accept-package-agreements --accept-source-agreements }
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $exe = Try-Native { & py -3.12 -c "import sys; print(sys.executable)" }
    if (-not $exe) { Fail "Python installé mais introuvable : relancez ce script dans une nouvelle fenêtre." }
    $Python = ([string]$exe).Trim()
}
Ok "Python : $Python"

# SDR of the station: -Sdr, else those of the existing (or imported) configuration, else sdrplay.
$MainDriver = ""
if ($Sdr) {
    $SdrList = @($Sdr.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $MainDriver = $SdrList[0]
} else {
    $candidates = @(Join-Path $DataDir "config.toml")
    if ($Import) { $candidates = @(Join-Path $Import "config.toml") + $candidates }
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) {
        $code = @'
import sys, tomllib
hw = tomllib.load(open(sys.argv[1], "rb")).get("hardware", {})
main = hw.get("driver", "sdrplay")
drivers = [main] + [o.get("driver") or main for o in hw.get("others", [])]
# A library installed by an earlier run (-Sdr) still belongs to the station.
drivers += [d for d in ("rtlsdr", "airspyhf")
            if str(hw.get(d, {}).get("library_path", "")).lower().endswith(".dll")]
print(",".join(dict.fromkeys(d for d in drivers if d != "simulated")) or "sdrplay")
'@
        $list = $code | & $Python - $found
        if ($LASTEXITCODE -ne 0) { Fail "lecture de $found impossible." }
        $SdrList = @(([string]$list).Trim().Split(","))
    } else {
        $SdrList = @("sdrplay")
        $MainDriver = "sdrplay"  # first installation
    }
}
foreach ($d in $SdrList) {
    if ($d -notin @("sdrplay", "rtlsdr", "airspyhf")) { Fail "SDR inconnu dans -Sdr : $d (sdrplay, rtlsdr ou airspyhf)." }
}
Ok ("SDR de la station : " + ($SdrList -join ","))

if ($SdrList -contains "sdrplay") {
    Step "3. API SDRplay"
    $dll = Join-Path $env:ProgramFiles "SDRplay\API\x64\sdrplay_api.dll"
    $service = Get-Service -Name $SdrplayService -ErrorAction SilentlyContinue
    if (-not (Test-Path $dll) -or -not $service) {
        Write-Host @"
L'API SDRplay 3.x n'est pas installée. Elle est propriétaire : elle ne peut pas être
fournie avec ce logiciel ni téléchargée automatiquement.
  1. Téléchargez et installez « SDRplay API for Windows » : $SdrplayUrl
  2. Relancez ce script.
"@
        Fail "API SDRplay absente." 3
    }
    Set-Service -Name $SdrplayService -StartupType Automatic
    if ($service.Status -ne "Running") { Start-Service -Name $SdrplayService }
    Ok "API SDRplay présente, service $SdrplayService actif"
}

Step "4. Station existante et port"
Stop-Station
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Fail "le port TCP $Port est déjà utilisé : relancez avec -Port <port libre>."
}
Ok "page web sur le port $Port"

$RtlsdrLib = ""
$AirspyhfLib = ""
if ($SdrList -contains "rtlsdr") {
    Step "5a. Bibliothèque librtlsdr (branche RTL-SDR Blog)"
    $zip = Get-Verified $RtlsdrZip
    $dest = Join-Path $LibDir "rtlsdr"
    $tmp = Join-Path $Downloads "rtl-sdr-blog"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item (Join-Path $tmp "x64\*") $dest -Force
    Remove-Item $tmp -Recurse -Force
    # rtlsdr.dll and the rtl_* tools need the VC++ 2015+ runtime: the one of Python is copied
    # next to them (rtl_eeprom.exe runs outside Python).
    $pyBase = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
    foreach ($rt in @("vcruntime140.dll", "vcruntime140_1.dll")) {
        $src = Join-Path $pyBase $rt
        if (Test-Path $src) { Copy-Item $src $dest -Force }
    }
    $RtlsdrLib = Join-Path $dest "rtlsdr.dll"
    Ok $RtlsdrLib
}
if ($SdrList -contains "airspyhf") {
    Step "5b. Bibliothèque libairspyhf (paquet SDR# d'Airspy)"
    $zip = Get-Verified $AirspyZip
    $dest = Join-Path $LibDir "airspyhf"
    $tmp = Join-Path $Downloads "airspy"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp
    New-Item -ItemType Directory -Force $dest | Out-Null
    foreach ($name in $AirspyZip.Files) {
        $src = Get-ChildItem -Path $tmp -Recurse -Filter $name | Select-Object -First 1
        if (-not $src) { Fail "$name absent du paquet $($AirspyZip.Name)." }
        $sig = Get-AuthenticodeSignature $src.FullName
        if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notlike "*$($AirspyZip.Signer)*") {
            Fail "$name : signature Airspy absente ou invalide ($($sig.Status))."
        }
        Copy-Item $src.FullName $dest -Force
    }
    Remove-Item $tmp -Recurse -Force
    Remove-Item (Join-Path $Downloads $AirspyZip.Name) -Force  # always take the current one
    $AirspyhfLib = Join-Path $dest "airspyhf.dll"
    Ok "$AirspyhfLib, signature AIRSPY vérifiée"
}
if ($RtlsdrLib -or $AirspyhfLib) {
    Step "5c. Pilote USB WinUSB (Zadig)"
    $zadig = Get-Verified $Zadig
    Copy-Item $zadig (Join-Path $LibDir "zadig.exe") -Force
    $ids = @()
    if ($RtlsdrLib) { $ids += $RtlUsb }
    if ($AirspyhfLib) { $ids += $AirspyUsb }
    $todo = @(Get-UsbDevices $ids | Where-Object { $_.Service -ne "WinUSB" })
    if ($todo.Count -gt 0) {
        $todo | ForEach-Object { Write-Host "  $($_.Name) : pilote « $($_.Service) »" }
        Write-Host @"
Ces SDR ont besoin du pilote WinUSB. Zadig va s'ouvrir :
  1. Menu Options : cochez « List All Devices ».
  2. Choisissez le SDR (RTL-SDR : « Bulk-In, Interface (Interface 0) »).
  3. Pilote cible « WinUSB », puis « Replace Driver » (ou « Install Driver »).
  4. Recommencez pour chaque SDR, puis fermez Zadig.
"@
        Start-Process -FilePath (Join-Path $LibDir "zadig.exe") -Wait
        $left = @(Get-UsbDevices $ids | Where-Object { $_.Service -ne "WinUSB" })
        if ($left.Count -gt 0) { Warn "pilote WinUSB toujours absent pour $($left.Count) SDR : menu Démarrer, « Pilote USB des SDR (Zadig) »." }
        else { Ok "pilote WinUSB en place" }
    } else {
        Ok "aucun SDR USB à équiper (branchés plus tard : menu Démarrer, « Pilote USB des SDR (Zadig) »)"
    }
}

Step "5d. Décodeurs des modes numériques (WSJT-X)"
# FT8, FT4 et WSPR sont décodés par les programmes de WSJT-X, installé par l'utilisateur
# depuis le site de ses auteurs (comme l'API SDRplay). L'installeur le cherche seulement.
$WsjtxBin = ""
foreach ($dir in @("C:\WSJT\wsjtx\bin", (Join-Path $env:ProgramFiles "wsjtx\bin"))) {
    if (Test-Path (Join-Path $dir "jt9.exe")) { $WsjtxBin = $dir; break }
}
if ($WsjtxBin) {
    Ok "WSJT-X : $WsjtxBin"
    if (-not (Test-Path (Join-Path $WsjtxBin "ft4code.exe"))) {
        Warn "WSJT-X pour Windows n'a pas d'encodeur FT4 ni WSPR : ces mesures suivront les tons lus dans le signal (moins précises près du seuil de décodage). Debian n'a pas cette limite."
    }
} else {
    Warn "WSJT-X introuvable : les campagnes FT8/FT4/WSPR seront refusées. Installez WSJT-X (https://wsjt.sourceforge.io/wsjtx.html) puis relancez ce script."
}

Step "6. Environnement Python"
New-Item -ItemType Directory -Force $Prefix | Out-Null
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { Run "création de l'environnement Python" { & $Python -m venv $Venv } }
Run "mise à jour de pip" { & $VenvPython -m pip install --quiet --upgrade pip }
Run "installation de la station" { & $VenvPython -m pip install --quiet --upgrade $RepoDir }
Ok (& $VenvPython -m open_antenna_validation --version)

Step "7. Configuration"
$env:OAV_HOME = $DataDir
if ($Import) {
    if (Test-Path (Join-Path $DataDir "config.toml")) { Fail "une station existe déjà dans $DataDir : import refusé pour ne rien écraser." }
    Copy-Item (Join-Path $Import "config.toml") $DataDir
    if (Test-Path (Join-Path $Import "oav.sqlite3")) { Copy-Item (Join-Path $Import "oav.sqlite3") $DataDir }
    Write-Host "Station importée depuis $Import"
}
# Keeps the identifier, call sign, gain and password; asks for the password when missing.
# The main SDR is changed only by -Sdr (or on the first installation).
if ($MainDriver) { & $VenvPython -m open_antenna_validation init --driver $MainDriver }
else { & $VenvPython -m open_antenna_validation init }
if ($LASTEXITCODE -ne 0) { Fail "création de la configuration impossible." }
$code = @'
import sys
from open_antenna_validation.config import CONFIG_FILENAME, default_home, load_config, save_config
path = default_home() / CONFIG_FILENAME
config = load_config(path)
config.web.port = int(sys.argv[1])
# Paths of the libraries installed here ("-": none); a Linux path (imported station) is
# cleared, another path is kept.
rtlsdr, airspyhf, wsjtx = ("" if a == "-" else a for a in (sys.argv[2:5] + ["-"] * 3)[:3])
if wsjtx:
    config.digital.wsjtx_path = wsjtx
for section, built in ((config.hardware.rtlsdr, rtlsdr),
                       (config.hardware.airspyhf, airspyhf),
                       (config.hardware.sdrplay, "")):
    if built:
        section.library_path = built
    elif section.library_path.startswith("/"):
        section.library_path = ""
save_config(config, path)
'@
# Empty arguments are dropped by PowerShell 5.1: "-" stands for none.
$a2 = if ($RtlsdrLib) { $RtlsdrLib } else { "-" }
$a3 = if ($AirspyhfLib) { $AirspyhfLib } else { "-" }
$a4 = if ($WsjtxBin) { $WsjtxBin } else { "-" }
$code | & $VenvPython - $Port $a2 $a3 $a4
if ($LASTEXITCODE -ne 0) { Fail "écriture de la configuration impossible." }
# Data readable only by the system, the administrators and the station's user (password
# hash, WordPress tokens), like the 0750 directory under Linux.
Run "droits sur $DataDir" { icacls $DataDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*${UserSid}:(OI)(CI)M" /T /Q }
Ok "configuration dans $DataDir"

Step "8. Détection des SDR"
& $VenvPython -m open_antenna_validation detect
if ($LASTEXITCODE -ne 0) { Warn "aucun SDR détecté (branché ? utilisé par un autre logiciel ? pilote WinUSB ?)" }

Step "9. Lanceur, raccourcis et pare-feu"
$launcher = Join-Path $Prefix "start-station.ps1"
Copy-Item (Join-Path $PSScriptRoot "start-station.ps1") $launcher -Force
Copy-Item (Join-Path $PSScriptRoot "uninstall.ps1") (Join-Path $Prefix "uninstall.ps1") -Force
$psExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$launchArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -Port $Port"
$icon = Join-Path $env:SystemRoot "System32\imageres.dll,1023"
New-Item -ItemType Directory -Force $MenuDir | Out-Null
# Window minimised (7): the station shows in the taskbar; Ctrl+C in it stops it cleanly.
New-Shortcut (Join-Path $env:PUBLIC "Desktop\openAntennaValidation.lnk") $psExe $launchArgs "Lancer la station et ouvrir sa page" $icon 7
New-Shortcut (Join-Path $MenuDir "openAntennaValidation.lnk") $psExe $launchArgs "Lancer la station et ouvrir sa page" $icon 7
Set-Content -Path (Join-Path $MenuDir "Page de la station.url") -Value "[InternetShortcut]`r`nURL=http://localhost:$Port/" -Encoding ASCII
if ($RtlsdrLib -or $AirspyhfLib) {
    New-Shortcut (Join-Path $MenuDir "Pilote USB des SDR (Zadig).lnk") (Join-Path $LibDir "zadig.exe") "" "Installer le pilote WinUSB d'un SDR USB"
}
New-Shortcut (Join-Path $MenuDir "Désinstaller openAntennaValidation.lnk") $psExe "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Prefix 'uninstall.ps1')`"" "Désinstaller la station (données conservées)"
Ok "icône « openAntennaValidation » sur le bureau et dans le menu Démarrer"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
if ($AutoStart) {
    $action = New-ScheduledTaskAction -Execute $psExe -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$launcher`" -Port $Port -NoBrowser"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    $principalTask = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principalTask -Settings $settings | Out-Null
    Ok "lancement à l'ouverture de session de $User"
}

# The page listens on the local network (host 0.0.0.0): private and domain networks only.
Remove-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $FirewallRule -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private, Domain | Out-Null
Ok "pare-feu : port $Port ouvert sur les réseaux privés"

Step "10. Horloge"
$w32 = Get-Service -Name W32Time -ErrorAction SilentlyContinue
if ($w32) {
    Set-Service -Name W32Time -StartupType Automatic
    if ($w32.Status -ne "Running") { Start-Service -Name W32Time }
    & w32tm /resync /nowait | Out-Null
    Ok "synchronisation de l'heure Windows active"
} else { Warn "service de temps Windows absent : l'horloge doit être synchronisée (FT8/WSPR)." }

Step "11. Test final"
# Short headless start: the page must answer, then the station is stopped again; the user
# starts it with the desktop icon.
$test = Start-Process -FilePath $VenvPython -ArgumentList "-m", "open_antenna_validation", "serve" -WindowStyle Hidden -PassThru
$answered = $false
for ($i = 0; $i -lt 30 -and -not $answered) {
    Start-Sleep -Seconds 1
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/session" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $answered = $true
    } catch { if ($test.HasExited) { break } }
}
Stop-Station
if (-not $test.HasExited) { Stop-Process -Id $test.Id -Force -ErrorAction SilentlyContinue }
if (-not $answered) { Fail "la station ne répond pas : voir les journaux dans $DataDir." }
Ok "la page répond sur http://localhost:$Port/"
Write-Host "Lancez la station par l'icône « openAntennaValidation » du bureau : sa page s'ouvre toute seule."
Write-Host "Journal de l'installation : $Log"
Stop-Transcript | Out-Null
exit 0
