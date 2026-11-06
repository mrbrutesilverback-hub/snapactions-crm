# -*- coding: utf-8 -*-
"""Le journal WhatsApp : ou en est chaque conversation, et avec quel club.

Julien travaille au telephone. Ses vrais echanges avec les clubs vivent dans
WhatsApp Business, pas dans le CRM : d'ou 190 brouillons marques « jamais
envoyes » alors qu'une partie etait partie depuis longtemps. Ce module recoit
l'index des conversations (nom affiche, date du dernier message, qui a parle
en dernier) et le raccroche aux clubs.

On ne lit ni n'ecrit aucun message. Rien ici n'envoie quoi que ce soit.
"""
import datetime as dt
import json
import re

from . import db, repo, util

# Au-dela, une conversation sans reponse merite une relance ecrite.
RELANCE_JOURS = 5

# Et au-dela de celui-la, le message ne passera plus : il faut decrocher.
# Un club qui n'a pas repondu en dix jours ne repondra pas au onzieme.
APPEL_JOURS = 10

# Quand l'evenement approche, on ne peut plus se permettre d'attendre une
# reponse ecrite : trois semaines avant, on appelle.
APPEL_AVANT_EVENEMENT = 21

# Ce qui n'est manifestement pas un club : on le range a part sans le perdre.
PERSO = re.compile(r"^(maman|papa|famille|moi|toi)$", re.I)

# Mots qui ne distinguent aucun club : les compter ferait matcher n'importe
# quoi avec n'importe quoi.
VIDES = set("""club sport sports association asso de du des la le les l d et
en sur au aux saint st sainte ste mer var azur cote ecole centre stade union
us as ac sc
""".split())

# « Stade Laurentin Basket » et « Basket St Laurent » sont le meme club : les
# gentiles des communes du coin ramenent au nom de la commune.
GENTILES = {
    "laurentin": "laurent", "laurentine": "laurent",
    "cagnois": "cagnes", "cagnoise": "cagnes",
    "cannois": "cannes", "cannoise": "cannes",
    "antibois": "antibes", "antiboise": "antibes",
    "grassois": "grasse", "grassoise": "grasse",
    "nicois": "nice", "nicoise": "nice",
    "mentonnais": "menton", "mentonnaise": "menton",
    "vencois": "vence", "vencoise": "vence",
    "mouginois": "mougins", "biotois": "biot",
}

# Un mot de fonction annonce que ce qui precede designe la personne.
ROLES = set("""president presidente presidence secretaire tresorier tresoriere
coach entraineur entraineuse responsable directeur directrice financiere
contact""".split())


def _mots(texte):
    plat = util.strip_accents(texte or "").lower()
    bruts = [m for m in re.split(r"[^a-z0-9]+", plat) if len(m) > 2]
    return {GENTILES.get(m, m) for m in bruts if GENTILES.get(m, m) not in VIDES}


def nom_club_probable(titre):
    """Le nom du club derriere le nom d'une conversation.

    « Maguy Presidente Basket St Laurent » est une personne : le club, c'est
    « Basket St Laurent ». On ne coupe que devant un mot de fonction — deviner
    qu'un premier mot est un prenom rate trop souvent : Kelo Escalade est bien
    un club, ASPTT aussi.
    """
    mots = [m for m in re.split(r"\s+", (titre or "").strip()) if m]
    dernier_role = -1
    for i, m in enumerate(mots):
        if util.strip_accents(m).lower().strip(".,-") in ROLES:
            dernier_role = i
    if dernier_role >= 0 and len(mots) > dernier_role + 1:
        mots = mots[dernier_role + 1:]
    return " ".join(mots).strip() or (titre or "").strip()


# Un nom de sport en tete n'est pas un prenom : « Foot Cogolin » ne designe
# personne, c'est le club.
SPORTS = set("""foot football futsal basket basketball volley volleyball hand
handball rugby tennis padel judo karate taekwondo boxe lutte danse natation
escalade voile nautique gym gymnastique athle athletisme velo cyclisme bmx ski
petanque tir hockey kravmaga surf aviron canoe kayak equitation golf plongee
tennisdetable pingpong badminton triathlon course trail rando randonnee
""".split())


ROLES_JOLIS = {
    "president": "President", "presidente": "Presidente",
    "presidence": "Presidence", "secretaire": "Secretaire",
    "tresorier": "Tresorier", "tresoriere": "Tresoriere",
    "coach": "Coach", "entraineur": "Entraineur", "entraineuse": "Entraineuse",
    "responsable": "Responsable", "directeur": "Directeur",
    "directrice": "Directrice", "financiere": "Responsable financiere",
    "contact": "Contact",
}


