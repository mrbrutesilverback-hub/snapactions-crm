# -*- coding: utf-8 -*-
"""Les gens croises sur le terrain, avant qu'on les oublie.

Julien revient d'un tournoi ou d'une rando avec des numeros dans le
telephone. Deux jours plus tard ils sont noyes dans WhatsApp, entre un taxi,
Chronopost et une conversation de famille — et le president de club qui
pouvait redonner du travail est perdu.

Cet ecran ne cherche rien de neuf : tout est deja dans le journal WhatsApp
que le CRM relit chaque matin. Il le regarde simplement par le bon bout —
QUI ai-je rencontre recemment, et qu'est-ce qu'on s'est dit.

Ce qui remonte en premier n'est pas le plus recent : c'est celui qui a ecrit
EN DERNIER. Quand la derniere parole est a eux, la balle est dans votre camp
et le dossier se refroidit.
"""

import datetime as dt
import json
import re

from . import db, util

# Au-dela, ce n'est plus une rencontre, c'est de l'histoire.
JOURS_DEFAUT = 21

# Ce qui n'est pas quelqu'un qu'on a rencontre : les robots, les boutiques,
# les services. On ne les ecarte pas de la base, seulement de cet ecran.
PAS_UNE_RENCONTRE = re.compile(
    r"chronopost|colissimo|\bla poste\b|amazon|uber|bolt|free\b|orange\b|sfr\b|"
    r"bouygues|sncf|doctolib|ameli|impots|banque|\bcic\b|\bbnp\b|caisse d|"
    r"boulanger|darty|fnac|leboncoin|vinted|booking|airbnb|taxi|pizz|"
    r"livraison|notification|no.?reply|verification|code de", re.I)

# Ce qui, dans un nom ou un message, annonce quelqu'un qui compte.
IMPORTANT = re.compile(
    r"pr[ée]sident|pr[ée]sidente|tr[ée]sorier|secr[ée]taire|directeur|directrice|"
    r"responsable|coach|entra[îi]neur|[ée]ducateur|manager|organisateur|"
    r"dirigeant|capitaine|\bbureau\b|comit[ée]|ligue|f[ée]d[ée]ration|"
    r"club de|\bclub\b|association", re.I)


# Ceux qui ne sont pas des clients mais qui comptent autant : les gens du
# metier. Julien recrute — un photographe, un videaste, un creatrice de
# contenu croises sur un tournoi peuvent couvrir une date qu'il ne peut pas
# prendre. Ils meritent leur propre tiroir, et surtout pas le message de
# vente destine aux clubs.
RECRUE = re.compile(
    r"photograph|vid[ée]ast|camera\s?man|cadreur|op[ée]rateur|droniste|\bdrone\b|"
    r"content\s?creator|cr[ée]at(?:eur|rice)s?\s+de\s+contenu|influenceu|"
    r"retouche|monteu|reporter|\bpresse\b|journalist|\bmedia\b|\bphoto\b", re.I)


def _fil(derniers, maxi=6):
    try:
        msgs = [m for m in json.loads(derniers or "[]") if isinstance(m, dict)]
    except (ValueError, TypeError):
        return []
    out = []
    for m in msgs[-maxi:]:
        texte = re.sub(r"\s+", " ", str(m.get("texte") or "")).strip()
        if texte:
            out.append({"de_moi": bool(m.get("de_moi")), "texte": texte[:400],
                        "quand": (m.get("quand") or "")[:10],
                        "heure": m.get("quand") or ""})
    return out


def qui_parle(nom, tel=None, club_id=None):
    """Qui parle, en deux mots, pour l'afficher devant ses messages.

    Le nom du repertoire WhatsApp est souvent une phrase — « Cd 06 aviron
    Daniel President ». Quand on a une fiche contact pour ce numero, c'est
    SON prenom qu'on montre : c'est le nom qu'on a ecrit nous-memes, et
    celui qu'on reconnait. Sinon on raccourcit le nom du repertoire.
    """
    chiffres = re.sub(r"\D", "", str(tel or ""))
    if len(chiffres) >= 9:
        fin = chiffres[-9:]
        for c in db.q("SELECT first_name, phone, phone2 FROM contacts "
                      "WHERE first_name IS NOT NULL AND first_name <> ''"):
            for v in (c["phone"], c["phone2"]):
                if v and re.sub(r"\D", "", v).endswith(fin):
                    return util.nom_bulle(c["first_name"])
    for n in (nom if isinstance(nom, (list, tuple)) else [nom]):
        court = util.nom_bulle(n)
        if court:
            return court
    return None


def _jours(iso):
    d = util.parse_date(str(iso or "")[:10])
    return util.days_between(d, util.today()) if d else None


