# -*- coding: utf-8 -*-
"""Les deux piles ou dort l'argent deja gagne.

Le CRM sait depuis longtemps qui attend une reponse et qui n'a jamais ete
relance. Il le savait si bien qu'il le rangeait soigneusement — dans une
route que personne n'affichait, et derriere un plafond de douze lignes sur
l'ecran du jour. Au 16 octobre 2026 : neuf clubs attendaient une reponse,
dont un depuis trente-huit jours apres avoir ecrit « vous pouvez passer
quand vous voulez » ; et soixante-treize clubs rencontres aux forums de
Saint-Laurent-du-Var et de Cagnes le 5 septembre n'avaient jamais ete
relances, leur rappel etant echu depuis le 9 septembre.

Ce module ne fait qu'une chose : sortir ces deux piles et, pour chacune,
preparer le mot qu'on enverrait. Rien ne part d'ici — c'est une avance de
travail, pas un envoi. Julien relit, corrige, et envoie lui-meme.
"""

import datetime as dt
import json

from . import db, drafts, rencontres, util, wa

# Un club qui a ecrit il y a moins de deux jours n'est pas « en attente » :
# on a encore le temps de repondre normalement.
ATTENTE_MIN = 2

# Au-dela, la relance n'est plus une relance, c'est une reprise de contact.
TRES_EN_RETARD = 30


def _depuis(jours):
    if jours is None:
        return ""
    if jours <= 0:
        return "aujourd'hui"
    if jours == 1:
        return "hier"
    return "il y a %d jours" % jours


def _jours_depuis(iso):
    d = util.parse_date(str(iso)[:10]) if iso else None
    return util.days_between(d, util.today()) if d else None


# ---------------------------------------------------------------------------
# Pile 1 : ils ont ecrit, on n'a pas repondu
# ---------------------------------------------------------------------------

def attendent(tout_montrer=False):
    """Les conversations ou la derniere parole est a eux.

    Ce qui a ete raye sur l'ecran du jour reste raye ici : deux ecrans qui ne
    sont pas d'accord sur ce qui est fait, c'est un ecran de trop. Le
    masquage ne dure qu'un jour, et `tout_montrer` le leve.
    """
    from . import journee
    masquees = set() if tout_montrer else journee._ecartees()
    sortie = []
    for col in wa.suivi()["colonnes"]:
        if col["cle"] != "a_traiter":
            continue
        for c in col["cartes"]:
            jours = c.get("jours") or 0
            if jours < ATTENTE_MIN:
                continue
            # La cle est celle de l'ecran du jour : « fait » la-bas vaut ici.
            if ("f1:%s" % c.get("id")) in masquees:
                continue
            titre, mot = wa.mot_de_reponse(c)
            # Le fil, pour decider sans ouvrir WhatsApp. On garde l'ordre :
            # savoir QUI a parle en dernier est la moitie de l'information.
            # « heure » garde l'heure du message (« 2026-10-23 14:32 ») : la
            # conversation l'affiche en fin de ligne, comme bitchat.
            fil = [{"de_moi": bool(m.get("de_moi")),
                    "texte": (m.get("texte") or "").strip(),
                    "heure": m.get("quand") or ""}
                   for m in (c.get("derniers") or []) if (m.get("texte") or "").strip()]
            sortie.append({
                "cle": "f1:%s" % c.get("id"),
                "chat_id": c.get("id"),
                "club": c.get("club") or c.get("personne") or "Sans nom",
                "club_id": c.get("club_id"),
                "personne": c.get("personne"),
                "sport": c.get("sport"), "ville": c.get("ville"),
                "tel": c.get("phone"),
                "jours": jours,
                "depuis": _depuis(jours),
                "urgence": "brulant" if jours >= 14 else "chaud",
                "resume": c.get("resume"),
                "fil": fil[-6:],
                "qui": rencontres.qui_parle(
                    [c.get("personne"), c.get("club")], c.get("phone"),
                    c.get("club_id")),
                "mot": mot, "mot_titre": titre or "Réponse proposée",
                "evenement": (c.get("evenement") or {}).get("name"),
            })
    sortie.sort(key=lambda x: -x["jours"])
    return sortie


# ---------------------------------------------------------------------------
# Pile 2 : le rappel est echu et rien n'est parti
# ---------------------------------------------------------------------------

