# -*- coding: utf-8 -*-
"""Le logo d'un club, recupere depuis son site.

Pourquoi pas une recherche par nom. Chercher « Cap d'Ail » sur le web et
prendre la premiere image ramenee, c'est poser le blason d'un autre club sur
une carte — une erreur qu'on ne voit pas, et qui finit dans un message. On ne
telecharge donc que depuis une adresse que Julien a fournie : le site du club,
ou l'image elle-meme. Faute d'adresse, la carte garde ses initiales.

L'image n'est pas retouchee : la bibliotheque standard ne sait pas redimensionner
un JPEG, et un logo de club pese rarement lourd. On la stocke telle quelle, on
la sert telle quelle, et c'est le navigateur qui la met a la taille.
"""
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import db, util

DOSSIER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "logos")

# Un logo au-dela de cette taille n'est pas un logo : on refuse plutot que de
# remplir le disque avec une photo de groupe en pleine definition.
POIDS_MAX = 2 * 1024 * 1024

TYPES = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/svg+xml": "svg", "image/webp": "webp", "image/gif": "gif",
    "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico",
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_META = re.compile(
    r"""<meta[^>]+(?:property|name)\s*=\s*["']og:image["'][^>]+content\s*=\s*["']([^"']+)""",
    re.I)
_APPLE = re.compile(
    r"""<link[^>]+rel\s*=\s*["'][^"']*apple-touch-icon[^"']*["'][^>]+href\s*=\s*["']([^"']+)""",
    re.I)
_ICON = re.compile(
    r"""<link[^>]+rel\s*=\s*["'][^"']*\bicon\b[^"']*["'][^>]+href\s*=\s*["']([^"']+)""",
    re.I)


class Echec(Exception):
    pass


def _ouvrir(url, limite=POIDS_MAX):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=12) as rep:
        return rep.read(limite + 1), (rep.headers.get("Content-Type") or "").split(";")[0].strip()


def candidats(url_site):
    """Les adresses d'image declarees par une page, de la meilleure a la pire."""
    page, _ = _ouvrir(url_site, limite=400_000)
    html = page.decode("utf-8", "replace")
    trouves = []
    for motif in (_META, _APPLE, _ICON):
        for m in motif.finditer(html):
            trouves.append(urllib.parse.urljoin(url_site, m.group(1)))
    trouves.append(urllib.parse.urljoin(url_site, "/favicon.ico"))
    vus, propres = set(), []
    for u in trouves:
        if u not in vus:
            vus.add(u)
            propres.append(u)
    return propres


def _ecrire(club_id, octets, ext):
    if not os.path.isdir(DOSSIER):
        os.makedirs(DOSSIER)
    # Un club ne garde qu'un logo : on efface les formats precedents, sinon
    # l'ancien .png continue d'etre servi a cote du nouveau .svg.
    for vieux in os.listdir(DOSSIER):
        if vieux.startswith("%d." % club_id):
            os.remove(os.path.join(DOSSIER, vieux))
    chemin = os.path.join(DOSSIER, "%d.%s" % (club_id, ext))
    with open(chemin, "wb") as fh:
        fh.write(octets)
    return chemin


def poser_depuis_image(club_id, url_image):
    """Telecharge une image precise et l'attache au club."""
    octets, ctype = _ouvrir(url_image)
    if len(octets) > POIDS_MAX:
        raise Echec("image trop lourde (plus de 2 Mo)")
    ext = TYPES.get(ctype)
    if not ext:
        # Certains serveurs servent une icone en octet-stream : on se rabat sur
        # l'extension, mais on refuse tout ce qu'on ne reconnait pas.
        bout = urllib.parse.urlparse(url_image).path.rsplit(".", 1)
        ext = bout[-1].lower() if len(bout) == 2 else ""
        if ext not in ("png", "jpg", "jpeg", "svg", "webp", "gif", "ico"):
            raise Echec("ce n'est pas une image (%s)" % (ctype or "type inconnu"))
        ext = "jpg" if ext == "jpeg" else ext
    if not octets:
        raise Echec("image vide")
    _ecrire(int(club_id), octets, ext)
    db.update("clubs", int(club_id),
              {"logo": ext, "updated_at": util.now_iso()})
    return {"ok": True, "ext": ext, "octets": len(octets), "source": url_image}


def poser_depuis_site(club_id, url_site=None):
    """Cherche le logo sur le site du club, et prend le premier qui repond."""
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(club_id),)))
    if not club:
        raise Echec("club introuvable")
    url_site = url_site or club.get("website")
    if not url_site:
        raise Echec("aucun site connu pour ce club")
    if not url_site.startswith(("http://", "https://")):
        url_site = "https://" + url_site
    dernier = None
    for essai in candidats(url_site):
        try:
            return poser_depuis_image(club_id, essai)
        except (Echec, urllib.error.URLError, OSError, ValueError) as exc:
            dernier = exc
    raise Echec("aucune image exploitable sur %s (%s)" % (url_site, dernier))


def chemin(club_id):
    """Le fichier stocke pour ce club, ou None."""
    if not os.path.isdir(DOSSIER):
        return None
    for nom in os.listdir(DOSSIER):
        if nom.startswith("%d." % int(club_id)):
            return os.path.join(DOSSIER, nom)
    return None


def retirer(club_id):
    p = chemin(club_id)
    if p:
        os.remove(p)
    db.update("clubs", int(club_id), {"logo": None, "updated_at": util.now_iso()})
    return {"ok": True}
