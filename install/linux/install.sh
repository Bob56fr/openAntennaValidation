#!/usr/bin/env bash
# Installation d'openAntennaValidation — Debian 12+ (amd64) et Raspberry Pi OS 64 bits.
#
# Usage : sudo ./install/linux/install.sh [--sdr LISTE] [--import DOSSIER] [--port N]
#   --sdr LISTE      : SDR de la station, séparés par des virgules : sdrplay, rtlsdr, airspyhf
#                      (le premier est le SDR principal). Par défaut : ceux de la configuration
#                      existante, sinon sdrplay.
#   --import DOSSIER : reprend une station existante (config.toml et oav.sqlite3 copiés
#                      depuis une autre machine, par exemple C:\ProgramData\open-antenna-validation).
#   --port N         : port de la page web (8660 par défaut).
#   --sdrplay-run F  : installeur officiel de l'API SDRplay (sinon cherché dans le dossier
#                      personnel et à côté du dépôt).
#
# Idempotent : relancer le script met à jour la station après une mise à jour du dépôt.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX=/opt/open-antenna-validation
HOME_DIR=/var/lib/open-antenna-validation
SERVICE_USER=oav
UNIT=/etc/systemd/system/oav.service
LOG=/var/log/oav-install.log
SDRPLAY_URL="https://www.sdrplay.com/downloads/"
PORT=8660
IMPORT_DIR=""
SDRPLAY_RUN=""
SDR_LIST=""
BUILD_DIR=/usr/local/src/oav-sdr
RTLSDR_GIT="https://github.com/rtlsdrblog/rtl-sdr-blog.git"
AIRSPYHF_GIT="https://github.com/airspy/airspyhf.git"
# Empreintes connues des installeurs officiels (référence : SlackBuilds.org).
declare -A SDRPLAY_MD5=(
  ["SDRplay_RSP_API-Linux-3.15.2.run"]="92feae82c39d2e33eec13fc5662a3b9b"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sdr) SDR_LIST="${2:?liste attendue après --sdr}"; shift 2 ;;
    --sdrplay-run) SDRPLAY_RUN="${2:?fichier attendu après --sdrplay-run}"; shift 2 ;;
    --import) IMPORT_DIR="${2:?dossier attendu après --import}"; shift 2 ;;
    --port) PORT="${2:?numéro attendu après --port}"; shift 2 ;;
    *) echo "Option inconnue : $1" >&2; exit 1 ;;
  esac
done

exec > >(tee -a "$LOG") 2>&1
step() { echo; echo "=== $(date '+%F %T') $*"; }
fail() { echo "ERREUR : $*" >&2; exit "${2:-1}"; }

step "1. Contrôle du système et des droits"
[[ $EUID -eq 0 ]] || fail "lancer ce script avec sudo."
. /etc/os-release
case "${ID:-}" in
  debian|raspbian) ;;
  *) fail "système non supporté (${PRETTY_NAME:-inconnu}) : Debian 12+ ou Raspberry Pi OS requis." ;;
esac
[[ "${VERSION_ID%%.*}" -ge 12 ]] || fail "Debian 12 (bookworm) ou plus récent requis."
ARCH="$(dpkg --print-architecture)"
[[ "$ARCH" == amd64 || "$ARCH" == arm64 ]] || fail "architecture $ARCH non supportée (amd64 ou arm64)."
if [[ -n "$IMPORT_DIR" ]]; then
  [[ -f "$IMPORT_DIR/config.toml" ]] || fail "$IMPORT_DIR/config.toml introuvable."
fi
echo "OK : ${PRETTY_NAME}, ${ARCH}"

# SDR de la station : option --sdr, sinon ceux de la configuration existante, sinon sdrplay.
MAIN_DRIVER=""
if [[ -n "$SDR_LIST" ]]; then
  MAIN_DRIVER="${SDR_LIST%%,*}"
else
  FOUND_CFG=""
  CANDIDATES=("$HOME_DIR/config.toml")
  [[ -n "$IMPORT_DIR" ]] && CANDIDATES=("$IMPORT_DIR/config.toml" "${CANDIDATES[@]}")
  for CFG in "${CANDIDATES[@]}"; do
    if [[ -f "$CFG" ]]; then
      FOUND_CFG="$CFG"
      SDR_LIST="$(python3 - "$CFG" <<'PY'
