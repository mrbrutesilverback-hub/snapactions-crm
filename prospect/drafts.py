"""Redaction de brouillons et rapprochement tournoi -> club.

Regle du projet : rien ne part sans relecture. Un brouillon passe par
draft -> approved -> sent, et le passage a 'sent' est toujours declenche
par l'humain. Aucune fonction d'envoi automatique n'existe dans ce module.
"""
import datetime as dt
import re

from . import db, repo, util

STATUS_FLOW = ["draft", "approved", "sent", "rejected"]


# --------------------------------------------------------------------------
# Rendu des modeles
# --------------------------------------------------------------------------

class _SafeDict(dict):
    """Un placeholder inconnu reste visible tel quel plutot que de planter."""

    def __missing__(self, key):
        return "{%s}" % key


def get_template(key):
    row = db.q1("SELECT * FROM templates WHERE key = ?", (key,))
    return dict(row) if row else None


def _sans_club(nom, club):
    """Retire le suffixe « — Nom du club » d'un intitule de tournoi.

    La comparaison est souple : « ASP Pignan » suffit a reconnaitre le suffixe
    d'un club enregistre sous « ASP Pignan (AS Pignan football) ».
    """
    if not nom:
        return ""
    texte = str(nom)
    reduit = lambda x: "".join(c for c in str(x).lower() if c.isalnum())
    cible = reduit(club) if club else ""
    for sep in (" — ", " - ", " – "):
        if sep in texte:
            tete, _, queue = texte.partition(sep)
            q = reduit(queue)
            if q and cible and (q == cible or q in cible or cible in q):
                texte = tete.strip()
                break

    # Le club est aussi souvent rattache par « de / du / de l' » :
    # « Challenge Jose Perez U12 du SC Cogolin ». Sans ca, le message disait
    # « votre Challenge ... du SC Cogolin de SC Cogolin ».
    if cible:
        m = re.search(r"\s+(?:de\s+l['’]|de\s+la\s+|du\s+|des\s+|de\s+)(.+)$",
                      texte, re.I)
        if m:
            q = reduit(m.group(1))
            if q and (q == cible or q in cible or cible in q):
                texte = texte[:m.start()].strip()
    return texte.strip()


GENERIQUES = {"tournoi", "tournois", "match", "matchs", "competition",
              "competitions", "rencontre", "gala", "evenement", "coupe",
              "challenge", "championnat", "stage",
              # Les annuaires d'associations rangent la structure elle-meme
              # dans la case « contact » : « Bonjour Association, » et
              # « Bonjour Club, » s'ecrivaient tels quels, ce qui signe un
              # envoi automatique a la premiere ligne.
              "association", "asso", "club", "section", "comite", "ligue",
              "federation", "amicale", "ecole", "centre", "service",
              "secretariat", "bureau", "president", "presidente", "contact",
              "accueil", "direction", "mairie", "office", "salon", "equipe",
              "famille", "monsieur", "madame", "info", "infos"}


def _faux_prenom(mot, nom_club):
    """Ce « prenom » en est-il vraiment un ?

    Les imports de flyers decoupent un intitule en prenom + nom : « Vacances
    Scolaires » donnait « Bonjour Vacances, », « Challenge Perez » donnait
    « Bonjour Challenge, ». Un mot repris du nom du club, ou un mot generique
    de competition, n'est pas quelqu'un.
    """
    plat = util.strip_accents(mot).lower()
    if plat in GENERIQUES:
        return True
    if nom_club:
        mots = {util.strip_accents(m).lower()
                for m in re.split(r"[^\w]+", nom_club) if m}
        if plat in mots:
            return True
    return False


# Les annuaires collent la discipline et la ville au titre, pour s'y retrouver
# dans une liste. Dans une phrase, ca donne « couvrir votre Randonnee des
# Marinieres (aviron de mer) - Villefranche-sur-Mer, dimanche 18 octobre » :
# on annonce au club sa propre ville et son propre sport. On ne retire que ce
# qu'on peut prouver redondant — la ville de la fiche, la discipline de la
# fiche — et jamais un fragment qu'on ne reconnait pas.
_QUEUE_VILLE = u"\s*[\u2013\u2014-]\s*%s\s*$"


def _alleger(texte, tournament):
    t = tournament or {}
    sortie = texte

    sport = util.strip_accents(str(t.get("sport") or "")).lower().strip()
    if sport:
        racine = sport.split()[0]
        if len(racine) >= 4:
            def oter(m):
                dedans = util.strip_accents(m.group(1)).lower()
                return "" if racine in dedans else m.group(0)
            sortie = re.sub(r"\s*\(([^()]{0,40})\)", oter, sortie)

    # « Coupe Regionale Jeunes - etape 1 (CRJ1) aviron - Mandelieu » porte la
    # ville en abrege : l'egalite stricte ne la reconnaissait pas. On accepte
    # donc le debut du nom officiel, coupe a un tiret ou a une espace —
    # Mandelieu pour Mandelieu-la-Napoule, Saint-Paul pour Saint-Paul-de-Vence.
    ville = str(t.get("city") or "").strip()
    if ville:
        bouts = [ville]
        for sep in ("-", " "):
            tete = ville.split(sep)[0]
            if len(tete) >= 4 and tete not in bouts:
                bouts.append(tete)
        for bout in sorted(bouts, key=len, reverse=True):
            neuf = re.sub(_QUEUE_VILLE % re.escape(bout), "", sortie, flags=re.I)
            if neuf != sortie:
                sortie = neuf
                break

    # Et la discipline restee en queue, hors parentheses : « ... aviron ».
    if sport:
        racine = sport.split()[0]
        if len(racine) >= 4:
            sortie = re.sub(r"\s*[\u2013\u2014-]?\s*%s\s*$" % re.escape(racine),
                            "", sortie, flags=re.I)

    sortie = sortie.strip(" \u2013\u2014-\u00b7,")
    # Si on a tout mange, on rend le titre d'origine : mieux vaut lourd que vide.
    return sortie or texte


def _intitule(nom, club, tournament=None):
    """L'intitule a mettre apres « votre ».

    « Tournoi — Vacances Scolaires » se reduit a « Tournoi », et « votre
    Tournoi » sonne faux. Un intitule qui n'est plus qu'un mot generique
    repasse donc en minuscule : « votre tournoi, dimanche 25 octobre ».
    """
    texte = _alleger(_sans_club(nom, club), tournament)
    if util.strip_accents(texte).lower().strip() in GENERIQUES:
        return texte.lower()
    return texte


