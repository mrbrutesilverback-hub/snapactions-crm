# -*- coding: utf-8 -*-
"""Extrait l'index des conversations WhatsApp : numero + date du dernier envoi.

Ne lit jamais le contenu des messages : la colonne ZTEXT n'est selectionnee
nulle part dans ce fichier. La base est copiee puis ouverte en lecture seule.
"""
import csv
import datetime as dt
import glob
import os
import shutil
import sqlite3
import sys
import tempfile

# Core Data compte les secondes depuis le 1er janvier 2001.
APPLE_EPOCH = dt.datetime(2001, 1, 1)
HOME = os.path.expanduser("~")
CANDIDATS = [
    # WhatsApp Business d'abord : c'est celui d'ou partent les messages aux clubs.
    HOME + "/Library/Group Containers/group.net.whatsapp.WhatsAppSMB.shared/ChatStorage.sqlite",
    HOME + "/Library/Group Containers/group.net.whatsapp.WhatsAppSMB.shared/Message/ChatStorage.sqlite",
    HOME + "/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite",
    HOME + "/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Message/ChatStorage.sqlite",
    HOME + "/Library/Group Containers/group.net.whatsapp.family/ChatStorage.sqlite",
]


def trouver_base():
    for chemin in CANDIDATS:
        if os.path.isfile(chemin):
            return chemin
    motif = HOME + "/Library/Group Containers/group.net.whatsapp*/**/ChatStorage.sqlite"
    trouves = glob.glob(motif, recursive=True)
    return trouves[0] if trouves else None


def apple_date(valeur):
    if valeur in (None, 0):
        return None
    try:
        return APPLE_EPOCH + dt.timedelta(seconds=float(valeur))
    except (TypeError, ValueError, OverflowError):
        return None


def main():
    base = trouver_base()
    if not base:
        print("Base WhatsApp introuvable.")
        print("Soit WhatsApp Desktop n'a jamais ete synchronise sur ce Mac,")
        print("soit le Terminal n'a pas l'Acces complet au disque :")
        print("  Reglages Systeme > Confidentialite et securite > Acces complet au disque")
        print("  puis ajoutez Terminal, et relancez ce script.")
        return 1
    try:
        os.stat(base)
    except PermissionError:
        print("Base trouvee mais illisible : il manque l'Acces complet au disque.")
        print("  Reglages Systeme > Confidentialite et securite > Acces complet au disque")
        print("  ajoutez Terminal, fermez-le entierement, puis relancez ce script.")
        return 1

    # On travaille sur une copie : WhatsApp n'est jamais modifie.
    tmp = tempfile.mkdtemp(prefix="wa-index-")
    copie = os.path.join(tmp, "index.sqlite")
    try:
        shutil.copy2(base, copie)
        for suffixe in ("-wal", "-shm"):
            if os.path.exists(base + suffixe):
                shutil.copy2(base + suffixe, copie + suffixe)
    except (PermissionError, OSError) as exc:
        print("Copie impossible : %s" % exc)
        print("=> Acces complet au disque manquant pour le Terminal.")
        return 1

    conn = sqlite3.connect(copie)
    conn.row_factory = sqlite3.Row
    lignes = []
    try:
        # Colonnes lues : identifiant du correspondant, son nom affiche,
        # la date du message, et le drapeau "envoye par moi". Pas de texte.
        requete = """
            SELECT s.ZCONTACTJID  AS jid,
                   s.ZPARTNERNAME AS nom,
                   MAX(CASE WHEN m.ZISFROMME = 1 THEN m.ZMESSAGEDATE END) AS dernier_envoi,
                   MAX(CASE WHEN m.ZISFROMME = 0 THEN m.ZMESSAGEDATE END) AS derniere_reception,
                   SUM(CASE WHEN m.ZISFROMME = 1 THEN 1 ELSE 0 END)       AS nb_envoyes,
                   SUM(CASE WHEN m.ZISFROMME = 0 THEN 1 ELSE 0 END)       AS nb_recus
            FROM ZWACHATSESSION s
            LEFT JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK
            WHERE s.ZCONTACTJID LIKE '%@s.whatsapp.net'
            GROUP BY s.Z_PK
        """
        for r in conn.execute(requete):
            envoi = apple_date(r["dernier_envoi"])
            recept = apple_date(r["derniere_reception"])
            if not envoi and not recept:
                continue
            numero = "+" + (r["jid"] or "").split("@")[0]
            lignes.append({
                "telephone": numero,
                "nom": r["nom"] or "",
                "dernier_envoi": envoi.strftime("%Y-%m-%d %H:%M") if envoi else "",
                "derniere_reception": recept.strftime("%Y-%m-%d %H:%M") if recept else "",
                "nb_envoyes": r["nb_envoyes"] or 0,
                "nb_recus": r["nb_recus"] or 0,
            })
    except sqlite3.DatabaseError as exc:
        print("Base illisible (probablement chiffree par WhatsApp) : %s" % exc)
        print("Dans ce cas, passez par l'export manuel d'une discussion,")
        print("ou par le marquage groupe dans l'ecran Clubs.")
        return 1
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)

    if not lignes:
        print("Aucune conversation individuelle trouvee.")
        return 1

    sortie = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "whatsapp-contacts.csv")
    with open(sortie, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(lignes[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(lignes)

    avec_envoi = sum(1 for l in lignes if l["dernier_envoi"])
    print("%d conversation(s) trouvee(s), dont %d ou vous avez ecrit."
          % (len(lignes), avec_envoi))
    print("Ecrit : %s" % sortie)
    print()
    print("Aucun texte de message n'a ete lu. Deposez ce CSV dans l'onglet Import.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
