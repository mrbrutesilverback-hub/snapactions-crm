# -*- coding: utf-8 -*-
"""Le menage : ce qui ne sert plus, et rien d'autre.

Une base de prospection grossit toute seule. Au 18 septembre : 205 tournois
dont 144 hors zone, 49 Mo d'affiches en double parce qu'un meme flyer redepose
six fois laissait six copies, et six sauvegardes de base d'une seule journee.
Rien de tout cela n'est faux — c'est simplement du poids mort qui noie ce qui
compte.

Le principe est celui de tout le projet : on ne jette que ce dont on est sur.
Une regle de purge doit pouvoir enoncer pourquoi la ligne ne servira jamais,
sinon elle n'existe pas. Et tout ce que Julien a touche est intouchable — une
mission, un message envoye, une note, un partenaire, une de ses propres dates.

Par defaut la fonction ne supprime rien : elle dit ce qu'elle ferait.
"""

import os

from . import db, util

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFFICHES = os.path.join(RACINE, "a-traiter", "traites")
DATA = os.path.join(RACINE, "data")

# Un tournoi passe garde un interet quelques semaines : on rappelle le club
# pour savoir comment ca s'est passe. Au-dela, il n'est plus qu'une ligne.
PASSE_JOURS = 90

# Le journal sert a comprendre le dernier scan, pas a tenir des archives.
JOURNAL_MAX = 500

# Assez de sauvegardes pour revenir en arriere, pas assez pour remplir le disque.
SAUVEGARDES_GARDEES = 3


def _intouchable(sql_alias="t"):
    """Les conditions qui protegent une fiche, quelles que soient les regles.

    Rendu comme une clause SQL a coller dans un NOT (...) : une fiche qui
    coche l'une d'elles n'est jamais candidate a la suppression.
    """
    return (
        "EXISTS (SELECT 1 FROM missions m WHERE m.tournament_id = {a}.id) "
        "OR EXISTS (SELECT 1 FROM drafts d WHERE d.tournament_id = {a}.id "
        "           AND d.status = 'sent') "
        "OR {a}.source IN ('SnapActions', 'manuel', 'annonce par le club', "
        "                  'Accord telephone', 'Flyer depose') "
        "OR {a}.validation IN ('oui', 'valide')"
    ).format(a=sql_alias)


# --------------------------------------------------------------------------
# Les regles, une par une. Chacune renvoie (libelle, lignes, poids_octets).
# --------------------------------------------------------------------------

def _tournois_passes():
    """Les tournois finis depuis longtemps, sur lesquels rien n'a ete engage."""
    lignes = db.rows_to_dicts(db.q(
        "SELECT t.id, t.name, t.end_date, t.city FROM tournaments t "
        "WHERE t.end_date IS NOT NULL "
        "  AND date(t.end_date) < date('now', '-%d day') "
        "  AND NOT (%s) "
        "ORDER BY t.end_date" % (PASSE_JOURS, _intouchable())))
    return ("Tournois terminés il y a plus de %d jours, jamais engagés" % PASSE_JOURS,
            lignes, 0)


def _tournois_hors_zone_passes():
    """Ceux qu'on n'aurait pas faits, et qui sont derriere nous."""
    lignes = db.rows_to_dicts(db.q(
        "SELECT t.id, t.name, t.end_date, t.city FROM tournaments t "
        "WHERE t.distance_verdict = 'hors zone' "
        "  AND t.end_date IS NOT NULL AND date(t.end_date) < date('now') "
        "  AND NOT (%s) "
        "ORDER BY t.end_date" % _intouchable()))
    return ("Tournois hors zone déjà passés", lignes, 0)


def _brouillons_orphelins():
    """Des messages rattaches a un tournoi qui n'existe plus."""
    lignes = db.rows_to_dicts(db.q(
        "SELECT d.id, d.club_id, d.tournament_id FROM drafts d "
        "WHERE d.tournament_id IS NOT NULL AND d.status IN ('draft', 'rejected') "
        "  AND NOT EXISTS (SELECT 1 FROM tournaments t WHERE t.id = d.tournament_id)"))
    return ("Messages dont le tournoi a disparu", lignes, 0)


