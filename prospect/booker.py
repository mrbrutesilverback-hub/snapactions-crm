# -*- coding: utf-8 -*-
"""Quand quelqu'un dit oui, la date va dans le calendrier.

Julien envoie la plaquette et son message. Deux jours plus tard l'organisateur
repond « c'est bon pour le 15 novembre » — et la date reste dans WhatsApp.
Le jour ou il regarde son agenda, il a oublie, ou il a accepte autre chose.

Ce module lit les conversations ou la derniere parole est a eux, cherche un
accord ET une date, et pose l'evenement dans le calendrier SnapActions.

Trois regles, parce qu'un faux evenement dans un agenda coute plus cher
qu'un evenement manque :

1. l'accord doit etre dans LEUR dernier message, pas dans celui de Julien ;
2. la date doit etre ecrite noir sur blanc dans la conversation — jamais
   deduite, jamais inventee ;
3. Julien doit avoir parle avant : on ne booke pas sur un message isole.

Rien n'est envoye a personne. On ecrit dans l'agenda de Julien, c'est tout,
et chaque pose est tracee pour pouvoir etre defaite.
"""

import datetime as dt
import json
import re

from . import agenda, db, util

# Ce qui ressemble a un oui. Les formules molles (« je vais voir », « je
# reviens vers vous ») n'y sont pas : elles ne valent pas un engagement.
# « C'est note » et « parfait » n'y sont pas : ce sont des accuses de
# reception, pas des engagements. Vu en vrai : « C'est note. Bonne journee »
# repondait a « tenez-moi au courant » — et aurait pose un faux shooting.
ACCORD = re.compile(
    r"\b(c'?est (?:bon|ok|valid[ée]|d'?accord)|on (?:valide|est d'?accord|"
    r"vous attend|compte sur vous|fait comme ca)|je (?:valide|confirme)|"
    r"valid[ée]e?\b|confirm[ée]e?\b|banco|ca marche|ca me va|ca nous va|"
    r"bloquez|on bloque|bloquer la date|r[ée]servez|on vous prend|"
    r"avec plaisir|volontiers|partant[es]?\b|vous pouvez venir|"
    r"venez|on vous attend|d'?accord pour|ok pour|oui pour)\b", re.I)

# Un refus annule tout, meme si un « ok » traine dans la phrase.
REFUS = re.compile(
    r"\b(pas (?:possible|interesse|dispo|pour nous|cette fois)|on ne (?:peut|"
    r"souhaite)|malheureusement|annul[ée]|report[ée]|une autre fois|"
    r"sans suite|on passe notre tour|non merci)\b", re.I)

MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}
_JOUR_MOIS = re.compile(
    r"\b(\d{1,2})(?:\s*er)?\s+(%s)\b(?:\s+(\d{4}))?" % "|".join(MOIS))
_NUMERIQUE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b")
_HEURE = re.compile(r"\b(\d{1,2})\s*[hH](?:\s*(\d{2}))?\b")