def build_context(club=None, contact=None, tournament=None, extra=None):
    settings = db.all_settings()
    club = club or {}
    contact = contact or {}
    tournament = tournament or {}

    prenom = (contact.get("first_name") or "").strip()
    if prenom and _faux_prenom(prenom, club.get("name")):
        prenom = ""
    ctx = {
        # Sans prenom on laisse vide : le nettoyage de rendu recolle "Bonjour,".
        "prenom": prenom,
        "nom": contact.get("last_name") or "",
        "nom_complet": (" ".join(x for x in [contact.get("first_name"),
                                             contact.get("last_name")] if x)).strip(),
        "role": contact.get("role") or "",
        "club": club.get("name") or "votre club",
        "ville": club.get("city") or "",
        "dept": club.get("dept") or "",
        "dept_nom": util.dept_name(club.get("dept")),
        "sport": club.get("sport") or settings.get("sport") or "",
        "source": club.get("source") or contact.get("source") or "le forum",
        # La premiere phrase, adossee a la facon dont le club est arrive.
        # Sans elle, « On a echange sur {source} » donnait « On a echange sur
        # Repertoire officiel des associations » : on n'a echange avec
        # personne, on les a trouves dans un annuaire.
        "accroche": _rencontre(club) or _trouve_ou(club),
        "sujet": (extra or {}).get("sujet") or "votre organisation",
        "pitch": settings.get("pitch") or "",
        "moi": _moi(settings),
        "my_name": settings.get("my_name") or "",
        "my_org": settings.get("my_org") or "",
        "my_email": settings.get("my_email") or "",
        "my_phone": settings.get("my_phone") or "",
        "signature": settings.get("my_signature") or _default_signature(settings),
        "tournoi": tournament.get("name") or "",
        "tournoi_ville": tournament.get("city") or tournament.get("venue") or "",
        "tournoi_lieu": tournament.get("venue") or "",
        "tournoi_url": tournament.get("url") or "",
        "tournoi_date": _fr_date_range(tournament.get("start_date"),
                                       tournament.get("end_date")),
        "tournoi_j": _days_until(tournament.get("start_date")),
        "tournoi_jour": _fr_date_jour(tournament.get("start_date")),
        # « Tournoi U11 — AS Cannes » -> « Tournoi U11 » : evite de repeter
        # le nom du club juste apres, dans « votre {tournoi_intitule} de {club} ».
        "tournoi_intitule": _intitule(tournament.get("name"), club.get("name"),
                                      tournament),
        "categories": tournament.get("categories") or "",
        "nb_equipes": tournament.get("nb_equipes") or "",
        "organisateur": tournament.get("organizer") or club.get("name") or "",
        # « de Elite Foot Events » se lit mal : on elide devant une voyelle.
        "de_club": _elider(club.get("name")),
        # Rappeler ou l'on s'est rencontres ne vaut que si c'est vrai : dire
        # « on s'est croises au forum » a un club recupere sur WhatsApp fait
        # passer pour quelqu'un qui recite un modele.
        "rencontre": _rencontre(club),
        # Pourquoi on ecrit a CE relais-la : sa propre raison d'etre, telle
        # qu'elle a ete relevee. Un message qui dit « je vous ecris parce que
        # vous etes un media » ne vaut pas mieux qu'un publipostage.
        "relais_pourquoi": _relais_pourquoi(club),
        # L'aviron ne dit pas « tournoi » : il dit randonnee, regate, course
        # ou epreuve, et ecrire l'un pour l'autre s'entend tout de suite.
        "aviron_epreuve": _aviron_epreuve(tournament),
        "nautique_qui": _nautique_qui(tournament),
        # Le shooting studio photographie des adherents, pas des « joueurs » :
        # un escrimeur pose avec son arme, un judoka en kimono.
        "studio_qui": _studio_qui(club),
        "studio_lieu": _studio_lieu(club),
    }
    ctx.update(extra or {})
    return ctx


# Les douze dates d'aviron a venir ne sont pas de meme nature : trois
# randonnees, deux regates, une course au piquet, et des coupes et
# championnats. Le message parle de « tout au long de la randonnee » — exact
# pour les trois premieres, faux pour les neuf autres. Plutot que d'entretenir
# neuf variantes du meme texte qui finiraient par diverger, un seul mot
# s'adapte.
def _aviron_epreuve(tournament):
    nom = util.strip_accents(" ".join(str((tournament or {}).get(k) or "")
                                      for k in ("name", "categories"))).lower()
    if "randonnee" in nom or "randon" in nom:
        return "la randonn\u00e9e"
    if "regate" in nom:
        return "la r\u00e9gate"
    if "course" in nom or "traversee" in nom:
        return "la course"
    return "l\u2019\u00e9preuve"


# Ce qui se photographie depuis l'eau partage la meme situation : des
# equipages et non des parents, un depart, un parcours, une arrivee, et un
# bateau d'accompagnement a demander. L'aviron et la voile prennent donc le
# meme message — celui que Julien a ecrit — a un mot pres.
# Ce qui se passe en salle (indoor, ergometre, epreuves au sol) n'a rien a
# voir et reprend le message ordinaire.
AVIRON = re.compile(r"\baviron\b|rowing|rameur", re.I)
VOILE = re.compile(r"\bvoile\b|r[ée]gate|catamaran|d[ée]riveur|optimist|\bnautique\b", re.I)
EN_SALLE = re.compile(r"indoor|ergom|au sol|\bsalle\b", re.I)


def est_nautique_sur_eau(tournament):
    t = tournament or {}
    champs = " ".join(str(t.get(k) or "")
                      for k in ("sport", "name", "categories", "description", "source"))
    if EN_SALLE.search(champs):
        return False
    return bool(AVIRON.search(champs) or VOILE.search(champs))


# Retro-compatible : le nom d'avant designait la meme chose.
est_aviron_sur_eau = est_nautique_sur_eau


def _nautique_qui(tournament):
    """Qui est sur l'eau. « les rameurs » n'a pas de sens sur une regate."""
    t = tournament or {}
    champs = " ".join(str(t.get(k) or "") for k in ("sport", "name", "categories"))
    if AVIRON.search(champs):
        return "les rameurs et les \u00e9quipages"
    return "les \u00e9quipages"


# Les trois familles de relais ne se demarchent pas de la meme facon : un
# media vend de la visibilite, une communaute se gagne par son animateur, un
# relais institutionnel diffuse gratuitement. La phrase d'accroche le dit.
_RELAIS_DEFAUT = {
    "media": u"vous parlez chaque semaine aux familles de la région, "
             u"et ce sont elles qui cherchent ces photos.",
    "communaute": u"votre communauté rassemble exactement les familles que nous "
                  u"photographions chaque week-end.",
    "institutionnel": u"vous relayez les bons plans famille du département, "
                      u"et les clubs que nous couvrons en font partie.",
}


def _relais_pourquoi(club):
    """La raison d'ecrire, propre a ce relais, et ecrite comme une phrase."""
    propre = (club.get("relais_accroche") or "").strip()
    if propre:
        return propre
    return _RELAIS_DEFAUT.get((club.get("relais_type") or "").strip(),
                              u"votre audience est celle des parents que nous "
                              u"photographions.")


