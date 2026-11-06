#!/bin/zsh
# Ouvre le CRM a votre sourceur, par un tunnel Cloudflare.
#
# L'ordre compte, et ce script l'impose : la serrure AVANT le tunnel.
# Un tunnel ouvert sur un CRM sans mot de passe donnerait a quiconque trouve
# l'adresse vos 822 clubs, vos 600 conversations et tout le carnet.
#
#   1. le CRM repond-il ?
#   2. met la serrure (tout demande alors un mot de passe, meme depuis le Mac)
#   3. verifie que la serrure tient vraiment, avant d'ouvrir quoi que ce soit
#   4. installe cloudflared s'il manque, dans ce dossier, sans mot de passe Mac
#   5. ouvre le tunnel et affiche l'adresse
#
# Ctrl-C ferme le tunnel. La serrure reste mise : c'est voulu, elle ne coute
# rien. Pour l'enlever : outils/refermer.sh

set -e
RACINE="${0:A:h:h}"
BIN="$RACINE/outils/bin"
CRM="http://127.0.0.1:8765"

echo "=== 1. Le CRM repond-il ? ==="
if ! curl -sf -o /dev/null --max-time 10 "$CRM/"; then
  echo "   Non. Lancez SnapActions, puis recommencez."
  exit 1
fi
echo "   oui"

echo
echo "=== 2. La serrure ==="
cd "$RACINE"
python3 outils/serrure.py mettre || exit 1

echo
echo "=== 3. On verifie qu'elle tient ==="
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$CRM/api/tournaments/a-valider")
if [ "$CODE" != "401" ]; then
  echo "   ECHEC : sans mot de passe, le CRM repond $CODE au lieu de 401."
  echo "   On n'ouvre pas de tunnel la-dessus. Je referme."
  python3 outils/serrure.py enlever
  exit 1
fi
echo "   verifiee : sans mot de passe, le CRM repond 401"

echo
echo "=== 4. cloudflared ==="
if [ ! -x "$BIN/cloudflared" ]; then
  mkdir -p "$BIN"
  case "$(uname -m)" in
    arm64) F=cloudflared-darwin-arm64.tgz ;;
    *)     F=cloudflared-darwin-amd64.tgz ;;
  esac
  echo "   telechargement depuis github.com/cloudflare/cloudflared ..."
  curl -fL --max-time 300 -o /tmp/cloudflared.tgz \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/$F"
  tar -xzf /tmp/cloudflared.tgz -C "$BIN"
  chmod +x "$BIN/cloudflared"
  rm -f /tmp/cloudflared.tgz
fi
"$BIN/cloudflared" --version

echo
echo "=== 5. Le tunnel ==="
echo "   L'adresse s'affiche dans quelques secondes : https://....trycloudflare.com"
echo "   Donnez-la a votre sourceur en ajoutant /espace.html a la fin."
echo "   Ctrl-C ferme le tunnel."
echo
exec "$BIN/cloudflared" tunnel --url "$CRM"