import sys, tomllib
hw = tomllib.load(open(sys.argv[1], "rb")).get("hardware", {})
main = hw.get("driver", "sdrplay")
drivers = [main] + [o.get("driver") or main for o in hw.get("others", [])]
print(",".join(dict.fromkeys(d for d in drivers if d != "simulated")) or "sdrplay")
PY
)"
      break
    fi
  done
  SDR_LIST="${SDR_LIST:-sdrplay}"
  # Première installation : le SDR principal par défaut est le SDRplay.
  [[ -n "$FOUND_CFG" ]] || MAIN_DRIVER="sdrplay"
fi
has_sdr() { [[ ",${SDR_LIST}," == *",$1,"* ]]; }
for d in ${SDR_LIST//,/ }; do
  case "$d" in sdrplay|rtlsdr|airspyhf) ;; *) fail "SDR inconnu dans --sdr : $d (sdrplay, rtlsdr ou airspyhf)." ;; esac
done
echo "OK : SDR de la station : ${SDR_LIST}"

SDRPLAY_UNIT=""
if has_sdr sdrplay; then
step "2. API SDRplay"
api_installed() { ls /usr/local/lib/libsdrplay_api.so* >/dev/null 2>&1; }
if ! api_installed; then
  # L'API est propriétaire et non redistribuable : l'installeur officiel, téléchargé par
  # l'utilisateur sur le site de SDRplay, est cherché à côté du dépôt et dans son dossier.
  USER_HOME="$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)"
  RUN_FILE="$SDRPLAY_RUN"
  if [[ -z "$RUN_FILE" ]]; then
    RUN_FILE="$(ls -1t "$REPO_DIR"/SDRplay_RSP_API-Linux-*.run "$REPO_DIR"/../SDRplay_RSP_API-Linux-*.run \
      "$USER_HOME"/SDRplay_RSP_API-Linux-*.run "$USER_HOME"/Downloads/SDRplay_RSP_API-Linux-*.run \
      "$USER_HOME"/Téléchargements/SDRplay_RSP_API-Linux-*.run 2>/dev/null | head -n 1 || true)"
  fi
  if [[ -z "$RUN_FILE" || ! -f "$RUN_FILE" ]]; then
    cat <<EOF
L'API SDRplay 3.x n'est pas installée. Elle est propriétaire : elle ne peut pas être
fournie avec ce logiciel ni téléchargée automatiquement (formulaire sur le site).
  1. Téléchargez « SDRplay API for Linux » : ${SDRPLAY_URL}
  2. Déposez le fichier SDRplay_RSP_API-Linux-3.x.x.run dans votre dossier personnel
     (ou à côté de ce dépôt).
  3. Relancez ce script : il l'installera lui-même (acceptez la licence quand elle s'affiche).
EOF
    exit 3
  fi
  NAME="$(basename "$RUN_FILE")"
  EXPECTED="${SDRPLAY_MD5[$NAME]:-}"
  ACTUAL="$(md5sum "$RUN_FILE" | cut -d' ' -f1)"
  if [[ -n "$EXPECTED" && "$ACTUAL" != "$EXPECTED" ]]; then
    fail "empreinte MD5 de ${NAME} inattendue (${ACTUAL}) : fichier corrompu ou modifié, retéléchargez-le sur ${SDRPLAY_URL}" 3
  fi
  [[ -n "$EXPECTED" ]] && echo "OK : ${NAME}, empreinte MD5 vérifiée" \
    || echo "ATTENTION : version ${NAME} non répertoriée, empreinte non vérifiée"
  echo "Installation de l'API SDRplay : lisez la licence et acceptez-la pour continuer."
  sh "$RUN_FILE" < /dev/tty
  api_installed || fail "l'installation de l'API SDRplay n'a pas abouti." 3
fi
for unit in sdrplay sdrplay_api sdrplayService; do
  if systemctl list-unit-files "${unit}.service" --no-legend 2>/dev/null | grep -q "${unit}"; then
    SDRPLAY_UNIT="${unit}.service"
    break
  fi
done
[[ -n "$SDRPLAY_UNIT" ]] || fail "service de l'API SDRplay introuvable : réinstallez l'API SDRplay." 3
systemctl enable --now "$SDRPLAY_UNIT"
echo "OK : API SDRplay présente, service ${SDRPLAY_UNIT} actif"
fi

