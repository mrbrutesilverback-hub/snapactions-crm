# -*- coding: utf-8 -*-
"""Poste de commande : composer un appel aux photographes pour un evenement.

Aucun envoi automatique n'est possible ici : WhatsApp n'expose pas d'API pour
poster dans un groupe. On prepare le message, vous le collez dans le bon groupe.
"""
import datetime as dt
import re

from . import db, drafts, util

JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]


def date_longue(valeur, avec_jour=True):
    """'2026-10-10' -> 'samedi 10 octobre'."""
    d = util.parse_date(valeur)
    if not d:
        return ""
    bout = "%d %s" % (d.day, MOIS_FR[d.month - 1])
    return "%s %s" % (JOURS_FR[d.weekday()], bout) if avec_jour else bout


def periode(debut, fin):
    d1, d2 = util.parse_date(debut), util.parse_date(fin)
    if not d1:
        return ""
    if not d2 or d2 == d1:
        return date_longue(debut)
    if d1.month == d2.month:
        return "du %d au %d %s" % (d1.day, d2.day, MOIS_FR[d1.month - 1])
    return "du %s au %s" % (date_longue(debut, False), date_longue(fin, False))


def list_crews():
    rows = db.rows_to_dicts(db.q("SELECT * FROM crews ORDER BY position, name"))
    for r in rows:
        r["sports_list"] = [s.strip() for s in (r.get("sports") or "").split(",") if s.strip()]
    return rows


def groupe_general():
    """Le groupe fourre-tout : le premier sans discipline attribuee.

    Il est reconnu a ce qu'il ne declare aucun sport, pas a son nom — le jour
    ou le groupe « General » est renomme, rien ne casse.
    """
    for crew in list_crews():
        if not crew["sports_list"]:
            return crew
    return None


def suggest_crew(sport):
    """Le groupe WhatsApp ou poster la mission.

    Les photographes se contactent par les groupes, pas un par un. Il y a donc
    toujours un groupe a proposer : faute de discipline reconnue — un drift, un
    tournoi hors region — c'est le groupe general. Afficher « groupe a choisir »
    laissait croire qu'il manquait quelque chose alors que la reponse existait.
    """
    cle = util.norm_key(sport) if sport else ""
    if cle:
        for crew in list_crews():
            for s in crew["sports_list"]:
                sk = util.norm_key(s)
                if sk and (sk in cle or cle in sk):
                    return crew
    return groupe_general()


def list_team(sport=None, actifs_seulement=True):
    rows = db.rows_to_dicts(db.q(
        "SELECT * FROM team WHERE (? = 0 OR active = 1) ORDER BY name",
        (1 if actifs_seulement else 0,)))
    out = []
    for r in rows:
        r["sports_list"] = [s.strip() for s in (r.get("sports") or "").split(",") if s.strip()]
        r["phone_fmt"] = util.format_phone(r.get("phone"))
        if sport and r["sports_list"]:
            cle = util.norm_key(sport)
            if not any(util.norm_key(s) and (util.norm_key(s) in cle or cle in util.norm_key(s))
                       for s in r["sports_list"]):
                r["hors_discipline"] = True
        out.append(r)
    return out


MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]


def champs_evenement(tournoi, crew=None):
    """Les champs a recopier dans le formulaire « Event » de WhatsApp.

    WhatsApp n'a aucune API pour poser un evenement dans un groupe : le
    formulaire se remplit a la main. On prepare donc exactement ce qu'il
    demande — un nom, une description, un jour, une heure, un lieu — pour
    qu'il n'y ait plus qu'a coller.
    """
    t = tournoi or {}
    nom_court = (t.get("name") or "Shooting").strip()
    # Le nom de l'evenement est lu dans une liste, sur telephone : court.
    if len(nom_court) > 60:
        nom_court = nom_court[:57].rstrip(" ,-–") + "…"

    d = util.parse_date(t.get("start_date"))
    jour = "%d %s %d" % (d.day, MOIS_FR[d.month - 1], d.year) if d else ""
    lieu = util.lieu_lisible(t.get("venue"), t.get("city"))

    details = []
    if t.get("sport"):
        details.append(t["sport"])
    if t.get("nb_equipes"):
        details.append("%s équipes" % t["nb_equipes"])
    if t.get("categories"):
        details.append(str(t["categories"]))
    if t.get("distance_km") is not None:
        details.append("%s km de Cannes" % t["distance_km"])
    description = " · ".join(details)

    return {
        "nom": nom_court,
        "description": description or None,
        "jour": jour,
        "date_iso": t.get("start_date"),
        "lieu": lieu or None,
        "groupe": (crew or {}).get("name"),
        "crew_id": (crew or {}).get("id"),
        "lien_groupe": (crew or {}).get("whatsapp_url"),
    }


