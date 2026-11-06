# -*- coding: utf-8 -*-
"""« Est-ce que j'ai deja ecrit a ce numero ? »

La question se pose juste avant d'appuyer, et elle se pose souvent : la
meme personne organise plusieurs tournois dans l'annee, un club revient
sous un autre nom, un numero trouve sur une affiche est deja dans le
carnet depuis six mois.

Avant, la reponse arrivait trop tard. L'envoi WhatsApp part pour de vrai
au clic — c'est voulu, c'est ce qui fait gagner du temps — mais quand on
se rend compte apres coup qu'on avait deja ecrit, il faut aller supprimer
le message dans la conversation, et l'autre l'a parfois deja lu.

Donc on regarde AVANT, dans les quatre endroits ou une trace existe :

  - les conversations WhatsApp (wa_chats), la trace la plus sure
  - les brouillons reellement partis (drafts.sent_at)
  - les echanges notes a la main (interactions)
  - la date de dernier contact du carnet (contacts.last_contact_at)

On compare sur les neuf derniers chiffres : 06 27 39 88 24, +33 6 27 39
88 24 et 0033627398824 sont le meme numero, et ils sont ecrits des trois
facons dans la base selon d'ou ils viennent.
"""

import re

from . import db, util

# Neuf chiffres : assez pour ne pas confondre deux numeros, assez peu pour
# que l'indicatif du pays ne compte pas.
LONGUEUR_CLE = 9

# Au-dela, ce n'est plus « je viens de lui ecrire », c'est de l'historique.
# On le montre quand meme, mais sans arreter la main de Julien.
RECENT_JOURS = 120


def cle(numero):
    """Les neuf derniers chiffres, ou None si ca ne ressemble pas a un numero."""
    chiffres = re.sub(r"\D", "", str(numero or ""))
    if len(chiffres) < LONGUEUR_CLE:
        return None
    return chiffres[-LONGUEUR_CLE:]


def _jours(quand):
    d = util.parse_dt(quand)
    if not d:
        return None
    import datetime as dt
    return (dt.datetime.now() - d).days