def _dernier_mot_sortant(club_id):
    """Ce qu'on leur a dit en dernier, et quand. Pour ne pas se repeter."""
    r = db.q1(
        "SELECT body, created_at FROM interactions "
        "WHERE club_id = ? AND direction = 'out' AND COALESCE(body,'') <> '' "
        "ORDER BY created_at DESC LIMIT 1", (club_id,))
    if r:
        return (r["body"] or "").strip(), r["created_at"]
    r = db.q1(
        "SELECT body, COALESCE(sent_at, updated_at) AS quand FROM drafts "
        "WHERE club_id = ? AND status = 'sent' ORDER BY id DESC LIMIT 1", (club_id,))
    if r:
        return (r["body"] or "").strip(), r["quand"]
    return None, None


def _modele_de_relance(club, deja_ecrit):
    """Quel mot leur envoyer, maintenant.

    Un club a qui l'on n'a jamais rien envoye ne se « relance » pas : il se
    contacte. Lui servir « je remonte mon message » serait un mensonge, et un
    mensonge verifiable — ils n'ont rien recu.
    """
    contact = drafts.best_contact(club["id"])
    tel = (contact or {}).get("phone") or club.get("phone")
    mail = (contact or {}).get("email") or club.get("email")
    mobile = util.is_mobile_fr(tel)
    if not deja_ecrit:
        # Jamais rien recu : c'est la proposition studio qui part, dans la
        # langue de la maison, avec le rappel du forum ou on s'est vus.
        return drafts.modele_studio(club, "whatsapp" if mobile else None)
    # Un club rencontre en personne ne se relance pas comme un club demarche a
    # froid. « Toujours partant pour 15 minutes ? » ne dit ni ou l'on s'est
    # vus, ni ce qu'on propose, ni pourquoi repondre maintenant : c'est la
    # relance qu'on ignore. On rappelle donc le stand, on propose une date, et
    # on laisse une porte de sortie — sans elle, le silence continue et on ne
    # saura jamais si c'est non.
    if "forum" in (club.get("source") or "").lower():
        return "relance_forum"
    if mobile and not mail:
        return "sms_relance"
    return "relance_1"


def a_relancer(limite=120):
    """Les clubs dont le rappel est echu, le mot deja redige."""
    rows = db.rows_to_dicts(db.q(
        "SELECT t.id AS task_id, date(t.due_at) AS du, t.title, "
        "       c.* "
        "FROM tasks t JOIN clubs c ON c.id = t.club_id "
        "WHERE t.kind = 'relance' AND t.status = 'pending' "
        "  AND date(t.due_at) < date('now') "
        "  AND COALESCE(c.do_not_contact, 0) = 0 "
        "GROUP BY c.id "
        "ORDER BY t.due_at LIMIT ?", (int(limite),)))

    sortie = []
    for r in rows:
        contact = drafts.best_contact(r["id"])
        tel = (contact or {}).get("phone") or r.get("phone")
        mail = (contact or {}).get("email") or r.get("email")
        if not (tel or mail):
            continue                      # rien pour les joindre : rien a faire
        # Le fil WhatsApp du club. Sans lui, la carte ne montrait qu'une note
        # (« Rencontre sur le stand puis message envoye ») et le message
        # propose venait d'un modele qui ignorait ce qu'on s'etait dit.
        chat = db.q1(
            "SELECT id, derniers FROM wa_chats WHERE club_id = ? "
            "AND COALESCE(derniers,'') <> '' "
            "ORDER BY dernier_le DESC LIMIT 1", (r["id"],))
        fil, chat_id = [], None
        if chat:
            chat_id = chat["id"]
            try:
                msgs = json.loads(chat["derniers"] or "[]")
            except (TypeError, ValueError):
                msgs = []
            fil = [{"de_moi": bool(m.get("de_moi")),
                    "texte": (m.get("texte") or "").strip(),
                    "quand": (m.get("quand") or "")[:10],
                    "heure": m.get("quand") or ""}
                   for m in msgs if (m.get("texte") or "").strip()][-6:]

        dit, le = _dernier_mot_sortant(r["id"])
        modele = _modele_de_relance(r, bool(dit))
        rendu = drafts.render(modele, r, contact, None)
        retard = _jours_depuis(r["du"])
        sortie.append({
            "cle": "relance:%s" % r["task_id"],
            "task_id": r["task_id"],
            "chat_id": chat_id,
            "fil": fil,
            "qui": rencontres.qui_parle(r["name"], tel, r["id"]),
            "club": r["name"], "club_id": r["id"],
            "sport": r.get("sport"), "ville": r.get("city"),
            "tel": tel, "mail": mail,
            "du": r["du"], "retard": retard,
            "depuis": _depuis(retard),
            "urgence": "chaud" if (retard or 0) >= TRES_EN_RETARD else "tiede",
            "rencontre": r.get("source") or "",
            "deja_dit": (dit or "")[:300] or None,
            "deja_dit_le": le[:10] if le else None,
            "jamais_ecrit": not dit,
            "mot": (rendu or {}).get("body"),
            "objet": (rendu or {}).get("subject"),
            "modele": modele,
            "canal": (rendu or {}).get("channel"),
        })
    # Ceux a qui l'on a deja parle d'abord : la conversation est commencee,
    # elle se reprend en une phrase. Les autres demandent un vrai message.
    sortie.sort(key=lambda x: (x["jamais_ecrit"], -(x["retard"] or 0)))
    return sortie


