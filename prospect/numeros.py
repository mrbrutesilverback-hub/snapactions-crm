# -*- coding: utf-8 -*-
"""Retrouver le numero d'un club, en le LISANT quelque part.

Julien demandait si une IA pouvait aller chercher le numero manquant. C'est
justement le seul endroit ou il ne faut pas en mettre une : un modele de
langue produit un numero plausible quand il ne sait pas, et un numero
plausible est indiscernable d'un vrai. Trois numeros inventes ont deja fini
en base dans ce projet.

On fait donc l'inverse : on va chercher des pages ou le numero a des chances
d'etre ecrit, on en extrait les suites de chiffres reellement presentes, et
on dit d'ou vient chacune. Rien n'est enregistre — Julien choisit.

Les pistes, de la plus sure a la moins sure :
  1. le site du club, quand on le connait ;
  2. la page d'ou vient l'annonce ;
  3. le domaine de son adresse mail — « contact@lequipenicoisefootball.fr »
     designe un site, et ce site porte presque toujours un telephone ;
  4. la page « contact » de ce domaine.
"""

import html as html_mod
import re
import urllib.error
import urllib.parse
import urllib.request

from . import util

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Au-dela, ce n'est plus une page de contact.
POIDS_MAX = 900_000
BALISES = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)

# Les domaines qui n'appartiennent pas au club et n'ont rien a nous apprendre.
GENERIQUES = ("gmail.com", "orange.fr", "wanadoo.fr", "free.fr", "hotmail.com",
              "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.fr", "yahoo.com",
              "laposte.net", "sfr.fr", "icloud.com", "me.com", "live.fr")


def _texte(html):
    sans = BALISES.sub(" ", html or "")
    sans = re.sub(r"<[^>]+>", " ", sans)
    return re.sub(r"\s+", " ", sans)


def _lire(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=12) as rep:
            brut = rep.read(POIDS_MAX)
        for enc in ("utf-8", "latin-1"):
            try:
                return brut.decode(enc)
            except UnicodeDecodeError:
                continue
        return brut.decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def _domaine_du_mail(mail):
    """« contact@lequipenicoisefootball.fr » -> https://lequipenicoisefootball.fr"""
    if not mail or "@" not in str(mail):
        return None
    dom = str(mail).rsplit("@", 1)[-1].strip().lower().strip(">.,;")
    if not dom or dom in GENERIQUES or "." not in dom:
        return None
    return "https://" + dom


def pistes(club=None, tournoi=None, extra=None):
    """Les adresses a visiter, sans doublon, dans l'ordre de confiance.

    `extra` : des adresses reperees ailleurs — par la recherche Google, par
    exemple. Elles passent en tete parce qu'un moteur sait trouver le site
    d'un club dont on ignore tout, la ou nos pistes partent de ce qu'on a
    deja. Elles ne sont que des adresses : les numeros, eux, continuent d'etre
    lus sur la page.
    """
    club, tournoi = club or {}, tournoi or {}
    out = []

    def poser(url, dit):
        if not url:
            return
        url = str(url).strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        if all(url != x[0] for x in out):
            out.append((url, dit))

    for url, dit in (extra or []):
        poser(url, dit)
    poser(club.get("website"), "le site du club")
    poser(tournoi.get("url"), "la page de l'annonce")
    poser(club.get("source_url"), "la source de la fiche")

    mail = club.get("email")
    if not mail:
        from . import db
        ligne = db.q1("SELECT email FROM contacts WHERE club_id = ? AND email IS NOT NULL "
                      "AND email != '' LIMIT 1", (club.get("id"),)) if club.get("id") else None
        mail = ligne["email"] if ligne else None
    racine = _domaine_du_mail(mail)
    if racine:
        poser(racine, "le site derriere leur adresse mail")

    # Les pages de contact du site du club. On ne les essayait que derriere une
    # adresse mail connue — c'est-a-dire seulement quand on avait deja ce qu'on
    # cherchait. Un club dont on connait le site n'y avait donc pas droit.
    for base in (club.get("website"), racine):
        if not base:
            continue
        base = str(base).strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = "https://" + base.lstrip("/")
        for suite in ("/contact", "/contacts", "/nous-contacter", "/le-club"):
            poser(base + suite, "leur page contact")
    return out


# Ce qui, dans un lien interne, sent la page ou se trouvent les coordonnees.
_LIEN_UTILE = re.compile(
    r"contact|nous-?ecrire|nous-?joindre|mentions|legal|equipe|bureau|"
    r"organigramme|inscription|adhesion|infos?-?pratiques|le-?club|qui-?sommes",
    re.I)


