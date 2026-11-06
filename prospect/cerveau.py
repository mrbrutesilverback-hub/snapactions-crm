# -*- coding: utf-8 -*-
"""Repondre a une question posee en francais, avec les faits du CRM.

La barre « Chercher » rendait des listes. Une liste n'est pas une reponse :
quand on demande « qu'est-ce que la natation m'a dit pour le salon ? », on
veut la phrase, pas quinze cartes a relire.

Trois regles tiennent tout ce module :

  1. Le modele ne voit QUE ce que la base contient. Il ne cherche pas, il ne
     sait rien de lui-meme, il ne complete pas de memoire. Ce qu'on ne lui
     donne pas n'existe pas pour lui.
  2. Il ne rend jamais un numero, un mail ni un nom qui ne figure pas dans ce
     qu'on lui a donne. Un numero plausible ne se distingue pas d'un vrai, et
     c'est ainsi qu'on ecrit a un inconnu en croyant ecrire a un client.
  3. Quand les faits ne repondent pas, il doit le dire. « Je ne sais pas » est
     une bonne reponse ; une phrase bien tournee et fausse n'en est pas une.

Les fiches qui ont servi sont renvoyees avec la reponse : on peut toujours
aller verifier, et c'est ce qui permet d'y croire.
"""

import datetime as dt
import json
import re

from . import db, ia, recherche, util

# Au-dela, le modele local perd le fil et se met a resumer au lieu de repondre.
MAX_FICHES = 12
MAX_EXTRAIT = 320


CONSIGNE = """Tu es l'assistant d'un photographe sportif independant (Julien, SnapActions, a Cannes). Il te pose une question sur SON carnet d'adresses et SES echanges. On te donne les FAITS extraits de sa base, et rien d'autre.

Tu rends UNIQUEMENT un objet JSON : {"reponse": "...", "fiches": [1, 4]}

- "reponse" : deux a cinq phrases, en francais, qui repondent a la question en t'appuyant SUR LES FAITS DONNES. Tu cites les noms tels qu'ils sont ecrits.
- "fiches" : les numeros des fiches qui t'ont servi, dans l'ordre d'importance. Vide si aucune n'a servi.

INTERDIT, sans aucune exception :
- Inventer un numero de telephone, un mail, un nom de club, une date ou un montant. Tu ne rends que ce qui figure MOT POUR MOT dans les faits.
- Completer avec ce que tu crois savoir du monde. Tu ne sais rien d'autre que ces faits.
- Affirmer qu'un echange a eu lieu si les faits ne le montrent pas.

Si les faits ne permettent pas de repondre, dis-le simplement : "Rien dans la base ne repond a ca" et explique en une phrase ce qui manque. Ne brode pas."""


def _bout(texte, maxi=MAX_EXTRAIT):
    t = re.sub(r"\s+", " ", str(texte or "")).strip()
    return t[:maxi]


def _faits(cartes):
    """Les fiches mises a plat, numerotees, lisibles par le modele."""
    lignes = []
    for i, c in enumerate(cartes[:MAX_FICHES], 1):
        bloc = ["FICHE %d — %s" % (i, c.get("nom") or "sans nom")]
        detail = [x for x in (c.get("sport"), c.get("ville"), c.get("statut")) if x]
        if detail:
            bloc.append("  " + " · ".join(detail))
        if c.get("telephone"):
            bloc.append("  telephone : %s" % c["telephone"])
        if c.get("email"):
            bloc.append("  mail : %s" % c["email"])
        for p in (c.get("personnes") or [])[:3]:
            qui = " ".join(x for x in (p.get("nom"), p.get("role")) if x)
            coord = " ".join(x for x in (p.get("telephone"), p.get("email")) if x)
            if qui or coord:
                bloc.append("  personne : %s %s" % (qui, coord))
        if c.get("note"):
            bloc.append("  note : %s" % _bout(c["note"]))
        # La conversation, pas seulement son dernier mot. La carte de
        # recherche ne porte que le message le plus recent ; a la question
        # « qu'est-ce qu'ils m'ont dit ? », c'est precisement l'avant-dernier
        # qui compte — celui ou ils repondent. On va donc lire le fil.
        for m in _fil(c.get("club_id"), c.get("nom")):
            bloc.append("  whatsapp %s, %s : %s" % (m[0], m[1], m[2]))
        ech = c.get("dernier_echange")
        if ech:
            bloc.append("  dernier echange : %s %s %s"
                        % ((ech.get("quand") or "")[:10], ech.get("type") or "",
                           ech.get("sujet") or ""))
        if c.get("extrait"):
            bloc.append("  trouve ici : %s" % _bout(c["extrait"]))
        lignes.append("\n".join(bloc))
    return "\n\n".join(lignes)