def personne_du_titre(titre, club_nom=None):
    """Separe la personne du club dans le nom d'une conversation.

    « Maguy Presidente Basket St Laurent » donne Maguy, presidente. Quand le
    club est connu, ce qui reste apres le prenom doit ressembler a son nom :
    c'est ce qui permet de dire que « Manu » dans « Manu Judo Cagnes » est
    quelqu'un, et que « Foot Cogolin » n'est personne.
    """
    mots = [m for m in re.split(r"\s+", (titre or "").strip()) if m]
    if not mots:
        return None, None

    role = None
    coupe = None
    for i, m in enumerate(mots):
        plat = util.strip_accents(m).lower().strip(".,-")
        if plat in ROLES:
            role = ROLES_JOLIS.get(plat, m.title())
            coupe = i
            break
    if coupe is not None:
        prenom = " ".join(mots[:coupe]).strip(" .,-")
        return (prenom or None), role

    if club_nom:
        propres = _mots(club_nom)
        # Le premier mot n'appartient pas au nom du club, la suite si : c'est
        # une personne devant le nom de son club.
        tete = _mots(mots[0])
        reste = _mots(" ".join(mots[1:]))
        if len(mots) > 1 and tete and not (tete & propres) and (reste & propres) \
                and not (tete & SPORTS):
            return mots[0].strip(" .,-"), None
        if not (tete - propres):
            return None, None          # le titre est le club lui-meme
    return None, None


_COMMUNES = None


def _communes():
    """Les mots qui ne sont qu'un nom de commune, pour ne pas s'y fier seuls."""
    global _COMMUNES
    if _COMMUNES is None:
        from . import geo
        mots = set()
        for ligne in geo._table():
            for m in re.split(r"[^a-z0-9]+", (ligne.get("cle") or "")):
                if len(m) > 2 and m not in VIDES:
                    mots.add(GENTILES.get(m, m))
        _COMMUNES = mots
    return _COMMUNES


def suggestions(nom, limite=2):
    """Clubs qui ressemblent au nom d'une conversation.

    On propose, on ne decide pas : un rattachement faux coute plus cher qu'un
    rattachement manquant — c'est un message envoye au mauvais club.
    """
    mots = _mots(nom_club_probable(nom))
    if not mots:
        return []
    trouves = []
    for club in db.q("SELECT id, name, city FROM clubs"):
        propres = _mots(club["name"])
        communs = mots & propres
        if not communs:
            continue
        # Une commune commune ne prouve rien : « Yacine Driver Cannes » et
        # « AS Cannes » partagent Cannes et n'ont rien a voir. On exige donc au
        # moins un mot qui distingue, en plus du nom de ville.
        #
        # Une exception, et une seule : le nom du club ecrit tel quel dans le
        # texte. « Challenge du Coq U10 du SC Cogolin » ne trouvait pas SC
        # Cogolin, parce qu'apres retrait de « sc » (trop court) il ne restait
        # que « cogolin », un nom de commune. Une correspondance litterale ne
        # laisse pourtant aucun doute.
        if not (communs - _communes()) and not _nom_entier(club["name"], nom):
            continue
        score = len(communs) / float(len(propres) or 1)
        if len(communs) >= 2 or score >= 0.6:
            trouves.append({"club_id": club["id"], "nom": club["name"],
                            "ville": club["city"], "score": round(score, 2),
                            "communs": len(communs)})
    trouves.sort(key=lambda x: (-x["communs"], -x["score"]))
    return trouves[:limite]


def _nom_entier(nom_club, texte):
    """Le nom du club apparait-il litteralement dans le texte ?"""
    reduit = lambda x: " ".join(
        m for m in re.split(r"[^a-z0-9]+", util.strip_accents(str(x or "")).lower()) if m)
    a, b = reduit(nom_club), reduit(texte)
    return bool(a) and len(a) > 4 and a in b


def _cle(nom, phone):
    """Identifiant stable d'une conversation : le numero s'il est connu."""
    if phone:
        return phone
    return "nom:" + util.norm_key(nom or "")


def _phone(nom, phone):
    """Le titre d'une conversation est souvent le numero lui-meme."""
    if phone:
        return util.normalize_phone(phone)
    numeros = util.extract_phones(nom or "")
    return numeros[0] if numeros else None


def _club_probable(nom, phone):
    """Retrouve le club a coup sur : par le numero, puis par le nom exact."""
    if phone:
        contact = repo.find_contact(phone=phone)
        if contact and contact.get("club_id"):
            return contact["club_id"], "numero"
        row = db.q1("SELECT id FROM clubs WHERE phone = ?", (phone,))
        if row:
            return row["id"], "numero"
    if nom and not PERSO.match(nom.strip()):
        club = repo.find_club(name=nom)
        if club:
            return club["id"], "nom"
    return None, None


