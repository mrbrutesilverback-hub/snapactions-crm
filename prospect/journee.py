# -*- coding: utf-8 -*-
"""Le point du jour : une liste d'actions, classee, et surtout courte.

Pourquoi ce module existe. La plateforme savait tout enregistrer et rien
conclure : 370 brouillons en attente de relecture, 174 relances programmees
dont 91 en retard, 128 tournois en veille. Aucune de ces piles ne dit par quoi
commencer, donc on n'en fait rien et elles grossissent.

Le principe retenu est celui des CRM d'action (OnePageCRM et sa « next
action ») : un dossier vaut par sa prochaine action, pas par sa fiche. On
produit donc une liste unique, ordonnee par ce qui expire le plus vite, et
plafonnee. Ce qui ne rentre pas dans le plafond attend demain — c'est voulu :
une liste de trente lignes ne se fait pas, une liste de dix se fait.

L'ordre des familles n'est pas negociable, il suit le cout de l'inaction :
quelqu'un qui attend une reponse passe avant un tournoi a trancher, qui passe
avant une relance de fond.
"""

import datetime

from . import db, util, wa

# Au-dela, la liste cesse d'etre une liste de courses pour redevenir une pile.
PLAFOND_DEFAUT = 12

# Un partenaire qu'on n'a pas touche depuis ce delai est en train de se refroidir.
PARTENAIRE_DORMANT_JOURS = 120

# Combien de lignes chaque famille a le droit d'occuper. Repondre a quelqu'un
# qui attend n'a pas de plafond utile — s'il y en a cinq, il en faut cinq.
# Les familles de fond, elles, sont volontairement bridees.
# Repondre a quelqu'un qui attend ne se plafonne pas : chaque ligne est un
# client qui a leve la main et qu'on laisse refroidir. S'il y en a neuf, il en
# faut neuf, et ils passent devant tout le reste.
SANS_PLAFOND = {1}

PAR_FAMILLE = {
    1: 99,  # repondre — voir SANS_PLAFOND
    2: 4,   # caler avant le jour J
    3: 4,   # mettre au calendrier
    4: 3,   # appeler
    5: 3,   # trancher un tournoi
    6: 3,   # demander les dates de la saison
    7: 2,   # relancer
    8: 2,   # trouver un numero
    9: 2,   # partenaire dormant
}


def _plafond():
    try:
        return int(db.get_setting("journee_max", PLAFOND_DEFAUT))
    except (TypeError, ValueError):
        return PLAFOND_DEFAUT


def _jours_avant(iso):
    d = util.parse_date(iso)
    return util.days_between(util.today(), d) if d else None


def _quand(jours):
    """Formule l'echeance en francais, sans jargon de J moins."""
    if jours is None:
        return ""
    if jours < 0:
        return "c'était il y a %d jour%s" % (-jours, "s" if jours < -1 else "")
    if jours == 0:
        return "aujourd'hui"
    if jours == 1:
        return "demain"
    if jours <= 7:
        return "dans %d jours" % jours
    return "dans %d jours" % jours


def _depuis(jours):
    if jours is None:
        return ""
    if jours <= 0:
        return "aujourd'hui"
    if jours == 1:
        return "hier"
    return "il y a %d jours" % jours


def _action(rang_famille, quoi, qui, pourquoi, **extra):
    item = {"famille": rang_famille, "quoi": quoi, "qui": qui, "pourquoi": pourquoi}
    item.update(extra)
    item["cle"] = _cle(item)
    return item


# --------------------------------------------------------------------------
# Rayer une ligne de la liste du jour
# --------------------------------------------------------------------------
# La page annoncait « elles remonteront quand celles-ci seront faites » sans
# jamais offrir le moyen de dire qu'une ligne etait faite : les douze memes
# cartes revenaient indefiniment, et les cent autres n'arrivaient jamais.
#
# « Fait » masque jusqu'a demain, pas pour toujours. Si la reponse est
# reellement partie, le fil change d'etape a la prochaine synchro WhatsApp et
# l'action ne revient pas d'elle-meme. Si rien n'a bouge, elle revient — c'est
# voulu : la liste ne doit pas pouvoir mentir.

