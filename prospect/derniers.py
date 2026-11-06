# -*- coding: utf-8 -*-
"""Les numeros ramasses sur le terrain, a trier au retour.

Julien rentre d'une journee de shooting avec des numeros. Il ne les tape pas :
les gens lui ecrivent, ou il leur ecrit, et une conversation WhatsApp apparait.
Le lendemain elle est noyee entre un taxi et Chronopost, et le photographe
rencontre au bord du bassin est perdu.

Cet ecran ne demande RIEN a Julien : il montre les conversations apparues ces
derniers jours, du plus recent au plus ancien, et il lui reste trois gestes —
renommer, trier, relancer.

Le carnet de l'iPhone ne descend plus depuis le 13 octobre (compte local
« Sur mon iPhone »). WhatsApp, lui, arrive tous les matins : c'est donc lui
la source qui marche.
"""

import json
import re

from . import db, util
from . import rencontres
from .rencontres import IMPORTANT, PAS_UNE_RENCONTRE, RECRUE

JOURS_DEFAUT = 4
TRIS = ("booker", "photographe", "non")


def _fil(brut, maxi=4):
    try:
        msgs = [m for m in json.loads(brut or "[]") if isinstance(m, dict)]
    except (ValueError, TypeError):
        return []
    out = []
    for m in msgs[-maxi:]:
        texte = re.sub(r"\s+", " ", str(m.get("texte") or "")).strip()
        if texte:
            out.append({"de_moi": bool(m.get("de_moi")), "texte": texte[:300],
                        "quand": (m.get("quand") or "")[:10],
                        "heure": m.get("quand") or ""})
    return out


def _anonyme(nom):
    return not nom or bool(re.match(r"^\+?[\d\s().-]+$", nom.strip()))


def liste(jours=JOURS_DEFAUT, tout=False, tri=None):
    """Les conversations apparues depuis N jours, la plus fraiche d'abord.

    Avec `tri`, on demande une categorie — « ceux qui peuvent me booker » —
    et la fenetre de temps ne s'applique plus : un contact classe il y a
    deux mois reste a relancer aujourd'hui.
    """
    where = ["COALESCE(w.groupe, 0) = 0", "COALESCE(w.ecarte, 0) = 0"]
    args = []
    if tri:
        where.append("COALESCE(w.tri, '') = ?")
        args.append(tri)
    else:
        where.append("w.created_at >= date('now', ?)")
        args.append("-%d day" % int(jours))
        if not tout:
            where.append("COALESCE(w.tri, '') = ''")

    rows = db.rows_to_dicts(db.q(
        "SELECT w.id, w.nom, w.phone, w.derniers, w.created_at, w.dernier_le, "
        "       w.club_id, w.tri, w.note, c.name AS club_nom "
        "FROM wa_chats w LEFT JOIN clubs c ON c.id = w.club_id "
        "WHERE %s ORDER BY w.created_at DESC, w.id DESC" % " AND ".join(where),
        tuple(args)))

    out = []
    for r in rows:
        nom = (r["nom"] or "").strip()
        # Les robots et les boutiques ne sont pas des rencontres.
        if nom and PAS_UNE_RENCONTRE.search(nom):
            continue
        fil = _fil(r["derniers"])
        texte = " ".join(m["texte"] for m in fil)
        out.append({
            "id": r["id"],
            "nom": nom or (r["phone"] or "Sans nom"),
            "anonyme": _anonyme(nom),
            "tel": r["phone"],
            "vu_le": (r["created_at"] or "")[:10],
            "jours": _jours((r["created_at"] or "")[:10]),
            "dernier_le": (r["dernier_le"] or "")[:10],
            "club_id": r["club_id"], "club_nom": r["club_nom"],
            "tri": r["tri"] or "",
            "memo": r["note"] or "",
            "fil": fil,
            # Le nom court affiche devant chaque message : « Daniel » plutot
            # que « eux ». Le prenom de la fiche d'abord, s'il existe.
            "qui": rencontres.qui_parle(nom, r["phone"], r["club_id"]),
            # Deux indices pour aider a trancher, jamais pour decider.
            "important": bool(IMPORTANT.search(nom + " " + texte)),
            "recrue": bool(RECRUE.search(nom)),
        })
    return out


def _jours(iso):
    d = util.parse_date(str(iso or "")[:10])
    return util.days_between(d, util.today()) if d else None


def comptes():
    """Combien dans chaque categorie, toutes dates confondues."""
    out = {}
    for c in TRIS:
        r = db.q1("SELECT COUNT(*) AS n FROM wa_chats WHERE COALESCE(tri,'') = ? "
                  "AND COALESCE(groupe,0)=0 AND COALESCE(ecarte,0)=0", (c,))
        out[c] = r["n"] if r else 0
    return out


def tout(jours=JOURS_DEFAUT, montrer_tout=False, tri=None):
    l = liste(jours, montrer_tout, tri)
    deja = 0
    if not tri and not montrer_tout:
        deja = len(liste(jours, True)) - len(l)
    return {"items": l, "n": len(l), "jours": jours, "deja_tries": deja,
            "tri": tri or "", "comptes": comptes(),
            "sans_nom": sum(1 for x in l if x["anonyme"])}


def renommer(chat_id, nom):
    """Le nom que Julien leur donne. C'est tout ce qu'il tape."""
    nom = (nom or "").strip()[:120]
    if not nom:
        return {"ok": False, "error": "il faut un nom"}
    if not db.q1("SELECT id FROM wa_chats WHERE id = ?", (int(chat_id),)):
        return {"ok": False, "error": "conversation introuvable"}
    db.ex("UPDATE wa_chats SET nom = ?, updated_at = ? WHERE id = ?",
          (nom, util.now_iso(), int(chat_id)))
    return {"ok": True, "nom": nom}


def noter(chat_id, memo):
    """Le memo de Julien sur cette personne.

    « Emmanuelle, rencontree au tournoi, peut me booker pour ses galas » :
    deux lignes qui valent toutes les etiquettes, et qui se retrouvent
    quand on relance.
    """
    memo = (memo or "").strip()[:600]
    if not db.q1("SELECT id FROM wa_chats WHERE id = ?", (int(chat_id),)):
        return {"ok": False, "error": "conversation introuvable"}
    db.ex("UPDATE wa_chats SET note = ?, note_le = ?, updated_at = ? WHERE id = ?",
          (memo or None, util.now_iso(), util.now_iso(), int(chat_id)))
    return {"ok": True, "memo": memo}


def trier(chat_id, tri):
    """« peut me booker », « photographe », « sans suite ». Ou on annule."""
    tri = (tri or "").strip().lower()
    if tri and tri not in TRIS:
        return {"ok": False, "error": "tri inconnu"}
    if not db.q1("SELECT id FROM wa_chats WHERE id = ?", (int(chat_id),)):
        return {"ok": False, "error": "conversation introuvable"}
    db.ex("UPDATE wa_chats SET tri = ?, tri_le = ? WHERE id = ?",
          (tri or None, util.now_iso() if tri else None, int(chat_id)))
    return {"ok": True, "tri": tri}