def enregistrer(conversations):
    """Range l'index recu. Ne touche jamais a un lien pose a la main."""
    vus, crees, lies = 0, 0, 0
    maintenant = util.now_iso()

    for c in conversations or []:
        nom = (c.get("nom") or "").strip()
        if not nom:
            continue
        phone = _phone(nom, c.get("telephone"))
        cle = _cle(nom, phone)
        vus += 1

        donnees = {
            "nom": nom,
            "phone": phone,
            "groupe": 1 if c.get("groupe") else 0,
            "apercu": (c.get("apercu") or "")[:200] or None,
            "dernier_le": c.get("quand") or None,
            "sortant": 1 if c.get("sortant") else (0 if c.get("sortant") is not None else None),
            "non_lus": int(c.get("non_lus") or 0),
            "updated_at": maintenant,
        }
        if c.get("derniers"):
            donnees["derniers"] = json.dumps(c["derniers"], ensure_ascii=False)

        existant = db.q1("SELECT * FROM wa_chats WHERE cle = ?", (cle,))
        if existant is None and phone:
            # La meme conversation avait ete vue sans son numero (lecture par
            # le navigateur) : on reprend sa fiche au lieu d'en creer une
            # seconde, sinon les rattachements deja faits se perdent.
            ancienne = db.q1("SELECT * FROM wa_chats WHERE cle = ?",
                             ("nom:" + util.norm_key(nom),))
            if ancienne:
                db.update("wa_chats", ancienne["id"], {"cle": cle, "phone": phone})
                existant = db.q1("SELECT * FROM wa_chats WHERE id = ?", (ancienne["id"],))
        if existant:
            db.update("wa_chats", existant["id"], donnees)
            if not existant["club_id"]:
                club_id, _ = _club_probable(nom, phone)
                if club_id:
                    db.update("wa_chats", existant["id"], {"club_id": club_id})
                    lies += 1
        else:
            club_id, _ = _club_probable(nom, phone)
            donnees.update({"cle": cle, "club_id": club_id, "created_at": maintenant})
            db.insert("wa_chats", donnees)
            crees += 1
            if club_id:
                lies += 1

    # On date la lecture. Sans cette marque, un journal qui cesse d'etre relu
    # ne se voit nulle part : les compteurs d'Aujourd'hui continuent d'afficher
    # des chiffres, simplement ils vieillissent en silence. C'est arrive — deux
    # semaines sans relecture, personne ne l'a vu.
    db.set_setting("wa_lu_le", maintenant)
    return {"vus": vus, "crees": crees, "lies": lies}


# --------------------------------------------------------------------------
# Ou en est la discussion : l'etape, pas seulement qui a parle en dernier
# --------------------------------------------------------------------------

# Un club qui accepte le dit rarement en un mot : « ok pour moi », « on bloque
# la date », « c'est bon pour nous ». Ces tournures sont ce qui separe une
# reponse polie d'un marche conclu.
ACCORD = re.compile(
    r"\b(?:c'?est\s+(?:ok|bon|not[ée])|ok\s+(?:pour|pas\s+de\s+souci)|"
    r"on\s+(?:bloque|valide|est\s+d'?accord|reserve)|"
    r"je\s+(?:bloque|valide|reserve|confirme)|"
    r"avec\s+plaisir|volontiers|"
    r"d'?accord|march[ée]\s+conclu|on\s+part\s+l[àa]-?dessus|"
    r"vous\s+pouvez\s+venir|on\s+vous\s+attend|hate\s+de)\b", re.I)

# Plus large qu'ACCORD, et separe de lui a dessein : « passez quand vous
# voulez » est une porte ouverte, pas une date bloquee. Confondre les deux
# ferait remonter le dossier en « date bloquee » alors que personne n'a de
# date. Ce motif ne sert donc qu'a choisir le ton de la reponse proposee.
OUVERTURE = re.compile(
    r"\b(?:vous\s+pouvez\s+(?:venir|passer)|quand\s+vous\s+voulez|"
    r"avec\s+plaisir|volontiers|pas\s+de\s+souci|int[ée]ress[ée])\b", re.I)

REFUS = re.compile(
    r"\b(?:pas\s+int[ée]ress|non\s+merci|sans\s+suite|on\s+ne\s+souhaite|"
    r"ce\s+ne\s+sera\s+pas\s+possible|pas\s+cette\s+(?:ann[ée]e|fois)|"
    r"on\s+(?:a\s+d[ée]j[àa]|passe)|d[ée]j[àa]\s+un\s+photographe|"
    r"pas\s+de\s+budget|trop\s+cher)\b", re.I)