# Le premier mot qui correspond l'emporte : « beach-volley » avant « volley ».
_STUDIO_QUI = [
    ("escrime", u"chaque escrimeur en portrait et en garde, en tenue et avec son arme"),
    ("judo", u"chaque judoka en kimono, en portrait et en posture"),
    ("karate", u"chaque karatéka en kimono, en portrait et en posture"),
    ("taekwondo", u"chaque pratiquant en dobok, en portrait et en posture"),
    ("boxe", u"chaque boxeur en portrait et en garde"),
    ("lutte", u"chaque lutteur en portrait et en posture"),
    ("gym", u"chaque gymnaste en portrait et en figure"),
    ("danse", u"chaque danseur et danseuse en portrait et en mouvement"),
    ("natation", u"chaque nageur en portrait, aux couleurs du club"),
    ("ping", u"chaque pongiste en portrait, raquette en main"),
    ("tennis de table", u"chaque pongiste en portrait, raquette en main"),
    ("tennis", u"chaque joueur en portrait, raquette en main"),
    ("padel", u"chaque joueur en portrait, raquette en main"),
    ("badminton", u"chaque joueur en portrait, raquette en main"),
    ("rugby", u"chaque joueur en portrait et en action, en maillot du club"),
    ("basket", u"chaque joueur en portrait et en action, ballon en main"),
    ("hand", u"chaque joueur en portrait et en action, ballon en main"),
    ("volley", u"chaque joueur en portrait et en action, ballon en main"),
    ("foot", u"chaque joueur en portrait et en action, en maillot du club"),
    ("equitation", u"chaque cavalier en selle sur son poney ou son cheval, et en portrait"),
    ("poney", u"chaque cavalier en selle sur son poney, et en portrait"),
    ("athletisme", u"chaque athlète en portrait et en action, en tenue du club"),
]


def _studio_qui(club):
    """Qui pose devant le fond, et comment, selon le sport du club."""
    sport = util.strip_accents(str((club or {}).get("sport") or "")).lower()
    for mot, phrase in _STUDIO_QUI:
        if mot in sport:
            return phrase
    return u"chaque adhérent en portrait, en tenue du club"


# Ou le studio s'installe : un club de foot dit « au stade », pas « au gymnase ».
_STUDIO_LIEU = [
    (("foot", "rugby", "athle"), u"au stade"),
    (("basket", "hand", "volley", "badminton", "gym"), u"au gymnase"),
    (("escrime", "judo", "karate", "taekwondo", "boxe", "lutte", "danse"), u"\u00e0 la salle"),
]


def _studio_lieu(club):
    if commerce_animalier(club):
        return u"chez vous"
    sport = util.strip_accents(str((club or {}).get("sport") or "")).lower()
    for mots, lieu in _STUDIO_LIEU:
        if any(m in sport for m in mots):
            return lieu
    return u"au club"


# Les commerces animaliers : un salon de toilettage n'a pas d'adherents, il a
# des clients, et on ne lui ecrit pas « nous sommes photographes SPORTIFS ».
# Les centres equestres sont volontairement HORS de cette liste : un club
# d'equitation a bien des adherents, et _STUDIO_QUI sait deja lui parler de
# ses cavaliers en selle. Les confondre ferait ecrire a un poney-club qu'on
# vient photographier « vos clients et leurs chiens ».
_COMMERCE_ANIMALIER = re.compile(
    r"toilettage|canin|\bchien|\bchat\b|f[ée]lin|v[ée]t[ée]rinaire|"
    r"pension animal|[ée]levage|animalerie|\bdog\b|\bpet\b", re.I)


def commerce_animalier(club):
    """Un salon, pas un club : on lui parle de ses clients et de leurs betes."""
    texte = "%s %s" % ((club or {}).get("sport") or "", (club or {}).get("name") or "")
    return bool(_COMMERCE_ANIMALIER.search(texte))


# Un club deja en discussion ne recoit pas la presentation : Julien lui ecrit
# directement pour caler le jour (« on en avait parle, la semaine prochaine ? »).
STUDIO = ("studio_club", "studio_whatsapp", "studio_rdv")


def modele_studio(club, canal=None):
    """Le modele studio qui convient a CE destinataire.

    Les ecrans Studio et « Semaines a remplir » ecrivaient leur propre texte
    en JavaScript, chacun le sien. Trois versions du meme message vivaient
    donc en parallele, et celles des ecrans avaient derive : plus de
    « galerie intelligente », plus de selection remise au club, plus de
    reference au Basket Cannes Olympique — c'est-a-dire plus aucune des
    phrases qui font vendre. Un seul endroit decide maintenant, et c'est la
    table des modeles, celle que Julien peut relire et corriger.
    """
    animalier = commerce_animalier(club)
    mobile = canal == "whatsapp" or (
        canal is None and util.is_mobile_fr((club or {}).get("phone"))
        and not (club or {}).get("email"))
    if animalier:
        return "studio_animaux_whatsapp" if mobile else "studio_animaux"
    if mobile:
        # Un club qui a deja repondu n'a pas besoin qu'on lui represente l'offre.
        return "studio_rdv" if en_discussion(club) else "studio_whatsapp"
    return "studio_club"


def en_discussion(club):
    """Le club a deja repondu, ou on l'a vu : la presentation est faite."""
    if club.get("partenaire") or club.get("status") in ("repondu", "interesse", "rdv", "client"):
        return True
    if db.q1("SELECT 1 FROM interactions WHERE club_id = ? "
             "AND (direction = 'in' OR kind = 'meeting') LIMIT 1", (club["id"],)):
        return True
    # Le statut ne suit pas toujours : une reponse lue dans WhatsApp suffit.
    return bool(db.q1("SELECT 1 FROM wa_chats WHERE club_id = ? AND (derniers LIKE '%\"de_moi\": 0%' "
                      "OR derniers LIKE '%\"de_moi\":0%') LIMIT 1", (club["id"],)))


def variantes(template_key):
    """Les modeles qui valent le meme message, pour ne pas l'envoyer deux fois."""
    if template_key in STUDIO:
        return STUDIO
    return tuple({template_key, CANAL_DE_SECOURS.get(template_key) or template_key})


def _trouve_ou(club):
    """Comment on est tombe sur eux, dit simplement.

    « Je me permets de vous ecrire » ne dit rien et sonne commercial. Mieux
    vaut l'honnetete : on les a cherches, on le dit.
    """
    ville = (club.get("city") or "").strip()
    return ("Je cherchais les clubs sportifs %s et je suis tombé sur le vôtre."
            % ("de " + ville if ville else "du secteur"))


