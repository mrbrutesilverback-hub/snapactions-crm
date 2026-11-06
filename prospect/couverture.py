# -*- coding: utf-8 -*-
"""Qui va sur quelle date.

Le CRM savait tout sauf ca. Il connaissait les onze photographes, les dix
groupes WhatsApp, les quinze cents tournois et les six cents conversations
— mais rien, nulle part, ne reliait un photographe a un jour. La table
`bookings` avait ete prevue pour, et elle est restee vide.

D'ou une page « L'equipe » qui ne pouvait pas dire ce qu'on lui demandait,
et qui a fini par afficher autre chose : les tournois dont la fiche avait
ete verifiee, ce qui n'a aucun rapport avec les dates qu'on tient.

Ici, une ligne par date du calendrier qu'on a attribuee. Pas de ligne =
personne n'y va encore. La date, elle, reste dans le calendrier : c'est lui
la source, on ne la recopie pas, on ne fait que l'annoter.
"""

from . import db, util

SCHEMA = """
CREATE TABLE IF NOT EXISTS couvertures (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    uid       TEXT NOT NULL UNIQUE,   -- l'evenement du calendrier
    jour      TEXT,                   -- recopie pour pouvoir trier sans le calendrier
    titre     TEXT,
    team_id   INTEGER,                -- le photographe, dans team
    qui       TEXT,                   -- son nom au moment du choix
    etat      TEXT NOT NULL DEFAULT 'pris',   -- pris | cherche | ignore
    note      TEXT,
    cree_le   TEXT NOT NULL,
    maj_le    TEXT
);
CREATE INDEX IF NOT EXISTS idx_couvertures_jour ON couvertures(jour);
"""

ETATS = ("pris", "cherche", "ignore")


def init():
    conn = db.connect()
    conn.executescript(SCHEMA)
    conn.commit()


def toutes():
    """Les annotations, rangees par uid d'evenement."""
    return {r["uid"]: db.row_to_dict(r)
            for r in db.q("SELECT * FROM couvertures")}


def poser(uid, team_id=None, etat="pris", note=None, jour=None, titre=None):
    """Note qui va sur cette date — ou qu'on cherche, ou qu'on l'ignore.

    « ignore » existe pour les cours hebdomadaires : le calendrier en est
    plein (le cours de MMA revient six fois par semaine), ce ne sont pas des
    sorties a pourvoir, et sans moyen de les ecarter la liste reste
    illisible.
    """
    uid = (uid or "").strip()
    if not uid:
        raise ValueError("quel événement ?")
    if etat not in ETATS:
        raise ValueError("état inconnu : %s" % etat)

    nom = None
    if team_id:
        r = db.q1("SELECT name FROM team WHERE id = ?", (int(team_id),))
        if not r:
            raise ValueError("photographe introuvable")
        nom = r["name"]
    if etat == "pris" and not nom:
        raise ValueError("qui y va ?")

    maintenant = util.now_iso()
    exist = db.q1("SELECT id FROM couvertures WHERE uid = ?", (uid,))
    champs = {"team_id": int(team_id) if team_id else None, "qui": nom,
              "etat": etat, "note": note, "maj_le": maintenant}
    if jour:
        champs["jour"] = jour
    if titre:
        champs["titre"] = titre
    if exist:
        db.update("couvertures", exist["id"], champs)
    else:
        champs.update({"uid": uid, "cree_le": maintenant})
        db.insert("couvertures", champs)
    db.log("couverture", "%s : %s" % (titre or uid,
                                      nom or ("on cherche" if etat == "cherche"
                                              else "ignorée")))
    return db.row_to_dict(db.q1("SELECT * FROM couvertures WHERE uid = ?", (uid,)))


def enlever(uid):
    db.ex("DELETE FROM couvertures WHERE uid = ?", ((uid or "").strip(),))
    return True