def _liens_internes(html, base, maximum=4):
    """Quelques pages du meme site ou un mail a des chances de se trouver.

    Le Centre Nautique de Bauduen publie son adresse dans une actualite, pas
    sur une page « contact » : la recherche rendait donc zero mail sur trois
    pages lues, alors qu'elle etait a un clic. On suit donc quelques liens.
    """
    if not html or not base:
        return []
    import urllib.parse
    racine = urllib.parse.urlsplit(base)
    hote = racine.netloc
    vus, out = set(), []
    for href in re.findall(r'href="([^"#?]{2,160})"', html):
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        plein = urllib.parse.urljoin(base, href)
        p = urllib.parse.urlsplit(plein)
        if p.netloc != hote or plein in vus:
            continue
        if re.search(r"\.(?:jpg|jpeg|png|gif|svg|pdf|zip|mp4|webp|ico|css|js)$",
                     p.path, re.I):
            continue
        m = _LIEN_UTILE.search(p.path)
        if not m:
            continue
        vus.add(plein)
        out.append(plein)
    # Les pages les plus sures d'abord : « contactez-nous » avant
    # « adhesion », sinon la bonne page passe apres le plafond.
    def rang(u):
        for i, mot in enumerate(("contact", "nous-ecrire", "nous-joindre",
                                 "mentions", "legal", "bureau", "organigramme",
                                 "equipe", "le-club", "qui-sommes")):
            if mot in u.lower():
                return i
        return 99
    out.sort(key=rang)
    return out[:maximum]


def chercher(club=None, tournoi=None, maximum=6, extra=None):
    """Va lire les pages et rapporte les numeros qui y figurent.

    Renvoie {trouves: [{numero, format, source, page}], vues: [...], pistes: n}.
    N'ecrit rien : c'est Julien qui retient un numero.
    """
    vus, trouves, vues = set(), [], []
    a_voir = [(u, d) for u, d in pistes(club, tournoi, extra)[:maximum]]
    suivis = 0
    while a_voir:
        url, dit = a_voir.pop(0)
        html = _lire(url)
        vues.append({"url": url, "dit": dit, "lue": html is not None})
        if not html:
            continue
        texte = _texte(html)
        # Aucun mail sur cette page : on suit quelques liens du meme site
        # plutot que de rendre « rien trouve » alors qu'il est a un clic.
        if not util.extract_emails(texte) and suivis < 4 and len(vues) < maximum + 5:
            for lien in _liens_internes(html, url, maximum=3):
                if all(lien != v["url"] for v in vues) and all(lien != u for u, _ in a_voir):
                    a_voir.append((lien, "une page de leur site"))
                    suivis += 1
        for num in util.extract_phones(texte):
            if num in vus:
                continue
            vus.add(num)
            trouves.append({
                "numero": num,
                "format": util.format_phone(num) or num,
                "mobile": util.is_mobile_fr(num),
                "source": dit,
                "page": url,
            })
        for mail in util.extract_emails(texte)[:3]:
            if mail not in vus:
                vus.add(mail)
                trouves.append({"email": mail, "source": dit, "page": url})

    # Toujours pas de mail apres avoir lu leur site ? Il n'y est peut-etre
    # pas. Le Centre Nautique de Bauduen publie le sien dans une actualite :
    # ni sa page « contactez-nous » ni ses mentions legales ne le portent,
    # aucune heuristique de lien ne pouvait le trouver — un moteur, si.
    if not any(x.get("email") for x in trouves):
        nom = (club or {}).get("name") or (tournoi or {}).get("organizer") or ""
        ville = (club or {}).get("city") or (tournoi or {}).get("city") or ""
        if nom:
            try:
                for r in interroger("%s %s contact mail" % (nom, ville), maximum=4):
                    html = _lire(r.get("url"))
                    if not html:
                        continue
                    vues.append({"url": r["url"], "dit": "trouvé par le moteur",
                                 "lue": True})
                    for mail in util.extract_emails(_texte(html))[:2]:
                        if mail in vus:
                            continue
                        vus.add(mail)
                        trouves.append({"email": mail, "source": "trouvé par le moteur",
                                        "page": r["url"]})
                    if any(x.get("email") for x in trouves):
                        break
            except Exception:  # noqa: BLE001 — le moteur peut ne pas repondre
                pass

    # Un portable passe devant : c'est lui qui ouvre WhatsApp.
    trouves.sort(key=lambda x: (0 if x.get("mobile") else 1 if x.get("numero") else 2))
    return {"trouves": trouves, "vues": vues, "pistes": len(vues)}


# ---------------------------------------------------------------------------
# Chercher sur le web ce que l'annonce ne dit pas
# ---------------------------------------------------------------------------
# Quand la source ne nomme pas l'organisateur, Julien ouvrait Google a la main,
# lisait le resultat et recopiait. Le moteur fait tres bien ce travail ; il
# suffit de le lancer et de LIRE les pages qu'il rend.
#
# Google refuse les robots. DuckDuckGo rend la meme recherche en HTML simple,
# sans cle et sans compte : c'est par la qu'on passe.
#
# Les numeros et les mails sont extraits par util, jamais par un modele : une
# suite de chiffres inventee ne se distingue pas d'une vraie. Le modele ne sert
# qu'a NOMMER le club, et son nom se verifie d'un coup d'oeil.
DDG = "https://html.duckduckgo.com/html/?q=%s"