step "3. Ports réseau"
# Une station déjà installée occupe ses propres ports : on l'arrête pendant la mise à jour.
if systemctl is-active --quiet oav.service; then
  systemctl stop oav.service
  echo "Service oav arrêté pendant la mise à jour"
fi
if ss -Htln "sport = :${PORT}" | grep -q .; then
  fail "le port TCP ${PORT} est déjà utilisé : relancez avec --port <port libre>."
fi
echo "OK : page web sur le port ${PORT}"

step "4. Paquets système"
apt-get update
apt-get install -y python3 python3-venv curl xdg-utils
if has_sdr rtlsdr || has_sdr airspyhf; then
  apt-get install -y git cmake build-essential pkg-config libusb-1.0-0-dev
fi

# Bibliothèques libres compilées depuis les dépôts des fabricants, avec leurs règles udev
# (groupe plugdev) : la branche RTL-SDR Blog gère le convertisseur HF de la V4, que les
# paquets Debian anciens ignorent ; libairspyhf récente règle l'atténuateur par pas.
build_lib() {  # nom, dépôt, options cmake
  local name="$1" url="$2"; shift 2
  install -d "$BUILD_DIR"
  if [[ -d "$BUILD_DIR/$name/.git" ]]; then
    git -C "$BUILD_DIR/$name" pull --ff-only
  else
    git clone --depth 1 "$url" "$BUILD_DIR/$name"
  fi
  echo "$name : version $(git -C "$BUILD_DIR/$name" rev-parse --short HEAD)"
  cmake -S "$BUILD_DIR/$name" -B "$BUILD_DIR/$name/build" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local -DINSTALL_UDEV_RULES=ON "$@"
  cmake --build "$BUILD_DIR/$name/build" -j "$(nproc)"
  cmake --install "$BUILD_DIR/$name/build"
}
built_lib() {  # chemin réel de la bibliothèque installée dans /usr/local
  find /usr/local/lib -name "$1.so*" -type f 2>/dev/null | sort | tail -n 1
}
RTLSDR_LIB=""
AIRSPYHF_LIB=""
if has_sdr rtlsdr; then
  step "4b. Bibliothèque librtlsdr (branche RTL-SDR Blog)"
  build_lib rtl-sdr-blog "$RTLSDR_GIT" -DDETACH_KERNEL_DRIVER=ON
  # Le pilote TNT du noyau prend la clé avant librtlsdr : on l'écarte.
  cat > /etc/modprobe.d/oav-rtlsdr-blacklist.conf <<EOF
# openAntennaValidation : la RTL-SDR sert de récepteur, pas de clé TNT.
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
  for m in dvb_usb_rtl28xxu rtl2832 rtl2830; do modprobe -r "$m" 2>/dev/null || true; done
  RTLSDR_LIB="$(built_lib librtlsdr)"
  [[ -n "$RTLSDR_LIB" ]] || fail "la compilation de librtlsdr n'a pas abouti."
  echo "OK : ${RTLSDR_LIB}"
fi
if has_sdr airspyhf; then
  step "4c. Bibliothèque libairspyhf"
  build_lib airspyhf "$AIRSPYHF_GIT"
  AIRSPYHF_LIB="$(built_lib libairspyhf)"
  [[ -n "$AIRSPYHF_LIB" ]] || fail "la compilation de libairspyhf n'a pas abouti."
  echo "OK : ${AIRSPYHF_LIB}"
fi
if has_sdr rtlsdr || has_sdr airspyhf; then
  ldconfig
  udevadm control --reload-rules && udevadm trigger
  echo "Débranchez et rebranchez les SDR USB pour appliquer les règles udev."
fi

step "5. Environnement Python"
id -u "$SERVICE_USER" >/dev/null 2>&1 || \
  useradd --system --home-dir "$HOME_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
usermod -a -G plugdev "$SERVICE_USER"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$HOME_DIR"
install -d "$PREFIX"
[[ -x "$PREFIX/venv/bin/python" ]] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet --upgrade "${REPO_DIR}"
echo "OK : $("$PREFIX/venv/bin/oav" --version)"

step "6. Configuration"
run_as_service() { sudo -u "$SERVICE_USER" env OAV_HOME="$HOME_DIR" "$PREFIX/venv/bin/oav" "$@"; }
if [[ -n "$IMPORT_DIR" ]]; then
  if [[ -f "$HOME_DIR/config.toml" ]]; then
    fail "une station existe déjà dans ${HOME_DIR} : import refusé pour ne rien écraser."
  fi
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0640 "$IMPORT_DIR/config.toml" "$HOME_DIR/config.toml"
  if [[ -f "$IMPORT_DIR/oav.sqlite3" ]]; then
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0640 \
      "$IMPORT_DIR/oav.sqlite3" "$HOME_DIR/oav.sqlite3"
  fi
  echo "Station importée depuis ${IMPORT_DIR}"