# Une question posee attend une reponse : c'est la que Julien perd des dossiers.
QUESTION = re.compile(r"\?\s*$|\bcombien\b|\bquel\s+(?:prix|tarif|co[uû]t)\b", re.I)

# Une semaine avant, on ne redemande pas l'accord : on cale l'heure d'arrivee,
# qui ouvre le gymnase, combien d'equipes. C'est la que les tournois derapent.
CALAGE_JOURS = 7

ETAPES = [
    ("a_traiter", "Ils ont répondu", "Ils attendent votre réponse."),
    ("a_caler", "À caler avant le jour J",
     "L'événement approche : heure d'arrivée, qui vous ouvre, combien d'équipes."),
    ("a_appeler", "À appeler maintenant",
     "Le message est resté sans réponse : décrochez, l'écrit ne passera plus."),
    ("conclu", "Date bloquée", "C'est dit, l'événement est à mettre au calendrier."),
    ("a_relancer", "Relancer par message", "Parti il y a plus de cinq jours, silence."),
    ("en_attente", "En attente", "Le message est parti, on laisse le temps."),
    ("a_contacter", "À contacter", "Le message est prêt, il n'est jamais parti."),
    ("sans_suite", "Sans suite", "Refus, ou dossier abandonné."),
]
ETAPES_NOMS = dict((c, n) for c, n, _ in ETAPES)


def _derniers(row):
    brut = row.get("derniers")
    if not brut:
        return []
    try:
        return json.loads(brut)
    except (ValueError, TypeError):
        return []


def _jours_avant(iso):
    d = util.parse_date(iso)
    return util.days_between(util.today(), d) if d else None


def etape_auto(row, a_un_brouillon=False, evenement=None):
    """Deduit l'etape du dernier echange.

    On ne lit que les messages recus : ce que Julien ecrit contient forcement
    « ok » et « parfait », les compter ferait conclure toutes les affaires.
    """
    msgs = _derniers(row)
    recus = [m for m in msgs if not m.get("de_moi")]
    dernier = msgs[-1] if msgs else None

    for m in reversed(recus):
        texte = m.get("texte") or ""
        if REFUS.search(texte):
            return "sans_suite"
        if ACCORD.search(texte) and not QUESTION.search(texte):
            return "conclu"
        break                     # seul le dernier message recu fait foi

    if dernier and not dernier.get("de_moi"):
        return "a_traiter"
    if row.get("non_lus"):
        return "a_traiter"

    jours = etat(row)[1]
    if row.get("sortant") == 1:
        avant = _jours_avant((evenement or {}).get("start_date"))
        # Sans numero, l'appel n'est pas une option : on reste sur l'ecrit.
        joignable = bool(row.get("phone"))
        if joignable and jours is not None:
            if jours >= APPEL_JOURS:
                return "a_appeler"
            # L'evenement arrive : plus le temps d'attendre un ecrit.
            if avant is not None and 0 <= avant <= APPEL_AVANT_EVENEMENT \
                    and jours >= RELANCE_JOURS:
                return "a_appeler"
        if jours is not None and jours >= RELANCE_JOURS:
            return "a_relancer"
        return "en_attente"
    return "a_contacter" if a_un_brouillon else "en_attente"


def etape(row, a_un_brouillon=False, evenement=None):
    """L'etape retenue : celle que Julien a posee a la main gagne toujours.

    Une exception, et une seule : un dossier conclu dont l'evenement arrive
    remonte de lui-meme dans « a caler ». Sinon un accord pris en septembre
    pour octobre dort dans sa colonne jusqu'au jour du tournoi.
    """
    pose = bool(row.get("etape_main"))
    cle = row["etape_main"] if pose else etape_auto(row, a_un_brouillon, evenement)
    if cle == "conclu":
        avant = _jours_avant((evenement or {}).get("start_date"))
        if avant is not None and 0 <= avant <= CALAGE_JOURS:
            return "a_caler", pose
    return cle, pose


def etat(row):
    """L'etat d'une conversation, dans les mots de la prospection."""
    dernier = util.parse_date(row.get("dernier_le"))
    jours = util.days_between(dernier, util.today()) if dernier else None
    if row.get("non_lus"):
        return "a_lire", jours
    if row.get("sortant") == 0:
        return "a_repondu", jours
    if row.get("sortant") == 1:
        if jours is not None and jours >= RELANCE_JOURS:
            return "a_relancer", jours
        return "en_attente", jours
    return "inconnu", jours


LIBELLES = {
    "a_lire": "Message non lu",
    "a_repondu": "A répondu",
    "en_attente": "En attente de réponse",
    "a_relancer": "Sans réponse, à relancer",
    "inconnu": "État inconnu",
}