def compose(tournament_id=None, crew_id=None, extra=None):
    """Prepare le texte de la mission a partir d'un evenement de l'agenda."""
    tournoi = db.row_to_dict(
        db.q1("SELECT * FROM tournaments WHERE id = ?", (int(tournament_id),))) \
        if tournament_id else None
    crew = db.row_to_dict(db.q1("SELECT * FROM crews WHERE id = ?", (int(crew_id),))) \
        if crew_id else (suggest_crew((tournoi or {}).get("sport")) if tournoi else None)

    lieu = ""
    if tournoi:
        lieu = util.lieu_lisible(tournoi.get("venue"), tournoi.get("city"))
        if not lieu:
            lieu = tournoi.get("postal_code") or ""
    details = []
    if tournoi:
        if tournoi.get("organizer"):
            details.append("Organisé par %s." % tournoi["organizer"])
        if tournoi.get("url"):
            details.append(tournoi["url"])
    ctx = {
        "titre": (tournoi or {}).get("name") or "Mission photo",
        "quand": periode((tournoi or {}).get("start_date"),
                         (tournoi or {}).get("end_date")) or "date à préciser",
        "lieu": lieu or "lieu à préciser",
        "details": "\n".join(details),
        "sport": (tournoi or {}).get("sport") or "",
        "groupe": (crew or {}).get("name") or "",
        "my_name": db.get_setting("my_name") or "",
        "my_org": db.get_setting("my_org") or "SnapActions",
    }
    ctx.update(extra or {})

    tpl = drafts.get_template("mission_photo")
    corps = tpl["body"].format_map(drafts._SafeDict(ctx)) if tpl else ""
    corps = re.sub(r"\n{3,}", "\n\n", corps).strip()
    return {
        "tournament_id": tournament_id,
        "tournament": tournoi,
        "crew": crew,
        "crews": list_crews(),
        "title": ctx["titre"],
        "starts_at": (tournoi or {}).get("start_date"),
        "place": lieu,
        "body": corps,
        "team": list_team((tournoi or {}).get("sport")),
    }


def save(data):
    now = util.now_iso()
    mission_id = data.get("id")
    champs = {
        "tournament_id": data.get("tournament_id"),
        "crew_id": data.get("crew_id"),
        "title": data.get("title") or "Mission photo",
        "starts_at": data.get("starts_at"),
        "place": data.get("place"),
        "body": data.get("body") or "",
        "assigned": data.get("assigned"),
        "updated_at": now,
    }
    if mission_id:
        db.update("missions", int(mission_id), champs)
        return get(int(mission_id))
    champs["status"] = "draft"
    champs["created_at"] = now
    return get(db.insert("missions", champs))


def get(mission_id):
    row = db.q1(
        "SELECT m.*, c.name AS crew_name, t.name AS tournament_name, t.sport "
        "FROM missions m LEFT JOIN crews c ON c.id = m.crew_id "
        "LEFT JOIN tournaments t ON t.id = m.tournament_id WHERE m.id = ?", (mission_id,))
    if not row:
        return None
    m = dict(row)
    m["team"] = list_team(m.get("sport"))
    return m