def _plat(texte):
    import unicodedata
    t = unicodedata.normalize("NFD", texte or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def dates_du_texte(texte, aujourdhui=None):
    """Les dates ecrites dans un texte, a venir uniquement.

    On ne devine pas l'annee : si elle manque, c'est l'annee en cours, et
    l'annee suivante quand le jour est deja passe. Une date passee est jetee.
    """
    auj = aujourdhui or util.today()
    plat = _plat(texte)
    trouvees = []

    for jour, mois, annee in _JOUR_MOIS.findall(plat):
        trouvees.append((int(jour), MOIS[mois], int(annee) if annee else None))
    for jour, mois, annee in _NUMERIQUE.findall(plat):
        m = int(mois)
        if 1 <= m <= 12:
            an = int(annee) if annee else None
            if an is not None and an < 100:
                an += 2000
            trouvees.append((int(jour), m, an))

    out = []
    for jour, mois, annee in trouvees:
        for essai in ([annee] if annee else [auj.year, auj.year + 1]):
            try:
                d = dt.date(essai, mois, jour)
            except ValueError:
                continue
            if d >= auj:
                out.append(d)
                break
    # Dedoublonne en gardant l'ordre d'apparition.
    vues, propres = set(), []
    for d in out:
        if d not in vues:
            vues.add(d)
            propres.append(d)
    return propres


def heure_du_texte(texte):
    """« a partir de 10h » -> (10, 0). None si rien de clair."""
    m = _HEURE.search(texte or "")
    if not m:
        return None
    h = int(m.group(1))
    mn = int(m.group(2) or 0)
    if 0 <= h <= 23 and 0 <= mn <= 59:
        return (h, mn)
    return None


def _messages(chat):
    try:
        msgs = [m for m in json.loads(chat["derniers"] or "[]")
                if isinstance(m, dict)]
    except (ValueError, TypeError):
        return []
    return msgs


def examiner(chat, aujourdhui=None):
    """Y a-t-il un oui et une date dans cette conversation ?

    Rend la proposition de reservation, ou None avec le motif du refus. On ne
    pose rien ici : examiner ne touche a rien.
    """
    msgs = _messages(chat)
    if not msgs:
        return None, "aucun message lu"

    dernier = msgs[-1]
    if dernier.get("de_moi"):
        return None, "le dernier mot est le vôtre"

    # Julien doit avoir parle avant : la plaquette, le message, une proposition.
    if not any(m.get("de_moi") for m in msgs[:-1]):
        return None, "vous ne leur avez encore rien envoyé"

    # Tout ce qu'ils ont dit depuis votre dernier message.
    recu = []
    for m in reversed(msgs):
        if m.get("de_moi"):
            break
        recu.append(m.get("texte") or "")
    recu = " ".join(reversed(recu))

    if REFUS.search(recu):
        return None, "leur message contient un refus ou un report"
    if not ACCORD.search(recu):
        return None, "pas d'accord clair dans leur message"

    # D'ou vient la date, et c'est la que tout se joue. Deux sources valent :
    # leur message d'accord, et la proposition de Julien a laquelle il repond
    # (« on couvre votre Koh-Lanta du 5 decembre » -> « ok, bloquez »).
    #
    # Leurs messages PLUS ANCIENS ne valent pas : vu en vrai, « nous avons une
    # reunion le 13 novembre » suivi plus tard de « c'est note » aurait pose
    # un shooting le jour de LEUR reunion interne.
    dates = dates_du_texte(recu, aujourdhui)
    source = "leur message"
    if not dates:
        propose = " ".join(m.get("texte") or "" for m in msgs if m.get("de_moi"))
        dates = dates_du_texte(propose, aujourdhui)
        source = "votre proposition"
    if not dates:
        return None, "aucune date à venir dans la conversation"
    if len(dates) > 1:
        return None, ("%d dates possibles (%s) — à caler à la main"
                      % (len(dates), ", ".join(d.isoformat() for d in dates[:4])))

    jour = dates[0]
    heure = heure_du_texte(recu) or heure_du_texte(
        " ".join(m.get("texte") or "" for m in msgs))
    return {
        "chat_id": chat["id"],
        "qui": (chat["nom"] or chat["phone"] or "").strip(),
        "tel": chat["phone"],
        "club_id": chat["club_id"],
        "jour": jour.isoformat(),
        "heure": heure,
        "ou_trouvee": source,
        "extrait": re.sub(r"\s+", " ", recu).strip()[:300],
    }, None


# -- La pose -----------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER,
    club_id    INTEGER,
    qui        TEXT,
    jour       TEXT NOT NULL,
    uid        TEXT,
    titre      TEXT,
    extrait    TEXT,
    annule     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (chat_id, jour)
);
"""


def _table():
    db.ex(SCHEMA)


def deja_pose(chat_id, jour):
    _table()
    return db.q1("SELECT id FROM bookings WHERE chat_id = ? AND jour = ? "
                 "AND COALESCE(annule, 0) = 0", (int(chat_id), jour))


# Les mots qui ne distinguent personne : ils ne valent pas une correspondance.
BANALS = frozenset("""
club sport sports association asso comite ligue federation equipe team
monsieur madame mme contact tournoi tournois championnat coupe stage gala
cannes nice antibes grasse mandelieu vallauris menton saint sainte var
alpes maritimes france photo photos shooting president presidente
""".split())


def _mots_cles(texte):
    mots = re.findall(r"[a-z]{4,}", _plat(texte))
    return {m for m in mots if m not in BANALS}


def deja_au_calendrier(jour, qui, titres=None):
    """L'evenement est-il deja dans le calendrier, pose a la main ?

    Julien booke souvent lui-meme en raccrochant. Sans ce garde-fou, le
    booker creait un doublon : vu en vrai, « Robert Kravmaga » aurait double
    « KOH LANTA KRAVMAGA CANNES », deja au 5 decembre.
    """
    cles = _mots_cles(qui)
    if not cles:
        return None
    if titres is None:
        try:
            evs = agenda.lister(depuis=jour) or []
        except Exception:
            return None
        titres = [(e.get("titre") or e.get("title") or "")
                  for e in evs
                  if str(e.get("debut") or e.get("start") or "")[:10] == jour]
    for t in titres:
        if _mots_cles(t) & cles:
            return t
    return None


def poser(proposition):
    """Ecrit l'evenement dans le calendrier et le trace."""
    _table()
    if deja_pose(proposition["chat_id"], proposition["jour"]):
        return {"ok": False, "motif": "déjà posé"}

    qui = proposition["qui"] or "contact"
    double = deja_au_calendrier(proposition["jour"], qui)
    if double:
        return {"ok": False, "motif": "déjà dans votre agenda : « %s »" % double,
                "deja": double}
    titre = "Shooting — %s" % qui[:60]
    heure = proposition.get("heure")
    if heure:
        debut = "%s %02d:%02d" % (proposition["jour"], heure[0], heure[1])
        journee = False
    else:
        debut = proposition["jour"]
        journee = True

    notes = "\n".join([
        "Posé automatiquement par SnapActions : ils ont donné leur accord.",
        "",
        "Leur message : « %s »" % proposition["extrait"],
        "",
        "Téléphone : %s" % (proposition.get("tel") or "—"),
        "À confirmer avec eux (horaire exact, lieu, accès).",
    ])

    try:
        fait = agenda.creer(titre, debut, lieu=None, notes=notes, journee=journee)
    except Exception as exc:
        return {"ok": False, "motif": "calendrier : %s" % exc}

    uid = (fait or {}).get("uid") if isinstance(fait, dict) else None
    db.insert("bookings", {
        "chat_id": proposition["chat_id"], "club_id": proposition.get("club_id"),
        "qui": qui, "jour": proposition["jour"], "uid": uid, "titre": titre,
        "extrait": proposition["extrait"], "created_at": util.now_iso(),
    })
    return {"ok": True, "titre": titre, "jour": proposition["jour"],
            "heure": heure, "uid": uid}