def liste(filtre=None):
    """Toutes les conversations, chacune sachant si elle touche un club.

    La lecture locale ramene aussi la vie privee de Julien : quatre cents
    conversations dont une bonne part n'a rien a voir avec la prospection.
    On ne jette rien — un contact de club se cache souvent derriere un prenom
    — mais l'ecran met devant ce qui concerne un club.
    """
    rows = db.rows_to_dicts(db.q(
        "SELECT w.*, c.name AS club_nom, c.city AS club_ville, c.status AS club_statut "
        "FROM wa_chats w LEFT JOIN clubs c ON c.id = w.club_id "
        "WHERE w.ecarte = 0 ORDER BY w.dernier_le DESC, w.id DESC"))
    for r in rows:
        cle, jours = etat(r)
        r["etat"] = cle
        r["etat_nom"] = LIBELLES[cle]
        r["jours"] = jours
        r["suggestions"] = [] if r["club_id"] else suggestions(r["nom"])
        r["nom_club"] = nom_club_probable(r["nom"])
        r["pro"] = bool(r["club_id"] or r["suggestions"])
    if filtre == "a_rattacher":
        # Le vrai travail : celles qu'on sait proposer et que personne n'a
        # encore tranchees. Sans ce filtre, l'ecran s'ouvrait sur les 57 deja
        # rattachees — c'est-a-dire sur ce qu'il n'y a plus a faire.
        rows = [r for r in rows if not r["club_id"] and r["suggestions"]]
    elif filtre == "clubs":
        rows = [r for r in rows if r["pro"]]
    elif filtre and filtre != "tout":
        rows = [r for r in rows if r["etat"] == filtre and r["pro"]]
    return rows


def synchroniser_contacts():
    """Cree dans le CRM la personne derriere chaque conversation rattachee.

    Julien connait ses interlocuteurs par leur prenom dans WhatsApp ; le CRM,
    lui, ne connaissait que des clubs. On reporte le nom, le role quand il est
    ecrit, et surtout le numero — c'est par la qu'il ecrit.
    """
    crees, completes = 0, 0
    lignes = db.q(
        "SELECT w.id, w.nom, w.phone, w.club_id, c.name AS club_nom "
        "FROM wa_chats w JOIN clubs c ON c.id = w.club_id "
        "WHERE w.ecarte = 0 AND w.groupe = 0 AND w.phone IS NOT NULL")
    for r in lignes:
        prenom, role = personne_du_titre(r["nom"], r["club_nom"])
        existant = repo.find_contact(phone=r["phone"])
        donnees = {
            "club_id": r["club_id"],
            "phone": r["phone"],
            "channel": "whatsapp",
            "source": "WhatsApp",
        }
        if prenom:
            donnees["first_name"] = prenom
        if role:
            donnees["role"] = role
        if existant:
            # upsert_contact ne remplace jamais une valeur deja saisie.
            avant = (existant.get("first_name"), existant.get("role"),
                     existant.get("club_id"))
            repo.upsert_contact(dict(donnees, id=existant["id"]))
            apres = repo.get_contact(existant["id"]) or {}
            if (apres.get("first_name"), apres.get("role"),
                    apres.get("club_id")) != avant:
                completes += 1
        else:
            # Sans prenom lisible, on n'invente pas d'identite : le role dit
            # d'ou vient la ligne, le numero suffit pour ecrire.
            if not prenom and not role:
                donnees["role"] = "Contact WhatsApp"
            repo.upsert_contact(donnees)
            crees += 1
    if crees or completes:
        db.log("whatsapp", "Contacts : %d cree(s), %d complete(s) depuis WhatsApp"
               % (crees, completes))
    return {"crees": crees, "completes": completes}