def _fil(club_id, nom_club, maxi=6):
    """Les derniers messages echanges avec ce club, dans l'ordre."""
    if not club_id:
        return []
    sortie = []
    for w in db.q("SELECT nom, dernier_le, derniers FROM wa_chats "
                  "WHERE club_id = ? AND ecarte = 0 "
                  "ORDER BY dernier_le DESC LIMIT 2", (club_id,)):
        try:
            msgs = [m for m in json.loads(w["derniers"] or "[]") if isinstance(m, dict)]
        except (ValueError, TypeError):
            continue
        for m in msgs[-maxi:]:
            texte = _bout(m.get("texte"), 260)
            if not texte:
                continue
            quand = str(m.get("quand") or w["dernier_le"] or "")[:10]
            qui = "Julien" if m.get("de_moi") else (w["nom"] or nom_club or "eux")
            sortie.append((quand, qui, texte))
    return sortie[-maxi:]


def _evenements(trouves):
    """Les dates a venir qui collent a la question."""
    lignes = []
    for i, t in enumerate(trouves[:6], 1):
        quoi = [x for x in (t.get("start_date"), t.get("city"), t.get("sport")) if x]
        lignes.append("DATE %d — %s (%s)" % (i, t.get("name") or "", " · ".join(quoi)))
    return "\n".join(lignes)


def demander(question, modele=None):
    """Une question en francais, une reponse appuyee sur la base.

    Renvoie toujours les fiches : la reponse sans ses sources ne se verifie
    pas, et une reponse qui ne se verifie pas ne vaut rien.
    """
    question = (question or "").strip()
    if not question:
        return {"question": question, "reponse": None, "fiches": [], "soucis": []}

    brut = recherche.chercher(question)
    cartes = brut.get("memoire") or []
    trouves = brut.get("trouves") or []
    soucis = list(brut.get("soucis") or [])

    if not cartes and not trouves:
        return {"question": question, "comprise": brut.get("comprise"),
                "reponse": "Rien dans la base ne correspond à cette question.",
                "fiches": [], "dates": [], "liens": brut.get("liens") or [],
                "soucis": soucis, "sans_ia": True}

    if not ia.disponible():
        soucis.append("Le modèle local est éteint : voici les fiches, sans la synthèse.")
        return {"question": question, "comprise": brut.get("comprise"),
                "reponse": None, "fiches": cartes[:MAX_FICHES],
                "dates": trouves[:6], "liens": brut.get("liens") or [],
                "soucis": soucis, "sans_ia": True}

    morceaux = ["QUESTION : %s" % question[:300], "", "LES FAITS :", _faits(cartes)]
    if trouves:
        morceaux += ["", "LES DATES A VENIR QUI CORRESPONDENT :", _evenements(trouves)]
    demande = "\n".join(morceaux)

    d = ia._json(CONSIGNE, demande, delai=90)
    if not d:
        soucis.append("Le modèle local n'a pas répondu : voici les fiches brutes.")
        return {"question": question, "comprise": brut.get("comprise"),
                "reponse": None, "fiches": cartes[:MAX_FICHES],
                "dates": trouves[:6], "liens": brut.get("liens") or [],
                "soucis": soucis, "sans_ia": True}

    reponse = _bout(d.get("reponse"), 1200) or None
    # Les fiches citees d'abord, les autres derriere : on ne cache jamais le
    # reste, parce qu'une synthese qui tait une fiche est une synthese fausse.
    cites = []
    for n in (d.get("fiches") or [])[:MAX_FICHES]:
        try:
            k = int(n) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= k < len(cartes) and cartes[k] not in cites:
            cites.append(cartes[k])
    reste = [c for c in cartes[:MAX_FICHES] if c not in cites]

    # Garde-fou : le modele a-t-il sorti un numero qui n'existe nulle part ?
    invente = _numero_invente(reponse, cartes)
    if invente:
        soucis.append("Un numéro de la réponse (%s) ne figure dans aucune fiche : "
                      "il a été retiré." % invente)
        reponse = reponse.replace(invente, "[numéro non vérifié]")

    return {"question": question, "comprise": brut.get("comprise"),
            "reponse": reponse, "fiches": cites + reste, "cites": len(cites),
            "dates": trouves[:6], "liens": brut.get("liens") or [], "soucis": soucis}