def _rencontre(club):
    """La phrase d'accroche, adossee a la facon dont le club est arrive."""
    source = util.strip_accents(str(club.get("source") or "")).lower()
    if "forum" in source:
        ou = "forum des sports" if "forum des sports" in source else "forum des associations"
        return u"On s'est crois\u00e9s au %s, je suis le photographe du studio mobile \U0001F4F8" % ou
    if "flyer" in source:
        return "J'ai r\u00e9cup\u00e9r\u00e9 votre flyer."
    if "whatsapp" in source or "groupe" in source:
        return "On a \u00e9chang\u00e9 il y a quelque temps."
    return ""


def _elider(nom):
    """« AS Cannes » -> « d\'AS Cannes » ; « Stade Laurentin » -> « de Stade Laurentin »."""
    nom = (nom or "").strip()
    if not nom:
        return ""
    import unicodedata
    initiale = unicodedata.normalize("NFD", nom[0])[0].lower()
    return ("d'" if initiale in "aeiouyh" else "de ") + nom


def _moi(settings):
    """'Alex de SnapActions', ou juste l'un des deux si l'autre manque."""
    name = (settings.get("my_name") or "").strip()
    org = (settings.get("my_org") or "").strip()
    if name and org:
        return "%s de %s" % (name, org)
    return name or org or ""


def _default_signature(settings):
    bits = [settings.get("my_name") or ""]
    if settings.get("my_org"):
        bits.append(settings["my_org"])
    line2 = " — ".join(x for x in [
        settings.get("my_email"),
        util.format_phone(util.normalize_phone(settings.get("my_phone")))
        or settings.get("my_phone")] if x)
    if line2:
        bits.append(line2)
    return "\n".join(b for b in bits if b)


MOIS_FR = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
           "aout", "septembre", "octobre", "novembre", "decembre"]


JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _fr_date_jour(value):
    """« samedi 5 septembre » — la forme que Julien emploie dans ses mails."""
    d = util.parse_date(value)
    if not d:
        return ""
    return "%s %d %s" % (JOURS_FR[d.weekday()], d.day, MOIS_FR[d.month - 1])


def _fr_date(value):
    d = util.parse_date(value)
    if not d:
        return ""
    return "%d %s %d" % (d.day, MOIS_FR[d.month - 1], d.year)


def _fr_date_range(start, end):
    s, e = util.parse_date(start), util.parse_date(end)
    if not s:
        return ""
    if not e or e == s:
        return "le %s" % _fr_date(s)
    if (s.month, s.year) == (e.month, e.year):
        return "du %d au %d %s %d" % (s.day, e.day, MOIS_FR[s.month - 1], s.year)
    return "du %s au %s" % (_fr_date(s), _fr_date(e))


def _days_until(value):
    d = util.parse_date(value)
    return str((d - util.today()).days) if d else ""


def render(template_key, club=None, contact=None, tournament=None, extra=None):
    tpl = get_template(template_key)
    if not tpl:
        return None
    ctx = build_context(club, contact, tournament, extra)
    subject = (tpl.get("subject") or "").format_map(_SafeDict(ctx)) or None
    body = tpl["body"].format_map(_SafeDict(ctx))
    body = _tidy(body)
    return {"subject": _tidy(subject) if subject else None, "body": body,
            "channel": tpl["channel"], "template_key": template_key}


