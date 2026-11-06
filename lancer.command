#!/bin/bash
# Double-cliquez ce fichier pour demarrer le CRM.
#
# La fenetre se ferme toute seule une fois le serveur parti : il tourne
# detache, et il survit a la fermeture. Avant, le serveur tournait au premier
# plan et le script attendait « Entree » — les fenetres s'empilaient.
#
# Le serveur doit rester lance depuis le Terminal : c'est de lui qu'il herite
# l'Acces complet au disque, sans lequel il ne peut lire ni le journal
# WhatsApp ni le carnet d'adresses.
cd "$(dirname "$0")" || exit 1

PY=""
for P in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
  if [ -x "$P" ]; then PY="$P"; break; fi
done
[ -n "$PY" ] || PY="$(command -v python3)"
if [ -z "$PY" ]; then
  echo "PROBLEME : aucun python3 sur ce Mac. Tapez  xcode-select --install"
  read -r -p "Entree pour fermer."; exit 1
fi

# L'ancien serveur, s'il y en a un.
for PID in $(/usr/sbin/lsof -ti tcp:8765 2>/dev/null); do kill "$PID" 2>/dev/null; done
for PID in $(pgrep -f "serveur.py serve" 2>/dev/null); do
  [ "$PID" != "$$" ] && kill "$PID" 2>/dev/null
done
sleep 1
for PID in $(/usr/sbin/lsof -ti tcp:8765 2>/dev/null); do kill -9 "$PID" 2>/dev/null; done

mkdir -p data
LOG="$(pwd)/data/serveur.log"
echo "" >> "$LOG"
echo "=== demarrage $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

# Detache : nohup pour ignorer le signal de fermeture, disown pour que le
# shell ne le compte plus comme un de ses travaux — sans quoi le Terminal
# demande « un processus tourne, fermer quand meme ? ».
nohup "$PY" "$(pwd)/serveur.py" serve >> "$LOG" 2>&1 &
disown

# On attend qu'il reponde avant de fermer : si quelque chose cloche, la
# fenetre reste ouverte avec le message, au lieu de disparaitre en silence.
PRET=0
for _ in $(seq 1 40); do
  if /usr/bin/curl -s -m 2 -o /dev/null "http://127.0.0.1:8765/api/version"; then
    PRET=1; break
  fi
  sleep 1
done

if [ "$PRET" != "1" ]; then
  echo "Le serveur n'a pas repondu en 40 secondes."
  echo "Le detail est dans : $LOG"
  tail -n 15 "$LOG"
  read -r -p "Entree pour fermer cette fenetre."
  exit 1
fi

# Tout va bien : on referme CETTE fenetre. On la designe par son tty, pas par
# son titre : les fenetres de Terminal rendent souvent un titre vide, et le
# filtre par nom ne fermait rien.
MOI="$(tty)"
osascript \
  -e 'on run argv' \
  -e 'tell application "Terminal"' \
  -e 'repeat with w in windows' \
  -e 'try' \
  -e 'repeat with t in tabs of w' \
  -e 'if (tty of t) is (item 1 of argv) then close w saving no' \
  -e 'end repeat' \
  -e 'end try' \
  -e 'end repeat' \
  -e 'end tell' \
  -e 'end run' "$MOI" >/dev/null 2>&1 &
exit 0