# ---------------------------------------------------------------------------

# Passe ce delai, le journal WhatsApp n'est plus une image du present : des
# clubs ont pu repondre sans que rien ne le montre.
JOURNAL_PERIME = 48       # heures


def fraicheur_journal():
    """Quand le journal WhatsApp a-t-il ete relu, et est-ce encore frais ?

    La relecture a echoue trois matins de suite sans que personne ne le sache :
    le scan de 7 h 30 n'a pas l'Acces complet au disque, il passe donc la main
    au serveur, et le serveur avait perdu la sienne en devenant orphelin de son
    Terminal. L'echec ne s'ecrivait que dans un fichier de journal, et s'est
    decouvert quatre jours plus tard au milieu d'une fiche. Une panne qui ne se
    voit pas est une panne qui dure.
    """
    r = db.q1("SELECT created_at FROM runlog WHERE message LIKE 'Relecture locale%' "
              "ORDER BY id DESC LIMIT 1")
    if not r:
        return {"lu_le": None, "heures": None, "perime": True}
    quand = str(r["created_at"])
    try:
        d = dt.datetime.strptime(quand[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {"lu_le": quand, "heures": None, "perime": False}
    heures = (dt.datetime.now() - d).total_seconds() / 3600.0
    return {"lu_le": quand, "heures": round(heures, 1),
            "perime": heures > JOURNAL_PERIME}


def tout(limite=120, tout_montrer=False):
    a = attendent(tout_montrer)
    r = a_relancer(limite)
    cachees = 0
    if not tout_montrer:
        cachees = len(attendent(True)) - len(a)
    return {
        "attendent": a, "a_relancer": r,
        "n_attendent": len(a), "n_relancer": len(r),
        "masques": cachees,
        "plus_vieux": a[0]["jours"] if a else 0,
        "journal": fraicheur_journal(),
    }


def fait(cle, motif="fait"):
    """Marque une ligne comme traitee. Rien n'est efface.

    Une relance « faite » ferme la tache et en programme la suivante : c'est
    ainsi qu'une sequence avance. Une reponse « faite » se contente de sortir
    de la pile du jour — si le message n'est reellement pas parti, le fil
    WhatsApp le dira a la prochaine synchro et la carte reviendra.
    """
    from . import journee
    cle = str(cle or "")
    if cle.startswith("relance:"):
        tid = int(cle.split(":", 1)[1])
        tache = db.q1("SELECT * FROM tasks WHERE id = ?", (tid,))
        if not tache:
            return {"ok": False, "error": "rappel introuvable"}
        maintenant = util.now_iso()
        if motif == "non":
            db.ex("UPDATE tasks SET status='cancelled', updated_at=? WHERE id=?",
                  (maintenant, tid))
        elif motif == "plus_tard":
            plus_tard = (dt.date.today() + dt.timedelta(days=14)).isoformat()
            db.ex("UPDATE tasks SET due_at=?, updated_at=? WHERE id=?",
                  (plus_tard, maintenant, tid))
        else:
            db.ex("UPDATE tasks SET status='done', done_at=?, updated_at=? WHERE id=?",
                  (maintenant, maintenant, tid))
            if tache["club_id"]:
                db.update("clubs", tache["club_id"],
                          {"last_contact_at": maintenant, "updated_at": maintenant})
        return {"ok": True, "cle": cle, "motif": motif}
    # Les reponses passent par le meme masquage que l'ecran du jour, pour que
    # « fait » ici vaille « fait » la-bas. La cle est deja celle du jour.
    return journee.ecarter(cle, motif if motif in ("fait", "plus_tard") else "fait")
