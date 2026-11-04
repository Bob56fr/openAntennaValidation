#!/usr/bin/env bash
# Démarre le WordPress de test, installe et active le plugin, crée les pages publiques, deux
# opérateurs et une station chacun (critère de P7 : deux stations d'opérateurs différents).
# Usage : ./dev-up.sh [--plain]   (--plain : permaliens « simples », comme certains sites)
# Le plugin doit être construit avant : python wordpress/build.py
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PORT="${OAV_WP_PORT:-8082}"
HOST="$(hostname -I | awk '{print $1}')"
URL="http://${HOST}:${PORT}"
PLAIN=0
[[ "${1:-}" == "--plain" ]] && PLAIN=1

[[ -f ../open-antenna-validation/assets/shared/results.js ]] || {
  echo "Plugin non construit : lancez d'abord python wordpress/build.py" >&2; exit 1; }

compose() { docker compose "$@" 2>/dev/null || docker-compose "$@"; }
wp() { compose run --rm -T cli wp "$@"; }

echo "=== Démarrage des conteneurs"
compose up -d db wordpress

echo "=== Attente de WordPress"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/wp-admin/install.php"; then break; fi
  sleep 2
done

if ! wp core is-installed >/dev/null 2>&1; then
  echo "=== Installation de WordPress"
  ADMIN_PASSWORD="$(openssl rand -base64 12)"
  wp core install --url="$URL" --title="Comparaison d'antennes HF (test)" --admin_user=admin \
    --admin_password="$ADMIN_PASSWORD" --admin_email=admin@example.invalid --skip-email
  wp language core install fr_FR --activate >/dev/null 2>&1 || true
  echo "$ADMIN_PASSWORD" > .admin-password
fi
wp option update home "$URL" >/dev/null
wp option update siteurl "$URL" >/dev/null
if [[ $PLAIN -eq 1 ]]; then
  wp rewrite structure '' --hard >/dev/null
else
  wp rewrite structure '/%postname%/' --hard >/dev/null
fi

echo "=== Plugin"
wp plugin activate open-antenna-validation
page() {  # slug, titre, contenu
  local id
  id="$(wp post list --post_type=page --name="$1" --field=ID)"
  if [[ -z "$id" ]]; then
    id="$(wp post create --post_type=page --post_status=publish --post_title="$2" --post_name="$1" \
      --post_content="$3" --porcelain)"
  fi
  echo "$id"
}
LIST_ID="$(page campagnes "Campagnes" '[oav_campagnes]')"
page stations "Stations" '[oav_stations]' >/dev/null
wp option update oav_list_page "$LIST_ID" >/dev/null

TOKENS=""
for n in 1 2; do
  if ! wp user get "operateur$n" >/dev/null 2>&1; then
    wp user create "operateur$n" "operateur$n@example.invalid" --role=oav_operator \
      --display_name="Opérateur $n" --user_pass="operateur$n-essai" >/dev/null
  fi
  USER_ID="$(wp user get "operateur$n" --field=ID)"
  TOKEN="$(wp eval "[\$id, \$t] = OAV_DB::create_station($USER_ID, 'Station de test $n'); echo \$t;")"
  TOKENS="${TOKENS}  operateur$n (mot de passe operateur$n-essai) : ${TOKEN}\n"
done

cat <<EOF

WordPress de test prêt.
  Site            : ${URL}/
  Liste publique  : ${URL}/$( [[ $PLAIN -eq 1 ]] && echo "?pagename=campagnes" || echo "campagnes/" )
  Administration  : ${URL}/wp-admin/  (admin / $(cat .admin-password 2>/dev/null || echo '?'))
Jetons de station (nouveaux à chaque lancement) :
$(printf "%b" "$TOKENS")
EOF