def _numero_invente(reponse, cartes):
    """Un numero dans la reponse qui ne figure dans aucune fiche.

    Le modele a pour consigne de ne jamais en ecrire. La consigne ne suffit
    pas : on verifie. Un numero plausible ne se distingue pas d'un vrai, et
    c'est le genre d'erreur qui fait appeler un inconnu.
    """
    if not reponse:
        return None
    connus = set()
    for c in cartes:
        for v in (c.get("telephone"), c.get("email")):
            if v:
                connus.add(re.sub(r"\D", "", str(v)) or str(v))
        for p in c.get("personnes") or []:
            for v in (p.get("telephone"), p.get("email")):
                if v:
                    connus.add(re.sub(r"\D", "", str(v)) or str(v))
    for brut in re.findall(r"(?:\+\d[\d\s.\-]{7,}|\b0\d(?:[\s.\-]?\d{2}){4}\b)", reponse):
        chiffres = re.sub(r"\D", "", brut)
        if chiffres and not any(chiffres in k or k in chiffres for k in connus if k):
            return brut.strip()
    return None


# ---------------------------------------------------------------------------
# Les routines : une question qu'on repose toute seule
# ---------------------------------------------------------------------------
# « Les clubs de danse a Nice » n'est pas une recherche a faire une fois : la
# reponse change chaque semaine, et le travail consiste justement a voir ce
# qui s'y ajoute. On garde donc la question, on la repose chaque nuit, et on
# ne remonte que ce qui n'y etait pas la veille.

SCHEMA_ROUTINES = """
CREATE TABLE IF NOT EXISTS routines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    actif       INTEGER NOT NULL DEFAULT 1,
    vus         TEXT,
    dernier_le  TEXT,
    dernier_n   INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);
"""


def _table():
    db.ex(SCHEMA_ROUTINES)


def routines():
    _table()
    return db.rows_to_dicts(db.q(
        "SELECT id, question, actif, dernier_le, dernier_n, created_at "
        "FROM routines ORDER BY id DESC"))


def ajouter_routine(question):
    _table()
    question = (question or "").strip()
    if len(question) < 3:
        return {"ok": False, "error": "la question est trop courte"}
    deja = db.q1("SELECT id FROM routines WHERE lower(question) = lower(?)", (question,))
    if deja:
        return {"ok": True, "id": deja["id"], "deja": True}
    rid = db.insert("routines", {"question": question, "actif": 1,
                                 "created_at": util.now_iso()})
    return {"ok": True, "id": rid, "deja": False}


def supprimer_routine(rid):
    _table()
    db.ex("DELETE FROM routines WHERE id = ?", (int(rid),))
    return {"ok": True}


def _cle_resultat(t):
    return "%s|%s" % (_bout(t.get("name"), 60).lower(), (t.get("start_date") or "")[:10])


def passer_les_routines():
    """Repose chaque question et ne retient que ce qui est nouveau."""
    _table()
    bilan = []
    for r in db.rows_to_dicts(db.q("SELECT * FROM routines WHERE actif = 1")):
        try:
            res = recherche.chercher(r["question"])
        except Exception as exc:  # noqa: BLE001 — une routine cassee n'arrete pas les autres
            bilan.append({"id": r["id"], "question": r["question"], "erreur": str(exc)})
            continue
        try:
            vus = set(json.loads(r.get("vus") or "[]"))
        except (ValueError, TypeError):
            vus = set()
        trouves = res.get("trouves") or []
        neufs = [t for t in trouves if _cle_resultat(t) not in vus]
        for t in trouves:
            vus.add(_cle_resultat(t))
        db.ex("UPDATE routines SET vus = ?, dernier_le = ?, dernier_n = ? WHERE id = ?",
              (json.dumps(sorted(vus)[-400:]), util.now_iso(), len(neufs), r["id"]))
        bilan.append({"id": r["id"], "question": r["question"],
                      "nouveaux": len(neufs),
                      "exemples": [t.get("name") for t in neufs[:4]]})
    return bilan


def nouveautes():
    """Ce que les routines ont rapporte a leur dernier passage."""
    _table()
    return [r for r in routines() if (r.get("dernier_n") or 0) > 0]