fi
# Garde l'identifiant, l'indicatif, le gain et le mot de passe existants ; demande le mot de
# passe s'il manque. Les chemins de bibliothèques propres à Windows sont remplacés par ceux
# installés ici. Le SDR principal n'est changé que par --sdr (ou à la première installation).
if [[ -n "$MAIN_DRIVER" ]]; then
  run_as_service init --driver "$MAIN_DRIVER"
else
  run_as_service init
fi
sudo -u "$SERVICE_USER" env OAV_HOME="$HOME_DIR" "$PREFIX/venv/bin/python" - "$PORT" \
  "$RTLSDR_LIB" "$AIRSPYHF_LIB" <<'PY'
import sys
from open_antenna_validation.config import CONFIG_FILENAME, default_home, load_config, save_config
path = default_home() / CONFIG_FILENAME
config = load_config(path)
config.web.port = int(sys.argv[1])
config.hardware.sdrplay.library_path = ""
config.hardware.rtlsdr.library_path = sys.argv[2]
config.hardware.airspyhf.library_path = sys.argv[3]
save_config(config, path)
PY

step "7. Détection du SDR"
run_as_service detect || echo "ATTENTION : aucun SDR détecté (branché ? utilisé par un autre logiciel ?)"

step "8. Service systemd"
REQUIRES=""
[[ -n "$SDRPLAY_UNIT" ]] && REQUIRES="Requires=${SDRPLAY_UNIT}"
cat > "$UNIT" <<EOF
[Unit]
Description=openAntennaValidation
After=network-online.target ${SDRPLAY_UNIT}
Wants=network-online.target
${REQUIRES}

[Service]
User=${SERVICE_USER}
Environment=OAV_HOME=${HOME_DIR}
ExecStart=${PREFIX}/venv/bin/oav serve
Restart=on-failure
RestartSec=5
# Arrêt propre : le service arrête l'acquisition et libère le SDR.
KillSignal=SIGINT
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable oav.service
systemctl restart oav.service

step "9. Icône de lancement"
install -D -m 0644 "$REPO_DIR/install/linux/oav.svg" /usr/share/icons/hicolor/scalable/apps/oav.svg
cat > /usr/share/applications/oav.desktop <<EOF
[Desktop Entry]
Type=Application
Name=openAntennaValidation
Comment=Ouvrir la page de la station openAntennaValidation
Exec=xdg-open http://localhost:${PORT}/
Icon=oav
Terminal=false
Categories=Network;HamRadio;
EOF
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
echo "Entrée « openAntennaValidation » ajoutée au menu des applications"
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
  USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  # Le dossier du bureau dépend de la langue (Bureau, Desktop…).
  DESKTOP="$(sudo -u "$SUDO_USER" xdg-user-dir DESKTOP 2>/dev/null || true)"
  [[ -n "$DESKTOP" && "$DESKTOP" != "$USER_HOME" ]] || DESKTOP="$USER_HOME/Desktop"
  if [[ -d "$DESKTOP" ]]; then
    install -o "$SUDO_USER" -g "$(id -gn "$SUDO_USER")" -m 0755 \
      /usr/share/applications/oav.desktop "$DESKTOP/oav.desktop"
    # GNOME n'exécute un lanceur du bureau que s'il est marqué de confiance (session requise).
    sudo -u "$SUDO_USER" gio set "$DESKTOP/oav.desktop" metadata::trusted true 2>/dev/null \
      || echo "Au premier double-clic sur l'icône, choisissez « Autoriser le lancement »."
    echo "Icône créée sur le bureau de ${SUDO_USER} (${DESKTOP})"
  else
    echo "Pas de dossier de bureau pour ${SUDO_USER} : utilisez le menu des applications."
  fi
fi

step "10. Test final"
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/session" >/dev/null 2>&1; then
    echo "OK : page web disponible sur http://$(hostname -I | awk '{print $1}'):${PORT}/"
    exit 0
  fi
  sleep 1
done
fail "le service ne répond pas : journalctl -u oav -n 50"
