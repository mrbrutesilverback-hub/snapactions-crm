# -*- coding: utf-8 -*-
"""Combien de fois chaque ecran est ouvert.

Julien voulait son menu range par frequence d'usage. Personne ne le savait :
le journal ne garde que les taches de fond, et rien ne notait les pages.
On compte donc, a partir d'aujourd'hui, et le menu se range tout seul.

Un compteur, pas une trace : on garde un nombre et une date, jamais ce qui
a ete regarde ni quand exactement.
"""

from . import db, util

SCHEMA = """
CREATE TABLE IF NOT EXISTS visites (
    ecran   TEXT PRIMARY KEY,
    n       INTEGER NOT NULL DEFAULT 0,
    dernier TEXT
);
"""

# Les ecrans qui comptent. Une page inconnue n'entre pas : sans cette liste,
# chaque image et chaque feuille de style aurait sa ligne.
CONNUS = {
    "repondre.html", "rencontres.html", "simple.html", "chercher.html",
    "studio.html", "creux.html", "partenaires.html", "relais.html",
    "index.html", "flyer.html",
    # Les onglets de la plateforme, annonces par le menu lui-meme.
    "today", "dates", "clubs", "tournois", "suivi", "whatsapp", "reglages",
}


def _table():
    db.ex(SCHEMA)


def noter(ecran):
    """Une ouverture de plus. Silencieux : jamais au prix d'une page."""
    ecran = (ecran or "").strip().lstrip("/")
    if ecran not in CONNUS:
        return False
    try:
        _table()
        db.ex("INSERT INTO visites (ecran, n, dernier) VALUES (?, 1, ?) "
              "ON CONFLICT(ecran) DO UPDATE SET n = n + 1, dernier = ?",
              (ecran, util.now_iso(), util.now_iso()))
        return True
    except Exception:
        return False


def comptes():
    """{ecran: nombre d'ouvertures}. Vide tant que rien n'a ete ouvert."""
    try:
        _table()
        return {r["ecran"]: r["n"] for r in
                db.q("SELECT ecran, n FROM visites")}
    except Exception:
        return {}


def liste():
    """Le detail, pour savoir ce qui sert vraiment et ce qui dort."""
    try:
        _table()
        return db.rows_to_dicts(db.q(
            "SELECT ecran, n, dernier FROM visites ORDER BY n DESC, ecran"))
    except Exception:
        return []