def suivi():
    """Les discussions rangees par etape, avec de quoi decider sur la carte.

    C'est l'ecran qui remplace « Messages » et « WhatsApp » : un annuaire ne
    dit pas quoi faire, une carte par dossier si.
    """
    rows = db.rows_to_dicts(db.q(
        "SELECT w.*, c.name AS club_nom, c.city AS club_ville, c.sport AS club_sport "
        "FROM wa_chats w LEFT JOIN clubs c ON c.id = w.club_id "
        "WHERE w.ecarte = 0 AND w.club_id IS NOT NULL "
        "ORDER BY w.dernier_le DESC"))

    cartes = []
    for r in rows:
        brouillon = db.q1(
            "SELECT id, body, to_addr, channel, status FROM drafts "
            "WHERE club_id = ? ORDER BY (status='draft') DESC, id DESC LIMIT 1",
            (r["club_id"],))
        brouillon = db.row_to_dict(brouillon) if brouillon else None
        pret = bool(brouillon and brouillon["status"] == "draft" and brouillon["to_addr"])

        # L'evenement dont on parle : le prochain tournoi du club.
        ev = db.q1(
            "SELECT id, name, start_date, city, nb_equipes, distance_km, trajet_min "
            "FROM tournaments WHERE club_id = ? "
            "  AND (start_date IS NULL OR date(start_date) >= date('now')) "
            "ORDER BY start_date LIMIT 1", (r["club_id"],))
        ev = db.row_to_dict(ev) if ev else None

        cle, pose = etape(r, pret, ev)
        _, jours = etat(r)
        cartes.append({
            "id": r["id"],
            "club_id": r["club_id"],
            "club": r["club_nom"],
            "ville": r["club_ville"],
            "sport": r["club_sport"],
            "personne": r["nom"] if r["nom"] != r["club_nom"] else None,
            "phone": r["phone"],
            "etape": cle,
            "etape_nom": ETAPES_NOMS.get(cle, cle),
            "jours_avant": _jours_avant((ev or {}).get("start_date")),
            "posee_main": pose,
            "jours": jours,
            "dernier_le": r["dernier_le"],
            "derniers": _derniers(r)[-2:],
            # La ligne de situation calculee par le modele local.
            "resume": r.get("resume"),
            "note": r["note"],
            "note_le": r["note_le"],
            "calage_le": None,
            "evenement": ev,
            "brouillon": brouillon if pret else None,
        })

    # Les mots a coller dependent de l'etape : on les prepare une fois la
    # carte complete, pas avant.
    for c in cartes:
        ev = c.get("evenement") or {}
        if c["etape"] in ("conclu", "a_caler"):
            c["mot"] = (mot_de_calage(c) if c["etape"] == "a_caler"
                        else mot_de_confirmation(c))
            c["mot_nom"] = ("Message de calage" if c["etape"] == "a_caler"
                            else "Message de confirmation")
            if ev.get("start_date"):
                rappel = util.parse_date(ev["start_date"])
                if rappel:
                    c["calage_le"] = (rappel - dt.timedelta(days=CALAGE_JOURS)).isoformat()

    colonnes = []
    for cle, nom, aide in ETAPES:
        dedans = [c for c in cartes if c["etape"] == cle]
        # Ce qui arrive le plus tot passe devant : c'est ce qui expire.
        dedans.sort(key=lambda c: ((c["evenement"] or {}).get("start_date") or "9999",
                                   -(c["jours"] or 0)))
        colonnes.append({"cle": cle, "nom": nom, "aide": aide,
                         "n": len(dedans), "cartes": dedans})
    return {"colonnes": colonnes, "total": len(cartes)}


def _jour_fr(iso):
    d = util.parse_date(iso)
    if not d:
        return ""
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return "%s %d %s" % (jours[d.weekday()], d.day, mois[d.month - 1])


def _prenom(carte):
    """Le prenom de la personne — jamais son numero.

    « personne » vient du repertoire WhatsApp, et c'est parfois le numero
    lui-meme : le message partait alors sur « Bonjour +33, ». Quand une
    fiche contact porte ce numero, c'est son prenom qu'on prend ; sinon on
    dit simplement « Bonjour, ».
    """
    from . import rencontres
    return rencontres.qui_parle([carte.get("personne")], carte.get("phone")) or ""


def _dernier_recu(carte):
    for m in reversed(carte.get("derniers") or []):
        if not m.get("de_moi"):
            return m.get("texte") or ""
    return ""


def _contexte_reponse(carte):
    ev = carte.get("evenement") or {}
    bouts = ["Club ou organisateur : %s" % (carte.get("club") or "inconnu")]
    if _prenom(carte):
        bouts.append("Prénom de la personne : %s" % _prenom(carte))
    if ev.get("name"):
        bouts.append("Événement repéré par Julien : %s%s%s" % (
            ev["name"],
            (", le " + _jour_fr(ev["start_date"])) if ev.get("start_date") else "",
            (", à " + ev["city"]) if ev.get("city") else ""))
    return "\n".join(bouts)