def liste(status=None, limit=200):
    where = "1=1" if not status or status == "all" else "m.status = ?"
    params = () if where == "1=1" else (status,)
    rows = db.rows_to_dicts(db.q(
        "SELECT m.*, c.name AS crew_name, t.name AS tournament_name, t.sport "
        "FROM missions m LEFT JOIN crews c ON c.id = m.crew_id "
        "LEFT JOIN tournaments t ON t.id = m.tournament_id "
        "WHERE %s ORDER BY m.starts_at IS NULL, m.starts_at, m.id DESC LIMIT ?" % where,
        params + (limit,)))
    for r in rows:
        r["quand"] = date_longue(r.get("starts_at"))
    return rows


def marquer_postee(mission_id):
    """A appeler apres avoir colle le message dans le groupe."""
    now = util.now_iso()
    db.update("missions", int(mission_id),
              {"status": "posted", "posted_at": now, "updated_at": now})
    m = get(int(mission_id))
    db.log("mission", "Mission postée : %s (%s)"
           % (m["title"], m.get("crew_name") or "groupe non précisé"))
    return m


def evenements_sans_mission(horizon_jours=60):
    """Les dates tenues pour lesquelles personne n'a encore ete appele.

    On ne cherche pas ici les tournois qu'on aimerait faire — c'est le travail
    de la page Tournois. On cherche ceux qu'on FAIT : une date au calendrier
    SnapActions, une fiche validee a la main, ou un club dont la discussion
    WhatsApp s'est conclue. Sans ce tri, la page proposait de mobiliser une
    equipe pour un tournoi U7 a Villeurbanne que personne n'ira couvrir.
    """
    limite = (util.today() + dt.timedelta(days=int(horizon_jours))).isoformat()
    rows = db.rows_to_dicts(db.q(
        "SELECT t.* FROM tournaments t WHERE t.start_date IS NOT NULL "
        "AND date(t.start_date) BETWEEN date('now') AND ? "
        "AND NOT EXISTS (SELECT 1 FROM missions m WHERE m.tournament_id = t.id "
        "                AND m.status != 'cancelled') "
        "AND ("
        "     t.source = 'SnapActions'"
        "  OR t.validation IN ('oui','valide')"
        "  OR EXISTS (SELECT 1 FROM wa_chats w WHERE w.club_id = t.club_id "
        "             AND w.etape_main IN ('conclu','a_caler'))"
        ") "
        "ORDER BY t.start_date", (limite,)))
    for r in rows:
        crew = suggest_crew(r.get("sport"))
        r["crew_suggere"] = crew["name"] if crew else None
        r["crew_id"] = crew["id"] if crew else None
        r["quand"] = date_longue(r["start_date"])
        d = util.parse_date(r["start_date"])
        r["jours"] = (d - util.today()).days if d else None
    return rows


# -- Les gros evenements, et celui qui cherche les photographes --------------
#
# Deux besoins differents, et il ne faut pas les melanger :
#
#   - Les dates qu'on TIENT (evenements_sans_mission) : il manque des
#     photographes, on appelle le groupe.
#   - Les gros plateaux qu'on a REPERES et qu'on ne couvre pas encore : on
#     n'a personne sur place, et c'est le sourceur qui va chercher des
#     photographes pour ces evenements-la.
#
# Un plateau se mesure au nombre d'equipes annoncees, pas a la distance : un
# tournoi de 250 equipes vaut le deplacement, meme loin.

GROS_EQUIPES = 32
SOURCEUR_CLE = "sourceur_id"


def sourceur():
    """Qui va chercher les photographes. Une fiche de l'equipe, ou rien."""
    try:
        cle = int(db.get_setting(SOURCEUR_CLE) or 0)
    except (TypeError, ValueError):
        return None
    if not cle:
        return None
    r = db.q1("SELECT * FROM team WHERE id = ?", (cle,))
    if not r:
        return None
    d = db.row_to_dict(r)
    d["phone_fmt"] = util.format_phone(d.get("phone"))
    return d


def definir_sourceur(team_id):
    db.set_setting(SOURCEUR_CLE, str(int(team_id)) if team_id else "")
    return sourceur()


