# -*- coding: utf-8 -*-
"""Demander a Google OU chercher le numero d'un club — jamais lequel c'est.

numeros.py explique pourquoi on n'utilise pas de modele de langue pour
produire un numero : il en fabrique un plausible quand il ne sait pas, et
trois numeros inventes ont deja fini dans cette base. Cette regle tient.

Ce module occupe l'autre moitie du probleme. Nos pistes partent toujours de
ce qu'on sait deja — un site, un mail, l'annonce. Pour un club dont on ne
connait que le nom et la ville, il n'y a aucune piste, donc rien a lire. Un
moteur de recherche, lui, sait trouver cette page.

Gemini ne rend donc que des **adresses**, avec les sources que sa recherche a
reellement citees. Les chiffres, eux, continuent d'etre lus sur la page par
numeros.py. Le modele n'ecrit jamais un numero, il dit ou regarder.

Ce qui sort du Mac : le nom et la ville du club. Rien d'autre.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import db, ia, util

API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
MODELE_DEFAUT = "gemini-3.8-flash"
# La recherche ancree de Google met 40 a 55 secondes pour un club : elle
# interroge le web, lit les pages, puis redige. A 60 s on passait juste, et
# un appel sur deux tombait en « Google injoignable ».
DELAI = 200

CONSIGNE = """Tu aides a retrouver les pages officielles d'un club de sport francais.

Club : %(nom)s
Ville : %(ville)s
Sport : %(sport)s

Cherche sur le web les pages ou les coordonnees de CE club sont publiees :
site officiel, page contact, page Facebook, fiche d'annuaire associatif,
page de la federation. Mefie-toi des homonymes : un club du meme nom dans une
autre ville n'est pas le bon.

Ne recopie AUCUN numero de telephone et AUCUNE adresse e-mail. On les lira
nous-memes sur les pages. Donne seulement les adresses web.

Reponds UNIQUEMENT par un objet JSON, sans texte autour :
{"pages": [{"url": "", "quoi": ""}], "nom_officiel": "", "ville_confirmee": ""}

- Cinq pages au maximum, la plus fiable en premier.
- "quoi" : deux ou trois mots disant ce que c'est ("site officiel",
  "page Facebook", "annuaire des associations").
- Si tu ne trouves rien de sur, rends une liste vide.
"""


class Epuise(Exception):
    """Le quota ou le plafond de depenses est atteint.

    Ce n'est pas un echec de la recherche : c'est une porte fermee. On
    arrete le passage au lieu d'user les essais des clubs suivants.
    """


class Indisponible(Exception):
    """Pas de cle, ou l'API Google ne repond pas."""


def cle():
    return (db.get_setting("gemini_cle", "") or "").strip()


def modele():
    return (db.get_setting("gemini_modele", "") or "").strip() or MODELE_DEFAUT


def disponible():
    """La recherche Google est-elle utilisable aujourd'hui ?

    Un plafond a zero n'est pas une panne : c'est un choix. Les veilleurs
    continuent de tourner et font tout le travail gratuitement — ce qu'ils
    faisaient deja pour la totalite des resultats obtenus.
    """
    return bool(cle()) and par_jour() > 0


def etat():
    k = cle()
    coupee = bool(k) and par_jour() <= 0
    return {"configure": bool(k), "modele": modele(), "coupee": coupee,
            "par_jour": par_jour(),
            "cle_apercu": ("…" + k[-4:]) if len(k) > 4 else "",
            "dit": ("Recherche Google coupée (plafond à 0). La veille lit les "
                    "pages et les annonces gratuitement, comme avant."
                    if coupee else
                    (None if k else "Collez une cle Gemini dans Reglages "
                                    "(aistudio.google.com, gratuit jusqu'a un quota)."))}


def _url_propre(valeur):
    """Une adresse http(s) utilisable, ou rien."""
    v = (valeur or "").strip()
    if not v:
        return None
    if not v.startswith(("http://", "https://")):
        v = "https://" + v.lstrip("/")
    m = urllib.parse.urlsplit(v)
    if m.scheme not in ("http", "https") or "." not in m.netloc:
        return None
    return urllib.parse.urlunsplit(m)[:400]