def reponse_du_fil(carte, calculer=True):
    """La reponse ecrite par le modele local d'apres TOUT le fil, ou None.

    Les trois modeles de mot_de_reponse ne lisent que deux indices (une date,
    un accord) : « je pensais pour fevrier, je vous redis » ne rentrait dans
    aucun et recevait le texte generique. Le modele, lui, lit la conversation.
    Stockee avec l'empreinte du fil : tant que rien ne bouge, on ne recalcule
    pas, et l'ecran reste rapide.
    """
    if not carte.get("id"):
        return None
    r = db.q1("SELECT derniers, reponse_ia, reponse_source FROM wa_chats WHERE id = ?",
              (carte["id"],))
    if not r or not (r["derniers"] or "").strip():
        return None
    # La version de la consigne entre dans l'empreinte : une consigne corrigee
    # refait les reponses deja stockees.
    empreinte = _empreinte(r["derniers"] + REPONSE_VERSION)
    if r["reponse_source"] == empreinte:
        corps = r["reponse_ia"]           # deja calculee pour ce fil (None compris)
    else:
        if not calculer:
            return None
        from . import ia
        if not ia.disponible():
            return None                   # on ne stocke rien : on reessaiera
        try:
            messages = json.loads(r["derniers"])
        except (TypeError, ValueError):
            return None
        corps = ia.repondre(messages, _contexte_reponse(carte))
        db.ex("UPDATE wa_chats SET reponse_ia = ?, reponse_source = ? WHERE id = ?",
              (corps, empreinte, carte["id"]))
    if not corps:
        return None
    qui = _prenom(carte)
    return "\n".join(["Bonjour%s," % ((" " + qui) if qui else ""), "", corps, "",
                      "Bonne journée,", "Julien — SnapActions"])


REPONSE_VERSION = "3"


def mot_de_reponse(carte):
    """Une reponse a quelqu'un qui vient d'ecrire.

    D'abord la reponse du modele local, qui a lu tout le fil. A defaut (Ollama
    eteint, reponse inutilisable), on regarde deux choses sures — y a-t-il une
    date dedans, et est-ce un accord — et on tombe sur trois reponses
    honnetes. Julien relit et corrige : c'est une avance, pas un envoi
    automatique.
    """
    from . import dates_fr
    texte = _dernier_recu(carte)
    if not texte:
        return None, None

    lue = reponse_du_fil(carte)
    if lue:
        return "Réponse — d'après la conversation", lue

    qui = _prenom(carte)
    tete = "Bonjour%s," % ((" " + qui) if qui else "")
    pied = ["", "Bonne journée,", "Julien — SnapActions"]

    trouvees = []
    try:
        trouvees = dates_fr.extraire(texte) or []
    except Exception:
        trouvees = []

    if trouvees:
        quand = _jour_fr(trouvees[0].get("debut")) or "la date"
        return "Réponse — ils ont donné une date", "\n".join(
            [tete, "",
             "Merci pour la date : je note %s, je bloque de notre côté." % quand,
             "",
             "Je reviens vers vous quelques jours avant pour l'heure d'arrivée "
             "et la personne qui nous accueille.",
             "",
             "Pour rappel, rien à payer pour le club : ce sont les familles qui "
             "achètent les photos si elles le souhaitent, et vous recevez une "
             "sélection pour votre communication."] + pied)

    if ACCORD.search(texte) or OUVERTURE.search(texte):
        return "Réponse — accord, mais pas de date", "\n".join(
            [tete, "",
             "Merci beaucoup, c'est noté.",
             "",
             "Dès que vous avez les dates, envoyez-les moi : je les bloque "
             "tout de suite dans notre planning. Et si vous avez déjà une "
             "période approximative, ça me suffit pour réserver une équipe.",
             ""] + pied[1:])

    return "Réponse — à compléter", "\n".join(
        [tete, "",
         "Merci pour votre retour.",
         "",
         "[À compléter : répondez à leur question ici.]",
         "",
         "Pour rappel, c'est sans frais pour le club — les photos sont "
         "financées par les familles qui souhaitent les acheter, et on vous "
         "offre une sélection pour votre communication."] + pied)


def mot_de_confirmation(carte):
    """Le message a envoyer juste apres un accord au telephone.

    Un accord oral que personne n'a ecrit n'existe plus trois semaines apres :
    ni pour eux, ni pour nous. Ce mot-la fixe la date et annonce la suite.
    """
    ev = carte.get("evenement") or {}
    quand = _jour_fr(ev.get("start_date"))
    qui = (carte.get("personne") or "").split()[0] if carte.get("personne") else ""
    lignes = ["Bonjour%s," % ((" " + qui) if qui else "")]
    lignes.append("")
    if quand:
        lignes.append("Comme convenu au téléphone, on note %s pour %s."
                      % (quand, ev.get("name") or "votre événement"))
    else:
        lignes.append("Comme convenu au téléphone, c'est noté de notre côté.")
    lignes.append("")
    lignes.append("On arrive une demi-heure avant le début pour s'installer. "
                  "Pouvez-vous me confirmer l'heure de démarrage et qui nous "
                  "accueille sur place ?")
    lignes.append("")
    lignes.append("Pour rappel : aucun frais pour le club, les photos sont "
                  "financées par les familles qui souhaitent les acheter, et "
                  "on vous offre une sélection pour votre communication.")
    lignes.append("")
    lignes.append("À très vite,")
    lignes.append("Julien — SnapActions")
    return "\n".join(lignes)