def gros_evenements(mini=GROS_EQUIPES, horizon_jours=400, limite=150):
    """Les plus gros plateaux a venir qu'on n'a confies a personne.

    On ne filtre pas sur la distance : c'est tout l'interet du tiroir « gros
    plateaux » du Reperage. Un tournoi de 250 equipes a six heures de route
    merite qu'on cherche un photographe sur place — c'est precisement le
    travail du sourceur.
    """
    limite_date = (util.today() + dt.timedelta(days=int(horizon_jours))).isoformat()
    rows = db.rows_to_dicts(db.q(
        "SELECT t.* FROM tournaments t "
        "WHERE t.start_date IS NOT NULL "
        "AND date(t.start_date) BETWEEN date('now') AND ? "
        # Soit un gros plateau annonce, soit une date rapportee d'une source
        # qui n'existe QUE pour le sourceur : le Nord, la Belgique. Celles-la
        # n'annoncent jamais leur nombre d'equipes, et les attendre revenait
        # a ne jamais rien lui confier.
        # Un gros plateau, une source faite pour le sourceur, ou simplement
        # une date hors de portee de Julien : dans les trois cas, il faut
        # quelqu'un sur place. C'est tout l'ecran « L'equipe ».
        "AND (COALESCE(t.nb_equipes, 0) >= ? OR t.source LIKE 'Pour le sourceur%' "
        "     OR COALESCE(t.portee, 'pour_moi') = 'reseau') "
        "AND COALESCE(t.validation,'') <> 'ecarte' "
        "AND NOT EXISTS (SELECT 1 FROM missions m WHERE m.tournament_id = t.id "
        "                AND m.status <> 'cancelled') "
        "ORDER BY (COALESCE(t.nb_equipes, 0) >= ?) DESC, "
        "t.nb_equipes DESC, t.start_date LIMIT ?",
        (limite_date, int(mini), int(mini), int(limite))))
    for r in rows:
        r["quand"] = date_longue(r.get("start_date"))
        r["ou"] = util.lieu_lisible(r.get("venue"), r.get("city")) \
            or (r.get("postal_code") or "")
        d = util.parse_date(r.get("start_date"))
        r["jours"] = (d - util.today()).days if d else None
        r["loin"] = bool(r.get("trajet_min") and r["trajet_min"] > 120)
    return rows


def message_sourceur(ids, mot=None):
    """Le texte de la demande : des evenements, et ce qu'on cherche.

    Le sourceur ne connait pas la base : il lui faut la date, le lieu, la
    taille et le lien de l'annonce, sinon il ne peut rien chercher.
    """
    ids = [int(i) for i in (ids or []) if str(i).strip()]
    if not ids:
        return {"corps": "", "evenements": []}
    marques = ",".join("?" for _ in ids)
    rows = db.rows_to_dicts(db.q(
        "SELECT * FROM tournaments WHERE id IN (%s) "
        "ORDER BY nb_equipes DESC, start_date" % marques, tuple(ids)))

    lignes = []
    for t in rows:
        ou = util.lieu_lisible(t.get("venue"), t.get("city")) or "lieu à préciser"
        bout = "• %s — %s, %s" % (t.get("name") or "sans nom",
                                  periode(t.get("start_date"), t.get("end_date")), ou)
        if t.get("nb_equipes"):
            bout += " (%s équipes)" % t["nb_equipes"]
        if t.get("url"):
            bout += "\n  %s" % t["url"]
        lignes.append(bout)

    qui = db.get_setting("my_name") or "Julien"
    org = db.get_setting("my_org") or "SnapActions"
    tete = (mot or "").strip() or (
        "Salut, j'ai repéré %d gros plateau%s où on n'a personne sur place. "
        "Est-ce que tu peux nous trouver un photographe sur chacun ?"
        % (len(rows), "x" if len(rows) > 1 else ""))
    corps = "\n\n".join([tete, "\n\n".join(lignes),
                          "Merci !\n%s — %s" % (qui, org)])
    return {"corps": corps, "evenements": rows}