ECARTS = {"fait": 1, "plus_tard": 7}

# De quoi chaque famille parle vraiment : deux actions sur le meme fil portent
# la meme cle, donc « fait » sur l'une ne masque pas l'autre par accident.
_PORTE = {1: "chat_id", 2: "chat_id", 3: "chat_id", 4: "chat_id", 7: "chat_id",
          5: "tournoi_id", 6: "club_id", 8: "task_id", 9: "club_id"}


def _cle(item):
    champ = _PORTE.get(item.get("famille"))
    ident = item.get(champ) if champ else None
    if ident is None:
        # Sans identifiant stable, on retombe sur le nom : moins sur, mais
        # toujours mieux que pas de bouton du tout.
        ident = "n:" + (item.get("qui") or "?")
    return "f%s:%s" % (item.get("famille"), ident)


def _ecartees():
    """Les cles encore masquees aujourd'hui."""
    try:
        rows = db.q("SELECT cle FROM journee_ecarts WHERE date(jusquau) > date('now')")
    except Exception:
        return set()
    return {r["cle"] for r in rows}


def ecarter(cle, motif="fait"):
    """Masque une ligne. Renvoie la date de retour."""
    cle = (cle or "").strip()
    if not cle:
        raise ValueError("aucune ligne designee")
    jours = ECARTS.get(motif, 1)
    jusquau = (util.today() + datetime.timedelta(days=jours)).isoformat()
    db.ex("INSERT INTO journee_ecarts (cle, jusquau, motif, pose_le) "
          "VALUES (?, ?, ?, ?) "
          "ON CONFLICT(cle) DO UPDATE SET jusquau=excluded.jusquau, "
          "  motif=excluded.motif, pose_le=excluded.pose_le",
          (cle, jusquau, motif, util.now_iso()))
    return {"cle": cle, "motif": motif, "jusquau": jusquau, "jours": jours}


def reprendre(cle):
    """Annule un ecart — la ligne redescend tout de suite dans la liste."""
    db.ex("DELETE FROM journee_ecarts WHERE cle = ?", ((cle or "").strip(),))
    return {"cle": cle}


def tout_reprendre():
    """Remet toutes les lignes rayees — le rattrapage d'un clic de travers."""
    n = len(db.q("SELECT cle FROM journee_ecarts WHERE date(jusquau) > date('now')"))
    db.ex("DELETE FROM journee_ecarts")
    return {"remises": n}


# --------------------------------------------------------------------------
# Les familles, de la plus chere a ignorer a la moins chere.
# --------------------------------------------------------------------------

def _proposition(carte, etape):
    """Le mot qu'on collerait, selon l'etape. Jamais envoye : propose."""
    if etape in ("conclu", "a_caler") and carte.get("mot"):
        return carte.get("mot_nom") or "Message prêt", carte["mot"]
    if etape == "a_traiter":
        return wa.mot_de_reponse(carte)
    if etape in ("a_relancer", "a_appeler"):
        brouillon = carte.get("brouillon") or {}
        if brouillon.get("body"):
            return "Message de relance", brouillon["body"]
    return None, None


