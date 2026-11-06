#!/bin/bash
# ---------------------------------------------------------------------------
# Lit UNIQUEMENT l'index des conversations WhatsApp de ce Mac :
#   - le numero de telephone du correspondant
#   - la date du dernier message que VOUS avez envoye
#
# Ce script ne lit JAMAIS le texte des messages. La colonne qui les contient
# (ZTEXT) n'est nommee nulle part ci-dessous — verifiez-le vous-meme.
# Il n'ecrit rien dans WhatsApp : la base est copiee en lecture seule.
#
# Resultat : un fichier whatsapp-contacts.csv, a deposer dans l'onglet Import.
# ---------------------------------------------------------------------------
cd "$(dirname "$0")"
exec python3 lire_whatsapp.py