def mot_de_calage(carte):
    """Le rappel a J-7 : on ne redemande pas l'accord, on cale le concret."""
    ev = carte.get("evenement") or {}
    quand = _jour_fr(ev.get("start_date"))
    qui = (carte.get("personne") or "").split()[0] if carte.get("personne") else ""
    return "\n".join([
        "Bonjour%s," % ((" " + qui) if qui else ""),
        "",
        "On se retrouve %s, on prépare le matériel." % (quand or "bientôt"),
        "",
        "Trois précisions pour être au point :",
        "— l'heure de début et l'heure à laquelle on peut entrer,",
        "— qui nous accueille sur place et son numéro,",
        "— le nombre d'équipes et les catégories, pour prévoir le bon nombre "
        "de photographes.",
        "",
        "Merci d'avance,",
        "Julien — SnapActions",
    ])


def poser_note(chat_id, texte):
    """Le compte rendu d'un appel.

    Le telephone reste le canal ou tout se joue vraiment, et rien de ce qui s'y
    dit n'atterrit dans WhatsApp : sans cette case, la moitie de l'histoire
    d'un dossier se perd.
    """
    texte = (texte or "").strip()
    db.update("wa_chats", int(chat_id),
              {"note": texte or None,
               "note_le": util.now_iso() if texte else None})
    return texte


def poser_etape(chat_id, cle):
    """Julien classe une carte a la main ; l'automatique ne la reprend plus."""
    if cle and cle not in ETAPES_NOMS:
        raise ValueError("etape inconnue")
    db.update("wa_chats", int(chat_id),
              {"etape_main": cle or None, "etape_le": util.now_iso()})
    return cle


def rapprocher_brouillons():
    """Marque « envoye » un brouillon dont le club a recu un message WhatsApp.

    C'est le point qui manquait : le CRM comptait 190 brouillons en attente
    alors que Julien avait deja ecrit a une partie d'entre eux.
    """
    faits = 0
    rows = db.q(
        "SELECT d.id, d.club_id, d.created_at, w.dernier_le, w.nom "
        "FROM drafts d JOIN wa_chats w ON w.club_id = d.club_id "
        "WHERE d.status = 'draft' AND w.sortant = 1 "
        "  AND w.dernier_le IS NOT NULL AND w.dernier_le >= date(d.created_at)")
    for r in rows:
        db.update("drafts", r["id"], {"status": "sent", "sent_at": r["dernier_le"],
                                      "review_note": "Retrouve dans WhatsApp"})
        repo.add_interaction(r["club_id"], kind="whatsapp", direction="out",
                             subject="Message envoye (retrouve dans WhatsApp)",
                             occurred_at=r["dernier_le"])
        faits += 1
    if faits:
        db.log("whatsapp", "%d brouillon(s) marque(s) envoyes d'apres WhatsApp" % faits)
    return faits


# ---------------------------------------------------------------------------
# Une phrase qui dit ou en est le dossier
# ---------------------------------------------------------------------------
# Les cartes affichaient les trois derniers messages en brut : douze pavés a
# relire chaque matin pour retrouver le fil. Le modele local les resume en une
# ligne — « accord de principe, ils attendent leurs dates ». Il ne lit que ce
# qui est la et n'ajoute rien ; le texte integral reste affiche en dessous,
# donc une erreur de resume ne peut pas tromper sur le fond.
#
# Le resume est STOCKE avec l'empreinte du fil qui l'a produit : tant que la
# conversation ne bouge pas, on ne refait pas le calcul.
import hashlib


def _empreinte(derniers):
    return hashlib.sha1((derniers or "").encode("utf-8")).hexdigest()[:16]


def resumer_fils(limite=40):
    """Calcule les resumes manquants. Pense pour la routine, pas pour l'ecran."""
    from . import ia
    if not ia.disponible():
        return {"faits": 0, "raison": "modele local indisponible"}

    lignes = db.q(
        "SELECT id, derniers, resume_source FROM wa_chats "
        "WHERE club_id IS NOT NULL AND ecarte = 0 "
        "  AND TRIM(COALESCE(derniers,'')) <> '' "
        "ORDER BY dernier_le DESC LIMIT ?", (int(limite) * 3,))

    faits = 0
    for r in lignes:
        if faits >= limite:
            break
        empreinte = _empreinte(r["derniers"])
        if r["resume_source"] == empreinte:
            continue                      # rien n'a bouge depuis le dernier resume
        try:
            messages = json.loads(r["derniers"])
        except (TypeError, ValueError):
            continue
        phrase = ia.resume_fil(messages)
        db.ex("UPDATE wa_chats SET resume = ?, resume_le = ?, resume_source = ? "
              "WHERE id = ?",
              (phrase, util.now_iso(), empreinte, r["id"]))
        faits += 1
    return {"faits": faits}