def confier_au_sourceur(ids, corps=None):
    """Marque les evenements comme confies : ils quittent la liste.

    On passe par une mission, comme pour un appel au groupe : c'est le meme
    objet — quelqu'un a ete sollicite pour cette date.
    """
    now = util.now_iso()
    poses = []
    for tid in [int(i) for i in (ids or []) if str(i).strip()]:
        t = db.q1("SELECT name, start_date, city, venue FROM tournaments WHERE id = ?", (tid,))
        if not t:
            continue
        deja = db.q1("SELECT id FROM missions WHERE tournament_id = ? "
                     "AND status <> 'cancelled'", (tid,))
        if deja:
            continue
        poses.append(db.insert("missions", {
            "tournament_id": tid, "crew_id": None,
            "title": t["name"] or "Gros plateau",
            "starts_at": t["start_date"],
            "place": util.lieu_lisible(t["venue"], t["city"]),
            "body": (corps or "")[:4000],
            "status": "sourceur", "assigned": (sourceur() or {}).get("name"),
            "created_at": now, "updated_at": now, "posted_at": now,
        }))
    if poses:
        db.log("mission", "%d événement(s) confié(s) au sourceur" % len(poses))
    return poses


# -- Retrouver les numeros des photographes ----------------------------------
#
# Ils ne sont PAS dans le groupe WhatsApp : depuis que WhatsApp identifie les
# participants par un « lid » — un numero de confidentialite qui ne ressemble
# a rien — la base locale ne garde plus leur telephone. Sur les 110 membres
# des groupes de l'equipe, un seul porte un vrai numero, et c'est celui de
# Julien. Le groupe donne en revanche les NOMS, et c'est deja beaucoup : on
# sait qui chercher.
#
# On va donc les chercher la ou ils sont vraiment : les conversations
# individuelles, le carnet d'adresses du Mac, les fiches contacts du CRM.
# Aucun numero n'est devine ni reconstitue — chacun vient d'une ligne lue.

def _plat(texte):
    return re.sub(r"[^a-z0-9]+", " ",
                  util.strip_accents(str(texte or "")).lower()).strip()


def _memes_personnes(a, b):
    """Deux ecritures d'un meme nom. « Oceane » et « Océane 🐉 » comptent."""
    # Le carnet ecrit « Jimmy De Paris », WhatsApp « jimmy deparis » : les
    # mots ne se recouvrent pas, alors que c'est la meme personne. On compare
    # donc aussi les deux noms colles, sans espaces.
    colle_a, colle_b = _plat(a).replace(" ", ""), _plat(b).replace(" ", "")
    if colle_a and colle_b and (colle_a == colle_b
                                or (len(colle_a) >= 8 and colle_a in colle_b)
                                or (len(colle_b) >= 8 and colle_b in colle_a)):
        return True
    ma, mb = set(_plat(a).split()), set(_plat(b).split())
    ma = {m for m in ma if len(m) > 2}
    mb = {m for m in mb if len(m) > 2}
    if not ma or not mb:
        return False
    return ma <= mb or mb <= ma


def noms_du_groupe(groupe="SnapActions"):
    """Les noms des participants du groupe, tels que WhatsApp les affiche."""
    from . import wa_local
    vus, sortie = set(), []
    try:
        base = wa_local.trouver_base()
    except Exception:
        return []
    import shutil, sqlite3, tempfile, os
    dossier = tempfile.mkdtemp(prefix="wa-noms-")
    copie = os.path.join(dossier, "n.sqlite")
    try:
        shutil.copy2(base, copie)
        for suffixe in ("-wal", "-shm"):
            if os.path.exists(base + suffixe):
                shutil.copy2(base + suffixe, copie + suffixe)
        conn = sqlite3.connect(copie)
        conn.row_factory = sqlite3.Row
        for r in conn.execute(
                "SELECT m.ZCONTACTNAME nom, m.ZFIRSTNAME prenom, m.ZMEMBERJID jid, "
                "       m.ZISADMIN admin, s.ZPARTNERNAME groupe "
                "FROM ZWAGROUPMEMBER m "
                "JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION "
                "WHERE s.ZCONTACTJID LIKE '%@g.us' AND s.ZPARTNERNAME LIKE ?",
                ("%" + groupe + "%",)):
            nom = (r["nom"] or r["prenom"] or "").strip()
            jid = r["jid"] or ""
            tel = ("+" + jid.split("@")[0]) if jid.endswith("@s.whatsapp.net") else None
            cle = _plat(nom) or jid
            if cle in vus:
                continue
            vus.add(cle)
            sortie.append({"nom": nom or None, "tel": tel,
                           "admin": bool(r["admin"]), "groupe": r["groupe"]})
        conn.close()
    except Exception:
        return sortie
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    return sortie