# Les annuaires et agregateurs rendent le nom du tournoi, pas celui du club.
SANS_INTERET = re.compile(
    r"facebook\.com/(?:tr|sharer)|google\.|duckduckgo|youtube\.com|twitter\.com|"
    r"pinterest\.|tiktok\.com|linkedin\.com|amazon\.|wikipedia\.org", re.I)

LIENS_DDG = re.compile(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                       re.I | re.S)
EXTRAIT_DDG = re.compile(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
                         re.I | re.S)


def _deplier(url):
    """DuckDuckGo enveloppe ses liens : on rend l'adresse reelle."""
    url = html_mod.unescape(url or "")
    if url.startswith("//"):
        url = "https:" + url
    m = re.search(r"[?&]uddg=([^&]+)", url)
    if m:
        url = urllib.parse.unquote(m.group(1))
    return url


def interroger(question, maximum=6):
    """Les resultats du moteur : (adresse, titre, extrait)."""
    html = _lire(DDG % urllib.parse.quote_plus(question))
    if not html:
        return []
    titres = LIENS_DDG.findall(html)
    extraits = EXTRAIT_DDG.findall(html)
    sortie = []
    for i, (url, titre) in enumerate(titres):
        url = _deplier(url)
        if not url.startswith("http") or SANS_INTERET.search(url):
            continue
        sortie.append({
            "url": url,
            "titre": _texte(titre).strip()[:160],
            "extrait": _texte(extraits[i]).strip()[:400] if i < len(extraits) else "",
        })
        if len(sortie) >= maximum:
            break
    return sortie


def fouiller(tournoi, pages=3):
    """Lance la recherche, lit les pages, rapporte ce qui y figure vraiment."""
    nom = (tournoi.get("name") or "").strip()
    if not nom:
        return {"question": None, "resultats": [], "trouves": [], "organisateur": None}
    ville = (tournoi.get("city") or "").strip()
    # Pas de guillemets : le moteur rend « aucun resultat » des qu'une
    # ponctuation du titre ne colle pas au caractere pres — un tiret long
    # suffit. Les mots seuls retrouvent la meme page.
    propre = re.sub(r"[^0-9A-Za-zÀ-ÿ'\- ]+", " ", nom)
    question = "%s %s club organisateur contact" % (propre.strip(), ville)

    resultats = interroger(question)
    vus, trouves = set(), []
    lus = []
    for r in resultats[:pages]:
        page = _lire(r["url"])
        if not page:
            continue
        texte = _texte(page)
        lus.append({"url": r["url"], "titre": r["titre"], "texte": texte[:4000]})
        for num in util.extract_phones(texte):
            if num in vus:
                continue
            vus.add(num)
            trouves.append({"numero": num, "format": util.format_phone(num) or num,
                            "mobile": util.is_mobile_fr(num),
                            "source": r["titre"] or r["url"], "page": r["url"]})
        for mail in util.extract_emails(texte)[:3]:
            if mail in vus:
                continue
            vus.add(mail)
            trouves.append({"email": mail, "source": r["titre"] or r["url"],
                            "page": r["url"]})

    # Le modele local nomme le club, a partir des titres et des extraits — il
    # ne lit que ce que le moteur a rendu, et ne touche a aucun numero.
    organisateur = None
    try:
        from . import ia
        if ia.disponible() and resultats:
            matiere = "\n".join(
                "%s\n%s" % (r["titre"], r["extrait"]) for r in resultats[:5])
            d = ia.organisateur_depuis_web(nom, ville, matiere)
            organisateur = (d or {}).get("organisateur")
    except Exception:
        organisateur = None

    # Deuxieme passe : une fois le club nomme, on cherche SON site. Les
    # coordonnees sont la, pas sur l'annuaire qui annonce le tournoi — c'est
    # exactement le chemin qu'on fait a la main.
    question2 = None
    if organisateur and not any(x.get("numero") or x.get("email") for x in trouves):
        question2 = "%s %s contact telephone mail" % (organisateur, ville)
        for r in interroger(question2)[:pages]:
            page = _lire(r["url"])
            if not page:
                continue
            texte = _texte(page)
            for num in util.extract_phones(texte):
                if num in vus:
                    continue
                vus.add(num)
                trouves.append({"numero": num, "format": util.format_phone(num) or num,
                                "mobile": util.is_mobile_fr(num),
                                "source": r["titre"] or r["url"], "page": r["url"]})
            for mail in util.extract_emails(texte)[:3]:
                if mail in vus:
                    continue
                vus.add(mail)
                trouves.append({"email": mail, "source": r["titre"] or r["url"],
                                "page": r["url"]})

    trouves.sort(key=lambda x: (0 if x.get("mobile") else 1 if x.get("numero") else 2))
    return {"question": question, "question_club": question2,
            "organisateur": organisateur, "resultats": resultats,
            "trouves": trouves, "pages_lues": len(lus)}