def tourner(jours=30, poser_vraiment=True):
    """Passe toutes les conversations recentes et pose ce qui est mûr."""
    _table()
    rows = db.rows_to_dicts(db.q(
        "SELECT id, nom, phone, club_id, derniers, dernier_le FROM wa_chats "
        "WHERE COALESCE(groupe, 0) = 0 AND COALESCE(ecarte, 0) = 0 "
        "  AND dernier_le >= date('now', ?) "
        "ORDER BY dernier_le DESC", ("-%d day" % int(jours),)))

    poses, vus, refus = [], 0, {}
    for chat in rows:
        vus += 1
        prop, motif = examiner(chat)
        if not prop:
            if motif:
                refus[motif] = refus.get(motif, 0) + 1
            continue
        if deja_pose(prop["chat_id"], prop["jour"]):
            continue
        if not poser_vraiment:
            poses.append(dict(prop, simule=True))
            continue
        out = poser(prop)
        if out.get("ok"):
            poses.append(dict(prop, titre=out["titre"], uid=out.get("uid")))
        else:
            motif = out.get("motif") or "refus"
            refus[motif] = refus.get(motif, 0) + 1
    return {"examines": vus, "poses": poses, "n": len(poses), "refus": refus}


def liste(limite=50):
    """Ce qui a ete pose, pour pouvoir le relire et le defaire."""
    _table()
    return db.rows_to_dicts(db.q(
        "SELECT * FROM bookings WHERE COALESCE(annule, 0) = 0 "
        "ORDER BY jour ASC LIMIT ?", (int(limite),)))


def annuler(bid):
    """Retire l'evenement du calendrier et marque la ligne annulee."""
    _table()
    r = db.q1("SELECT * FROM bookings WHERE id = ?", (int(bid),))
    if not r:
        return {"ok": False, "motif": "introuvable"}
    if r["uid"]:
        try:
            agenda.supprimer(r["uid"])
        except Exception:
            pass          # l'evenement a pu etre efface a la main : on continue
    db.ex("UPDATE bookings SET annule = 1 WHERE id = ?", (int(bid),))
    return {"ok": True}


# -- Le veilleur -------------------------------------------------------------
#
# Julien ne doit pas avoir a lancer un scan. Toutes les dix minutes, on relit
# les conversations recentes ; quand un accord et une date se rejoignent, la
# date entre dans le calendrier. Rien n'est envoye a personne.

INTERVALLE = 600
_dernier = {"le": None, "poses": 0, "erreur": None}


def dernier_passage():
    return dict(_dernier)


def un_tour():
    try:
        d = tourner()
    except Exception as exc:
        _dernier.update({"le": util.now_iso(), "poses": 0, "erreur": str(exc)})
        return {"poses": 0, "erreur": str(exc)}
    _dernier.update({"le": util.now_iso(), "poses": d["n"], "erreur": None})
    return {"poses": d["n"], "details": d["poses"]}


def veiller(arret=None):
    import threading
    import time

    def tourner_sans_fin():
        while True:
            if arret is not None and arret.is_set():
                return
            try:
                un_tour()
            except Exception:
                pass
            if arret is not None:
                if arret.wait(INTERVALLE):
                    return
            else:
                time.sleep(INTERVALLE)

    fil = threading.Thread(target=tourner_sans_fin, name="booker-veilleur",
                           daemon=True)
    fil.start()
    return fil