def _json_du_texte(texte):
    brut = re.sub(r"^```(?:json)?|```$", "", (texte or "").strip(), flags=re.M).strip()
    debut, fin = brut.find("{"), brut.rfind("}")
    if debut < 0 or fin <= debut:
        return {}
    try:
        return json.loads(brut[debut:fin + 1])
    except ValueError:
        return {}


def _appel(invite):
    k = cle()
    if not k:
        raise Indisponible("aucune cle Gemini enregistree")
    # Chaque appel ancre coute quelques centimes : on compte, et on s'arrete.
    if budget_restant() <= 0:
        raise Epuise("plafond du jour atteint (%d recherches Google)" % par_jour())
    _consommer()
    corps = json.dumps({
        "contents": [{"parts": [{"text": invite}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0},
    }).encode("utf-8")
    url = (API % modele()) + "?" + urllib.parse.urlencode({"key": k})
    req = urllib.request.Request(url, data=corps,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=DELAI) as rep:
            data = json.loads(rep.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        # Un quota depasse n'est pas une panne passagere : reessayer ne sert
        # a rien et brule les essais. Cent un clubs ont ete ecartes a tort
        # parce qu'on comptait un « spend cap » comme un timeout.
        if exc.code in (403, 429) or "quota" in detail.lower() \
                or "spend cap" in detail.lower():
            raise Epuise("Google ne repond plus : quota ou plafond atteint")
        raise Indisponible("Google a refuse (%s) : %s" % (exc.code, detail))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Indisponible("Google injoignable : %s" % exc)

    bouts, cites = [], []
    for cand in data.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            if part.get("text"):
                bouts.append(part["text"])
        for bloc in (cand.get("groundingMetadata") or {}).get("groundingChunks", []):
            web = bloc.get("web") or {}
            if web.get("uri"):
                cites.append((web["uri"], web.get("title") or "page citée par Google"))
    return "\n".join(bouts), cites


def pages_du_club(club):
    """Rend [(url, d'ou ca vient)] : des adresses a lire, rien de plus.

    Les pages que la recherche a reellement citees sont ajoutees apres celles
    que le modele met en avant : si son JSON derape, il reste les citations.
    """
    texte, cites = _appel(CONSIGNE % {
        "nom": (club.get("name") or "").strip(),
        "ville": (club.get("city") or "").strip(),
        "sport": (club.get("sport") or "non precise").strip(),
    })
    lu = _json_du_texte(texte)

    sortie, vus = [], set()

    def poser(url, dit):
        propre = _url_propre(url)
        if propre and propre not in vus:
            vus.add(propre)
            sortie.append((propre, dit))

    for page in (lu.get("pages") or [])[:5]:
        if isinstance(page, dict):
            quoi = str(page.get("quoi") or "trouvé par Google").strip()[:60]
            poser(page.get("url"), "Google : %s" % quoi)
    for url, titre in cites[:6]:
        poser(url, "Google a cité : %s" % str(titre)[:60])

    return {
        "pages": sortie,
        "nom_officiel": (lu.get("nom_officiel") or "").strip() or None,
        "ville_confirmee": (lu.get("ville_confirmee") or "").strip() or None,
    }


def verifier_ville(club, ville_dite):
    """Dit si la ville trouvee contredit celle de la fiche — signe d'homonyme."""
    ville = (club.get("city") or "").strip()
    if not ville or not ville_dite:
        return None
    if util.norm_key(ville) == util.norm_key(ville_dite):
        return None
    return "Google parle de « %s », la fiche dit « %s » : vérifiez que c'est " \
           "bien le même club." % (ville_dite, ville)


CONSIGNE_ORGA = """Tu cherches QUI ORGANISE un evenement sportif francais.

Evenement : %(nom)s
Ville : %(ville)s
Date : %(date)s
Sport : %(sport)s

Cherche sur le web, puis rends UNIQUEMENT un objet JSON :
{"organisateur": "...", "pages": [{"url": "...", "quoi": "..."}], "pourquoi": "..."}

- "organisateur" : le nom du CLUB, de l'association ou du comite qui
  organise. Jamais le nom de l'evenement lui-meme, jamais une mairie seule,
  jamais un annuaire, jamais une billetterie, jamais une federation
  nationale quand un club local organise.
- Si la recherche ne le dit pas clairement, mets null. N'invente pas un nom
  plausible : un club qui n'existe pas coute plus cher qu'une case vide.
- "pages" : les adresses ou tu l'as lu, deux ou trois au plus.
- "pourquoi" : ou tu l'as lu, en cinq mots.

Ne rends aucun numero de telephone ni aucune adresse mail : ils sont lus
ailleurs, sur les pages elles-memes."""


def organisateur_du_tournoi(tournoi):
    """Qui organise cet evenement, et ou on l'a lu.

    Rend {organisateur, pages, pourquoi}. L'organisateur peut etre None :
    c'est une reponse, pas un echec. Un nom invente ferait une fiche club
    fantome, et on en a deja nettoye assez.
    """
    t = tournoi or {}
    texte, cites = _appel(CONSIGNE_ORGA % {
        "nom": t.get("name") or "",
        "ville": t.get("city") or t.get("venue") or "",
        "date": (t.get("start_date") or "")[:10],
        "sport": t.get("sport") or "",
    })
    d = _json_du_texte(texte) or {}
    nom = (d.get("organisateur") or "").strip()
    if nom.lower() in ("null", "none", "inconnu", ""):
        nom = ""
    pages = []
    for p in (d.get("pages") or [])[:4]:
        u = _url_propre((p or {}).get("url"))
        if u:
            pages.append((u, "Google : %s" % ((p.get("quoi") or "page citée")[:40])))
    vus = {u for u, _ in pages}
    for u, titre in cites:
        if u not in vus:
            pages.append((u, titre))
    return {"organisateur": nom or None, "pages": pages,
            "pourquoi": (d.get("pourquoi") or "")[:120]}


# -- Le budget ---------------------------------------------------------------
#
# La recherche ancree de Google se facture a la requete, pas au jeton :
# quelques centimes a chaque appel. Dix euros sont partis en une apres-midi
# parce qu'on interrogeait Google pour des clubs dont la page etait deja
# lisible gratuitement. Le plafond est la pour que ca ne recommence pas.

PAR_JOUR_DEFAUT = 25


def par_jour():
    try:
        return max(0, int(db.get_setting("google_par_jour", PAR_JOUR_DEFAUT)))
    except (TypeError, ValueError):
        return PAR_JOUR_DEFAUT


def consommation():
    """(date, nombre d'appels ancres faits aujourd'hui)."""
    brut = (db.get_setting("google_consomme", "") or "").strip()
    jour, _, n = brut.partition(":")
    auj = util.today().isoformat()
    if jour != auj:
        return auj, 0
    try:
        return auj, int(n)
    except ValueError:
        return auj, 0


def budget_restant():
    _, fait = consommation()
    return max(0, par_jour() - fait)


def _consommer():
    auj, fait = consommation()
    db.set_setting("google_consomme", "%s:%d" % (auj, fait + 1))


# -- La recherche libre, en francais -----------------------------------------
#
# « le club de natation qu'on a rencontre au forum », « judo a Vence ».
# cerveau.demander() s'appuie la-dessus pour repondre par une phrase ; la page
# Chercher l'appelle pour tout ce qui n'est ni un numero ni un mail.
#
# Tout se lit DANS LA BASE. Google n'est pas interroge ici : la recherche
# ancree se facture a la requete, et une question tapee dans une barre n'a
# pas a couter quelques centimes. Les liens rendus sont des adresses de
# recherche a ouvrir soi-meme, pas des appels.

VIDES = {
    "a", "au", "aux", "avec", "ce", "ces", "cette", "chez", "club", "clubs",
    "dans", "de", "des", "du", "elle", "en", "est", "et", "il", "je", "la",
    "le", "les", "leur", "ma", "me", "mes", "moi", "mon", "ne", "nos", "notre",
    "on", "ou", "par", "pas", "pour", "qu", "que", "qui", "quoi", "sa", "se",
    "ses", "son", "sur", "ta", "tes", "toi", "ton", "tous", "toute", "toutes",
    "tu", "un", "une", "vos", "votre", "vous", "y",
    # Les verbes de la demande elle-meme : ils disent ce qu'on veut faire,
    # pas ce qu'on cherche. « trouve-moi le judo » cherche « judo ».
    "appeler", "cherche", "chercher", "connais", "contacte", "contacter",
    "demarcher", "dis", "donne", "ecrit", "ecrire", "est-ce", "fait",
    "quel", "quelle", "quels", "quelles", "rappelle", "relance", "relancer",
    "sais", "trouve", "trouver", "veux", "voir",
}
MEMOIRE_MAX = 14
DATES_MAX = 12


def _plat(texte):
    return util.strip_accents(str(texte or "")).lower()


def _mots(texte):
    return [m for m in re.findall(r"[a-z0-9]{2,}", _plat(texte)) if m not in VIDES]


def _dedans(mots, *champs):
    """Combien des mots tapes figurent dans ces champs."""
    plat = _plat(" ".join(str(c or "") for c in champs))
    return sum(1 for m in mots if m in plat)


def _extrait(texte, mots, largeur=110):
    """Le bout de texte ou le mot apparait, pour qu'on reconnaisse l'echange."""
    if not texte:
        return None
    plat = _plat(texte)
    for m in mots:
        i = plat.find(m)
        if i < 0:
            continue
        a = max(0, i - largeur // 3)
        bout = str(texte)[a:a + largeur].strip()
        return ("…" if a else "") + bout + ("…" if a + largeur < len(str(texte)) else "")
    return None


def _villes_connues():
    """Les villes qui existent vraiment dans la base, les plus longues d'abord.

    On ne devine pas une ville : on reconnait celles qu'on a deja en fiche.
    « Vence » ne doit pas se faire avaler par « Villeneuve-Loubet ».
    """
    vues = {}
    for table in ("clubs", "tournaments"):
        for r in db.q("SELECT DISTINCT city FROM %s WHERE city IS NOT NULL "
                      "AND city <> ''" % table):
            nom = (r["city"] or "").strip()
            if nom:
                vues.setdefault(_plat(nom), nom)
    return sorted(vues.items(), key=lambda kv: -len(kv[0]))


def _comprendre(question, soucis):
    """La phrase mise a plat : son sujet, sa ville, son departement, ses mots.

    Le modele local sert a degager les mots-cles, rien de plus. S'il est
    eteint, on decoupe la phrase nous-memes : la recherche marche toujours,
    elle est seulement moins fine. Aucun nom de club ne sort d'ici.
    """
    plat = _plat(question)

    ville = None
    for cle, nom in _villes_connues():
        if len(cle) >= 4 and re.search(r"\b%s\b" % re.escape(cle), plat):
            ville = nom
            break

    dept = None
    code = util.extract_postal(question)
    if code:
        dept = util.dept_from_postal(code)
    else:
        m = re.search(r"\b(?:dans le |departement )(0[1-9]|[1-8]\d|9[0-5]|2[ab])\b", plat)
        if m:
            dept = m.group(1).upper()

    mots, par_ia = _mots(question), False
    try:
        if ia.disponible():
            lu = ia.comprendre(question)
            if lu and lu.get("mots"):
                # On garde les mots du modele ET ceux de la phrase : il resume
                # parfois trop, et un nom propre perdu est une fiche perdue.
                for m in _mots(" ".join(lu["mots"])):
                    if m not in mots:
                        mots.append(m)
                par_ia = True
    except Exception as exc:                      # le modele local, jamais bloquant
        soucis.append("Le modèle local n'a pas pu lire la demande : %s" % exc)

    if ville:
        for m in _mots(ville):
            if m in mots:
                mots.remove(m)

    return {"ia": par_ia, "sujet": (question or "").strip()[:120],
            "ville": ville, "dept": dept, "mots": mots[:10]}


def _personnes(club_id, mots):
    sortie, score = [], 0
    for c in db.q("SELECT * FROM contacts WHERE club_id = ?", (club_id,)):
        c = db.row_to_dict(c)
        nom = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x).strip()
        score += _dedans(mots, nom, c.get("role"), c.get("notes"))
        if c.get("phone") or c.get("email") or nom:
            sortie.append({"nom": nom or None, "role": c.get("role"),
                           "telephone": c.get("phone") or c.get("phone2"),
                           "email": c.get("email")})
    return sortie[:6], score


def _derniers_messages(chat):
    try:
        msgs = [m for m in json.loads(chat.get("derniers") or "[]")
                if isinstance(m, dict)]
    except (ValueError, TypeError):
        return []
    return msgs


def _carte_chat(chat, mots):
    """La conversation telle que la carte l'affiche : qui, quand, quoi."""
    msgs = _derniers_messages(chat)
    dernier = None
    if msgs:
        m = msgs[-1]
        dernier = {"quand": m.get("quand") or chat.get("dernier_le"),
                   "de_moi": bool(m.get("de_moi")),
                   "texte": _bout_message(m.get("texte"))}
    return {"nom": chat.get("nom"), "telephone": chat.get("phone"),
            "groupe": bool(chat.get("groupe")),
            "dernier_le": chat.get("dernier_le"), "dernier": dernier,
            "extrait": _extrait(" ".join(str(m.get("texte") or "") for m in msgs), mots)}


def _bout_message(texte, maxi=260):
    return re.sub(r"\s+", " ", str(texte or "")).strip()[:maxi]


def _dernier_echange(club_id):
    r = db.q1("SELECT kind, subject, occurred_at FROM interactions "
              "WHERE club_id = ? ORDER BY COALESCE(occurred_at, created_at) DESC "
              "LIMIT 1", (club_id,))
    if not r:
        return None
    return {"quand": r["occurred_at"], "type": r["kind"], "sujet": r["subject"]}


def _memoire(mots, comprise):
    """Les fiches de la base qui repondent, chacune avec ou on l'a trouvee."""
    if not mots and not comprise.get("ville"):
        return []

    ville = _plat(comprise.get("ville") or "")
    dept = (comprise.get("dept") or "").upper()

    # Les conversations WhatsApp d'abord : c'est la que « le gars du gymnase »
    # se retrouve, et elles se rattachent ensuite a la fiche de leur club.
    chats = {}
    orphelins = []
    for w in db.q("SELECT * FROM wa_chats WHERE ecarte = 0 ORDER BY dernier_le DESC"):
        w = db.row_to_dict(w)
        textes = " ".join(str(m.get("texte") or "") for m in _derniers_messages(w))
        n = _dedans(mots, w.get("nom"), w.get("resume"), w.get("apercu"), textes)
        if not n:
            continue
        if w.get("club_id"):
            chats.setdefault(w["club_id"], []).append((n, w))
        else:
            orphelins.append((n, w))

    cartes = []
    for c in db.q("SELECT * FROM clubs"):
        c = db.row_to_dict(c)
        if ville and _plat(c.get("city")) != ville:
            # Une ville demandee ecarte les autres, sauf si le nom du club
            # porte quand meme tous les mots : « Judo Cagnes » vu de Nice.
            if not mots or _dedans(mots, c.get("name")) < len(mots):
                continue
        if dept and (c.get("dept") or "").upper() != dept:
            continue

        ou = []
        score = 0
        n = _dedans(mots, c.get("name"))
        if n:
            score += n * 3
            ou.append("le nom du club")
        n = _dedans(mots, c.get("sport"))
        if n:
            score += n * 2
            ou.append("le sport")
        n = _dedans(mots, c.get("city"))
        if n:
            score += n
            ou.append("la ville")
        n = _dedans(mots, c.get("notes"), c.get("tags"))
        if n:
            score += n
            ou.append("une note")

        gens, n = _personnes(c["id"], mots)
        if n:
            score += n * 2
            ou.append("une personne")

        fils = [w for _, w in sorted(chats.get(c["id"], []), key=lambda t: -t[0])]
        if fils:
            score += sum(k for k, _ in chats[c["id"]]) * 2
            ou.append("WhatsApp")

        if ville and _plat(c.get("city")) == ville:
            score += 1
            if "la ville" not in ou:
                ou.append("la ville")

        if score <= 0:
            continue

        ech = _dernier_echange(c["id"])
        if ech and _dedans(mots, ech.get("sujet")):
            score += 1
            ou.append("un échange")

        cartes.append({
            "club_id": c["id"], "nom": c.get("name"), "sport": c.get("sport"),
            "ville": c.get("city"), "statut": c.get("status"),
            "statut_cle": c.get("status"),
            "ne_pas_contacter": bool(c.get("do_not_contact")),
            "telephone": c.get("phone"), "email": c.get("email"),
            "site": c.get("website") or c.get("source_url"),
            "note": _bout_message(c.get("notes"), 300) or None,
            "personnes": gens,
            "whatsapp": [_carte_chat(w, mots) for w in fils[:3]],
            "dernier_echange": ech,
            "extrait": _extrait(c.get("notes"), mots),
            "trouve_dans": ou or ["la base"], "score": score,
        })

    # Les conversations sans fiche club : ce sont souvent les plus recentes,
    # et ce sont elles qu'on vient chercher apres une journee sur le terrain.
    for n, w in orphelins:
        carte = _carte_chat(w, mots)
        cartes.append({
            "club_id": None, "nom": w.get("nom") or "Conversation WhatsApp",
            "sport": None, "ville": None, "statut": "pas encore en fiche",
            "statut_cle": "prospect", "ne_pas_contacter": False,
            "telephone": w.get("phone"), "email": None, "site": None,
            "note": _bout_message(w.get("resume"), 300) or None,
            "personnes": [], "whatsapp": [carte], "dernier_echange": None,
            "extrait": carte.get("extrait"),
            "trouve_dans": ["WhatsApp"], "score": n * 2,
        })

    cartes.sort(key=lambda m: (-m["score"], m["nom"] or ""))
    return cartes[:MEMOIRE_MAX]


def _dates(mots, comprise):
    """Les dates a venir de la base qui collent a la question."""
    ville = _plat(comprise.get("ville") or "")
    dept = (comprise.get("dept") or "").upper()
    sortie = []
    for t in db.q("SELECT * FROM tournaments "
                  "WHERE date(COALESCE(start_date, '2099-01-01')) >= date('now') "
                  "ORDER BY start_date LIMIT 1200"):
        t = db.row_to_dict(t)
        if ville and _plat(t.get("city")) != ville:
            continue
        if dept and (t.get("dept") or "").upper() != dept:
            continue
        n = _dedans(mots, t.get("name"), t.get("sport"), t.get("city"),
                    t.get("description"))
        if mots and not n:
            continue
        sortie.append((n, {
            "id": t["id"], "name": t.get("name"), "sport": t.get("sport"),
            "start_date": t.get("start_date"), "end_date": t.get("end_date"),
            "city": t.get("city"), "postal_code": t.get("postal_code"),
            "venue": t.get("venue"), "url": t.get("url"),
            "source": t.get("source"),
            "description": _bout_message(t.get("description"), 220) or None,
            "ici": t.get("trajet_min") is not None and t["trajet_min"] <= 45,
            "deja": True,
        }))
    sortie.sort(key=lambda p: (-p[0], p[1]["start_date"] or ""))
    return [d for _, d in sortie[:DATES_MAX]]


def _liens(comprise):
    """Des recherches a ouvrir soi-meme. Aucune n'est lancee d'ici.

    Les petits evenements n'existent souvent que sur Facebook, et le
    repertoire des associations ne les connait pas. On prepare l'adresse,
    Julien clique — c'est lui qui regarde, avec son propre navigateur.
    """
    quoi = " ".join(x for x in (comprise.get("sujet"), comprise.get("ville")) if x).strip()
    if not quoi:
        return []
    q = urllib.parse.quote_plus(quoi[:120])
    return [
        {"libelle": "Google", "url": "https://www.google.com/search?q=" + q},
        {"libelle": "Facebook", "url": "https://www.facebook.com/search/top?q=" + q},
        {"libelle": "Google Actualités",
         "url": "https://www.google.com/search?tbm=nws&q=" + q},
    ]


def chercher(question):
    """Une question en francais, ce que la base en sait.

    Rend {comprise, memoire, trouves, total, liens, soucis}. cerveau.demander()
    y ajoute la phrase du modele local ; la page Chercher affiche les fiches
    telles quelles. Rien n'est invente : tout sort d'une ligne de la base.
    """
    q = (question or "").strip()
    soucis = []
    if not q:
        return {"q": q, "comprise": {"ia": False, "sujet": "", "ville": None,
                                     "dept": None, "mots": []},
                "memoire": [], "trouves": [], "total": 0, "liens": [],
                "soucis": []}

    comprise = _comprendre(q, soucis)
    mots = comprise["mots"]
    if not mots and not comprise["ville"]:
        soucis.append("Je n'ai rien retenu de cette phrase : essayez un nom, "
                      "un sport ou une ville.")

    memoire = _memoire(mots, comprise)
    trouves = _dates(mots, comprise)
    return {"q": q, "comprise": comprise, "memoire": memoire,
            "trouves": trouves, "total": len(trouves),
            "liens": _liens(comprise), "soucis": soucis}