def _depuis_le_suivi(cartes):
    """Les discussions WhatsApp deja classees par etape par wa.suivi()."""
    sortie = []
    for c in cartes:
        etape = c["etape"]
        ev = c.get("evenement") or {}
        avant = c.get("jours_avant")
        # Sans le fil, la ligne dit qui relancer mais pas de quoi on a parle
        # — et on reste bloque devant. On embarque donc les deux derniers
        # messages, et le mot qu'on proposerait.
        titre_mot, mot = _proposition(c, etape)
        # Les trois choses que Julien regarde en premier — la date, la route
        # et le nombre d'equipes — etaient noyees dans la phrase d'explication
        # ou absentes. Elles voyagent maintenant a part, pour etre affichees
        # comme des reperes et non comme du texte.
        base = dict(club_id=c.get("club_id"), chat_id=c.get("id"),
                    tel=c.get("phone"), onglet="suivi",
                    sport=c.get("sport"), ville=ev.get("city") or c.get("ville"),
                    date=ev.get("start_date"), jours_avant=avant,
                    nb_equipes=ev.get("nb_equipes"),
                    trajet_min=ev.get("trajet_min"), distance_km=ev.get("distance_km"),
                    tournoi=ev.get("name"), tournoi_id=ev.get("id"),
                    echange=[{"de_moi": bool(m.get("de_moi")),
                              "texte": (m.get("texte") or "").strip(),
                              "heure": m.get("quand") or ""}
                             for m in (c.get("derniers") or []) if m.get("texte")],
                    # Une ligne qui dit ou en est le dossier, ecrite par le
                    # modele local. Le fil complet reste dessous : le resume
                    # fait gagner du temps, il ne remplace pas la lecture.
                    resume=c.get("resume"),
                    proposition=mot, proposition_titre=titre_mot)

        if etape == "a_traiter":
            sortie.append(_action(
                1, "Répondre", c["club"],
                "Ils ont écrit %s et attendent votre réponse." % _depuis(c.get("jours")),
                urgence="chaud", **base))

        elif etape == "a_caler":
            sortie.append(_action(
                2, "Caler les détails", c["club"],
                "%s %s : heure d'arrivée, qui vous ouvre, combien d'équipes."
                % (ev.get("name") or "L'événement", _quand(avant)),
                urgence="chaud", **base))

        elif etape == "conclu" and ev.get("id"):
            sortie.append(_action(
                3, "Mettre au calendrier", c["club"],
                "Date bloquée sur %s, %s — à inscrire et à pourvoir."
                % (ev.get("name") or "l'événement", _quand(avant)),
                urgence="chaud", **base))

        elif etape == "a_appeler":
            sortie.append(_action(
                4, "Appeler", c["club"],
                "Écrit resté sans réponse depuis %d jours : l'écrit ne passera plus."
                % (c.get("jours") or 0),
                urgence="tiede", **base))

        elif etape == "a_relancer" and c.get("brouillon"):
            sortie.append(_action(
                7, "Relancer", c["club"],
                "Silence depuis %d jours, le message de relance est prêt."
                % (c.get("jours") or 0),
                urgence="fond", draft_id=(c.get("brouillon") or {}).get("id"), **base))
    return sortie


def _tournois_a_trancher():
    """Ce qui est atteignable, encore a temps, et sur quoi on n'a rien decide."""
    rows = db.rows_to_dicts(db.q(
        "SELECT id, name, sport, start_date, city, dept, nb_equipes, trajet_min, "
        "       distance_km, distance_verdict "
        "FROM tournaments "
        "WHERE date(start_date) >= date('now') "
        "  AND COALESCE(validation,'a_valider') = 'a_valider' "
        "  AND (distance_verdict IS NULL OR distance_verdict IN ('proche','correct')) "
        "ORDER BY start_date LIMIT 40"))
    sortie = []
    for r in rows:
        avant = _jours_avant(r["start_date"])
        if avant is None or avant > 75:
            continue
        # La date, la route et les equipes sont devenues des reperes affiches
        # a part : la phrase ne redit donc plus ce qu'on lit deja au-dessus.
        sortie.append(_action(
            5, "Trancher", r["name"],
            "Personne n'a encore dit oui ou non sur ce tournoi.",
            urgence="tiede" if avant <= 21 else "fond",
            onglet="tournois", tournoi=r["name"], tournoi_id=r["id"],
            sport=r.get("sport"), ville=r.get("city"), date=r.get("start_date"),
            jours_avant=avant, nb_equipes=r.get("nb_equipes"),
            trajet_min=r.get("trajet_min"), distance_km=r.get("distance_km"),
            echeance=avant))
    return sortie


