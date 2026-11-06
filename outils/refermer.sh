#!/bin/zsh
# Enleve la serrure : le CRM redevient ce qu'il etait, visible du seul Mac.
# A faire quand le tunnel est ferme et que vous ne voulez plus taper de mot
# de passe chez vous. Tant qu'un tunnel tourne, ne lancez pas ceci.

set -e
RACINE="${0:A:h:h}"
cd "$RACINE"

if pgrep -f "cloudflared tunnel" >/dev/null 2>&1; then
  echo "Attention : un tunnel cloudflared tourne encore."
  echo "Fermez-le (Ctrl-C dans sa fenetre) avant d'enlever la serrure,"
  echo "sinon le CRM reste joignable de l'exterieur SANS mot de passe."
  exit 1
fi

python3 outils/serrure.py enlever
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 \
  "http://127.0.0.1:8765/api/tournaments/a-valider")
echo "le CRM repond $CODE depuis le Mac (200 = comme avant)"
