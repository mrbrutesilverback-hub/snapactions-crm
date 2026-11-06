#!/bin/bash
# Fabrique SnapActions.app : une enveloppe autour du serveur.
#
# Le code ne change pas d'un octet. L'application lance serveur.py au premier
# plan : tant qu'elle tourne, le CRM tourne ; on la quitte, il s'arrete.
#
# Le vrai gain n'est pas le double-clic, c'est l'Acces complet au disque :
# on l'accorde une fois a l'application, et il n'y a plus besoin de passer
# par le Terminal pour que le CRM lise WhatsApp et le carnet d'adresses.
set -e
PROJET="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/SnapActions.app"
MAC="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"

rm -rf "$APP"
mkdir -p "$MAC" "$RES"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SnapActions</string>
  <key>CFBundleDisplayName</key><string>SnapActions</string>
  <key>CFBundleIdentifier</key><string>com.snapactions.crm</string>
  <key>CFBundleExecutable</key><string>SnapActions</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# L'icone : logo blanc sur fond noir, compose par icone.py.
# sips ne sait pas composer — il laissait la transparence du logo, et
# l'icone etait percee avec un glyphe minuscule au milieu d'un carre blanc.
SRC="$PROJET/prospect/static/img/marque-sombre.png"
if [ -f "$SRC" ]; then
  TMP="$(mktemp -d)"
  ICON="$TMP/AppIcon.iconset"
  mkdir -p "$ICON"
  PY3="$(command -v python3 || echo /usr/bin/python3)"
  if "$PY3" "$PROJET/outils/icone.py" "$SRC" "$TMP/icone.png" 1024 >/dev/null 2>&1; then
    for T in 16 32 128 256 512; do
      /usr/bin/sips -z $T $T "$TMP/icone.png" --out "$ICON/icon_${T}x${T}.png" >/dev/null 2>&1
      D=$((T * 2))
      /usr/bin/sips -z $D $D "$TMP/icone.png" --out "$ICON/icon_${T}x${T}@2x.png" >/dev/null 2>&1
    done
    /usr/bin/iconutil -c icns "$ICON" -o "$RES/AppIcon.icns" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP"
fi

# L'executable principal est un VRAI binaire, pas un script.
# Avec un script, macOS attribuait l'Acces complet au disque au binaire
# reellement charge — /bin/bash puis le Python de Xcode — et la case cochee
# dans les Reglages ne servait a rien.
if ! /usr/bin/clang -O2 -Wall -DPROJET="\"$PROJET\"" \
      "$PROJET/outils/lanceur.c" -o "$MAC/SnapActions" 2>/dev/null; then
  echo "ATTENTION : compilation du lanceur impossible"
  exit 1
fi
chmod +x "$MAC/SnapActions"


# Une signature, meme ad hoc, donne au paquet une identite stable. Sans
# elle macOS ne sait a qui rattacher l'Acces complet au disque, et la case
# cochee dans les Reglages ne sert a rien.
/usr/bin/codesign --force --deep --sign - "$APP" >/dev/null 2>&1 \
  && echo "signee (ad hoc)" || echo "ATTENTION : signature impossible"

echo "SnapActions.app construite : $APP"