def _calendriers_a_demander():
    """Les clubs avec qui on parle, et dont on ignore encore les dates.

    C'est la source de dates la plus rentable qu'on ait : 45 clubs rencontres
    en forum, deja en discussion, a qui personne n'a jamais pose la question.
    Aucun moissonnage de federation n'approche ce chiffre — et pour cause, les
    federations ferment leurs API quand elles en ont une.
    """
    rows = db.rows_to_dicts(db.q(
        "SELECT c.id, c.name, c.city, c.sport, c.source, "
        "  (SELECT MAX(w.dernier_le) FROM wa_chats w "
        "   WHERE w.club_id = c.id AND w.ecarte = 0) AS vu_le, "
        "  (SELECT w.phone FROM wa_chats w WHERE w.club_id = c.id "
        "   AND w.ecarte = 0 LIMIT 1) AS tel "
        "FROM clubs c "
        "WHERE c.do_not_contact = 0 "
        "  AND EXISTS (SELECT 1 FROM wa_chats w WHERE w.club_id = c.id AND w.ecarte = 0) "
        "  AND NOT EXISTS (SELECT 1 FROM tournaments t WHERE t.club_id = c.id "
        "                  AND date(t.start_date) >= date('now')) "
        "  AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.club_id = c.id "
        "                  AND d.template_key LIKE 'calendrier_%' "
        "                  AND d.status != 'rejected') "
        "ORDER BY vu_le DESC LIMIT 30"))
    return [_action(
        6, "Demander les dates", r["name"],
        "Vous échangez avec eux, mais aucune date connue pour la saison.",
        urgence="fond", onglet="clubs", club_id=r["id"], tel=r.get("tel"),
        sport=r.get("sport"), ville=r.get("city"), modele="calendrier")
        for r in rows]


def _contacts_manquants():
    rows = db.rows_to_dicts(db.q(
        "SELECT t.id, t.title, t.notes, c.name AS club, c.id AS club_id, "
        "       c.website, c.sport, c.city "
        "FROM tasks t JOIN clubs c ON c.id = t.club_id "
        "WHERE t.status = 'pending' AND t.kind = 'contact_manquant' "
        "ORDER BY t.due_at LIMIT 20"))
    return [_action(
        8, "Trouver un numéro", r["club"],
        r.get("notes") or "Aucun contact en base : le message ne peut partir de nulle part.",
        urgence="fond", onglet="clubs", club_id=r["club_id"], task_id=r["id"],
        sport=r.get("sport"), ville=r.get("city"))
        for r in rows]


def _relances_en_retard():
    """Les rappels echus : la table des taches, que personne ne lisait.

    Deux cent onze rappels etaient en retard au 16 octobre 2026, dont
    cinquante-cinq de plus d'un mois, pour soixante-treize clubs rencontres
    en personne aux forums du 5 septembre. Le moteur les programmait
    fidelement ; aucun ecran ne les affichait.
    """
    rows = db.rows_to_dicts(db.q(
        "SELECT MIN(t.id) AS task_id, date(MIN(t.due_at)) AS du, c.id AS club_id, "
        "       c.name, c.phone, c.email, c.sport, c.city, c.source "
        "FROM tasks t JOIN clubs c ON c.id = t.club_id "
        "WHERE t.kind = 'relance' AND t.status = 'pending' "
        "  AND date(t.due_at) < date('now') "
        "  AND COALESCE(c.do_not_contact, 0) = 0 "
        "  AND (COALESCE(c.phone,'') <> '' OR COALESCE(c.email,'') <> '' "
        "       OR EXISTS (SELECT 1 FROM contacts ct WHERE ct.club_id = c.id "
        "                  AND COALESCE(ct.do_not_contact,0) = 0 "
        "                  AND (COALESCE(ct.phone,'') <> '' OR COALESCE(ct.email,'') <> ''))) "
        "GROUP BY c.id ORDER BY MIN(t.due_at) LIMIT 40"))
    sortie = []
    for r in rows:
        d = util.parse_date(r["du"])
        retard = util.days_between(d, util.today()) if d else None
        sortie.append(_action(
            7, "Relancer", r["name"],
            "Rappel échu depuis %d jours, jamais repris." % (retard or 0),
            urgence="chaud" if (retard or 0) >= 30 else "tiede",
            onglet="clubs", club_id=r["club_id"], task_id=r["task_id"],
            tel=r.get("phone"), sport=r.get("sport"), ville=r.get("city"),
            echeance=-(retard or 0), ecran="/repondre.html"))
    return sortie