def _numero_connu(nom):
    """Le numero de cette personne, lu quelque part dans la maison.

    Trois gisements, dans l'ordre de confiance : une conversation WhatsApp
    en tete a tete (c'est SON numero, on s'y est parle), une fiche contact
    du CRM, puis le carnet d'adresses du Mac.
    """
    if not nom or len(_plat(nom)) < 3:
        return None, None

    for w in db.q("SELECT nom, phone FROM wa_chats "
                  "WHERE groupe = 0 AND COALESCE(phone,'') <> ''"):
        if _memes_personnes(nom, w["nom"]):
            return w["phone"], "conversation WhatsApp"

    for c in db.q("SELECT first_name, last_name, phone, phone2 FROM contacts "
                  "WHERE COALESCE(phone,'') <> '' OR COALESCE(phone2,'') <> ''"):
        entier = " ".join(x for x in (c["first_name"], c["last_name"]) if x)
        if _memes_personnes(nom, entier):
            return c["phone"] or c["phone2"], "fiche contact du CRM"

    try:
        from . import carnet
        for g in carnet.lire_bases():
            if not g.get("tels"):
                continue
            if _memes_personnes(nom, g.get("nom")):
                return g["tels"][0], "carnet d'adresses du Mac"
    except Exception:
        pass
    return None, None


def recuperer_numeros(groupe="SnapActions", ecrire=True):
    """Remplit les numeros manquants de l'equipe, sans jamais en inventer.

    Rend un compte rendu : qui a ete complete et d'ou vient le numero, qui
    reste introuvable, et qui du groupe n'est pas encore dans l'equipe.
    """
    gens = noms_du_groupe(groupe)
    equipe = db.rows_to_dicts(db.q("SELECT * FROM team ORDER BY name"))
    trouves, manquants, nouveaux = [], [], []

    for p in equipe:
        if (p.get("phone") or "").strip():
            continue
        # Le nom du groupe est souvent plus complet que celui de la fiche :
        # « Antoine Ferrera » plutot que « Antoine ». On cherche les deux.
        pistes = [p["name"]]
        for g in gens:
            if g["nom"] and _memes_personnes(p["name"], g["nom"]):
                if g["tel"]:
                    pistes.insert(0, g["nom"])
                    trouves.append({"qui": p["name"], "tel": g["tel"],
                                    "ou": "le groupe %s" % groupe, "id": p["id"]})
                    pistes = []
                    break
                pistes.append(g["nom"])
        if not pistes:
            continue
        tel, ou = None, None
        for piste in pistes:
            tel, ou = _numero_connu(piste)
            if tel:
                break
        if tel:
            trouves.append({"qui": p["name"], "tel": tel, "ou": ou, "id": p["id"]})
        else:
            manquants.append({"qui": p["name"],
                              "vu_dans_le_groupe": any(
                                  g["nom"] and _memes_personnes(p["name"], g["nom"])
                                  for g in gens)})

    if ecrire:
        for t in trouves:
            db.update("team", t["id"], {"phone": t["tel"],
                                        "updated_at": util.now_iso()})
        if trouves:
            db.log("mission", "%d numero(s) de photographe retrouve(s)" % len(trouves))

    connus = [p["name"] for p in equipe]
    for g in gens:
        nom = (g["nom"] or "").strip()
        # Un participant « lid » sans nom porte un jeton encode : ce n'est
        # pas un nom, et le proposer comme recrue n'aurait aucun sens.
        if not nom or not re.search(r"[A-Za-zÀ-ÿ]{3}", nom) or nom.startswith("+"):
            continue
        if not any(_memes_personnes(nom, n) for n in connus):
            nouveaux.append(nom)

    return {"groupe": groupe, "membres": len(gens), "complete": trouves,
            "introuvables": manquants, "dans_le_groupe_pas_dans_l_equipe": nouveaux}