def _bout(texte, n=120):
    t = " ".join(str(texte or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def chercher(tel=None, club_id=None, email=None):
    """Tout ce qu'on a deja envoye a ce numero, ce club, cette adresse.

    Rend les traces les plus recentes d'abord. Une liste vide veut dire
    qu'on n'a rien trouve — pas qu'on n'a jamais ecrit : si le message est
    parti d'un telephone, le CRM n'en sait rien.
    """
    k = cle(tel)
    mail = (email or "").strip().lower() or None
    club_id = int(club_id) if club_id else None
    traces = []

    # 1. Les conversations WhatsApp. C'est la trace la plus fiable : elle
    #    vient de WhatsApp lui-meme, pas de ce qu'on a note.
    if k:
        for r in db.rows_to_dicts(db.q(
                "SELECT phone, nom, apercu, dernier_le, sortant, club_id, "
                "       groupe FROM wa_chats "
                "WHERE phone IS NOT NULL AND phone <> '' "
                "  AND COALESCE(groupe, 0) = 0")):
            if cle(r.get("phone")) != k:
                continue
            traces.append({
                "canal": "whatsapp",
                "quand": r.get("dernier_le"),
                "sens": "envoyé" if r.get("sortant") else "reçu",
                "qui": r.get("nom") or util.format_phone(r.get("phone")),
                "quoi": _bout(r.get("apercu")),
                "club_id": r.get("club_id"),
            })

    # 2. Les brouillons vraiment partis. sent_at, pas created_at : un
    #    brouillon ecrit et jamais envoye ne compte pas.
    lignes = db.rows_to_dicts(db.q(
        "SELECT d.to_addr, d.channel, d.subject, d.body, d.sent_at, "
        "       d.club_id, c.name AS club, t.name AS tournoi "
        "FROM drafts d "
        "LEFT JOIN clubs c ON c.id = d.club_id "
        "LEFT JOIN tournaments t ON t.id = d.tournament_id "
        "WHERE d.sent_at IS NOT NULL AND d.sent_at <> ''"))
    for r in lignes:
        a = r.get("to_addr") or ""
        touche = False
        if k and cle(a) == k:
            touche = True
        elif mail and a.strip().lower() == mail:
            touche = True
        elif club_id and r.get("club_id") == club_id:
            touche = True
        if not touche:
            continue
        traces.append({
            "canal": r.get("channel") or "message",
            "quand": r.get("sent_at"),
            "sens": "envoyé",
            "qui": r.get("club") or a,
            "quoi": _bout(r.get("subject") or r.get("body")),
            "tournoi": r.get("tournoi"),
            "club_id": r.get("club_id"),
        })

    # 3. Ce qu'on a note a la main : un appel, une rencontre au bord du
    #    terrain. Ca compte autant qu'un message.
    if club_id:
        for r in db.rows_to_dicts(db.q(
                "SELECT kind, direction, subject, body, occurred_at, club_id "
                "FROM interactions WHERE club_id = ? "
                "ORDER BY COALESCE(occurred_at, created_at) DESC LIMIT 10",
                (club_id,))):
            traces.append({
                "canal": r.get("kind") or "note",
                "quand": r.get("occurred_at"),
                "sens": "envoyé" if (r.get("direction") or "") == "out" else "reçu",
                "qui": None,
                "quoi": _bout(r.get("subject") or r.get("body")),
                "club_id": r.get("club_id"),
            })

    # 4. Le carnet. Il ne dit pas quoi, seulement quand — c'est deja
    #    beaucoup quand on hesite a ecrire.
    if k:
        for r in db.rows_to_dicts(db.q(
                "SELECT first_name, last_name, phone, phone2, "
                "       last_contact_at, club_id FROM contacts "
                "WHERE last_contact_at IS NOT NULL AND last_contact_at <> ''")):
            if cle(r.get("phone")) != k and cle(r.get("phone2")) != k:
                continue
            nom = " ".join(x for x in (r.get("first_name"), r.get("last_name"))
                           if x).strip()
            traces.append({
                "canal": "carnet",
                "quand": r.get("last_contact_at"),
                "sens": "envoyé",
                "qui": nom or None,
                "quoi": "dernier contact noté dans le carnet",
                "club_id": r.get("club_id"),
            })

    traces = [t for t in traces if t.get("quand")]
    traces.sort(key=lambda t: str(t["quand"]), reverse=True)

    # Le nom du club, quand on l'a : « déjà écrit » sans savoir a qui ne
    # sert a rien.
    for t in traces:
        if t.get("club_id") and not t.get("club"):
            c = db.q1("SELECT name FROM clubs WHERE id = ?", (t["club_id"],))
            if c:
                t["club"] = c["name"]

    for t in traces:
        t["jours"] = _jours(t["quand"])
        t["quand_court"] = str(t["quand"])[:10]

    sortants = [t for t in traces if t.get("sens") == "envoyé"]
    dernier = sortants[0] if sortants else (traces[0] if traces else None)
    return {
        "deja": bool(sortants),
        "combien": len(sortants),
        "recent": bool(dernier and dernier.get("jours") is not None
                       and dernier["jours"] <= RECENT_JOURS),
        "dernier": dernier,
        "traces": traces[:6],
    }


def phrase(d):
    """Une ligne lisible, pour l'ecran comme pour la fenetre de confirmation."""
    if not d or not d.get("deja"):
        return ""
    t = d["dernier"]
    bouts = ["Déjà écrit"]
    if t.get("club") or t.get("qui"):
        bouts.append("à " + (t.get("club") or t["qui"]))
    j = t.get("jours")
    if j == 0:
        bouts.append("aujourd’hui")
    elif j == 1:
        bouts.append("hier")
    elif j is not None:
        bouts.append("il y a %d jours" % j)
    bouts.append("(%s, le %s)" % (t.get("canal") or "message",
                                  t.get("quand_court") or "?"))
    if d["combien"] > 1:
        bouts.append("— %d envois en tout" % d["combien"])
    return " ".join(bouts)
