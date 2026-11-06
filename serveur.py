#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Demarre le CRM, et se recharge tout seul quand le code change.

Lance par chemin absolu, Python place toujours le dossier de ce fichier en
tete de sys.path : le paquet prospect est trouve, que l'appel vienne du
Terminal, du Dock ou d'ailleurs.

Un guetteur surveille prospect/*.py : des qu'un fichier bouge, le serveur
redemarre dans la seconde. Plus besoin de fermer et rouvrir quoi que ce soit.
"""
import os
import sys
import threading
import time

RACINE = os.path.dirname(os.path.abspath(__file__))
if RACINE not in sys.path:
    sys.path.insert(0, RACINE)
os.chdir(RACINE)


def _empreinte():
    """Date de derniere modification de chaque fichier de code."""
    dossier = os.path.join(RACINE, "prospect")
    marques = {}
    for nom in os.listdir(dossier):
        if nom.endswith(".py"):
            try:
                marques[nom] = os.path.getmtime(os.path.join(dossier, nom))
            except OSError:
                pass
    return marques


def _guetteur():
    reference = _empreinte()
    while True:
        time.sleep(2)
        try:
            actuel = _empreinte()
        except OSError:
            continue
        if actuel != reference:
            time.sleep(1.2)          # laisse l'ecriture se terminer
            print("\n[code modifie] redemarrage du serveur...", flush=True)
            try:
                # os.execv remplace le processus SANS rien fermer : la
                # connexion SQLite disparaissait en plein vol et laissait un
                # journal a moitie ecrit. On le vide d'abord.
                try:
                    from prospect import db as _db
                    _db.ranger_avant_de_partir()
                except Exception:
                    pass
                # Le redemarrage ne doit pas rouvrir un onglet de navigateur.
                os.environ["PROSPECT_RELANCE"] = "1"
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except OSError:
                reference = actuel   # on ne peut pas relancer : on continue

from prospect.__main__ import main  # noqa: E402

if __name__ == "__main__":
    # Le guetteur relance le serveur des qu'un fichier change : pratique
    # quand on developpe, deplace dans une application livree. Lancee depuis
    # SnapActions.app, elle ne doit pas se redemarrer toute seule — et
    # os.execv s'y comporte mal.
    if not os.environ.get("SNAPACTIONS_LANCEUR"):
        threading.Thread(target=_guetteur, daemon=True).start()
    argv = sys.argv[1:] or ["serve"]
    sys.exit(main(argv))