def _partenaires_dormants():
    rows = db.rows_to_dicts(db.q(
        "SELECT id, name, city, sport, phone, last_contact_at, partenaire_note "
        "FROM clubs WHERE partenaire = 1 AND do_not_contact = 0 "
        "ORDER BY COALESCE(last_contact_at, '0000') LIMIT 20"))
    sortie = []
    for r in rows:
        if r.get("last_contact_at"):
            d = util.parse_date(r["last_contact_at"][:10])
            depuis = util.days_between(d, util.today()) if d else None
            if depuis is not None and depuis < PARTENAIRE_DORMANT_JOURS:
                continue
            motif = "Client déjà servi, plus de nouvelles depuis %d jours." % depuis
        else:
            motif = "Client déjà servi, jamais relancé depuis la mise en fiche."
        sortie.append(_action(
            9, "Reprendre contact", r["name"], motif,
            urgence="fond", onglet="partenaires", club_id=r["id"],
            tel=r.get("phone"), sport=r.get("sport"), ville=r.get("city"),
            partenaire=True))
    return sortie


# --------------------------------------------------------------------------

def liste(plafond=None):
    """La journee : une liste ordonnee, plafonnee, plus le reste en compteur."""
    plafond = int(plafond) if plafond else _plafond()

    cartes = []
    for col in wa.suivi()["colonnes"]:
        cartes.extend(col["cartes"])

    tout = (_depuis_le_suivi(cartes)
            + _tournois_a_trancher()
            + _calendriers_a_demander()
            + _relances_en_retard()
            + _contacts_manquants()
            + _partenaires_dormants())

    # Ce qui a ete raye aujourd'hui sort de la liste, et laisse donc la place
    # a une des lignes qui attendaient derriere le plafond.
    masquees = _ecartees()
    en_attente = len(masquees)
    if masquees:
        tout = [a for a in tout if a.get("cle") not in masquees]

    # Famille d'abord — c'est le cout de l'inaction. A famille egale, ce qui
    # expire le plus tot. Puis l'ordre alphabetique, pour que la liste ne
    # danse pas d'un rafraichissement a l'autre.
    tout.sort(key=lambda a: (a["famille"],
                             a.get("echeance") if a.get("echeance") is not None else 999,
                             a.get("qui") or ""))

    # Un plafond global ne suffit pas : dix relances a passer occupent toute la
    # liste et enterrent le tournoi qu'il fallait trancher. On limite donc
    # chaque famille, pour que la journee garde un peu de chaque chose.
    par_famille, retenus = {}, []
    for a in tout:
        f = a["famille"]
        if f not in SANS_PLAFOND and par_famille.get(f, 0) >= PAR_FAMILLE.get(f, 3):
            continue
        par_famille[f] = par_famille.get(f, 0) + 1
        retenus.append(a)
        # Le plafond global ne coupe pas non plus dans les reponses : une pile
        # de douze lignes qui enterre le onzieme client ayant ecrit, c'est le
        # plafond qui decide a la place de Julien.
        if len(retenus) >= plafond and f not in SANS_PLAFOND:
            break

    for i, a in enumerate(retenus, 1):
        a["rang"] = i

    reste = len(tout) - len(retenus)
    return {
        "actions": retenus,
        "total": len(tout),
        "reste": reste,
        "plafond": plafond,
        "rayees": en_attente,
        "chaud": sum(1 for a in retenus if a.get("urgence") == "chaud"),
    }