def _tidy(text):
    """Recolle un texte dont certains champs etaient vides.

    'Bonjour ,' -> 'Bonjour,'   'de .' -> supprime   espaces doubles -> simples
    """
    if not text:
        return text
    # En francais l'espace se garde avant ! ? ; : — on ne recolle que , et .
    text = re.sub(r"[ \t]+([,.])(?=\s|$)", r"\1", text)
    text = re.sub(r"\bde\s+\.", ".", text)              # "de ." laisse par un champ vide
    text = re.sub(r"\(\s*\)", "", text)                  # parentheses vides
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    # Un champ vide en fin de ligne y laisse une espace : invisible a l'ecran,
    # bien reelle dans le message copie.
    text = re.sub(r"[ \t]+(?=\n)", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # « proposer la meme chose a Les Bains Canins » : un nom de club colle apres
    # une preposition ne se contracte pas tout seul, et ca s'entend tout de
    # suite — c'est la phrase d'un robot. On recolle les quatre cas francais.
    text = re.sub(r"\b([àa]) Le(?=\s)", r"au", text)
    text = re.sub(r"\b([àa]) Les(?=\s)", r"aux", text)
    text = re.sub(r"\bde Le(?=\s)", "du", text)
    text = re.sub(r"\bde Les(?=\s)", "des", text)
    # « a L'Olympique » -> « a l'Olympique » : la majuscule de l'article saute,
    # celle du nom reste.
    text = re.sub(r"\b([àa]|de) L['\u2019](?=[A-ZÀ-Þ])", lambda m: m.group(1) + " l\u2019", text)
    return text.strip()


# --------------------------------------------------------------------------
# Cycle de vie des brouillons
# --------------------------------------------------------------------------

def best_contact(club_id):
    """Le meilleur interlocuteur : email personnel > email de structure > telephone."""
    rows = db.rows_to_dicts(db.q(
        "SELECT * FROM contacts WHERE club_id = ? AND do_not_contact = 0", (club_id,)))
    if not rows:
        return None
    def score(c):
        s = 0
        if c.get("email") and not util.is_role_email(c["email"]):
            s += 50
        elif c.get("email"):
            s += 20
        if c.get("phone"):
            s += 10
        if (c.get("role") or "").lower().startswith(("president", "resp", "secretaire")):
            s += 15
        if c.get("first_name"):
            s += 5
        return s
    return sorted(rows, key=score, reverse=True)[0]


# Quand le mail manque mais qu'on a un portable, le message part par WhatsApp.
# La valeur donne le modele a employer a la place ; None signifie « le meme
# texte, simplement envoye autrement ». Pour le tournoi, c'est exactement ce
# qu'il faut : Julien a son message, il ne doit pas exister en deux versions
# qui divergeront le jour ou il en modifie une seule.
CANAL_DE_SECOURS = {
    "tournoi_avant": None,
    "tournoi_adultes": None,
    "tournoi_aviron": None,
    # Memes textes, simplement envoyes par WhatsApp quand le club n'a qu'un
    # portable. Sans ces trois lignes leur brouillon etait jete : cent
    # trente-quatre evenements a venir sont dans ce cas.
    "course_avant": None,
    "gala_avant": None,
    "rassemblement_avant": None,
    "premier_contact": "whatsapp_premier",
    "relance_1": "sms_relance",
    "relance_2": "sms_relance",
    "calendrier_mail": "calendrier_whatsapp",
    "partenaire_relance": "partenaire_whatsapp",
    "studio_club": "studio_whatsapp",
}


# Les categories de jeunes s'ecrivent U6 a U19 ; leur presence est le seul
# signal fiable qu'on s'adresse a des parents plutot qu'aux joueurs eux-memes.
JEUNES = re.compile(r"\bU\s?(?:[6-9]|1[0-9])\b", re.I)


# Ce qui n'est pas un tournoi, et ne doit donc pas recevoir le message des
# « joueurs ». Vu en vrai : le Cross UNSS departemental de Fontmerle recevait
# « nous photographions les joueurs pendant le tournoi » — personne n'y joue,
# il n'y a pas d'equipes, et ce sont des coureurs qu'on prend a l'arrivee.
# Le moteur passe avant la course a pied : « Course de cote de Villecroze »
# et « Meeting Porsche » recevaient le message des coureurs, avec dossards
# et ligne d'arrivee. Ce sont des vehicules, et ce sont leurs proprietaires
# qui achetent la photo.
MOTEUR = re.compile(
    # « circuit » tout court est trop gourmand : le « Circuit Jeune
    # Departemental » est un championnat de badminton. On exige le contexte.
    r"\bauto\b|automobile|\bmoto\b|\bkart|rallye|\bporsche\b|"
    r"circuit\s+(?:auto|moto|du castellet|paul ricard)|"
    r"ferrari|\bbmw\b|alpine|\b4l\b|2cv|vespa|scooter|\bquad\b|"
    r"course de cote|\bdrift|youngtimer|oldtimer|\bvehicule", re.I)

FAMILLES_EVENEMENT = [
    ("course_avant", re.compile(
        r"\bcross\b|\bcourse|\btrail\b|foulee|marathon|semi[- ]marathon|"
        r"\brun\b|running|corrida|\bboucle|kilometre|\b10\s?km\b|\b5\s?km\b|"
        r"duathlon|triathlon|\bronde\b|randonn", re.I)),
    ("gala_avant", re.compile(
        r"\bgala\b|spectacle|demonstration|\bdemo\b|\bshow\b|representation|"
        r"\bdanse|chore|ballet|gymnastique|\bgr\b|rythmique|twirling|"
        r"majorette|\bfete du club\b", re.I)),
    ("rassemblement_avant", re.compile(
        r"rassemblement|\bexpo\b|exposition|\bmeeting\b|bourse d.echange|"
        r"\bretro\b|anciennes|collection|\bclassic\b|concentration", re.I)),
]


def modele_evenement(tournament):
    """La famille d'evenement, quand ce n'est pas un tournoi.

    Rend None si rien ne correspond : modele_tournoi tranche alors comme
    avant, entre jeunes, adultes et aviron.
    """
    t = tournament or {}
    # Le nom et le sport disent ce qu'est l'evenement. La description, elle,
    # parle d'horaires, de buvette et de « remise des prix » — des mots de
    # logistique qui faisaient passer un tournoi de volley pour un gala.
    matiere = util.strip_accents(" ".join(str(t.get(k) or "") for k in (
        "name", "sport", "categories")))
    if MOTEUR.search(matiere):
        return "rassemblement_avant"
    for cle, motif in FAMILLES_EVENEMENT:
        if motif.search(matiere):
            return cle
    return None


def modele_tournoi(tournament):
    """Le modele qui colle au public du tournoi.

    Le message type parle des « jeunes joueurs » et des « parents » : envoye
    a un tournoi de beach-volley 4x4 entre adultes, il tombe a cote et se
    voit. On regarde donc les categories annoncees, puis le nom.
    """
    t = tournament or {}
    # L'aviron d'abord : « Coupe Regionale Jeunes » porte un marqueur U15 et
    # serait parti avec le message des parents de footballeurs.
    if est_nautique_sur_eau(t):
        return "tournoi_aviron"
    # Puis la famille d'evenement : une course, un gala ou un rassemblement
    # ne se demarchent pas avec le vocabulaire du tournoi.
    autre = modele_evenement(t)
    if autre:
        return autre
    indices = " ".join(str(t.get(k) or "") for k in ("categories", "name", "description"))
    if JEUNES.search(indices):
        return "tournoi_avant"
    # « Ouvert a tous », « mixte », « 4x4 » : des formules qui n'existent pas
    # dans le foot des categories jeunes, et qui disent qu'on s'adresse aux
    # joueurs eux-memes.
    if any(m in util.strip_accents(indices).lower()
           for m in ("veteran", "senior", "adulte", "loisir", "corpo",
                     "ouvert a tous", "mixte", "4x4", "3x3", "beach")):
        return "tournoi_adultes"
    # Sans indication, on garde le message historique : c'est celui que Julien
    # a ecrit, et le foot des jeunes reste l'essentiel de la veille.
    return "tournoi_avant"


def _tache_contact_manquant(club, reason=None):
    """Inscrit « trouver un numero » comme prochaine action, sans doublon."""
    deja = db.q1("SELECT id FROM tasks WHERE club_id = ? AND kind = 'contact_manquant' "
                 "AND status = 'pending'", (club["id"],))
    if deja:
        return None
    return repo.add_task(
        club["id"],
        "Trouver un contact chez %s" % club["name"],
        util.today().isoformat(),
        notes=reason,
        kind="contact_manquant")


def create_draft(club_id, template_key, contact_id=None, tournament_id=None,
                 reason=None, extra=None, created_by="agent"):
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (club_id,)))
    if not club or club["do_not_contact"]:
        return None
    contact = repo.get_contact(contact_id) if contact_id else best_contact(club_id)
    if contact and contact.get("do_not_contact"):
        return None
    tournament = None
    if tournament_id:
        tournament = db.row_to_dict(
            db.q1("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)))

    portable = (contact or {}).get("phone") or club.get("phone")
    portable = portable if portable and util.is_mobile_fr(portable) else None
    if template_key in STUDIO and portable and en_discussion(club):
        template_key = "studio_rdv"

    rendered = render(template_key, club, contact, tournament, extra)
    if not rendered:
        return None

    to_addr = None
    if rendered["channel"] in ("sms", "whatsapp"):
        to_addr = (contact or {}).get("phone")
        if not to_addr and template_key == "studio_rdv":
            to_addr = portable
    else:
        to_addr = (contact or {}).get("email") or club.get("email")

    # Pas d'adresse mail, mais un portable : le message doit partir par WhatsApp
    # plutot que d'etre abandonne. C'est le cas d'un flyer, ou le seul contact
    # imprime est un numero — on avait la, et on jetait le brouillon.
    if not to_addr and rendered["channel"] not in ("sms", "whatsapp"):
        portable = (contact or {}).get("phone") or club.get("phone")
        if portable and util.is_mobile_fr(portable) \
                and template_key in CANAL_DE_SECOURS:
            secours = CANAL_DE_SECOURS[template_key]
            if secours is None:
                rendered = dict(rendered, channel="whatsapp")
                to_addr = portable
            else:
                autre = render(secours, club, contact, tournament, extra)
                if autre:
                    rendered, template_key, to_addr = autre, secours, portable

    # Un brouillon qu'on ne peut envoyer a personne n'est pas du travail, c'est
    # de l'encombrement : il occupe la pile de relecture et n'aboutit jamais.
    # A la place on inscrit la vraie tache suivante — retrouver le contact.
    if not to_addr and created_by == "agent":
        _tache_contact_manquant(club, reason)
        return None

    now = util.now_iso()
    draft_id = db.insert("drafts", {
        "club_id": club_id,
        "contact_id": (contact or {}).get("id"),
        "tournament_id": tournament_id,
        "channel": rendered["channel"],
        "to_addr": to_addr,
        "subject": rendered["subject"],
        "body": rendered["body"],
        "template_key": template_key,
        "reason": reason,
        "status": "draft",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    })
    return get_draft(draft_id)


def get_draft(draft_id):
    row = db.q1(
        "SELECT d.*, c.name AS club_name, c.dept AS club_dept, c.city AS club_city, "
        "ct.first_name, ct.last_name, ct.role AS contact_role, ct.phone AS contact_phone, "
        "t.name AS tournament_name, t.start_date AS tournament_start "
        "FROM drafts d LEFT JOIN clubs c ON c.id=d.club_id "
        "LEFT JOIN contacts ct ON ct.id=d.contact_id "
        "LEFT JOIN tournaments t ON t.id=d.tournament_id WHERE d.id = ?", (draft_id,))
    if not row:
        return None
    draft = dict(row)
    draft["mailto"] = _mailto(draft)
    draft["whatsapp"] = _whatsapp_link(draft)
    return draft


def list_drafts(status="draft", limit=300):
    where = "1=1" if status in (None, "", "all") else "d.status = ?"
    params = () if where == "1=1" else (status,)
    rows = db.rows_to_dicts(db.q(
        "SELECT d.*, c.name AS club_name, c.dept AS club_dept, "
        "ct.first_name, ct.last_name, ct.phone AS contact_phone, "
        "t.name AS tournament_name, t.start_date AS tournament_start "
        "FROM drafts d LEFT JOIN clubs c ON c.id=d.club_id "
        "LEFT JOIN contacts ct ON ct.id=d.contact_id "
        "LEFT JOIN tournaments t ON t.id=d.tournament_id "
        "WHERE %s ORDER BY d.created_at DESC LIMIT ?" % where, params + (limit,)))
    for r in rows:
        r["mailto"] = _mailto(r)
        r["whatsapp"] = _whatsapp_link(r)
    return rows


def _whatsapp_link(draft):
    """Lien wa.me : ouvre WhatsApp avec le message pre-rempli.

    Rien n'est envoye — c'est l'utilisateur qui appuie sur Envoyer dans
    WhatsApp. Pas d'API, pas de compte business, pas de risque de bannissement.
    """
    import urllib.parse
    number = draft.get("contact_phone") or draft.get("to_addr")
    number = util.normalize_phone(number)
    if not number or not util.is_mobile_fr(number):
        return None
    body = draft.get("body") or ""
    if draft.get("subject") and draft.get("channel") == "email":
        body = "%s\n\n%s" % (draft["subject"], body)
    return "https://wa.me/%s?text=%s" % (number.lstrip("+"),
                                         urllib.parse.quote(body[:1500]))


def _mailto(draft):
    import urllib.parse
    if draft.get("channel") != "email" or not draft.get("to_addr"):
        return None
    params = urllib.parse.urlencode(
        {"subject": draft.get("subject") or "", "body": draft.get("body") or ""},
        quote_via=urllib.parse.quote)
    return "mailto:%s?%s" % (draft["to_addr"], params)


def recoller_destinataires():
    """Rend envoyables les brouillons ecrits quand le club etait injoignable.

    Cent quarante-cinq messages attendaient sans destinataire : le brouillon
    avait ete redige avant qu'on ne retrouve le telephone de l'organisateur.
    On ne change que l'adresse, jamais le texte deja relu.
    """
    lignes = db.q(
        "SELECT d.id, d.channel, c.phone AS club_phone, c.email AS club_email, "
        "       ct.phone AS contact_phone, ct.email AS contact_email "
        "FROM drafts d JOIN clubs c ON c.id = d.club_id "
        "LEFT JOIN contacts ct ON ct.id = d.contact_id "
        "WHERE d.status = 'draft' AND COALESCE(d.to_addr, '') = ''")
    faits = 0
    for r in lignes:
        if (r["channel"] or "") in ("sms", "whatsapp"):
            adresse = r["contact_phone"] or r["club_phone"]
        else:
            adresse = r["contact_email"] or r["club_email"] or r["club_phone"]
        if not adresse:
            continue
        if "@" not in adresse:
            # Format international : c'est ce qu'attend le lien wa.me.
            adresse = util.normalize_phone(adresse) or adresse
        patch = {"to_addr": adresse, "updated_at": util.now_iso()}
        # Un telephone dans un brouillon email : c'est WhatsApp qu'il faut.
        if "@" not in adresse and (r["channel"] or "") not in ("sms", "whatsapp"):
            patch["channel"] = "whatsapp"
        db.update("drafts", r["id"], patch)
        faits += 1
    if faits:
        db.log("brouillons", "%d brouillon(s) ont retrouve un destinataire" % faits)
    return faits


def update_draft(draft_id, subject=None, body=None, to_addr=None, review_note=None):
    patch = {"updated_at": util.now_iso()}
    if subject is not None:
        patch["subject"] = subject
    if body is not None:
        patch["body"] = body
    if to_addr is not None:
        patch["to_addr"] = to_addr
    if review_note is not None:
        patch["review_note"] = review_note
    db.update("drafts", draft_id, patch)
    return get_draft(draft_id)


def approve(draft_id, note=None):
    """Validation humaine. N'envoie rien : rend le brouillon pret a partir."""
    draft = get_draft(draft_id)
    if not draft or draft["status"] not in ("draft", "rejected", ABANDON):
        return draft
    db.update("drafts", draft_id, {"status": "approved", "approved_at": util.now_iso(),
                                   "review_note": note, "updated_at": util.now_iso()})
    return get_draft(draft_id)


def reject(draft_id, note=None):
    db.update("drafts", draft_id, {"status": "rejected", "review_note": note,
                                   "updated_at": util.now_iso()})
    return get_draft(draft_id)


# Un brouillon ecrit il y a deux mois et jamais envoye ne partira pas. Il
# reste pourtant dans tous les compteurs, et on finit par ne plus regarder
# la pile du tout. « abandonne » le sort de la vue sans rien effacer :
# la ligne est intacte, on peut la reprendre.
#
# Pourquoi pas « rejected » : deux endroits de ce fichier verifient
# « s'il existe deja un brouillon non rejete pour ce club et ce tournoi,
# n'en cree pas un autre ». Marquer rejete rouvrirait donc la porte a la
# veille, qui en recreerait aussitot trois cent soixante-quinze.
ABANDON = "abandonne"


def abandonner_les_vieux(jours=14, pour_de_vrai=False):
    """Range les brouillons jamais partis, plus vieux que `jours`.

    Sans `pour_de_vrai`, ne touche a rien et rend seulement le compte : on
    regarde avant de ranger.
    """
    ou = ("(sent_at IS NULL OR sent_at = '') "
          "AND status IN ('draft', 'approved') "
          "AND date(created_at) < date('now', ?)")
    arg = ("-%d day" % int(jours),)
    combien = db.q1("SELECT COUNT(*) AS n FROM drafts WHERE " + ou, arg)["n"]
    plus_vieux = db.q1("SELECT MIN(date(created_at)) AS d FROM drafts WHERE " + ou,
                       arg)["d"]
    if not pour_de_vrai or not combien:
        return {"combien": combien, "plus_vieux": plus_vieux, "fait": False}
    db.ex("UPDATE drafts SET status = ?, review_note = ?, updated_at = ? "
          "WHERE " + ou,
          (ABANDON, "rangé automatiquement : plus de %d jours sans partir" % jours,
           util.now_iso()) + arg)
    db.log("drafts", "%d brouillon(s) de plus de %d jours rangés" % (combien, jours))
    return {"combien": combien, "plus_vieux": plus_vieux, "fait": True}


def reprendre(draft_id):
    """Remet un brouillon range dans la pile."""
    draft = get_draft(draft_id)
    if not draft or draft["status"] != ABANDON:
        return draft
    db.update("drafts", draft_id, {"status": "draft", "review_note": None,
                                   "updated_at": util.now_iso()})
    return get_draft(draft_id)


def mark_sent(draft_id, schedule_followups=True):
    """A appeler apres un envoi reel, fait a la main. Trace l'echange et programme
    les relances. Exige que le brouillon ait ete valide au prealable."""
    draft = get_draft(draft_id)
    if not draft:
        return None
    if draft["status"] != "approved":
        return {"error": "Le brouillon doit etre valide avant d'etre marque envoye.",
                "draft": draft}
    now = util.now_iso()
    db.update("drafts", draft_id, {"status": "sent", "sent_at": now, "updated_at": now})
    repo.add_interaction(draft["club_id"], kind=draft["channel"], direction="out",
                         subject=draft.get("subject") or draft.get("template_key"),
                         body=draft.get("body"), contact_id=draft.get("contact_id"))
    club = db.q1("SELECT status FROM clubs WHERE id = ?", (draft["club_id"],))
    if club and club["status"] == "prospect":
        db.update("clubs", draft["club_id"], {"status": "contacte", "updated_at": now})
    # La tache de relance qui a produit ce brouillon est soldee.
    db.ex("UPDATE tasks SET status='done', done_at=?, updated_at=? WHERE draft_id=?",
          (now, now, draft_id))
    if schedule_followups:
        repo.schedule_sequence(draft["club_id"], draft.get("contact_id"))
    return get_draft(draft_id)


# --------------------------------------------------------------------------
# Moteur de rapprochement tournoi -> club
# --------------------------------------------------------------------------

def matching_clubs(tournament, radius=None, limit=25):
    """Clubs a demarcher pour un tournoi : proximite geographique + statut actif."""
    radius = radius or db.get_setting("match_radius", "neighbours")

    # Sans discipline connue, on n'invite personne. Le filtre sur le sport
    # n'etait pose QUE si la fiche en portait un ; soixante-deux fiches n'en
    # portent pas, et pour celles-la la requete rendait tous les clubs du
    # departement, toutes disciplines confondues. La Regate Trophee d'Automne,
    # a Antibes, a ainsi ete proposee a un club de basket, un club d'escrime
    # et un club de tennis.
    #
    # C'est d'ailleurs la bonne reponse pour les rassemblements — carnavals,
    # brocantes, meetings auto — qui arrivent sans sport : ils se vendent aux
    # gens presents, pas aux clubs voisins. L'organisateur, lui, recoit son
    # message : il est traite a part dans generate_for_tournament.
    if not (tournament.get("sport") or "").strip():
        return []

    dept = tournament.get("dept") or util.dept_from_postal(tournament.get("postal_code"))
    if radius == "same_dept":
        depts = [dept] if dept else []
    elif radius == "neighbours":
        depts = util.nearby_depts(dept)
    else:
        depts = []

    params = []
    where = ["c.do_not_contact = 0", "c.status IN ('prospect','contacte','repondu','dormant')"]
    if depts:
        where.append("c.dept IN (%s)" % ",".join("?" for _ in depts))
        params += depts
    where.append("(c.sport IS NULL OR c.sport = '' OR c.sport = ?)")
    params.append(tournament["sport"])
    # On ne re-sollicite pas un club deja relance sur ce meme tournoi.
    where.append("NOT EXISTS (SELECT 1 FROM drafts d WHERE d.club_id = c.id "
                 "AND d.tournament_id = ? AND d.status != 'rejected')")
    params.append(tournament.get("id"))
    # Ni un club contacte tres recemment.
    where.append("(c.last_contact_at IS NULL OR "
                 "date(c.last_contact_at) < date('now','-14 day'))")

    rows = db.rows_to_dicts(db.q(
        "SELECT c.* FROM clubs c WHERE %s "
        "ORDER BY (c.dept = ?) DESC, c.priority ASC, c.updated_at DESC LIMIT ?"
        % " AND ".join(where), tuple(params) + (dept or "", limit)))
    for r in rows:
        r["match_reason"] = _match_reason(r, tournament, dept)
    return rows


def _match_reason(club, tournament, dept):
    if club.get("dept") and dept and club["dept"] == dept:
        return "même département (%s)" % util.dept_name(dept)
    if club.get("dept"):
        return "département limitrophe (%s)" % util.dept_name(club["dept"])
    return "correspondance large"


def in_lead_window(tournament):
    """Le tournoi est-il dans la bonne fenetre pour declencher un message ?"""
    start = util.parse_date(tournament.get("start_date"))
    if not start:
        return False, "date inconnue"
    days = (start - util.today()).days
    lo = int(db.get_setting("lead_days_min", "10") or 10)
    hi = int(db.get_setting("lead_days_max", "75") or 75)
    if days < lo:
        return False, "trop proche (J-%d, seuil %d)" % (days, lo)
    if days > hi:
        return False, "trop lointain (J-%d, seuil %d)" % (days, hi)
    return True, "J-%d" % days


def generate_for_tournament(tournament_id, radius=None, limit=None, dry_run=False):
    """Cree un brouillon par club pertinent. Les brouillons restent en attente
    de relecture : aucun envoi n'est declenche ici."""
    tournament = db.row_to_dict(
        db.q1("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)))
    if not tournament:
        return {"ok": False, "error": "tournoi introuvable"}
    # Le bouton « Preparer les messages » n'a jamais regarde la distance : il a
    # ecrit a des clubs pour des tournois a 1 200 km, en Bretagne et dans le
    # Nord. La generation automatique, elle, filtrait deja — pas celle-ci.
    if not dry_run and not (radius == "force" or limit == "force") \
            and tournament.get("distance_verdict") == "hors zone":
        return {"ok": False, "tournament": tournament,
                "error": "hors zone : %s est à %s km (%s h de route). Forcez "
                         "depuis la fiche si vous y allez quand même."
                         % (tournament.get("city") or "ce lieu",
                            tournament.get("distance_km"),
                            round((tournament.get("trajet_min") or 0) / 60.0, 1))}

    if tournament.get("lieu_verifie") == 0 and not dry_run \
            and not (radius == "force" or limit == "force"):
        return {"ok": False, "tournament": tournament,
                "error": "lieu a confirmer : %s n'est corrobore ni par un code "
                         "postal ni par le texte de la fiche. Corrigez le lieu, "
                         "ou forcez depuis la fiche." % (tournament.get("city") or "le lieu")}
    ok, why = in_lead_window(tournament)
    if not ok and not dry_run:
        return {"ok": False, "error": "hors fenetre de contact : %s" % why,
                "tournament": tournament}
    if limit:
        plafond = int(limit)
    else:
        try:
            plafond = int(db.get_setting("max_brouillons_tournoi", "6"))
        except (TypeError, ValueError):
            plafond = 6
    clubs = matching_clubs(tournament, radius=radius, limit=plafond)
    if dry_run:
        return {"ok": True, "dry_run": True, "window": why, "in_window": ok,
                "tournament": tournament, "clubs": clubs, "n": len(clubs)}
    created = []

    # L'organisateur d'abord. C'est lui qui decide si une equipe photo vient,
    # et c'est son numero qui est imprime sur l'affiche — pourtant il ne
    # recevait rien : on n'ecrivait qu'aux clubs des environs, susceptibles
    # d'y participer. On voyait donc « pas encore de message » sur une fiche
    # dont le telephone etait affiche juste au-dessus.
    hote = tournament.get("club_id")
    if hote and not db.q1("SELECT id FROM drafts WHERE club_id = ? AND tournament_id = ? "
                          "AND status != 'rejected'", (hote, tournament_id)):
        draft = create_draft(hote, modele_tournoi(tournament),
                             tournament_id=tournament_id,
                             reason="%s — organisateur" % tournament["name"])
        if draft:
            created.append(draft)

    for club in clubs:
        if club["id"] == hote:
            continue
        draft = create_draft(club["id"], modele_tournoi(tournament),
                             tournament_id=tournament_id,
                             reason="%s — %s" % (tournament["name"],
                                                  club["match_reason"]))
        if draft:
            created.append(draft)
    db.update("tournaments", tournament_id,
              {"status": "processed", "updated_at": util.now_iso()})
    db.log("draft", "Tournoi %s : %d brouillons crees" % (tournament["name"], len(created)))
    return {"ok": True, "n": len(created), "drafts": created, "window": why}


def generate_for_new_tournaments(limit_tournaments=10):
    """Balaye les tournois 'new' dans la fenetre de contact et prepare les brouillons.

    On n'ecrit tout seul que pour ce qui est faisable dans la journee. Un tournoi
    a cinq heures de route peut valoir le deplacement, mais c'est un arbitrage —
    il attend le bouton « Preparer les messages » plutot que d'inonder la pile.
    """
    if db.get_setting("auto_draft", "1") != "1":
        return {"ok": False, "error": "generation automatique desactivee dans les reglages"}
    # Un lieu qu'on n'a pas su situer n'est pas un lieu proche : c'est un lieu
    # inconnu. La nuance a coute cher — un tournoi a Agadir, faute de trajet
    # calculable, avait declenche des messages vers des clubs de Rouen et de
    # Toulouse. On n'ecrit donc que pour ce qu'on sait atteignable, ou pour
    # ce qui est a la maison.
    maison = [d.strip() for d in
              (db.get_setting("depts_maison", "06,83") or "").split(",") if d.strip()]
    trous = ",".join("?" for _ in maison) or "''"
    # Un lieu que rien ne corrobore ne declenche pas de message : c'est ainsi
    # qu'un tournoi de Vedene, entre en base comme Vidauban, a fait ecrire a un
    # club a 231 km en croyant qu'il etait a 65.
    rows = db.q("SELECT id, name, categories, description FROM tournaments "
                "WHERE status = 'new' "
                "AND start_date IS NOT NULL AND date(start_date) >= date('now') "
                "AND COALESCE(lieu_verifie, 1) = 1 "
                "AND (distance_verdict IN ('proche','correct') OR dept IN (%s)) "
                "ORDER BY start_date LIMIT ?" % trous,
                tuple(maison) + (limit_tournaments * 3,))

    # Une ligne d'agenda dont on ne sait pas si c'est une competition ne
    # declenche pas de demarchage. Le premier scan des agendas de clubs a
    # ecrit a six clubs de football au sujet d'une randonnee pedestre : le
    # titre « Le Thoronet - Le chemin des moines » ne disait ni l'un ni
    # l'autre, et le doute avait ete tranche dans le mauvais sens.
    from . import sources
    retenus = [r for r in rows if sources.vaut_le_deplacement(dict(r)) is True]
    rows = retenus[:limit_tournaments]
    total, touched = 0, []
    for row in rows:
        result = generate_for_tournament(row["id"])
        if result.get("ok"):
            total += result["n"]
            touched.append(row["id"])
    return {"ok": True, "n_drafts": total, "n_tournaments": len(touched)}


def generate_for_due_tasks(horizon_days=0):
    """Prepare un brouillon de relance pour chaque tache echue qui n'en a pas."""
    tasks = repo.list_tasks(status="pending", horizon_days=horizon_days)
    created = []
    for task in tasks:
        if task.get("draft_id"):
            continue
        step = task.get("step") or 1
        key = "relance_1" if step <= 1 else "relance_2"
        draft = create_draft(task["club_id"], key, contact_id=task.get("contact_id"),
                             reason="Relance programmee — %s" % task["title"])
        if draft:
            db.update("tasks", task["id"], {"draft_id": draft["id"],
                                            "updated_at": util.now_iso()})
            created.append(draft)
    return {"ok": True, "n": len(created), "drafts": created}