def _ecartees():
    """Les rencontres mises de cote, et pas encore revenues."""
    auj = util.today().isoformat()
    return {r["cle"].split(":", 1)[1] for r in db.q(
        "SELECT cle FROM journee_ecarts WHERE cle LIKE 'rencontre:%' AND jusquau > ?",
        (auj,))}


def liste(jours=JOURS_DEFAUT, tout=False):
    """Les personnes croisees recemment, la plus en attente d'abord."""
    masquees = set() if tout else _ecartees()
    rows = db.rows_to_dicts(db.q(
        "SELECT w.id, w.nom, w.phone, w.dernier_le, w.derniers, w.sortant, "
        "       w.club_id, w.note, c.name AS club_nom, c.status AS club_statut "
        "FROM wa_chats w LEFT JOIN clubs c ON c.id = w.club_id "
        "WHERE COALESCE(w.groupe, 0) = 0 AND COALESCE(w.ecarte, 0) = 0 "
        "  AND w.dernier_le >= date('now', ?) "
        "ORDER BY w.dernier_le DESC", ("-%d day" % int(jours),)))

    sortie = []
    for r in rows:
        nom = (r["nom"] or "").strip()
        if str(r["id"]) in masquees:
            continue
        # Un numero brut n'a pas de nom : ce n'est pas une raison de l'ecarter,
        # c'est souvent justement celui qu'on vient de prendre sur le terrain.
        if nom and PAS_UNE_RENCONTRE.search(nom):
            continue
        fil = _fil(r["derniers"])
        if not fil:
            continue
        dernier = fil[-1]
        texte_tout = " ".join(m["texte"] for m in fil)
        anonyme = not nom or bool(re.match(r"^\+?[\d\s]+$", nom))

        sortie.append({
            "cle": "rencontre:%s" % r["id"],
            "chat_id": r["id"],
            "nom": nom or (r["phone"] or "Sans nom"),
            "anonyme": anonyme,
            "tel": r["phone"],
            "quand": (r["dernier_le"] or "")[:10],
            "jours": _jours(r["dernier_le"]),
            "a_moi": not dernier["de_moi"],       # la derniere parole est a eux
            "fil": fil,
            "note": r["note"],
            "club_id": r["club_id"],
            "club_nom": r["club_nom"],
            "deja_client": (r["club_statut"] or "") in ("client", "repondu"),
            # Un titre dans le nom ou dans ce qu'on s'est dit : president,
            # coach, organisateur. C'est ce que Julien appelle « quelqu'un
            # d'important » — quelqu'un qui peut redonner du travail.
            "important": bool(IMPORTANT.search(nom + " " + texte_tout)),
            # Du metier : quelqu'un qui pourrait shooter pour SnapActions.
            # On le lit surtout dans le nom — « Leslie Content Creator » — et
            # un « photo » perdu dans un message ne suffit pas.
            "recrue": bool(RECRUE.search(nom)),
        })

    # Celui qui attend une reponse d'abord, puis le plus important, puis le
    # plus recent. Un dossier ou la balle est dans votre camp se refroidit.
    sortie.sort(key=lambda x: (not x["a_moi"],
                               not (x["important"] or x["recrue"]),
                               x["jours"] if x["jours"] is not None else 999))
    return sortie


def tout(jours=JOURS_DEFAUT, montrer_tout=False):
    l = liste(jours, montrer_tout)
    caches = 0
    if not montrer_tout:
        caches = len(liste(jours, True)) - len(l)
    return {
        "items": l, "jours": jours, "masques": caches,
        "n": len(l),
        "n_attendent": sum(1 for x in l if x["a_moi"]),
        "n_importants": sum(1 for x in l if x["important"]),
        "n_sans_fiche": sum(1 for x in l if not x["club_id"]),
        "n_recrues": sum(1 for x in l if x["recrue"]),
    }


ECARTS = {"fait": 1, "plus_tard": 14, "non": 365}


def ecarter(cle, motif="fait"):
    """Sort une rencontre de la liste. Rien n'est efface."""
    jours = ECARTS.get(motif, 1)
    jusquau = (dt.date.today() + dt.timedelta(days=jours)).isoformat()
    cle = str(cle or "")
    if not cle.startswith("rencontre:"):
        cle = "rencontre:%s" % cle
    # La colonne s'appelle pose_le, pas created_at : tous les autres appelants
    # (journee, creux) l'ecrivent ainsi. Ecrire created_at faisait echouer le
    # bouton « Traite » avec une OperationalError, en silence cote serveur.
    db.ex("INSERT OR REPLACE INTO journee_ecarts (cle, motif, jusquau, pose_le) "
          "VALUES (?, ?, ?, ?)", (cle, motif, jusquau, util.now_iso()))
    return {"ok": True, "cle": cle, "motif": motif, "jusquau": jusquau}