def _affiches_orphelines():
    """Des images qu'aucune fiche ne reference — surtout des doublons.

    Un meme flyer redepose se rangeait en « IMG_3891-2 », « -3 », « -4 »… et
    seule la derniere version restait reliee a une fiche. Les autres sont des
    copies exactes que personne ne peut plus retrouver.
    """
    if not os.path.isdir(AFFICHES):
        return ("Affiches orphelines", [], 0)
    refs = {r["affiche"] for r in
            db.q("SELECT DISTINCT affiche FROM tournaments WHERE affiche IS NOT NULL")}
    lignes, poids = [], 0
    for nom in sorted(os.listdir(AFFICHES)):
        chemin = os.path.join(AFFICHES, nom)
        if not os.path.isfile(chemin) or nom in refs or nom.startswith("."):
            continue
        try:
            taille = os.path.getsize(chemin)
        except OSError:
            continue
        poids += taille
        lignes.append({"fichier": nom, "octets": taille})
    return ("Affiches qu'aucune fiche ne référence", lignes, poids)


def _journal():
    reste = db.q1("SELECT COUNT(*) n FROM runlog")["n"] - JOURNAL_MAX
    lignes = []
    if reste > 0:
        lignes = db.rows_to_dicts(db.q(
            "SELECT id, created_at, kind FROM runlog ORDER BY id LIMIT ?", (reste,)))
    return ("Entrées de journal au-delà des %d dernières" % JOURNAL_MAX, lignes, 0)


def _sauvegardes():
    """Les vieilles copies de la base, au-dela des plus recentes."""
    if not os.path.isdir(DATA):
        return ("Sauvegardes de base", [], 0)
    # Trier par nom garderait « crm-avant-purge » devant « crm-avant-lieux »
    # alors que la seconde est plus recente : on garde les plus recentes
    # d'apres la date du fichier, la seule chronologie fiable ici.
    def _date(nom):
        try:
            return os.path.getmtime(os.path.join(DATA, nom))
        except OSError:
            return 0.0

    copies = sorted((n for n in os.listdir(DATA)
                     if n.startswith("crm-") and n.endswith(".db")),
                    key=_date, reverse=True)
    lignes, poids = [], 0
    for nom in copies[SAUVEGARDES_GARDEES:]:
        chemin = os.path.join(DATA, nom)
        try:
            taille = os.path.getsize(chemin)
        except OSError:
            continue
        poids += taille
        lignes.append({"fichier": nom, "octets": taille})
    return ("Sauvegardes de base au-delà des %d dernières" % SAUVEGARDES_GARDEES,
            lignes, poids)


REGLES = [
    ("tournois_passes", _tournois_passes, "tournaments"),
    ("tournois_hors_zone", _tournois_hors_zone_passes, "tournaments"),
    ("brouillons_orphelins", _brouillons_orphelins, "drafts"),
    ("journal", _journal, "runlog"),
    ("affiches", _affiches_orphelines, "fichier:" + AFFICHES),
    ("sauvegardes", _sauvegardes, "fichier:" + DATA),
]


# --------------------------------------------------------------------------

def passer(pour_de_vrai=False):
    """Passe les regles. Sans argument, ne supprime rien et dit ce qu'il y a."""
    bilan = {"regles": [], "lignes": 0, "octets": 0, "applique": bool(pour_de_vrai)}

    for cle, fonction, cible in REGLES:
        libelle, lignes, poids = fonction()
        if pour_de_vrai and lignes:
            if cible.startswith("fichier:"):
                dossier = cible.split(":", 1)[1]
                for l in lignes:
                    try:
                        os.remove(os.path.join(dossier, l["fichier"]))
                    except OSError:
                        pass
            else:
                for l in lignes:
                    db.delete(cible, l["id"])

        bilan["regles"].append({
            "cle": cle, "libelle": libelle, "n": len(lignes),
            "octets": poids,
            # De quoi verifier avant d'appuyer, sans deverser mille lignes.
            "exemples": [(l.get("name") or l.get("fichier") or
                          ("#%s" % l.get("id"))) for l in lignes[:5]],
        })
        bilan["lignes"] += len(lignes)
        bilan["octets"] += poids

    if pour_de_vrai and bilan["lignes"]:
        db.log("purge", "Ménage : %d élément(s) retiré(s), %.1f Mo libérés"
               % (bilan["lignes"], bilan["octets"] / 1048576.0))
    return bilan


def resume(bilan):
    """Une phrase par regle, pour le journal du scan."""
    lignes = []
    for r in bilan["regles"]:
        if not r["n"]:
            continue
        poids = " (%.1f Mo)" % (r["octets"] / 1048576.0) if r["octets"] else ""
        lignes.append("%d %s%s" % (r["n"], r["libelle"].lower(), poids))
    return " · ".join(lignes) or "rien à retirer"
