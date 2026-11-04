#!/usr/bin/env bash
# Désinstallation d'openAntennaValidation.
# Usage : sudo ./install/linux/uninstall.sh [--purge]
#   --purge : supprime aussi les données (configuration, base, journaux) et l'utilisateur.
# L'API SDRplay, partagée avec d'autres logiciels, est conservée.
set -euo pipefail

PREFIX=/opt/open-antenna-validation
HOME_DIR=/var/lib/open-antenna-validation
SERVICE_USER=oav
UNIT=/etc/systemd/system/oav.service

[[ $EUID -eq 0 ]] || { echo "lancer ce script avec sudo." >&2; exit 1; }

if [[ -f "$UNIT" ]]; then
  systemctl disable --now oav.service || true
  rm -f "$UNIT"
  systemctl daemon-reload
fi
rm -rf "$PREFIX"
rm -f /usr/share/applications/oav.desktop /usr/share/icons/hicolor/scalable/apps/oav.svg
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
  USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  DESKTOP="$(sudo -u "$SUDO_USER" xdg-user-dir DESKTOP 2>/dev/null || true)"
  rm -f "${DESKTOP:-$USER_HOME/Desktop}/oav.desktop" "$USER_HOME/Desktop/oav.desktop"
fi

if [[ "${1:-}" == "--purge" ]]; then
  rm -rf "$HOME_DIR"
  if id -u "$SERVICE_USER" >/dev/null 2>&1; then userdel "$SERVICE_USER"; fi
  echo "Station désinstallée, données supprimées."
else
  echo "Station désinstallée. Données conservées dans ${HOME_DIR} (--purge pour les supprimer)."
fi
