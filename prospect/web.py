"""Serveur HTTP local (stdlib) : API JSON + flux iCal + interface web.

Ecoute uniquement sur la boucle locale. Les flux .ics sont servis avec un
jeton, pour que l'app Calendrier puisse s'y abonner sans session.
"""
import io
import datetime
import json
import os
import posixpath
import re
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import agenda, creux, dates_fr, db, drafts, extract, flyer, geo, ics, journee, lieux, \
    logos, missions, photos, purge, rassemblements, recherche, relancer, repo, sources, \
    util, wa, wa_local, whatsapp, xlsx, ia

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "exports")

ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


# --------------------------------------------------------------------------
# Tableau de bord / recherche
# --------------------------------------------------------------------------

VERSION = "2026-09-09.4"


@route("GET", r"/api/version")
def api_version(req):
    """Sert a l'interface pour savoir si le serveur tourne encore sur du vieux code."""
    return {"version": VERSION}


def relancer_le_serveur(delai=0.4):
    """Relance le processus. Appele par la route, et par la base elle-meme.

    La base s'en sert quand plus aucune connexion ne la lit : dans un
    processus, elles partagent toutes le meme fichier de memoire partagee,
    et s'il est casse, seul un processus neuf repart.
    """
    import os
    import sys
    import threading

    def _relance():
        import time
        time.sleep(delai)
        # Un execv remplace ce processus, mais le tunnel peut rester debout :
        # le processus suivant le retrouve et sait le fermer. On lache donc
        # la main dessus sans le couper — sinon chaque mise a jour du code
        # change l'adresse du sourceur.
        try:
            from . import tunnel
            tunnel.au_revoir(garder=True)
        except Exception:
            pass
        # os.execv remplace le processus mais laisse derriere lui les
        # descripteurs ouverts — ceux de SQLite compris, avec le fichier de
        # memoire partagee qui va avec. Au bout de quelques relances, le
        # nouveau processus retombe sur un etat incoherent et annonce une
        # base « malformed » alors qu'elle est intacte. On ferme avant.
        try:
            db.fermer_toutes()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_relance, daemon=True).start()
    return True


@route("POST", r"/api/redemarrer")
def api_redemarrer(req):
    """Relance le serveur dans le meme processus, pour prendre en compte le code a jour."""
    relancer_le_serveur()
    return {"ok": True}


@route("GET", r"/api/logos/(\d+)")
def api_logo(req, cid):
    """Sert le logo stocke pour un club. Rien d'externe n'est appele ici."""
    chemin = logos.chemin(int(cid))
    if not chemin:
        raise ApiError("aucun logo", 404)
    ext = chemin.rsplit(".", 1)[-1].lower()
    ctype = {"png": "image/png", "jpg": "image/jpeg", "svg": "image/svg+xml",
             "webp": "image/webp", "gif": "image/gif",
             "ico": "image/x-icon"}.get(ext, "application/octet-stream")
    with open(chemin, "rb") as fh:
        return {"_raw": fh.read(), "_ctype": ctype}


@route("POST", r"/api/clubs/(\d+)/logo")
def api_logo_poser(req, cid):
    """Attache un logo : depuis une image precise, ou depuis le site du club."""
    image = (req.body.get("image") or "").strip()
    site = (req.body.get("site") or "").strip()
    try:
        if image:
            return logos.poser_depuis_image(int(cid), image)
        if site:
            db.update("clubs", int(cid), {"website": site, "updated_at": util.now_iso()})
        return logos.poser_depuis_site(int(cid), site or None)
    except logos.Echec as exc:
        raise ApiError(str(exc), 400)
    except Exception as exc:
        raise ApiError("impossible de récupérer l'image : %s" % exc, 400)


@route("DELETE", r"/api/clubs/(\d+)/logo")
def api_logo_retirer(req, cid):
    return logos.retirer(int(cid))


AFFICHES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "a-traiter", "traites")


@route("GET", r"/api/affiches/(\d+)")
def api_affiche(req, tid):
    """Sert l'affiche d'un tournoi. Lecture locale, rien de distant."""
    row = db.q1("SELECT affiche FROM tournaments WHERE id = ?", (int(tid),))
    nom = row["affiche"] if row else None
    if not nom:
        raise ApiError("aucune affiche pour cette fiche", 404)
    # Le nom vient de la base, mais on refuse quand meme tout ce qui sortirait
    # du dossier : une fiche modifiee a la main ne doit pas servir de passe-partout.
    chemin = os.path.join(AFFICHES, os.path.basename(nom))
    if not os.path.abspath(chemin).startswith(os.path.abspath(AFFICHES)) \
            or not os.path.isfile(chemin):
        raise ApiError("affiche introuvable", 404)
    ext = chemin.rsplit(".", 1)[-1].lower()
    ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "webp": "image/webp", "gif": "image/gif",
             "pdf": "application/pdf"}.get(ext, "application/octet-stream")
    with open(chemin, "rb") as fh:
        return {"_raw": fh.read(), "_ctype": ctype}


@route("GET", r"/api/menage")
def api_menage(req):
    """Ce que le menage retirerait. Ne supprime rien."""
    return purge.passer()


@route("POST", r"/api/menage")
def api_menage_faire(req):
    """Fait le menage. Appele par vous, ou par la routine de 7 h 30."""
    bilan = purge.passer(pour_de_vrai=True)
    bilan["resume"] = purge.resume(bilan)
    return bilan


PHOTOS_DIR = os.path.join(RACINE_PROJET, "photos")
IMAGES_OK = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif")


# Les planches, rangees par discipline. Un club de voile ne se decide pas
# sur des photos de football : il veut voir des bateaux, et des enfants sur
# l'eau. Chaque dossier de photos/ porte un sport ; la racine garde le jeu
# par defaut, pour les disciplines qui n'ont pas encore le leur.
PHOTOS_DOSSIERS = {
    "foot": ("foot", "football", "futsal", "soccer"),
    "aviron": ("aviron", "rowing", "rame", "yole"),
    "voile": ("voile", "nautique", "nautisme", "optimist", "catamaran",
              "regate", "planche a voile", "windsurf", "kayak", "canoe",
              "paddle", "surf"),
    "badminton": ("badminton", "bad"),
    "volley": ("volley", "volleyball", "beach volley"),
    "basket": ("basket", "basketball"),
    "hand": ("hand", "handball"),
    "rugby": ("rugby",),
    "lutte": ("lutte", "grappling"),
    "judo": ("judo", "jujitsu", "ju-jitsu"),
    "boxe": ("boxe", "boxing", "muay", "kickboxing", "savate", "full contact"),
    "gym": ("gym", "gymnastique", "danse", "gr", "twirling"),
    "natation": ("natation", "nage", "water polo"),
    "tennis": ("tennis", "padel"),
    "equitation": ("equitation", "cheval", "poney", "hippique"),
    "ski": ("ski", "snowboard", "neige"),
    "course": ("course", "trail", "athletisme", "running", "cross"),
    # « studio » n'est pas un sport mais un contexte : l'ecran Studio montre
    # ce que donne une seance en salle, pas un tournoi.
    "studio": ("studio", "portrait", "seance"),
}


def _dossiers_photos():
    """Les dossiers de planches reellement presents, par nom."""
    if not os.path.isdir(PHOTOS_DIR):
        return {}
    out = {}
    for nom in sorted(os.listdir(PHOTOS_DIR)):
        chemin = os.path.join(PHOTOS_DIR, nom)
        if os.path.isdir(chemin) and not nom.startswith("."):
            out[util.strip_accents(nom).lower()] = chemin
    return out


def _dossier_pour(sport=None, dossier=None):
    """Quel dossier de planches pour ce sport. La racine a defaut.

    Rend (chemin, nom_affiche). Le nom sert a dire sur le bouton quelle
    serie est partie : se tromper de planches se voit tout de suite.
    """
    presents = _dossiers_photos()
    if dossier:
        cle = util.strip_accents(str(dossier)).lower().strip()
        if cle in presents:
            return presents[cle], cle
    plat = util.strip_accents(str(sport or "")).lower()
    if plat:
        for cle, mots in PHOTOS_DOSSIERS.items():
            if cle not in presents:
                continue
            if any(m in plat for m in mots):
                return presents[cle], cle
        # Un dossier nomme exactement comme le sport, meme s'il n'est pas
        # dans la table : Julien peut en creer un sans toucher au code.
        for cle, chemin in presents.items():
            if cle in plat or plat in cle:
                return chemin, cle
    return PHOTOS_DIR, None


def _dossiers_vides():
    """Les packs qui n'ont aucune image. Un dossier vide ne refuse rien : il
    laisse partir les photos par defaut a sa place, en silence. Autant le
    dire sur l'ecran."""
    vides = []
    for cle, racine in _dossiers_photos().items():
        try:
            fichiers = [n for n in os.listdir(racine)
                        if not n.startswith(".")
                        and n.lower().endswith(IMAGES_OK)
                        and os.path.isfile(os.path.join(racine, n))]
        except OSError:
            fichiers = []
        if not fichiers:
            vides.append(cle)
    return sorted(vides)


def _photos_exemple(sport=None, dossier=None):
    """Les planches a envoyer apres la plaquette, dans l'ordre alphabetique."""
    def _lire(racine):
        if not os.path.isdir(racine):
            return []
        out = []
        for n in sorted(os.listdir(racine)):
            if n.startswith(".") or not n.lower().endswith(IMAGES_OK):
                continue
            chemin = os.path.join(racine, n)
            if os.path.isfile(chemin):
                out.append(chemin)
        return out

    racine, nom = _dossier_pour(sport, dossier)
    out = _lire(racine)
    vide = None
    if not out and nom:
        # Le dossier du sport existe mais il est vide : plutot que de
        # repondre « aucune photo », on envoie le jeu par defaut.
        #
        # Mais on dit lequel on a pris. Avant, le bouton annoncait « 3
        # photos » au lieu de « 3 photos aviron », et c'est tout : on croyait
        # envoyer les planches d'aviron a un club d'aviron alors que partaient
        # trois photos de foot. Un dossier vide doit se voir.
        vide = nom
        out = _lire(PHOTOS_DIR)
        nom = None
    return out, nom, vide


# AppleScript sait « mettre un POSIX file dans le presse-papiers », mais il y
# ecrit un format que le Finder n'emploie pas, et que WhatsApp ignore : on
# collait dans le vide. On passe donc par le presse-papiers natif, en y
# ecrivant de vraies NSURL de fichiers — exactement ce que produit un Cmd+C
# dans le Finder, donc ce que toutes les applications savent coller.
_JXA_COPIE = """
ObjC.import('AppKit');
function run(argv) {
  const pb = $.NSPasteboard.generalPasteboard;
  // On declare le type AVANT d'ecrire, et on ecrit une liste de chaines :
  // c'est une donnee simple, copiee tout de suite. writeObjects(NSURL) laisse
  // au contraire des promesses que le systeme resout plus tard — et comme
  // osascript se termine aussitot, une partie des fichiers disparaissait.
  // Le compte passait de 4 a 3 a 2 d'un appel a l'autre.
  const chemins = $.NSMutableArray.alloc.init;
  argv.forEach(function (p) { chemins.addObject($(p)); });
  pb.declareTypesOwner($([$.NSFilenamesPboardType, 'public.file-url']), $());
  const a = pb.setPropertyListForType(chemins, $.NSFilenamesPboardType);
  // Un seul fichier : beaucoup d'applications ne lisent que ce type-la.
  let b = true;
  if (argv.length === 1) {
    b = pb.setStringForType(
      ObjC.unwrap($.NSURL.fileURLWithPath($(argv[0])).absoluteString),
      $('public.file-url'));
  }
  return (a && b) ? 'ok' : 'refus';
}
"""


def _vers_presse_papiers(chemins):
    """Met un ou plusieurs FICHIERS dans le presse-papiers de macOS.

    C'est le seul chemin possible : un lien wa.me ne transporte que du texte,
    donc aucune piece jointe ne part avec le message. WhatsApp accepte en
    revanche qu'on colle des fichiers, a condition qu'ils y soient dans le
    bon format.
    """
    if not chemins:
        raise ApiError("rien à copier")
    import subprocess
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", _JXA_COPIE]
                           + list(chemins),
                           check=True, capture_output=True, timeout=20, text=True)
    except Exception as exc:
        raise ApiError("copie refusée par le système : %s" % exc, 500)
    if "ok" not in (r.stdout or ""):
        raise ApiError("le presse-papiers a refusé les fichiers")
    return len(chemins)


# --------------------------------------------------------------------------
# Les comptes, et l'espace de ceux qui ne sont pas Julien
# --------------------------------------------------------------------------

def _poser_cookie(req, jeton, jours=None):
    """Demande au gestionnaire HTTP de poser le cookie de session."""
    from . import comptes
    req.cookie = ("snapsession=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                  % (jeton, (jours or comptes.SESSION_JOURS) * 86400))


@route("POST", r"/api/connexion")
def api_connexion(req):
    """Ouvre une session. Le mot de passe ne sort jamais d'ici.

    Il n'est ni journalise, ni renvoye, ni conserve : seule son empreinte
    existe en base, et la comparaison se fait sur l'empreinte.
    """
    from . import comptes
    b = req.body or {}
    try:
        jeton, qui = comptes.connexion(b.get("identifiant"),
                                       b.get("mot_de_passe"),
                                       agent=req.agent)
    except ValueError as exc:
        raise ApiError(str(exc), 401)
    _poser_cookie(req, jeton)
    return {"ok": True, "compte": qui}


@route("POST", r"/api/deconnexion")
def api_deconnexion(req):
    from . import comptes
    comptes.deconnexion(req.jeton)
    req.cookie = "snapsession=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    return {"ok": True}


@route("GET", r"/api/moi")
def api_moi(req):
    """Qui est connecté, et le CRM est-il ouvert sur internet."""
    from . import comptes
    return {"compte": comptes.compte_de_session(req.jeton),
            "ouvert": db.get_setting("ouvert_internet", "0") == "1"}


def _compte_courant(req, roles=None):
    from . import comptes
    qui = comptes.compte_de_session(req.jeton)
    if not qui:
        raise ApiError("connexion requise", 401)
    if roles and qui.get("role") not in roles:
        raise ApiError("cet espace ne vous est pas ouvert", 403)
    return qui


@route("GET", r"/api/espace/missions")
def api_espace_missions(req):
    """Les missions de CE compte, et rien d'autre.

    Le filtre est ici, cote serveur, sur l'identite de la session. Il n'y a
    aucun parametre qui permette d'en demander d'autres.
    """
    qui = _compte_courant(req)
    rows = db.rows_to_dicts(db.q(
        "SELECT m.id, m.title, m.starts_at, m.place, m.body, m.status, "
        "       m.assigned, m.posted_at, m.updated_at, "
        "       t.name AS tournoi, t.city, t.sport, t.nb_equipes, t.url, "
        "       t.start_date, t.end_date "
        "FROM missions m LEFT JOIN tournaments t ON t.id = m.tournament_id "
        "WHERE m.compte_id = ? AND COALESCE(m.status,'') <> 'cancelled' "
        "ORDER BY m.starts_at IS NULL, m.starts_at", (qui["id"],)))
    for r in rows:
        r["quand"] = missions.date_longue(r.get("starts_at"))
    return {"compte": qui, "items": rows}


@route("POST", r"/api/espace/missions/(\d+)/repondre")
def api_espace_repondre(req, mid):
    """Le sourceur dit où il en est : trouvé, cherche encore, ou rien.

    Il ne peut repondre que sur SES missions : on verifie le compte avant
    d'ecrire, pas apres.
    """
    qui = _compte_courant(req)
    b = req.body or {}
    m = db.q1("SELECT id, compte_id, title FROM missions WHERE id = ?", (int(mid),))
    if not m or m["compte_id"] != qui["id"]:
        raise ApiError("mission introuvable", 404)

    etat = (b.get("etat") or "").strip()
    if etat not in ("en cours", "trouvé", "rien"):
        raise ApiError("état inconnu")
    mot = (b.get("mot") or "").strip()[:1000]
    trouve = (b.get("trouve") or "").strip()[:200]

    note = "[%s] %s" % (util.now_iso()[:16], etat)
    if trouve:
        note += " — " + trouve
    if mot:
        note += "\n" + mot
    db.ex("UPDATE missions SET status = ?, assigned = ?, "
          "body = COALESCE(body,'') || ? , updated_at = ? WHERE id = ?",
          (etat, trouve or qui["nom"], "\n\n--- " + qui["nom"] + " ---\n" + note,
           util.now_iso(), int(mid)))
    db.log("mission", "%s a répondu sur « %s » : %s"
           % (qui["nom"], m["title"], etat))
    return {"ok": True}


@route("GET", r"/api/comptes")
def api_comptes(req):
    from . import comptes
    return {"items": comptes.lister(), "roles": list(comptes.ROLES),
            "ouvert": db.get_setting("ouvert_internet", "0") == "1"}


@route("POST", r"/api/comptes")
def api_comptes_creer(req):
    from . import comptes
    b = req.body or {}
    try:
        return {"compte": comptes.creer(b.get("identifiant"), b.get("mot_de_passe"),
                                        b.get("nom"), b.get("role") or "sourceur",
                                        b.get("team_id"))}
    except ValueError as exc:
        raise ApiError(str(exc))


@route("PATCH", r"/api/comptes/(\d+)")
def api_comptes_changer(req, cid):
    from . import comptes
    b = req.body or {}
    try:
        if b.get("identifiant") or "nom" in b:
            comptes.renommer(int(cid), b.get("identifiant"), b.get("nom"))
        if b.get("mot_de_passe"):
            comptes.changer_mot_de_passe(int(cid), b["mot_de_passe"])
        if "actif" in b:
            comptes.activer(int(cid), bool(b["actif"]))
    except ValueError as exc:
        raise ApiError(str(exc))
    return {"compte": comptes.lire(int(cid))}


@route("DELETE", r"/api/comptes/(\d+)")
def api_comptes_supprimer(req, cid):
    from . import comptes
    comptes.supprimer(int(cid))
    return {"ok": True}


@route("POST", r"/api/comptes/ouverture")
def api_comptes_ouverture(req):
    """Ouvre ou ferme le CRM sur internet.

    Ferme, rien ne demande de mot de passe et le CRM n'ecoute que la maison.
    Ouvert, TOUT en demande un — y compris depuis le Mac. C'est volontaire :
    derriere un tunnel, une requete venue du monde entier se presente comme
    venant de 127.0.0.1, et une serrure fondee sur l'adresse s'ouvrirait
    toute seule.
    """
    from . import comptes
    b = req.body or {}
    ouvrir = bool(b.get("ouvert"))
    if ouvrir and not db.q1("SELECT id FROM comptes WHERE role = 'patron' "
                            "AND actif = 1"):
        raise ApiError("créez d'abord VOTRE compte (rôle « patron »), "
                       "sinon vous vous enfermez dehors")
    db.set_setting("ouvert_internet", "1" if ouvrir else "0")
    db.log("comptes", "CRM %s sur internet" % ("ouvert" if ouvrir else "fermé"))
    return {"ouvert": ouvrir}


@route("POST", r"/api/espace/mot-de-passe")
def api_espace_mot_de_passe(req):
    """Le sourceur change SON mot de passe, sans passer par personne.

    Sinon il faudrait que Julien lui en fabrique un, le lui dicte par
    message, et que ce message traine ensuite dans une conversation. Un mot
    de passe qu'on ne peut pas changer soi-meme finit toujours ecrit quelque
    part.

    L'ancien est exige : une session ouverte sur un telephone pose quelque
    part ne doit pas suffire a mettre le titulaire dehors.
    """
    from . import comptes
    qui = _compte_courant(req)
    b = req.body or {}
    if not comptes.verifier(qui["id"], b.get("actuel") or ""):
        raise ApiError("l'ancien mot de passe ne correspond pas")
    try:
        comptes.changer_mot_de_passe(qui["id"], b.get("nouveau") or "")
    except ValueError as exc:
        raise ApiError(str(exc))
    # changer_mot_de_passe ferme toutes les sessions, y compris celle-ci :
    # c'est voulu, et l'ecran le dit avant de redemander l'entree.
    return {"ok": True, "reconnexion": True}


@route("GET", r"/api/equipe/dates")
def api_equipe_dates(req):
    """Vos dates a venir, groupees par jour, avec qui y va.

    La vieille liste « Vos dates sans photographe » ne regardait pas le
    calendrier : elle prenait les tournois dont la FICHE avait ete validee.
    Quarante cartes, dont trente-six que Julien ne couvre pas, et aucune de
    celles qu'il couvre vraiment. Ici, une seule source : son calendrier.

    Groupe par jour, parce qu'une sortie n'est pas un evenement : quatre
    « Shoot » le meme matin au stade de Brulat, c'est un seul deplacement et
    un seul photographe a trouver.
    """
    from . import couverture
    jours_devant = int(req.query.get("jours", 45))
    brut, erreur = _calendrier_a_venir(force=bool(req.query.get("force")))
    if erreur and not brut:
        return {"jours": [], "erreur": erreur}

    limite = (util.today() + datetime.timedelta(days=jours_devant)).isoformat()
    brut = [e for e in brut if (e.get("start_date") or "") <= limite]
    notes = couverture.toutes()

    groupes = {}
    for e in brut:
        # Le calendrier publie ne remplit pas « uid » — il n'a jamais ete
        # rempli pour cette source, et personne ne s'en etait apercu parce
        # que rien ne s'en servait. C'est « external_id » qui porte l'UID du
        # fichier .ics, et il est unique. Sans identifiant stable, on ne peut
        # pas noter qui va sur quelle date.
        uid = e.get("uid") or e.get("external_id") or ""
        note = notes.get(uid) or {}
        if note.get("etat") == "ignore" and not req.query.get("tout"):
            continue
        jour = e.get("start_date") or ""
        lieu = (e.get("venue") or e.get("city") or "").strip()
        cle = (jour, lieu.lower())
        g = groupes.setdefault(cle, {
            "jour": jour, "lieu": lieu, "ville": e.get("city") or "",
            "quand": missions.date_longue(jour),
            "jours": _jours_avant(jour), "shoots": [],
        })
        g["shoots"].append({
            "uid": uid, "nom": e.get("name") or "",
            "debut": e.get("debut") or "", "fin": e.get("fin") or "",
            "qui": note.get("qui"), "team_id": note.get("team_id"),
            "etat": note.get("etat") or "",
        })

    sortie = sorted(groupes.values(), key=lambda g: (g["jour"], g["lieu"]))
    for g in sortie:
        pris = [s for s in g["shoots"] if s.get("qui")]
        cherche = [s for s in g["shoots"] if s.get("etat") == "cherche"]
        g["pourvu"] = len(pris) == len(g["shoots"]) and bool(pris)
        g["noms"] = sorted({s["qui"] for s in pris if s["qui"]})
        g["cherche"] = bool(cherche)
        g["combien"] = len(g["shoots"])
        if g["ville"]:
            d = geo.depuis_cannes(g["ville"], None)
            g["km"] = (d or {}).get("km")
            g["minutes"] = (d or {}).get("minutes")

    sans = [g for g in sortie if not g["pourvu"]]
    return {
        "jours": sortie,
        "combien": len(sortie),
        "sans_personne": len(sans),
        "prochaine_sans_personne": sans[0]["jour"] if sans else None,
        "equipe": [dict(r) for r in db.q(
            "SELECT id, name, sports FROM team "
            "WHERE COALESCE(active, 1) = 1 ORDER BY name")],
        "ignorees": sum(1 for n in notes.values() if n.get("etat") == "ignore"),
    }


@route("POST", r"/api/equipe/dates/qui")
def api_equipe_dates_qui(req):
    """Note qui va sur une date — ou qu'on cherche, ou qu'on l'écarte."""
    from . import couverture
    b = req.body or {}
    try:
        if b.get("etat") == "rien":
            couverture.enlever(b.get("uid"))
            return {"ok": True, "efface": True}
        return {"ok": True, "couverture": couverture.poser(
            b.get("uid"), team_id=b.get("team_id"),
            etat=b.get("etat") or "pris", note=b.get("note"),
            jour=b.get("jour"), titre=b.get("titre"))}
    except ValueError as exc:
        raise ApiError(str(exc))


@route("POST", r"/api/tournaments/(\d+)/sourceur")
def api_tournoi_sourceur(req, tid):
    """« Peux-tu monter une équipe là-bas ? » — la question part au sourceur.

    Avant de contacter l'organisateur, pas apres : un tournoi a six heures
    de route ne vaut un message que si quelqu'un peut y aller. Boby est un
    sourceur de photographes, pas un renfort pour les dates deja bookees —
    celles-la se couvrent avec les equipiers en place.
    """
    from . import comptes, couverture  # noqa: F401 — couverture reste pour l'autre voie
    b = req.body or {}
    t = db.row_to_dict(db.q1(
        "SELECT t.*, c.name AS club FROM tournaments t "
        "LEFT JOIN clubs c ON c.id = t.club_id WHERE t.id = ?", (int(tid),)))
    if not t:
        raise ApiError("tournoi introuvable", 404)

    cid = b.get("compte_id")
    if not cid:
        actifs = [c for c in comptes.lister()
                  if c.get("actif") and c.get("role") == "sourceur"]
        if len(actifs) != 1:
            raise ApiError("à quel compte ? (plusieurs sourceurs, ou aucun)")
        cid = actifs[0]["id"]
    qui = comptes.lire(int(cid))
    if not qui or not qui.get("actif"):
        raise ApiError("compte introuvable ou suspendu", 404)

    deja = db.q1("SELECT id FROM missions WHERE tournament_id = ? "
                 "AND compte_id IS NOT NULL AND COALESCE(status,'') <> 'cancelled'",
                 (int(tid),))
    if deja:
        raise ApiError("déjà demandé pour ce tournoi")

    lieu = util.lieu_lisible(t.get("venue"), t.get("city"))
    mot = (b.get("mot") or "").strip() or "\n".join(x for x in [
        t.get("name") or t.get("club") or "Tournoi",
        " · ".join(x for x in [
            missions.date_longue(t.get("start_date")), lieu,
            t.get("sport"),
            ("%s équipes" % t["nb_equipes"]) if t.get("nb_equipes") else None,
            ("%s km de Cannes" % t["distance_km"]) if t.get("distance_km") else None,
        ] if x),
        "",
        "Peux-tu monter une équipe sur place ? Je ne contacte "
        "l'organisateur que si oui.",
    ] if x is not None)

    mid = db.insert("missions", {
        "tournament_id": int(tid),
        "title": t.get("name") or t.get("club") or "Tournoi",
        "starts_at": t.get("start_date"),
        "place": lieu or None,
        "body": mot[:4000],
        "status": "à pourvoir",
        "compte_id": int(cid),
        "assigned": qui.get("nom") or qui["identifiant"],
        # updated_at est NOT NULL dans la table : l'oublier fait echouer
        # l'insertion sur une contrainte, au clic, sous le nez de Julien.
        "created_at": util.now_iso(), "posted_at": util.now_iso(),
        "updated_at": util.now_iso(),
    })
    db.log("mission", "« %s » : équipe possible ? demandé à %s"
           % (t.get("name") or tid, qui.get("nom")))
    return {"ok": True, "mission_id": mid, "a": qui.get("nom"),
            "sourceur": _ou_en_est_le_sourceur(int(tid))}


@route("POST", r"/api/dispatch/confier")
def api_dispatch_confier(req):
    """Envoie une sortie au sourceur : « trouve-moi quelqu'un là-bas ».

    C'est le cas qui manquait. Une date bookée à six heures de route n'a pas
    de solution dans l'équipe : personne n'ira. Elle n'a pas non plus a
    rester affichee en rouge jusqu'au dernier jour. Elle part chez le
    sourceur, qui cherche sur place — et elle reste visible ici, marquee
    « on cherche », avec son nom dessus.
    """
    from . import comptes, couverture
    b = req.body or {}
    uid = (b.get("uid") or "").strip()
    cid = b.get("compte_id")
    if not uid:
        raise ApiError("quel événement ?")
    if not cid:
        # Un seul sourceur, le cas courant : pas la peine de le demander.
        actifs = [c for c in comptes.lister()
                  if c.get("actif") and c.get("role") == "sourceur"]
        if len(actifs) != 1:
            raise ApiError("à quel compte ? (plusieurs sourceurs, ou aucun)")
        cid = actifs[0]["id"]
    qui = comptes.lire(int(cid))
    if not qui or not qui.get("actif"):
        raise ApiError("compte introuvable ou suspendu", 404)

    titre = (b.get("titre") or "Sortie").strip()
    jour = (b.get("jour") or "").strip()
    lieu = (b.get("lieu") or "").strip()
    mot = (b.get("mot") or "").strip()

    mid = db.insert("missions", {
        "title": titre,
        "starts_at": (jour + " 09:00:00") if len(jour) == 10 else (jour or None),
        "place": lieu or None,
        "body": mot or None,
        "status": "à pourvoir",
        "compte_id": int(cid),
        "assigned": qui.get("nom") or qui["identifiant"],
        "created_at": util.now_iso(),
        "posted_at": util.now_iso(),
        "updated_at": util.now_iso(),      # NOT NULL dans la table
    })
    # Marquee « on cherche » ici aussi : sinon la sortie reste en rouge et on
    # la confie une deuxieme fois trois jours plus tard.
    couverture.poser(uid, etat="cherche", jour=jour or None, titre=titre,
                     note="confiée à %s" % (qui.get("nom") or qui["identifiant"]))
    db.log("mission", "« %s » confiée à %s" % (titre, qui.get("nom")))
    return {"ok": True, "mission_id": mid, "a": qui.get("nom")}


@route("GET", r"/api/deja-ecrit")
def api_deja_ecrit(req):
    """A-t-on deja ecrit a ce numero, ce club, cette adresse ?

    Interrogee juste avant un envoi. L'envoi WhatsApp part pour de vrai au
    clic : s'apercevoir apres coup qu'on avait deja ecrit oblige a aller
    supprimer le message dans la conversation, quand il n'est pas deja lu.
    """
    from . import deja
    d = deja.chercher(tel=req.query.get("tel"),
                      club_id=req.query.get("club_id"),
                      email=req.query.get("email"))
    d["phrase"] = deja.phrase(d)
    return d


@route("GET", r"/api/tunnel")
def api_tunnel(req):
    from . import tunnel
    d = tunnel.etat()
    d["version"] = tunnel.version()
    return d


@route("POST", r"/api/tunnel/ouvrir")
def api_tunnel_ouvrir(req):
    """Serrure, verification, tunnel — sur un clic, dans cet ordre.

    L'ordre n'est pas negociable, et il est impose la ou il compte : cote
    serveur. Meme en appelant cette adresse a la main, on ne peut pas obtenir
    le tunnel sans la serrure.
    """
    from . import tunnel
    try:
        return tunnel.ouvrir()
    except RuntimeError as exc:
        raise ApiError(str(exc))


@route("POST", r"/api/tunnel/fermer")
def api_tunnel_fermer(req):
    from . import tunnel
    b = req.body or {}
    return tunnel.fermer(enlever_serrure=not bool(b.get("garder_serrure")))


@route("GET", r"/api/equipe/confiees")
def api_equipe_confiees(req):
    """Ce qu'on a confie, a qui, et ou chacun en est.

    Le pendant de l'espace du sourceur, vu de votre cote : la reponse a
    « qu'est-ce que je lui ai envoye, et qu'est-ce qu'il en a fait ».
    """
    rows = db.rows_to_dicts(db.q(
        "SELECT m.id, m.title, m.starts_at, m.place, m.status, m.assigned, "
        "       m.posted_at, m.updated_at, m.compte_id, "
        "       c.identifiant, c.nom AS qui, c.role, "
        "       t.name AS tournoi, t.city, t.sport, t.nb_equipes "
        "FROM missions m "
        "JOIN comptes c ON c.id = m.compte_id "
        "LEFT JOIN tournaments t ON t.id = m.tournament_id "
        "WHERE COALESCE(m.status,'') <> 'cancelled' "
        "ORDER BY m.starts_at IS NULL, m.starts_at"))
    for r in rows:
        r["quand"] = missions.date_longue(r.get("starts_at"))
    return {"items": rows}


@route("GET", r"/api/photos")
def api_photos(req):
    fichiers, serie, vide = _photos_exemple(req.query.get("sport"),
                                            req.query.get("dossier"))
    return {"n": len(fichiers), "serie": serie, "dossier_vide": vide,
            "series": sorted(_dossiers_photos().keys()),
            "vides": _dossiers_vides(),
            "noms": [os.path.basename(f) for f in fichiers],
            "octets": sum(os.path.getsize(f) for f in fichiers)}


@route("POST", r"/api/photos/copier")
def api_photos_copier(req):
    """Met les planches de photos dans le presse-papiers, toutes d'un coup.

    Celles du sport demande, si elles existent ; sinon celles de la racine.
    """
    b = req.body or {}
    fichiers, serie, vide = _photos_exemple(b.get("sport"), b.get("dossier"))
    if not fichiers:
        raise ApiError("aucune photo dans le dossier photos/%s"
                       % ((serie + "/") if serie else ""))
    return {"ok": True, "n": _vers_presse_papiers(fichiers), "serie": serie,
            "dossier_vide": vide,
            "noms": [os.path.basename(f) for f in fichiers]}


# Deux plaquettes, deux metiers : celle des tournois montre des photos
# d'action, celle du studio montre des portraits. Envoyer la mauvaise, c'est
# proposer un reportage a un club qui voulait des portraits.
PLAQUETTES = {"sport": "plaquette", "studio": "plaquette_studio"}


@route("GET", r"/api/plaquette")
def api_plaquette(req):
    """Ou en est la plaquette, et peut-on la joindre."""
    cle = PLAQUETTES.get(req.query.get("quelle") or "sport", "plaquette")
    rel = db.get_setting(cle) or ""
    chemin = os.path.join(RACINE_PROJET, rel) if rel else ""
    existe = bool(rel) and os.path.isfile(chemin)
    return {"chemin": rel, "prete": existe,
            "nom": os.path.basename(rel) if rel else None,
            "octets": os.path.getsize(chemin) if existe else 0}


@route("GET", r"/api/plaquette/vignettes")
def api_plaquette_vignettes(req):
    """Les images de la plaquette, pour montrer le rendu sur l'ecran de vente.

    On sert depuis le dossier de la plaquette, jamais ailleurs : le chemin est
    verifie avant d'etre rendu.
    """
    quelle = req.query.get("quelle") or "sport"
    rel = db.get_setting(PLAQUETTES.get(quelle, "plaquette")) or ""
    if not rel:
        return {"images": []}
    dossier = os.path.join(RACINE_PROJET, os.path.dirname(rel),
                           "studio" if quelle == "studio" else "", "img")
    dossier = os.path.abspath(dossier)
    if not dossier.startswith(os.path.abspath(RACINE_PROJET)) or not os.path.isdir(dossier):
        return {"images": []}
    noms = sorted(n for n in os.listdir(dossier)
                  if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
    base = "/plaquettes/" + os.path.dirname(rel).replace(
        "plaquettes", "").strip("/")
    base = "/img-plaquette/%s/" % quelle
    return {"images": [base + n for n in noms[:12]]}


@route("POST", r"/api/plaquette/copier")
def api_plaquette_copier(req):
    """Met la plaquette dans le presse-papiers, prete a coller dans WhatsApp.

    Un lien wa.me ne transporte que du texte : aucune piece jointe ne peut
    partir avec. En revanche macOS sait mettre un FICHIER dans le presse-
    papiers, et WhatsApp accepte de le coller. Le geste devient donc : copier
    le message, le coller, copier la plaquette, la coller. Deux Cmd+V au lieu
    d'aller chercher le PDF dans le Finder a chaque fois.
    """
    cle = PLAQUETTES.get((req.body or {}).get("quelle") or "sport", "plaquette")
    rel = db.get_setting(cle) or ""
    if not rel:
        raise ApiError("aucune plaquette enregistrée dans les réglages")
    chemin = os.path.abspath(os.path.join(RACINE_PROJET, rel))
    if not chemin.startswith(os.path.abspath(RACINE_PROJET)) or not os.path.isfile(chemin):
        raise ApiError("plaquette introuvable : %s" % rel, 404)
    _vers_presse_papiers([chemin])
    return {"ok": True, "nom": os.path.basename(chemin)}


@route("GET", r"/api/relais")
def api_relais(req):
    """Les medias, communautes de parents et relais institutionnels.

    Ce ne sont pas des clubs : ils n'organisent rien, ils parlent aux parents
    — c'est-a-dire aux acheteurs. On les demarche autrement, et on les range
    donc a part, mais dans la meme table, pour qu'ils heritent des messages,
    des relances et du suivi des echanges.
    """
    rows = db.rows_to_dicts(db.q(
        "SELECT * FROM clubs WHERE relais = 1 AND do_not_contact = 0 "
        "ORDER BY relais_priorite DESC, name"))
    for r in rows:
        r["dernier_echange"] = r.get("last_contact_at")
        r["brouillon"] = db.row_to_dict(db.q1(
            "SELECT id, status, channel, to_addr FROM drafts WHERE club_id = ? "
            "ORDER BY id DESC LIMIT 1", (r["id"],)) or {}) or None
        r["joignable"] = bool(r.get("phone") or r.get("email"))
    familles = [("media", "Médias sorties enfants"),
                ("communaute", "Communautés de parents"),
                ("institutionnel", "Relais institutionnels")]
    return {
        "items": rows,
        "familles": [{"cle": c, "nom": n,
                      "n": sum(1 for r in rows if r.get("relais_type") == c)}
                     for c, n in familles],
        "total": len(rows),
        "a_contacter": sum(1 for r in rows if not r.get("last_contact_at")),
    }


@route("GET", r"/api/relais/(\d+)/message")
def api_relais_message(req, cid):
    """Le mot qu'on enverrait a ce relais. Rendu a la volee, rien n'est envoye."""
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("relais introuvable", 404)
    rendu = drafts.render("relais_premier", club=club)
    return {"objet": rendu.get("subject"), "corps": rendu.get("body"),
            "destinataire": club.get("email") or club.get("phone") or None,
            "modele": "relais_premier"}


@route("GET", r"/api/badges")
def api_badges(req):
    """Les compteurs du menu, les memes depuis n'importe quel ecran.

    Le menu vit sur quatre pages ; s'il allait chercher ses chiffres dans le
    DOM de la plateforme, il serait vide ailleurs. Une seule route, un seul
    jeu de chiffres.
    """
    def n(sql, *a):
        return db.q1(sql, a)["n"]

    chats = wa.liste("tout")
    # Les memes definitions que les ecrans : « mes dates » compte ce qui tombe
    # dans les sept jours, « suivi » ce qui est a traiter maintenant. Un badge
    # qui compte autre chose que l'ecran qu'il annonce ne sert a rien.
    # « Mes dates », ce sont les evenements du calendrier de travail, pas les
    # tournois reperes : le badge compte ceux qui tombent dans les sept jours.
    proches = 0
    auj = datetime.date.today()
    try:
        # Sans attendre : le menu ne doit JAMAIS retarder l'ouverture d'une
        # page. Calendrier mettait quarante secondes a rendre ses sept
        # colonnes, le garde-temps coupait a vingt-cinq, et comme un echec ne
        # se mettait pas en cache, les quatre ecrans repayaient l'attente a
        # chaque ouverture. Le compteur « Mes dates » apparait maintenant des
        # que le calendrier a ete lu une fois, et la page s'ouvre tout de suite.
        evs, _, _ = agenda.lister_vite(attendre=False)
        for e in evs:
            try:
                d = datetime.date.fromisoformat((e.get("debut") or "")[:10])
            except (TypeError, ValueError):
                continue
            if 0 <= (d - auj).days <= 7:
                proches += 1
    except Exception:
        pass           # calendrier muet : pas de chiffre, pas de panne
    colonnes = {c["cle"]: len(c.get("cartes") or []) for c in wa.suivi()["colonnes"]}
    # « Repondre » : ceux qui attendent une reponse PLUS les rappels echus.
    # C'est le seul compteur du menu qui chiffre de l'argent deja gagne.
    try:
        repondre = (colonnes.get("a_traiter", 0)
                    + db.q1("SELECT COUNT(DISTINCT t.club_id) AS n FROM tasks t "
                            "JOIN clubs c ON c.id = t.club_id "
                            "WHERE t.kind = 'relance' AND t.status = 'pending' "
                            "AND date(t.due_at) < date('now') "
                            "AND COALESCE(c.do_not_contact, 0) = 0")["n"])
    except Exception:
        repondre = colonnes.get("a_traiter", 0)
    # « Rencontres » : les gens croises recemment dont la derniere parole est
    # a eux. Le chiffre ne compte que ceux-la — pas toute la liste, sinon le
    # menu afficherait cent cinquante et ne voudrait plus rien dire.
    try:
        from . import rencontres as _renc
        n_renc = _renc.tout(_renc.JOURS_DEFAUT)["n_attendent"]
    except Exception:
        n_renc = 0
    return {
        "rencontres": n_renc,
        "repondre": repondre,
        "dates": proches,
        "suivi": colonnes.get("a_traiter", 0),
        "partenaires": n("SELECT COUNT(*) AS n FROM clubs WHERE COALESCE(partenaire,0) = 1"),
        "relais": n("SELECT COUNT(*) AS n FROM clubs WHERE COALESCE(relais,0) = 1 "
                    "AND COALESCE(do_not_contact,0) = 0"),
        "rattacher": sum(1 for c in chats
                         if not c.get("club_id") and c.get("suggestions")),
    }


@route("GET", r"/api/journee")
def api_journee(req):
    """Le point du jour : quoi faire, dans quel ordre, et rien de plus."""
    d = journee.liste(req.query.get("max"))
    d["whatsapp"] = _fraicheur_whatsapp()
    return d


# Les chiffres de cet ecran sortent du journal WhatsApp. Si le journal cesse
# d'etre relu, ils ne disparaissent pas : ils vieillissent sans rien dire.
# C'est exactement ce qui s'est produit — quatorze jours, repere seulement
# dans le journal du scan. L'ecran doit donc porter son propre age.
VIEUX_JOURS = 3


def _fraicheur_whatsapp():
    lu = db.get_setting("wa_lu_le")
    if not lu:
        return {"lu_le": None, "jours": None, "vieux": True,
                "dit": "Le journal WhatsApp n'a jamais été relu."}
    try:
        quand = datetime.datetime.fromisoformat(lu)
    except ValueError:
        return {"lu_le": lu, "jours": None, "vieux": False, "dit": None}
    jours = (datetime.datetime.now() - quand).days
    if jours < VIEUX_JOURS:
        return {"lu_le": lu, "jours": jours, "vieux": False, "dit": None}
    return {
        "lu_le": lu, "jours": jours, "vieux": True,
        "dit": ("Les chiffres de cet écran datent du %s, il y a %d jours : "
                "le journal WhatsApp n'a pas été relu depuis. Un club qui vous "
                "a répondu entre-temps n'apparaît pas, et un club que vous avez "
                "relancé compte encore comme sans réponse."
                % (lu[:10], jours)),
    }


@route("POST", r"/api/journee/rayer")
def api_journee_rayer(req):
    """Raye une ligne du jour : « fait » jusqu'a demain, « plus tard » une semaine.

    Rien n'est efface. Si l'action n'a pas reellement avance, elle revient a
    l'echeance — la liste ne doit pas pouvoir se mentir a elle-meme.
    """
    try:
        return journee.ecarter(req.body.get("cle"), req.body.get("motif") or "fait")
    except ValueError as e:
        raise ApiError(str(e))


@route("POST", r"/api/journee/reprendre")
def api_journee_reprendre(req):
    """Remet une ligne rayee dans la liste, tout de suite.

    Sans « cle », remet tout : c'est le rattrapage d'un clic de travers.
    """
    if not (req.body.get("cle") or "").strip():
        return journee.tout_reprendre()
    return journee.reprendre(req.body.get("cle"))


@route("GET", r"/api/dashboard")
def api_dashboard(req):
    stats = repo.dashboard()
    stats["today"] = repo.list_tasks(status="pending", horizon_days=0, limit=50)
    stats["drafts"] = drafts.list_drafts("draft", limit=20)
    stats["next_tournaments"] = db.rows_to_dicts(db.q(
        "SELECT * FROM tournaments WHERE start_date >= date('now') "
        "ORDER BY start_date LIMIT 8"))
    stats["settings_ready"] = bool(db.get_setting("my_name") and db.get_setting("pitch"))
    return stats


# --------------------------------------------------------------------------
# Clubs
# --------------------------------------------------------------------------

@route("GET", r"/api/clubs")
def api_clubs(req):
    """La liste des clubs, contacts compris.

    L'ecran Clubs affichait « Contacts : 2 » sans jamais dire lesquels : il
    fallait ouvrir une fiche pour trouver un numero. On envoie donc les
    contacts avec, et l'onglet Contacts n'a plus lieu d'etre.
    """
    p = req.query
    items = repo.list_clubs(
        search=p.get("q"), status=p.get("status"), dept=p.get("dept"),
        tag=p.get("tag"), limit=int(p.get("limit", 500)),
        order=_safe_order(p.get("order")))
    if p.get("light") == "1" or not items:
        return {"items": items}

    ids = [c["id"] for c in items]
    marques = ",".join("?" for _ in ids)
    par_club = {}
    for r in db.q(
            "SELECT club_id, id, first_name, last_name, role, phone, phone2, email "
            "FROM contacts WHERE club_id IN (%s) "
            "ORDER BY (phone IS NULL OR phone=''), last_name, first_name" % marques,
            tuple(ids)):
        par_club.setdefault(r["club_id"], []).append(db.row_to_dict(r))

    # Ou en est la discussion, quand il y en a une : c'est ce qu'on veut
    # savoir en regardant un club, pas la date d'une tache automatique.
    etapes = {}
    for r in db.q("SELECT club_id, id FROM wa_chats "
                  "WHERE ecarte = 0 AND club_id IN (%s)" % marques, tuple(ids)):
        etapes.setdefault(r["club_id"], []).append(r["id"])

    suivi = {}
    try:
        for col in wa.suivi()["colonnes"]:
            for carte in col["cartes"]:
                suivi.setdefault(carte["club_id"], carte["etape_nom"])
    except Exception:
        suivi = {}

    for c in items:
        c["contacts"] = par_club.get(c["id"], [])
        c["ou_en_est"] = suivi.get(c["id"])
    return {"items": items}


def _safe_order(value):
    allowed = {
        "recent": "c.updated_at DESC", "nom": "c.name COLLATE NOCASE",
        "dept": "c.dept, c.name COLLATE NOCASE", "statut": "c.status, c.name COLLATE NOCASE",
        "froid": "c.last_contact_at IS NOT NULL, c.last_contact_at",
    }
    return allowed.get(value or "recent", "c.updated_at DESC")


@route("GET", r"/api/clubs/(\d+)")
def api_club_get(req, club_id):
    club = repo.get_club(int(club_id))
    if not club:
        raise ApiError("club introuvable", 404)
    return club


@route("POST", r"/api/clubs")
def api_club_create(req):
    club_id, action = repo.upsert_club(req.body, merge=bool(req.body.get("merge", True)))
    if not club_id:
        raise ApiError("il faut au moins un nom de club")
    return {"id": club_id, "action": action, "club": repo.get_club(club_id)}


@route("PATCH", r"/api/clubs/(\d+)")
def api_club_update(req, club_id):
    data = dict(req.body)
    data.pop("id", None)
    allowed = {"name", "sport", "city", "postal_code", "dept", "website", "email", "phone",
               "address", "status", "priority", "source", "source_url", "notes", "tags",
               "do_not_contact", "country"}
    patch = {k: v for k, v in data.items() if k in allowed}
    if "postal_code" in patch and not patch.get("dept"):
        patch["dept"] = util.dept_from_postal(patch["postal_code"])
    if "name" in patch:
        patch["name_key"] = util.norm_key(patch["name"])
    if "phone" in patch:
        patch["phone"] = util.normalize_phone(patch["phone"]) or patch["phone"]
    patch["updated_at"] = util.now_iso()
    db.update("clubs", int(club_id), patch)
    return repo.get_club(int(club_id))


@route("DELETE", r"/api/clubs/(\d+)")
def api_club_delete(req, club_id):
    db.delete("clubs", int(club_id))
    return {"ok": True}


@route("POST", r"/api/clubs/(\d+)/status")
def api_club_status(req, club_id):
    status = req.body.get("status")
    if status not in dict(repo.STATUSES):
        raise ApiError("statut inconnu : %s" % status)
    return repo.set_club_status(int(club_id), status, req.body.get("note"))


@route("POST", r"/api/clubs/(\d+)/optout")
def api_club_optout(req, club_id):
    repo.opt_out(int(club_id), req.body.get("reason"))
    return repo.get_club(int(club_id))


@route("POST", r"/api/clubs/(\d+)/interactions")
def api_club_interaction(req, club_id):
    body = req.body
    repo.add_interaction(int(club_id), kind=body.get("kind", "note"),
                         direction=body.get("direction", "out"),
                         subject=body.get("subject"), body=body.get("body"),
                         contact_id=body.get("contact_id"),
                         occurred_at=body.get("occurred_at"),
                         outcome=body.get("outcome"))
    if body.get("schedule_followups"):
        repo.schedule_sequence(int(club_id), body.get("contact_id"))
    return repo.get_club(int(club_id))


@route("POST", r"/api/clubs/(\d+)/sequence")
def api_club_sequence(req, club_id):
    repo.schedule_sequence(int(club_id), req.body.get("contact_id"),
                           start=req.body.get("start"))
    return repo.get_club(int(club_id))


# --------------------------------------------------------------------------
# Contacts
# --------------------------------------------------------------------------

@route("GET", r"/api/contacts")
def api_contacts(req):
    return {"items": repo.list_contacts(search=req.query.get("q"),
                                        club_id=req.query.get("club_id"),
                                        has_phone=req.query.get("has_phone"))}


@route("POST", r"/api/contacts")
def api_contact_create(req):
    contact_id, action = repo.upsert_contact(req.body)
    if not contact_id:
        raise ApiError("il faut au moins un nom, un email ou un telephone")
    return {"id": contact_id, "action": action, "contact": repo.get_contact(contact_id)}


@route("PATCH", r"/api/contacts/(\d+)")
def api_contact_update(req, contact_id):
    allowed = {"club_id", "first_name", "last_name", "role", "email", "phone", "phone2",
               "handle", "channel", "notes", "source", "source_url", "do_not_contact"}
    patch = {k: v for k, v in req.body.items() if k in allowed}
    for key in ("phone", "phone2"):
        if key in patch:
            patch[key] = util.normalize_phone(patch[key]) or patch[key] or None
    if "email" in patch:
        patch["email"] = util.normalize_email(patch["email"]) or patch["email"] or None
    patch["updated_at"] = util.now_iso()
    db.update("contacts", int(contact_id), patch)
    return repo.get_contact(int(contact_id))


@route("DELETE", r"/api/contacts/(\d+)")
def api_contact_delete(req, contact_id):
    db.delete("contacts", int(contact_id))
    return {"ok": True}


# --------------------------------------------------------------------------
# Taches / relances
# --------------------------------------------------------------------------

# Le calendrier de Julien, garde en memoire un quart d'heure : le tableau de
# bord se recharge souvent, iCloud n'a pas besoin d'etre interroge a chaque fois.
_CAL_CACHE = {"quand": 0.0, "items": []}


def _jours_avant(iso):
    """Combien de jours avant l'echeance. Negatif si c'est passe."""
    d = util.parse_date(iso)
    return (d - util.today()).days if d else None


# « calendrier illisible : HTTP Error 404: Not Found » ne dit ni ce qui est
# casse ni quoi faire. Un lien iCloud publie meurt des qu'on republie le
# calendrier — le jeton change, l'ancien cesse de repondre — et le reglage
# n'avait meme pas de champ pour le remplacer. On nomme donc la panne.
def _pourquoi_calendrier_muet(exc, url):
    code = getattr(exc, "code", None)
    ou = "iCloud" if "icloud.com" in (url or "") else "le serveur du calendrier"
    if code == 404:
        return (u"%s ne renvoie pas votre calendrier (404), deux fois de suite. "
                u"C'est souvent passager de leur côté — réessayez dans quelques "
                u"minutes. Si ça dure, c'est que le lien a changé : reprenez-le "
                u"dans Calendrier (clic droit sur le calendrier, puis Partager "
                u"le calendrier) et collez-le dans Réglages, « Lien de votre "
                u"calendrier publié »." % ou)
    if code in (401, 403):
        return (u"Le calendrier n'est plus public (%s refuse l'accès). Recochez "
                u"« Calendrier public » dans Calendrier, puis recopiez le lien "
                u"dans Réglages." % code)
    return u"Calendrier injoignable : %s" % exc


def _calendrier_a_venir(force=False):
    """Les dates du calendrier publie, a partir d'aujourd'hui. (items, erreur)

    Sorti de la route pour que l'ecran Equipe s'en serve aussi : ces dates
    sont les seules vraies — celles ou Julien sera sur le terrain — et deux
    ecrans qui repondent a « ou dois-je etre » ne peuvent pas lire deux
    sources differentes.
    """
    import time
    url = db.get_setting("calendrier_url", "") or ""
    if not url:
        return [], "aucun calendrier configuré"

    frais = (time.time() - _CAL_CACHE["quand"]) < 900
    if not frais or force:
        # iCloud repond parfois 404 sur un calendrier parfaitement valide —
        # vu en direct : meme lien, 404 puis 200 quelques minutes plus tard.
        # Un seul echec ne prouve donc rien, et annoncer « votre lien est
        # mort » sur cette base envoie chercher un probleme qui n'existe pas.
        # On redemande une fois avant de conclure.
        dernier = None
        for essai in range(2):
            try:
                texte = sources.fetch(url)
                _CAL_CACHE["items"] = sources.from_ics(texte, "Calendrier SnapActions")
                _CAL_CACHE["quand"] = time.time()
                dernier = None
                break
            except Exception as exc:
                dernier = exc
                if essai == 0:
                    time.sleep(1.2)
        if dernier is not None and not _CAL_CACHE["items"]:
            return [], _pourquoi_calendrier_muet(dernier, url)

    auj = util.today().isoformat()
    a_venir = [e for e in _CAL_CACHE["items"] if (e.get("start_date") or "") >= auj]
    a_venir.sort(key=lambda e: e.get("start_date") or "")
    return a_venir, None


@route("GET", r"/api/mes-dates")
def api_mes_dates(req):
    """Les dates de tournage de Julien, lues sur son calendrier publie.

    Ce ne sont pas des prospects : ce sont les jours ou il sera sur le terrain.
    C'est ce qui doit s'afficher en premier, avant tout le reste.
    """
    a_venir, erreur = _calendrier_a_venir(force=bool(req.query.get("force")))
    if erreur and not a_venir:
        return {"items": [], "erreur": erreur}
    return _habiller_dates(a_venir, modifiable=False)


def _habiller_dates(items, modifiable, limite=40):
    """Met la distance et le compte a rebours sur chaque date.

    La coupure etait muette : au-dela de quarante dates, la suivante
    n'existait plus pour le CRM et rien ne le disait. Elle est toujours la
    — une page qui affiche deux cents lignes ne sert personne — mais elle
    s'annonce maintenant (`tronque`).
    """
    out = []
    for e in items[:limite]:
        ville = e.get("city") or ""
        d = geo.depuis_cannes(ville, e.get("postal_code")) if ville else None
        out.append({
            "uid": e.get("uid") or e.get("external_id"), "name": e.get("name"),
            "start_date": e.get("start_date"), "end_date": e.get("end_date"),
            "debut": e.get("debut"), "fin": e.get("fin"),
            "journee": e.get("journee"), "notes": e.get("notes") or "",
            "city": ville, "venue": e.get("venue") or "", "url": e.get("url") or "",
            "jours": _jours_avant(e.get("start_date")),
            "distance_km": (d or {}).get("km"), "trajet_min": (d or {}).get("minutes"),
            "distance_verdict": (d or {}).get("verdict"),
        })
    return {"items": out, "total": len(items), "modifiable": modifiable,
            "tronque": max(0, len(items) - len(out))}


# --------------------------------------------------------------------------
# L'agenda en ecriture : Calendrier du Mac, pas le lien publie
# --------------------------------------------------------------------------
# Lire Calendrier coute quatre secondes et demie — c'est le prix des Apple
# Events, mesure et irreductible. On garde donc le resultat une minute, et on
# le jette des qu'on ecrit, pour que l'ecran montre toujours l'etat reel.
_AGENDA_CACHE = {"quand": 0.0, "items": None}
_AGENDA_TTL = 60


def _agenda_vider():
    _AGENDA_CACHE["items"] = None
    _AGENDA_CACHE["quand"] = 0.0


def _agenda_lire(force=False):
    import time
    frais = (_AGENDA_CACHE["items"] is not None
             and (time.time() - _AGENDA_CACHE["quand"]) < _AGENDA_TTL)
    if frais and not force:
        return _AGENDA_CACHE["items"]
    # Deux caches vivaient cote a cote et ne se parlaient pas : celui-ci
    # rappelait Calendrier alors que le prechauffage venait d'en rapporter
    # les evenements. Quinze secondes d'attente pour une lecture deja faite.
    brut = agenda.lister_vite(frais=force)[0]
    items = []
    for e in brut:
        debut = e.get("debut") or ""
        items.append({
            "uid": e.get("uid"), "name": e.get("titre"),
            "start_date": debut[:10], "end_date": (e.get("fin") or "")[:10],
            "debut": debut, "fin": e.get("fin"),
            "journee": bool(e.get("journee")),
            # Calendrier ne donne qu'un champ « lieu » : on le recopie dans
            # les deux, faute de savoir lequel est la salle et lequel est la
            # ville. C'est la source des « Stade de Gilly, Stade de Gilly » —
            # tout ce qui assemble venue + city doit passer par
            # util.lieu_lisible(), qui n'écrit pas deux fois la même chose.
            "venue": e.get("lieu") or "", "city": e.get("lieu") or "",
            "notes": e.get("notes") or "",
        })
    _AGENDA_CACHE["items"] = items
    _AGENDA_CACHE["quand"] = time.time()
    return items


@route("POST", r"/api/carnet/relever")
def api_carnet_relever(req):
    """Compare le carnet d'adresses a la derniere empreinte.

    La date de creation ne sert a rien : la synchronisation iCloud de juillet
    a donne la meme a 5 278 fiches sur 5 280. On garde donc l'empreinte de ce
    qu'on a deja vu, et ce qui n'y figure pas est nouveau — quelle que soit
    la date que la fiche s'attribue.
    """
    from . import carnet
    try:
        d = carnet.relever()
    except carnet.CarnetIndisponible as exc:
        raise ApiError(str(exc), 409)
    if d["premier_releve"]:
        return {"ok": True, "premier": True, "total": d["total"],
                "message": "Empreinte posée sur %d contacts. Tout ce qui "
                           "arrivera désormais sera repéré." % d["total"]}
    n = carnet.enregistrer_rencontres(d["nouveaux"])
    return {"ok": True, "premier": False, "total": d["total"],
            "nouveaux": len(d["nouveaux"]), "enregistres": n,
            "depuis": d.get("depuis")}


@route("POST", r"/api/carnet/ajouter")
def api_carnet_ajouter(req):
    """Un numero pris sur le terrain, tape en dix secondes.

    Le chemin qui ne depend de rien : ni d'iCloud, ni de WhatsApp, ni d'une
    synchronisation qui arrivera peut-etre ce soir.
    """
    from . import carnet
    b = req.body or {}
    out = carnet.ajouter_a_la_main(
        b.get("nom"), b.get("telephone"), b.get("email"), b.get("role"),
        b.get("organisation"), b.get("ou"), b.get("note"))
    if not out.get("ok"):
        raise ApiError(out.get("error") or "enregistrement impossible")
    return out


@route("POST", r"/api/carnet/vcard")
def api_carnet_vcard(req):
    """Des cartes de contact collees, ou posees par AirDrop.

    Le chemin qui ne depend pas d'iCloud : sur l'iPhone, on selectionne les
    fiches, Partager, AirDrop vers le Mac. Elles tombent dans Telechargements
    et on les ramasse. Trente secondes, aucun reglage a changer.
    """
    from . import carnet
    b = req.body or {}
    ou = (b.get("ou") or "").strip() or None
    texte = (b.get("text") or "").strip()
    if texte:
        if "BEGIN:VCARD" not in texte.upper():
            raise ApiError("ce n'est pas une carte de contact : sur l'iPhone, "
                           "ouvrez la fiche, Partager, puis AirDrop vers ce Mac")
        return carnet.avaler_vcards(texte, ou=ou, source="Carte collée")
    out = carnet.ramasser_telechargements(ou=ou)
    if not out.get("ok"):
        raise ApiError(out.get("error") or "lecture impossible")
    return out


def _ramasser_sans_bruit():
    """Avale les .vcf poses par AirDrop, a chaque ouverture de la page.

    Julien n'a pas a cliquer sur un bouton pour que ses numeros rentrent :
    il envoie les fiches depuis l'iPhone, il ouvre la page, elles y sont.
    Un listdir sur Telechargements, et les fichiers deja lus sont ignores —
    on ne touche a rien dans son dossier.
    """
    from . import carnet
    try:
        out = carnet.ramasser_telechargements()
    except Exception:          # un carnet qui tousse ne doit jamais
        return None            # empecher la page de s'afficher
    if not out.get("ok") or not out.get("crees"):
        return None
    return {"fichiers": out["fichiers"], "crees": len(out["crees"]),
            "noms": [c["nom"] for c in out["crees"]][:6]}


@route("GET", r"/api/carnet")
def api_carnet(req):
    """Les gens notes sur le terrain, pas encore traites."""
    from . import carnet
    ramasse = _ramasser_sans_bruit()
    tout = str(req.query.get("tout", "")).lower() in ("1", "true", "oui")
    items = carnet.liste(tout)
    out = {"items": items, "n": len(items), "ramasse": ramasse,
           "veille": carnet.dernier_passage()}
    # Dire l'etat plutot qu'un mode d'emploi. Si rien n'arrive du telephone,
    # Julien doit le savoir : ce n'est pas le CRM qui dort. On le calcule
    # meme quand des fiches s'affichent — sinon l'avertissement disparaissait
    # des qu'une seule fiche, fut-elle traitee, etait a l'ecran.
    jour = carnet.derniere_arrivee()
    if jour and jour < util.today().isoformat():
        out["bloque"] = (
            "Votre carnet n'a rien reçu depuis le %s. Les numéros "
            "enregistrés sur l'iPhone restent dans le compte « Sur mon "
            "iPhone » : Réglages \u2192 Apps \u2192 Contacts \u2192 "
            "Compte par défaut \u2192 iCloud." % jour)
    return out


@route("POST", r"/api/message/retoucher")
def api_message_retoucher(req):
    """Julien dit ce qu'il veut changer, le modele local refait le message.

    « plus court », « plus chaleureux », « parle du tournoi de decembre ».
    Le texte de depart est celui qu'il a sous les yeux, pas un original
    stocke : il peut donc enchainer les retouches. Rien n'est envoye.
    """
    from . import ia
    b = req.body or {}
    texte = (b.get("texte") or "").strip()
    consigne = (b.get("consigne") or "").strip()
    if not texte:
        raise ApiError("il n'y a rien à retoucher")
    if not consigne:
        raise ApiError("dites ce qu'il faut changer")
    if len(texte) > 4000 or len(consigne) > 600:
        raise ApiError("c'est trop long pour une retouche")
    try:
        out = ia.reformuler(texte, consigne, (b.get("contexte") or "").strip() or None)
    except ia.PasLa as exc:
        raise ApiError("le modèle local ne répond pas : %s" % exc, 503)
    except ia.Refus as exc:
        raise ApiError("le modèle n'a pas pu : %s" % exc)
    neuf = (out.get("texte") or "").strip()
    if not neuf:
        raise ApiError("le modèle n'a rien rendu")
    return {"texte": neuf, "modele": out.get("modele")}


def _url_quote(texte):
    from urllib.parse import quote
    return quote(texte or "", safe="")


@route("GET", r"/api/carnet/(\d+)/message")
def api_carnet_message(req, rid):
    """Le message de reprise de contact pour quelqu'un qu'on vient de croiser.

    Par defaut le modele maison, qui ne se trompe jamais : il n'ecrit que ce
    qu'on sait. Avec ?ia=1, le modele local reecrit le corps — et s'il dort,
    on retombe sur le modele maison plutot que de ne rien rendre.
    """
    from . import carnet
    r = db.q1("SELECT * FROM rencontres WHERE id = ?", (int(rid),))
    if not r:
        raise ApiError("fiche introuvable", 404)
    r = dict(r)
    veut_ia = str(req.query.get("ia", "")).lower() in ("1", "true", "oui")
    texte, source = None, "modèle maison"
    if veut_ia:
        try:
            texte = carnet.mot_par_ia(r)
            source = "modèle local" if texte else source
        except Exception:
            texte = None
    if not texte:
        texte = carnet.mot_pour(r)
    tel = (r.get("telephone") or "").strip()
    brut = re.sub(r"\D", "", tel)
    wa = ""
    if brut:
        n = brut[2:] if brut.startswith("00") else brut
        if len(n) >= 10:
            wa = "https://wa.me/%s?text=%s" % (n, _url_quote(texte))
    return {"texte": texte, "source": source, "wa": wa,
            "nom": r.get("nom"), "tel": tel}


@route("POST", r"/api/carnet/(\d+)/sujet")
def api_carnet_sujet(req, rid):
    """« On a parlé d'une couverture de régate. »

    Deux mots qui changent tout le message : avec eux, on écrit une suite de
    conversation ; sans eux, une présentation.
    """
    from . import carnet
    out = carnet.noter_sujet(int(rid), (req.body or {}).get("sujet"))
    if not out.get("ok"):
        raise ApiError(out.get("error") or "enregistrement impossible", 404)
    return out


@route("POST", r"/api/carnet/(\d+)/trancher")
def api_carnet_trancher(req, rid):
    from . import carnet
    return carnet.trancher(int(rid), bool((req.body or {}).get("traite", True)))


@route("GET", r"/api/derniers")
def api_derniers(req):
    """Les numeros ramasses ces derniers jours, a trier au retour d'une op."""
    from . import derniers
    try:
        jours = max(1, min(60, int(req.query.get("jours", derniers.JOURS_DEFAUT))))
    except (TypeError, ValueError):
        jours = derniers.JOURS_DEFAUT
    tout = str(req.query.get("tout", "")).lower() in ("1", "true", "oui")
    tri = (req.query.get("tri") or "").strip().lower() or None
    if tri and tri not in derniers.TRIS:
        tri = None
    return derniers.tout(jours, tout, tri)


@route("POST", r"/api/derniers/(\d+)/nom")
def api_derniers_nom(req, chat_id):
    """Renommer un numero brut. Le seul geste ou Julien tape quelque chose."""
    from . import derniers
    out = derniers.renommer(int(chat_id), (req.body or {}).get("nom"))
    if not out.get("ok"):
        raise ApiError(out.get("error") or "renommage impossible")
    return out


@route("POST", r"/api/derniers/(\d+)/note")
def api_derniers_note(req, chat_id):
    """Le memo sur cette personne : ce qu'on s'est dit, ce qu'elle peut faire."""
    from . import derniers
    out = derniers.noter(int(chat_id), (req.body or {}).get("memo"))
    if not out.get("ok"):
        raise ApiError(out.get("error") or "memo impossible")
    return out


@route("POST", r"/api/derniers/(\d+)/tri")
def api_derniers_tri(req, chat_id):
    """« peut me booker », « photographe », « sans suite ». Jamais un envoi."""
    from . import derniers
    out = derniers.trier(int(chat_id), (req.body or {}).get("tri"))
    if not out.get("ok"):
        raise ApiError(out.get("error") or "tri impossible")
    return out


@route("GET", r"/api/rencontres")
def api_rencontres(req):
    """Les gens croises sur le terrain, avant qu'on les oublie.

    Julien revient d'un tournoi avec des numeros dans le telephone ; deux
    jours plus tard ils sont noyes entre un taxi et Chronopost. Tout est
    pourtant deja dans le journal WhatsApp — il suffisait de le regarder par
    le bon bout : qui ai-je rencontre, et qu'est-ce qu'on s'est dit.
    """
    from . import rencontres
    try:
        jours = max(2, min(180, int(req.query.get("jours", rencontres.JOURS_DEFAUT))))
    except (TypeError, ValueError):
        jours = rencontres.JOURS_DEFAUT
    montrer = str(req.query.get("tout", "")).lower() in ("1", "true", "oui")
    ramasse = _ramasser_sans_bruit()
    out = rencontres.tout(jours, montrer)
    if ramasse:
        out["ramasse"] = ramasse
    return out


@route("GET", r"/api/bookings")
def api_bookings(req):
    """Ce que le booker a pose dans le calendrier, pour pouvoir le defaire."""
    from . import booker
    return {"items": booker.liste(), "veille": booker.dernier_passage()}


@route("POST", r"/api/bookings/scanner")
def api_bookings_scanner(req):
    """Un passage a la demande. `simuler` montre sans rien ecrire."""
    from . import booker
    simuler = str((req.body or {}).get("simuler", "")).lower() in ("1", "true", "oui")
    return booker.tourner(poser_vraiment=not simuler)


@route("POST", r"/api/bookings/(\d+)/annuler")
def api_booking_annuler(req, bid):
    """Retire l'evenement du calendrier. Julien reste maitre de son agenda."""
    from . import booker
    out = booker.annuler(int(bid))
    if not out.get("ok"):
        raise ApiError(out.get("motif") or "annulation impossible")
    return out


@route("GET", r"/api/rencontres/(\d+)/aider")
def api_rencontre_aider(req, chat_id):
    """Un brouillon de reponse, ecrit par le modele local.

    Il lit la conversation et rend un texte que Julien relit et corrige. Rien
    n'est envoye : il n'existe aucune fonction d'envoi de ce cote, et c'est
    voulu. Le modele tourne sur la machine — la conversation ne sort pas.
    """
    from . import ia, wa
    chat = db.q1("SELECT * FROM wa_chats WHERE id = ?", (int(chat_id),))
    if not chat:
        raise ApiError("conversation introuvable", 404)
    if not (chat["derniers"] or "").strip():
        return {"texte": None, "motif": "cette conversation n'a aucun message lu"}
    if not ia.disponible():
        return {"texte": None,
                "motif": "le modèle local ne répond pas — vérifiez qu'Ollama tourne"}
    carte = dict(chat)
    carte["id"] = chat["id"]
    # Le prenom sert la salutation. Un numero brut n'en est pas un, et un
    # intitule de fonction non plus : « Caroline Arbelot Directrice… » donne
    # « Caroline », « +33 6 13 01 64 50 » ne donne rien.
    nom = (chat["nom"] or "").strip()
    if nom and not re.match(r"^\+?[\d\s().-]+$", nom):
        carte["personne"] = nom
    texte = wa.reponse_du_fil(carte, calculer=True)
    if not texte:
        return {"texte": None,
                "motif": "le dernier mot est le vôtre, ou le modèle n'a rien rendu d'utilisable"}
    return {"texte": texte, "modele": ia.modele()}


@route("POST", r"/api/rencontres/ecarter")
def api_rencontre_ecarter(req):
    """« Traite », « plus tard », « pas utile ». Jamais un envoi."""
    from . import rencontres
    b = req.body or {}
    cle = (b.get("cle") or "").strip()
    if not cle:
        raise ApiError("quelle rencontre ?")
    motif = (b.get("motif") or "fait").strip()
    if motif not in rencontres.ECARTS:
        raise ApiError("motif inconnu")
    return rencontres.ecarter(cle, motif)


@route("POST", r"/api/rencontres/en-club")
def api_rencontre_en_club(req):
    """Fait une fiche club d'une rencontre, et y rattache la conversation.

    Le nom vient de Julien ou du carnet WhatsApp ; le numero, lui, vient du
    journal — jamais saisi deux fois, jamais devine.
    """
    from . import rencontres  # noqa: F401 — coherence des imports
    b = req.body or {}
    chat_id = b.get("chat_id")
    nom = (b.get("nom") or "").strip()
    if not chat_id or not nom:
        raise ApiError("il faut la conversation et un nom")
    chat = db.q1("SELECT * FROM wa_chats WHERE id = ?", (int(chat_id),))
    if not chat:
        raise ApiError("conversation introuvable", 404)
    cid, etat = repo.upsert_club({
        "name": nom,
        "sport": (b.get("sport") or "").strip() or None,
        "city": (b.get("ville") or "").strip() or None,
        "phone": chat["phone"] or None,
        "source": "Rencontre sur le terrain",
        "notes": (b.get("note") or "").strip() or None,
        "status": "contacte",
    })
    if not cid:
        raise ApiError("fiche impossible")
    db.ex("UPDATE wa_chats SET club_id = ?, updated_at = ? WHERE id = ?",
          (cid, util.now_iso(), int(chat_id)))
    if chat["phone"]:
        repo.upsert_contact({"club_id": cid, "phone": chat["phone"],
                             "first_name": (b.get("prenom") or "").strip() or None,
                             "role": (b.get("role") or "").strip() or None})
    return {"ok": True, "club_id": cid, "etat": etat}


@route("GET", r"/api/repondre")
def api_repondre(req):
    """Les deux piles ou dort l'argent deja gagne.

    Ceux qui ont ecrit et attendent, et ceux dont le rappel est echu. Le CRM
    les connaissait tous les deux et ne les montrait nulle part : la premiere
    liste vivait dans une route que personne n'affichait, la seconde dans la
    table des taches, qu'aucun ecran ne lisait.
    """
    try:
        limite = max(10, min(300, int(req.query.get("limite", 120))))
    except (TypeError, ValueError):
        limite = 120
    tout_montrer = str(req.query.get("tout", "")).lower() in ("1", "true", "oui")
    return relancer.tout(limite, tout_montrer)


@route("GET", r"/api/repondre/(\d+)/mot")
def api_repondre_mot(req, task_id):
    """La relance ecrite d'apres la conversation, pas d'apres un modele.

    Le modele fige ignorait ce qu'on s'etait dit : il proposait « je remonte
    le message » a un club a qui Julien avait demande des dates de tournoi
    puis envoye un « ? ». Ici le modele local lit le fil et reprend le sujet.
    Calcule a la demande — l'ecran n'affiche qu'une carte a la fois.
    """
    from . import ia
    t = db.q1("SELECT club_id FROM tasks WHERE id = ?", (int(task_id),))
    if not t:
        raise ApiError("relance introuvable", 404)
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (t["club_id"],)))
    chat = db.q1("SELECT id, derniers FROM wa_chats WHERE club_id = ? "
                 "AND COALESCE(derniers,'') <> '' "
                 "ORDER BY dernier_le DESC LIMIT 1", (t["club_id"],))
    if not chat:
        return {"texte": None, "motif": "aucune conversation WhatsApp pour ce club"}
    if not ia.disponible():
        return {"texte": None,
                "motif": "le modèle local ne répond pas — vérifiez qu'Ollama tourne"}
    try:
        messages = json.loads(chat["derniers"] or "[]")
    except (TypeError, ValueError):
        messages = []
    contexte = " · ".join(x for x in [(club or {}).get("name"),
                                      (club or {}).get("sport"),
                                      (club or {}).get("city")] if x)
    corps = ia.relance_du_fil(messages, contexte)
    if not corps:
        return {"texte": None, "motif": "le modèle n'a rien rendu d'utilisable"}
    qui = (drafts.best_contact(t["club_id"]) or {}).get("first_name") or ""
    tete = "Bonjour%s," % ((" " + qui) if qui else "")
    return {"texte": "\n".join([tete, "", corps, "", "Bonne journée,",
                                 "Julien — SnapActions"]),
            "modele": ia.modele()}


@route("POST", r"/api/repondre/fait")
def api_repondre_fait(req):
    """« Fait », « plus tard », « ne plus relancer ». Jamais un envoi."""
    b = req.body or {}
    cle = (b.get("cle") or "").strip()
    if not cle:
        raise ApiError("quelle ligne ?")
    motif = (b.get("motif") or "fait").strip()
    if motif not in ("fait", "plus_tard", "non"):
        raise ApiError("motif inconnu")
    out = relancer.fait(cle, motif)
    if isinstance(out, dict) and out.get("error"):
        raise ApiError(out["error"])
    return out if isinstance(out, dict) else {"ok": True}


@route("GET", r"/api/alertes")
def api_alertes(req):
    """Ce qui se perd si on ne fait rien aujourd'hui.

    Deux choses, et deux seulement. Un club qui a ecrit et qu'on laisse sans
    reponse se refroidit ; une date qui approche sans qu'on ait pris contact
    est perdue le jour ou elle passe. Tout le reste — semaines creuses,
    partenaires qui tiedissent — attend demain sans rien couter, et n'a donc
    rien a faire dans un bandeau : le bruit, c'est ce qui a tue les onglets
    precedents.
    """
    out = {"attendent": [], "bientot": []}

    # 1. Ils ont ecrit, on n'a pas repondu.
    try:
        for col in wa.suivi()["colonnes"]:
            if col["cle"] != "a_traiter":
                continue
            for c in col["cartes"]:
                if (c.get("jours") or 0) < 3:
                    continue
                out["attendent"].append({
                    "club": c.get("club"), "jours": c.get("jours"),
                    "tel": c.get("phone"), "club_id": c.get("club_id"),
                    "dernier": ((c.get("derniers") or [{}])[-1] or {}).get("texte", "")[:120],
                })
    except Exception:
        pass
    out["attendent"].sort(key=lambda x: -(x["jours"] or 0))

    # 2. Ca approche, a portee, et personne n'a ecrit.
    rows = db.rows_to_dicts(db.q(
        "SELECT t.id, t.name, t.city, t.start_date, t.trajet_min, t.nb_equipes, "
        "       c.name AS club, c.phone, c.email "
        "FROM tournaments t LEFT JOIN clubs c ON c.id = t.club_id "
        # Une date d'aujourd'hui ne se demarche plus, et une date sans contact
        # n'est pas une action : ni l'une ni l'autre n'a sa place dans un
        # bandeau. On ne garde que ce sur quoi on peut encore agir ce matin.
        "WHERE date(t.start_date) BETWEEN date('now','+2 day') AND date('now','+21 day') "
        "  AND COALESCE(t.trajet_min, 9999) <= 120 "
        "  AND COALESCE(t.lieu_verifie, 1) = 1 "
        "  AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.tournament_id = t.id "
        "                  AND d.sent_at IS NOT NULL) "
        "ORDER BY t.start_date LIMIT 12"))
    for r in rows:
        j = _jours_avant(r["start_date"])
        r["jours"] = j
        r["joignable"] = bool(r.get("phone") or r.get("email"))
    out["bientot"] = [r for r in rows
                      if r["jours"] is not None and r["jours"] >= 2 and r["joignable"]]
    out["n_attendent"] = len(out["attendent"])
    out["n_bientot"] = len(out["bientot"])
    return out


@route("GET", r"/api/studio")
def api_studio(req):
    """Les clubs a qui proposer une seance photo studio pour leurs adherents.

    C'est l'autre moitie du metier : la ou les tournois se vendent au
    week-end, le studio se vend en semaine, et aux memes familles.
    """
    try:
        portee = int(req.query.get("portee", creux.PORTEE_SEMAINE_MIN))
    except (TypeError, ValueError):
        portee = creux.PORTEE_SEMAINE_MIN
    items = creux.clubs_studio(portee)
    ecartes = creux._ecartes_studio()
    compte = {
        "tout": len(items),
        "relancer": sum(1 for c in items if c["connu"]),
        "demarcher": sum(1 for c in items if not c["connu"]),
        "animaux": sum(1 for c in items if c["genre"] == "animaux"),
        "clubs": sum(1 for c in items if c["genre"] != "animaux"),
        "proches": sum(1 for c in items
                       if c["trajet_min"] is not None and c["trajet_min"] <= 20),
    }
    return {"items": items, "compte": compte, "portee": portee,
            "ecartes": len(ecartes)}


@route("POST", r"/api/studio/(\d+)/ecarter")
def api_studio_ecarter(req, club_id):
    """Retire une fiche de la liste studio : contacte, plus tard, ou non.

    Rien n'est efface. « Contacte » revient dans deux mois, « plus tard » dans
    deux semaines, « non » dans un an : un refus d'octobre peut devenir un oui
    en janvier, et la liste ne doit pas pouvoir mentir par omission definitive.
    """
    motif = (req.body or {}).get("motif") or "plus_tard"
    if motif not in creux.ECARTS_STUDIO:
        raise ApiError("motif inconnu")
    return creux.ecarter_studio(int(club_id), motif)


# (modele mail, modele WhatsApp, nom affiche)
ANGLES = {
    "premier": ("premier_contact", "whatsapp_premier", "Premier contact"),
    "studio": ("studio_club", "studio_whatsapp", "Studio"),
    "dates": ("calendrier_mail", "calendrier_whatsapp", "Leurs dates"),
}


@route("POST", r"/api/clubs/(\d+)/enrichir")
def api_club_enrichir(req, cid):
    """Trouve les pages de CE club et y lit ses coordonnees, tout de suite.

    Le veilleur fait la meme chose en fond, deux clubs par quart d'heure.
    Ici c'est pour celui qu'on vient de mettre en fiche : on ne va pas
    attendre son tour pour pouvoir lui ecrire.
    """
    from . import enrichir
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("club introuvable", 404)
    return enrichir.un_club(club)


@route("POST", r"/api/clubs/(\d+)/coordonnee")
def api_club_coordonnee(req, cid):
    """Un numero ou un mail colle a la main, retenu sur la fiche.

    Julien le lit souvent sur la page du club avant que le veilleur n'y
    passe. Il le colle une fois, et il est la pour toujours — c'est lui qui
    l'a vu, donc on le croit sans le passer au juge des numeros inventes.
    """
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("club introuvable", 404)
    brut = ((req.body or {}).get("valeur") or "").strip()
    if not brut:
        raise ApiError("collez un numéro ou une adresse mail")

    patch, quoi = {}, None
    if "@" in brut:
        mail = util.normalize_email(brut) or brut
        if "@" not in mail:
            raise ApiError("cette adresse mail n'est pas valide")
        patch["email"] = mail
        quoi = mail
    else:
        tel = util.normalize_phone(brut)
        if not tel:
            raise ApiError("ce numéro n'est pas lisible")
        patch["phone"] = tel
        quoi = tel
    patch["updated_at"] = util.now_iso()
    patch["notes"] = ((club.get("notes") or "").strip() + "\n"
                      + "Coordonnée saisie à la main le %s : %s"
                      % (util.today().isoformat(), quoi)).strip()
    db.update("clubs", int(cid), patch)
    return {"ok": True, "phone": patch.get("phone"), "email": patch.get("email")}


@route("GET", r"/api/clubs/(\d+)/message")
def api_club_message(req, cid):
    """Le premier mot a un club qu'on vient de mettre en fiche.

    Rendu a la volee, rien n'est enregistre : la prospection en cree soixante
    d'un coup, et pre-rediger pour tous remplirait la pile de relecture de
    brouillons que Julien n'a jamais demandes.

    Le modele suit le canal : un mobile part en WhatsApp, une adresse part
    en mail, et les deux textes sont les memes a la mise en forme pres.
    """
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("club introuvable", 404)
    contact = drafts.best_contact(int(cid))
    tel = (contact or {}).get("phone") or club.get("phone")
    mail = (contact or {}).get("email") or club.get("email")
    par_whatsapp = bool(tel and util.is_mobile_fr(tel))
    # Trois accroches, parce qu'un meme club ne se demarche pas pareil selon
    # ce qu'on lui propose. Chaque angle a sa version WhatsApp et sa version
    # mail : c'est le meme texte, mis en forme autrement.
    angle = (req.query.get("angle") or "premier").strip().lower()
    if angle not in ANGLES:
        angle = "premier"
    modele = ANGLES[angle][1 if par_whatsapp else 0]
    rendu = drafts.render(modele, club=club, contact=contact)
    if not rendu or not (rendu.get("body") or "").strip():
        return {"texte": None,
                "motif": "aucun modèle utilisable pour ce club"}
    return {"texte": rendu.get("body"), "objet": rendu.get("subject"),
            "tel": tel, "mail": mail, "modele": modele, "angle": angle,
            "angles": [{"cle": k, "nom": v[2]} for k, v in ANGLES.items()],
            "joignable": bool(tel or mail)}


@route("GET", r"/api/studio/(\d+)/message")
def api_message_studio(req, cid):
    """Le message studio pour CE club, redige depuis les modeles — pas ailleurs.

    Les ecrans Studio et « Semaines a remplir » fabriquaient chacun leur
    propre texte en JavaScript. Trois versions du meme message coexistaient,
    et celles des ecrans avaient maigri : plus de galerie intelligente, plus
    de selection remise au club, plus de reference au Basket Cannes Olympique.
    Il ne restait que « je monte un studio chez les clubs et les commerces »,
    ce qui ne dit ni ce qu'on y gagne ni ce que ca coute.

    On rend donc le texte des modeles, ceux que Julien relit et corrige dans
    les reglages. Rien n'est enregistre : c'est un brouillon a l'ecran, et
    aucun message ne part d'ici.
    """
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("club introuvable", 404)
    canal = (req.query.get("canal") or "").strip() or None
    modele = drafts.modele_studio(club, canal)
    contact = drafts.best_contact(club["id"])
    rendu = drafts.render(modele, club, contact, None)
    if not rendu:
        raise ApiError("modèle %s introuvable" % modele, 404)
    tel = (contact or {}).get("phone") or club.get("phone")
    mail = (contact or {}).get("email") or club.get("email")
    return {"corps": rendu["body"], "objet": rendu.get("subject"),
            "canal": rendu["channel"], "modele": modele,
            "destinataire": mail or tel, "tel": tel, "mail": mail,
            "enregistre": False}


@route("POST", r"/api/studio/reprendre")
def api_studio_reprendre(req):
    """Remet tout dans la liste."""
    return creux.reprendre_studio()


@route("GET", r"/api/semaines")
def api_semaines(req):
    """Les semaines creuses, et les tournois qui pourraient les remplir.

    Une semaine sans date est une semaine sans chiffre d'affaires, et on s'en
    apercoit d'ordinaire trop tard pour demarcher. On rapproche donc l'agenda
    de travail et la veille, qui vivaient chacun de leur cote.
    """
    try:
        n = max(2, min(16, int(req.query.get("semaines", creux.SEMAINES_DEFAUT))))
    except (TypeError, ValueError):
        n = creux.SEMAINES_DEFAUT
    frais = str(req.query.get("frais", "")).lower() in ("1", "true", "oui")
    return creux.semaines(n, frais=frais)


@route("GET", r"/api/agenda")
def api_agenda(req):
    """Les dates, lues dans Calendrier — donc modifiables.

    Le lien iCloud publie ne sert plus que de filet : c'est une sortie en
    lecture seule, et iCloud y reattribue des UID qui ne correspondent a rien
    dans le calendrier reel. Impossible de designer un evenement a partir de
    lui ; on lit donc la source.
    """
    try:
        items = _agenda_lire(force=bool(req.query.get("force")))
    except agenda.AgendaIndisponible as exc:
        secours = api_mes_dates(req)
        secours["avertissement"] = (
            u"Calendrier n'a pas répondu (%s). Affichage du lien publié, "
            u"en lecture seule." % exc)
        secours["modifiable"] = False
        return secours
    auj = util.today().isoformat()
    a_venir = [e for e in items if (e.get("start_date") or "") >= auj]
    a_venir.sort(key=lambda e: e.get("debut") or "")
    return _habiller_dates(a_venir, modifiable=True)


@route("POST", r"/api/agenda")
def api_agenda_creer(req):
    b = req.body or {}
    try:
        ev = agenda.creer(b.get("titre"), b.get("debut"), b.get("fin"),
                          lieu=b.get("lieu"), notes=b.get("notes"),
                          journee=bool(b.get("journee")))
    except ValueError as e:
        raise ApiError(str(e))
    except agenda.AgendaIndisponible as e:
        raise ApiError(u"Calendrier n'a pas pu enregistrer : %s" % e, 503)
    _agenda_vider()
    return ev


@route("PATCH", r"/api/agenda/([^/]+)")
def api_agenda_modifier(req, uid):
    b = req.body or {}
    try:
        ev = agenda.modifier(uid, titre=b.get("titre"), debut=b.get("debut"),
                             fin=b.get("fin"), lieu=b.get("lieu"),
                             notes=b.get("notes"), journee=b.get("journee"))
    except ValueError as e:
        raise ApiError(str(e))
    except agenda.AgendaIndisponible as e:
        raise ApiError(u"Calendrier n'a pas pu enregistrer : %s" % e, 503)
    _agenda_vider()
    return ev


@route("DELETE", r"/api/agenda/([^/]+)")
def api_agenda_supprimer(req, uid):
    try:
        out = agenda.supprimer(uid)
    except ValueError as e:
        raise ApiError(str(e))
    except agenda.AgendaIndisponible as e:
        raise ApiError(u"Calendrier n'a pas pu supprimer : %s" % e, 503)
    _agenda_vider()
    return out


@route("GET", r"/api/relances")
def api_relances(req):
    """Les relances a faire : celles en retard d'abord, puis la semaine qui vient.

    Le message est rendu a la volee, pas enregistre : tant que Julien ne l'a
    pas envoye, ce n'est pas un brouillon de plus a gerer.
    """
    horizon = req.query.get("jours")
    jours = int(horizon) if horizon not in (None, "") else 7
    limite = (util.today() + datetime.timedelta(days=jours)).isoformat() + " 23:59:59"

    lignes = db.rows_to_dicts(db.q(
        "SELECT k.id AS task_id, k.title, k.due_at, k.step, k.contact_id, "
        "       c.id AS club_id, c.name AS club_name, c.city, c.postal_code, c.sport, "
        "       c.phone AS club_phone, c.email AS club_email, c.website AS club_site, "
        "       c.last_contact_at AS club_contacte_le, c.status AS club_statut "
        "FROM tasks k JOIN clubs c ON c.id = k.club_id "
        "WHERE k.status = 'pending' AND k.kind = 'relance' "
        "  AND c.do_not_contact = 0 AND k.due_at <= ? "
        # Une seule relance par club : on n'en envoie qu'une a la fois, les
        # etapes suivantes attendront leur tour.
        "  AND k.due_at = (SELECT MIN(k2.due_at) FROM tasks k2 "
        "                  WHERE k2.club_id = k.club_id AND k2.status = 'pending' "
        "                    AND k2.kind = 'relance') "
        "GROUP BY c.id "
        "ORDER BY k.due_at LIMIT 40", (limite,)))

    items = []
    for l in lignes:
        club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (l["club_id"],)))
        contact = db.row_to_dict(db.q1("SELECT * FROM contacts WHERE id = ?",
                                       (l["contact_id"],))) if l["contact_id"] else None
        # Le dernier tournoi rattache au club donne le contexte : distance, date.
        t = db.row_to_dict(db.q1(
            "SELECT * FROM tournaments WHERE club_id = ? "
            "ORDER BY (start_date >= date('now')) DESC, start_date LIMIT 1", (l["club_id"],)))
        modele = "relance_2" if (l["step"] or 1) >= 2 else "relance_1"
        msg = drafts.render(modele, club=club, contact=contact, tournament=t) \
            or drafts.render("relance_1", club=club, contact=contact, tournament=t) or {}

        items.append({
            "id": l["task_id"], "task_id": l["task_id"], "step": l["step"],
            "relance_le": l["due_at"],
            "name": (t or {}).get("name") or l["title"],
            "club_id": l["club_id"], "club_name": l["club_name"],
            "city": (t or {}).get("city") or l["city"],
            "postal_code": (t or {}).get("postal_code") or l["postal_code"],
            "sport": (t or {}).get("sport") or l["sport"],
            "start_date": (t or {}).get("start_date"),
            "end_date": (t or {}).get("end_date"),
            "venue": (t or {}).get("venue"),
            "url": (t or {}).get("url"),
            "distance_km": (t or {}).get("distance_km"),
            "trajet_min": (t or {}).get("trajet_min"),
            "distance_verdict": (t or {}).get("distance_verdict"),
            "nb_equipes": (t or {}).get("nb_equipes"),
            "categories": (t or {}).get("categories"),
            "club_phone": l["club_phone"], "club_email": l["club_email"],
            "club_site": l["club_site"], "club_contacte_le": l["club_contacte_le"],
            "club_statut": l["club_statut"],
            "source": "Relance",
            "draft": {
                "subject": msg.get("subject"), "body": msg.get("body"),
                "contact_phone": (contact or {}).get("phone"),
                "contact_email": (contact or {}).get("email"),
                "to_addr": (contact or {}).get("email") or l["club_email"],
            },
        })
    return {"items": items}


@route("POST", r"/api/relances/(\d+)/envoye")
def api_relance_envoyee(req, task_id):
    """La relance est partie : on la trace et on solde l'echeance."""
    corps = req.body or {}
    tache = db.row_to_dict(db.q1("SELECT * FROM tasks WHERE id = ?", (int(task_id),)))
    if not tache:
        raise ApiError("relance introuvable")
    canal = (corps.get("channel") or "whatsapp").strip()
    repo.add_interaction(tache["club_id"], kind=canal, direction="out",
                         subject=tache.get("title"), body=corps.get("body"),
                         contact_id=tache.get("contact_id"))
    repo.complete_task(int(task_id), outcome=corps.get("outcome") or "relance envoyee")
    return {"ok": True}


@route("POST", r"/api/relances/(\d+)/plus-tard")
def api_relance_plus_tard(req, task_id):
    """Repousse l'echeance sans rien effacer."""
    return repo.snooze_task(int(task_id), int((req.body or {}).get("jours", 7)))


@route("GET", r"/api/tasks")
def api_tasks(req):
    horizon = req.query.get("horizon")
    return {"items": repo.list_tasks(
        status=req.query.get("status", "pending"),
        horizon_days=int(horizon) if horizon not in (None, "", "all") else None,
        club_id=req.query.get("club_id"))}


@route("POST", r"/api/tasks")
def api_task_create(req):
    body = req.body
    due = util.parse_dt(body.get("due_at"))
    if not body.get("club_id") or not due:
        raise ApiError("club_id et due_at sont requis")
    task_id = repo.add_task(int(body["club_id"]), body.get("title") or "Relance",
                            due.isoformat(sep=" "), contact_id=body.get("contact_id"),
                            notes=body.get("notes"), kind=body.get("kind", "relance"))
    return {"id": task_id}


@route("POST", r"/api/tasks/(\d+)/done")
def api_task_done(req, task_id):
    return repo.complete_task(int(task_id), req.body.get("outcome"))


@route("POST", r"/api/tasks/(\d+)/snooze")
def api_task_snooze(req, task_id):
    return repo.snooze_task(int(task_id), req.body.get("days", 7))


@route("DELETE", r"/api/tasks/(\d+)")
def api_task_delete(req, task_id):
    db.delete("tasks", int(task_id))
    return {"ok": True}


# --------------------------------------------------------------------------
# Tournois
# --------------------------------------------------------------------------

@route("GET", r"/api/tournaments")
def api_tournaments(req):
    where, params = ["1=1"], []
    if req.query.get("status"):
        where.append("t.status = ?")
        params.append(req.query["status"])
    if req.query.get("dept"):
        where.append("t.dept = ?")
        params.append(req.query["dept"])
    if req.query.get("q"):
        like = "%" + req.query["q"] + "%"
        where.append("(t.name LIKE ? OR t.city LIKE ? OR t.organizer LIKE ?)")
        params += [like] * 3
    if req.query.get("upcoming", "1") == "1":
        where.append("(t.start_date IS NULL OR date(t.start_date) >= date('now'))")
    rows = db.rows_to_dicts(db.q(
        "SELECT t.*, c.name AS club_name FROM tournaments t "
        "LEFT JOIN clubs c ON c.id=t.club_id WHERE %s "
        "ORDER BY t.start_date IS NULL, t.start_date LIMIT 500" % " AND ".join(where),
        tuple(params)))
    maison = [d.strip() for d in
              (db.get_setting("depts_maison", "06,83") or "").split(",") if d.strip()]
    try:
        marge = int(db.get_setting("lead_days_min", "10") or 10)
    except (TypeError, ValueError):
        marge = 10

    for r in rows:
        ok, why = drafts.in_lead_window(r)
        r["in_window"] = ok
        r["window"] = why
        r["dept_name"] = util.dept_name(r.get("dept"))
        # Un message ecarte n'est pas un message pret : le compter faisait
        # afficher « 5 message(s) » sur une fiche qui n'en avait plus un seul.
        r["n_drafts"] = db.q1("SELECT COUNT(*) AS n FROM drafts WHERE tournament_id = ? "
                              "AND status IN ('draft','approved','sent')",
                              (r["id"],))["n"]
        # Le doute sur un lieu doit s'expliquer, sinon il ne sert a rien :
        # « lieu a confirmer » sans motif, on passe outre.
        if r.get("lieu_verifie") == 0:
            _, r["lieu_phrase"] = sources.lieu_corrobore(r)
        r["zone"] = _zone_du_tournoi(r, maison)
        # Une date deja a nous ne se « demarche » pas : lui afficher une date
        # limite de prospection n'a pas de sens.
        r["a_soi"] = sources._protegee(r.get("source"))
        r["trajet"] = _trajet_en_clair(r)
        r["decision_avant"] = _decision_avant(r, marge)
        r["jours_avant"] = _jours_avant(r.get("start_date"))
    return {"items": rows}


# L'ordre compte : c'est celui dans lequel la page les empile.
ZONES = [
    ("jour", "Faisable dans la journée", "Aller-retour dans la journée — le cœur du métier."),
    ("bassin", "Autres bassins — Lyon, Toulouse",
     "Loin, mais vous y allez : ça se décide au cas par cas."),
    ("ailleurs", "Ailleurs", "Gardé pour mémoire. Rien ne s'écrit tout seul ici."),
]


def _zone_du_tournoi(rec, maison):
    """Range la fiche par ce qui decide vraiment : peut-on y aller."""
    # Ce que Julien a saisi lui-meme, ou qu'un club lui a annonce, est chez lui
    # par construction : ces fiches n'ont souvent ni ville ni code postal, et
    # les laisser tomber dans « ailleurs » les enterrait sous les tournois
    # lyonnais. Mais seulement quand on ignore la distance — un flyer depose
    # pour Pignan, a 3 h 20 de route, n'est pas une sortie a la journee parce
    # qu'il vient d'un flyer.
    if sources._protegee(rec.get("source")) and not rec.get("trajet_min"):
        return "jour"
    if str(rec.get("dept") or "") in maison:
        return "jour"
    if rec.get("distance_verdict") in ("proche", "correct"):
        return "jour"
    if str(rec.get("dept") or "") in sources.BASSINS:
        return "bassin"
    return "ailleurs"


def _trajet_en_clair(rec):
    """« 1 h 45 · 140 km » plutot qu'un numero de departement."""
    bouts = []
    minutes = rec.get("trajet_min")
    if minutes:
        h, m = divmod(int(minutes), 60)
        bouts.append("%d h %02d" % (h, m) if h else "%d min" % m)
    if rec.get("distance_km"):
        bouts.append("%d km" % round(rec["distance_km"]))
    return " · ".join(bouts)


def _jours_avant(iso):
    d = util.parse_date(iso)
    return util.days_between(util.today(), d) if d else None


def _decision_avant(rec, marge):
    """La date apres laquelle il est trop tard pour demarcher les clubs."""
    d = util.parse_date(rec.get("start_date"))
    if not d:
        return None
    return (d - datetime.timedelta(days=marge)).isoformat()


def _jours_occupes():
    """Le calendrier de travail, range par jour : {date: [titres]}.

    Sans attendre : si le calendrier n'a pas encore ete lu, on rend un
    calendrier vide plutot que de retarder la page. Le repere reapparait au
    rafraichissement suivant.
    """
    pris = {}
    try:
        evs, _, _ = agenda.lister_vite(attendre=False)
    except Exception:  # noqa: BLE001 — un repere absent n'est pas une panne
        return pris
    for e in evs:
        debut = (e.get("debut") or "")[:10]
        fin = (e.get("fin") or debut)[:10]
        if not debut:
            continue
        try:
            d = datetime.date.fromisoformat(debut)
            f = datetime.date.fromisoformat(fin or debut)
        except ValueError:
            continue
        # Un evenement de plusieurs jours occupe chacun d'eux.
        if f < d:
            f = d
        for n in range((f - d).days + 1):
            jour = (d + datetime.timedelta(days=n)).isoformat()
            titre = (e.get("titre") or "Sans titre").strip()
            pris.setdefault(jour, [])
            if titre not in pris[jour]:
                pris[jour].append(titre)
    return pris


def _ce_qui_est_pris(occupe, debut, fin=None):
    """Ce qui est deja cale sur les jours de cette fiche."""
    if not occupe or not debut:
        return []
    try:
        d = datetime.date.fromisoformat(str(debut)[:10])
        f = datetime.date.fromisoformat(str(fin or debut)[:10])
    except ValueError:
        return []
    if f < d:
        f = d
    sortie = []
    for n in range(min((f - d).days + 1, 8)):
        jour = (d + datetime.timedelta(days=n)).isoformat()
        for titre in occupe.get(jour, []):
            if titre not in [x["titre"] for x in sortie]:
                sortie.append({"jour": jour, "titre": titre})
    return sortie


def _ou_en_est_le_sourceur(tid):
    """A-t-on demande au sourceur s'il peut monter une equipe la-bas ?

    La question se pose AVANT de contacter l'organisateur : promettre une
    couverture a six heures de route sans savoir si quelqu'un peut y aller,
    c'est prendre un rendez-vous qu'on ne tiendra pas. La reponse revient
    donc sur la carte de Reperage, la ou se prend la decision d'ecrire.
    """
    r = db.q1("SELECT m.status, m.assigned, m.body, m.updated_at, m.posted_at, "
              "       c.nom AS qui "
              "FROM missions m LEFT JOIN comptes c ON c.id = m.compte_id "
              "WHERE m.tournament_id = ? AND m.compte_id IS NOT NULL "
              "ORDER BY m.id DESC LIMIT 1", (int(tid),))
    if not r:
        return None
    d = db.row_to_dict(r)
    etat = (d.get("status") or "").lower()
    d["repondu"] = etat in ("trouvé", "trouve", "rien")
    d["possible"] = etat.startswith("trouv")
    return d


def _deja_booke(rec):
    """Le titre de l'evenement dans l'agenda, si ce tournoi y figure deja.

    On rapproche par les mots distinctifs du nom — le meme juge que le
    booker, qui evite les doublons. « LORIOL'S CUP » et « Tournoi de
    Loriol 2027 » partagent « loriol », et c'est assez.
    """
    debut = str(rec.get("start_date") or "")[:10]
    if not debut:
        return None
    try:
        from . import booker
    except Exception:
        return None
    titres = [t["titre"] for t in (rec.get("occupe") or [])]
    if not titres:
        return None
    for quoi in (rec.get("name"), rec.get("club_name"), rec.get("organizer")):
        if not (quoi or "").strip():
            continue
        trouve = booker.deja_au_calendrier(debut, quoi, titres)
        if trouve:
            return trouve
    return None


def _brouillon_de_la_fiche(rec):
    """Le brouillon ecrit A L'ORGANISATEUR de cette fiche — et lui seul.

    Une fiche porte volontairement plusieurs brouillons : generate_for_tournament
    en ecrit un par club voisin qu'on veut voir venir, parfois dix. Ce sont des
    messages a des tiers ; ils n'ont rien a faire sur la carte de l'evenement.

    En prenant simplement le dernier par numero, la liste collait a la fiche le
    message d'un AUTRE club, avec SON numero et SON texte. La carte de la Regate
    Trophee d'Automne, a Antibes, ouvrait ainsi WhatsApp sur Maguy du Stade
    Laurentin Basket. Dix-sept cartes etaient dans ce cas.

    Sans club rattache, il n'y a pas d'organisateur connu : on ne rend rien, et
    la carte propose d'aller le chercher — ce qui est la verite.
    """
    club_id = rec.get("club_id")
    if not club_id:
        return None
    d = db.q1("SELECT d.*, ct.phone AS contact_phone, ct.email AS contact_email, "
              "ct.first_name, ct.last_name FROM drafts d "
              "LEFT JOIN contacts ct ON ct.id = d.contact_id "
              "WHERE d.tournament_id = ? AND d.club_id = ? AND d.status = 'draft' "
              "ORDER BY d.id DESC LIMIT 1", (rec["id"], club_id))
    return db.row_to_dict(d) if d else None


@route("GET", r"/api/tournaments/a-valider")
def api_tournaments_a_valider(req):
    """Les tournois repares sur un flyer et pas encore tranches par Julien."""
    # Apres une analyse, on veut revoir la fiche qui vient d'etre creee, meme
    # si son tournoi est passe : sinon l'application dit « flyer depouille »
    # et ne montre rien.
    ids = (req.query.get("ids") or "").strip()
    if ids:
        choisis = [int(x) for x in re.findall(r"\d+", ids)][:20]
        if not choisis:
            return {"items": []}
        marques = ",".join("?" for _ in choisis)
        rows = db.rows_to_dicts(db.q(
            "SELECT t.*, c.name AS club_name, c.phone AS club_phone, c.email AS club_email, "
            "c.website AS club_site, c.last_contact_at AS club_contacte_le, "
            "c.status AS club_statut, "
            "(SELECT MIN(k.due_at) FROM tasks k WHERE k.club_id = c.id "
            " AND k.status = 'pending' AND k.kind = 'relance') AS relance_le "
            "FROM tournaments t LEFT JOIN clubs c ON c.id = t.club_id "
            "WHERE t.id IN (%s)" % marques, tuple(choisis)))
        for r in rows:
            r["draft"] = _brouillon_de_la_fiche(r)
        return {"items": rows}

    # Depuis que Julien recrute des photographes partout en France, les dates
    # lointaines ne sont plus jetees : elles sont marquees « reseau ». Par
    # defaut l'ecran ne montre que ce qu'il peut faire lui-meme — sinon celui
    # ou il tranche se remplit de Lille et de Mons. Mais les tiroirs
    # « Belgique » et « Hors de France » ont besoin du reste : d'ou ce
    # parametre. `portee=tout` rend les deux casiers.
    demande = (req.query.get("portee") or "pour_moi").strip()
    if demande == "tout":
        portee_sql = ""
        plafond = 1200
    elif demande == "reseau":
        portee_sql = "AND COALESCE(t.portee, 'pour_moi') = 'reseau' "
        plafond = 900
    else:
        portee_sql = "AND COALESCE(t.portee, 'pour_moi') = 'pour_moi' "
        plafond = 400

    rows = db.rows_to_dicts(db.q(
        "SELECT t.*, c.name AS club_name, c.phone AS club_phone, c.email AS club_email, "
        "c.website AS club_site, c.last_contact_at AS club_contacte_le, "
        "c.status AS club_statut, "
        "(SELECT MIN(k.due_at) FROM tasks k WHERE k.club_id = c.id "
        " AND k.status = 'pending' AND k.kind = 'relance') AS relance_le "
        "FROM tournaments t LEFT JOIN clubs c ON c.id = t.club_id "
        "WHERE COALESCE(t.validation, 'a_valider') = 'a_valider' "
        + portee_sql +
        "AND (t.start_date IS NULL OR t.start_date >= date('now')) "
        # Pas de plafond serre : ce que l'agent trouve doit remonter a l'ecran.
        # Les tiroirs font le tri, et le contenu lourd d'une carte n'est bati
        # qu'a son ouverture. Le classement, lui, se fait en Python juste
        # apres : il demande plus que ce qu'une clause ORDER BY sait dire.
        "ORDER BY COALESCE(t.distance_km, 99999), t.start_date LIMIT %d" % plafond))
    # On rattache le message prepare, s'il y en a un : c'est ce que Julien veut
    # voir en meme temps que l'analyse du flyer.
    try:
        lead_min = int(db.get_setting("lead_days_min", "10") or 10)
        lead_max = int(db.get_setting("lead_days_max", "75") or 75)
    except (TypeError, ValueError):
        lead_min, lead_max = 10, 75

    # Ce qui est deja pris dans le calendrier de travail. Sans ca, la carte
    # d'un tournoi du 7 novembre ne dit pas qu'on est deja quelque part ce
    # jour-la : on redige un message, on obtient un oui, et on decouvre le
    # conflit la veille. On lit le calendrier SANS attendre — il est tenu au
    # chaud par le prechauffage, et un repere absent vaut mieux qu'une page
    # qui met quarante secondes a s'ouvrir.
    occupe = _jours_occupes()
    for r in rows:
        r["draft"] = _brouillon_de_la_fiche(r)
        # Le bassin d'Hyeres se calcule sur les temps de route : le navigateur
        # ne peut pas le savoir, on le lui dit.
        r["autour_hyeres"] = (r.get("hyeres_min") is not None
                              and r["hyeres_min"] <= geo.HYERES_MINUTES)
        r["valeur"], r["valeur_dit"] = photos.valeur(r, lead_min, lead_max)
        # Les jours couverts par la fiche, debut et fin comprises.
        r["occupe"] = _ce_qui_est_pris(occupe, r.get("start_date"), r.get("end_date"))
        # Et surtout : CE tournoi est-il deja dans l'agenda ? Sans ca, la
        # carte du Tournoi de Loriol annonçait « hors zone, 328 km » alors
        # qu'il etait booke depuis aout et note « 80 equipes, besoin de deux
        # photographes ». On ne demarche pas ce qu'on a deja.
        r["deja_booke"] = _deja_booke(r)
        if r["deja_booke"]:
            # L'evenement lui-meme n'est pas un conflit avec lui-meme.
            r["occupe"] = [x for x in r["occupe"]
                           if x["titre"] != r["deja_booke"]]
        r["sourceur"] = _ou_en_est_le_sourceur(r["id"])

    # Le classement par distance seule enterrait ce qui se vend. Le Marathon
    # Nice-Cannes, la No Finish Line de Monaco, le Telethon : des milliers de
    # personnes a moins de cinquante kilometres, tous dans la fenetre de
    # demarchage, et tous loin derriere un plateau de quatre equipes qui se
    # trouvait a dix kilometres de moins. On range donc par ce que la fiche
    # peut rapporter — du monde, atteignable, encore dans les temps — et la
    # carte dit pourquoi elle est la.
    #
    # Les flyers deposes restent devant : ce sont les papiers que Julien vient
    # de poser sur la table, il veut les voir tout de suite.
    rows.sort(key=lambda r: (r.get("source") != "Flyer depose", -r["valeur"],
                             r.get("start_date") or "9999"))
    return {"items": rows}


@route("POST", r"/api/tournaments")
def api_tournament_create(req):
    created, updated, skipped, _ = sources.save_tournaments([req.body], "manuel")
    return {"created": len(created), "updated": updated, "skipped": skipped,
            "items": created}


@route("PATCH", r"/api/tournaments/(\d+)")
def api_tournament_update(req, tid):
    allowed = {"name", "sport", "level", "start_date", "end_date", "city", "postal_code",
               "dept", "venue", "organizer", "club_id", "url", "description", "status",
               "distance_km", "trajet_min", "distance_verdict", "nb_equipes",
               # La portee est calculee a l'entree en base, et rien ne pouvait
               # la corriger ensuite : une fiche mal lue — un flyer du
               # Pas-de-Calais range « pour moi » parce que le flyer a ete
               # scanne ici — restait fausse pour toujours.
               "categories", "validation", "portee"}
    patch = {k: v for k, v in req.body.items() if k in allowed}
    if "postal_code" in patch:
        patch["dept"] = util.dept_from_postal(patch["postal_code"]) or patch.get("dept")
    patch["updated_at"] = util.now_iso()
    db.update("tournaments", int(tid), patch)
    return db.row_to_dict(db.q1("SELECT * FROM tournaments WHERE id = ?", (int(tid),)))


def _pistes_moteur(club):
    """Les adresses a lire en plus de celles qu'on connait deja.

    Nos pistes partent toujours de ce qu'on sait : un site, un mail, une
    annonce. Un club dont on n'a que le nom et la ville n'en a aucune. Un
    moteur de recherche, lui, trouve sa page — et c'est gratuit.

    Gemini vient en renfort quand une cle est posee ET qu'elle fonctionne ;
    sinon on se contente du moteur, qui suffit dans la plupart des cas. Dans
    les deux cas on ne recolte que des ADRESSES : les numeros continuent
    d'etre lus sur la page par numeros.py.
    """
    from . import numeros
    nom = (club.get("name") or "").strip()
    if not nom:
        return [], None, None

    extra = []
    question = " ".join(x for x in (nom, club.get("city") or "", "contact telephone") if x)
    try:
        for res in numeros.interroger(question, maximum=5):
            extra.append((res["url"], "le moteur : %s" % (res.get("titre") or "")[:60]))
    except Exception:
        pass

    souci = alerte = None
    # Google se facture a la requete : on ne l'appelle que si le gratuit n'a
    # rien donne. Un club qui a deja son site, ou pour lequel le moteur a
    # rendu des pages, n'a pas besoin de lui.
    deja = bool(extra) or bool((club.get("website") or "").strip())
    if recherche.disponible() and not deja:
        try:
            d = recherche.pages_du_club(club)
            vus = {u for u, _ in extra}
            extra = [p for p in d["pages"] if p[0] not in vus] + extra
            alerte = recherche.verifier_ville(club, d.get("ville_confirmee"))
        except recherche.Epuise as exc:
            # Plafond atteint : ce n'est pas une panne, on le dit simplement.
            souci = str(exc)
        except recherche.Indisponible as exc:
            souci = str(exc)
    return extra, souci, alerte


@route("POST", r"/api/envoyer/mail")
def api_envoyer_mail(req):
    """Envoie vraiment le mail, par Mail.app, sur un clic de Julien.

    Le CRM ne decide jamais d'envoyer : il n'y a pas de declencheur
    automatique, pas de file d'envoi, pas de veilleur qui ecrit aux clubs.
    Cette route n'existe que pour le bouton, sur un texte affiche.

    Un club en opt-out n'est jamais joignable, meme par ce chemin.
    """
    from . import envoi
    b = req.body or {}
    club_id = b.get("club_id")
    a_ = (b.get("a") or "").strip()
    objet = (b.get("objet") or "").strip()
    corps = (b.get("corps") or "").strip()

    club = None
    if club_id:
        club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(club_id),)))
        if not club:
            raise ApiError("club introuvable", 404)
        if club.get("do_not_contact"):
            raise ApiError("ce club a demandé à ne plus être contacté")
        if not a_:
            contact = drafts.best_contact(int(club_id))
            a_ = (contact or {}).get("email") or club.get("email") or ""

    try:
        out = envoi.par_mail(a_, objet, corps)
    except envoi.Refus as exc:
        raise ApiError(str(exc))

    envoi.noter_envoi(club_id, "mail", out["a"], objet, corps, b.get("draft_id"))
    suite = _ranger_apres_envoi(club_id, b.get("tournament_id"))
    return {"ok": True, "a": out["a"], "objet": out["objet"],
            "range": suite["range"], "prochaine": suite["prochaine"]}


def _ranger_apres_envoi(club_id, tournament_id=None, quand=None):
    """Ce qui suit un message parti : le club avance, la date se range.

    Appuyer sur le bouton vert et devoir ensuite cliquer « c'est envoye »,
    c'est faire deux fois le meme geste. L'envoi fait maintenant tout : il
    trace (noter_envoi s'en charge), fait passer le club de prospect a
    contacte, tranche le tournoi — on y va, puisqu'on a ecrit — et pose la
    cadence de relance J+4 / J+10 / J+21 a partir d'aujourd'hui.
    """
    quand = quand or util.now_iso()
    bilan = {"range": False, "prochaine": None}
    if not club_id:
        return bilan
    club_id = int(club_id)
    try:
        club = db.q1("SELECT status FROM clubs WHERE id = ?", (club_id,))
        if club and club["status"] == "prospect":
            db.update("clubs", club_id, {"status": "contacte",
                                         "updated_at": util.now_iso()})
        if tournament_id:
            tid = int(tournament_id)
            db.ex("UPDATE drafts SET status='sent', sent_at=?, updated_at=? "
                  "WHERE tournament_id=? AND club_id=? AND status IN ('draft','approved')",
                  (quand, util.now_iso(), tid, club_id))
            db.update("tournaments", tid, {"validation": "valide",
                                           "updated_at": util.now_iso()})
            bilan["range"] = True
        repo.schedule_sequence(club_id, start=quand)
        suite = db.q1("SELECT due_at FROM tasks WHERE club_id = ? AND status='pending' "
                      "AND kind='relance' ORDER BY due_at LIMIT 1", (club_id,))
        if suite:
            bilan["prochaine"] = suite["due_at"]
    except Exception as exc:                 # ranger ne doit jamais perdre un envoi
        db.log("envoi", "rangement apres envoi impossible : %s" % exc)
    return bilan


@route("POST", r"/api/envoyer/note")
def api_envoyer_note(req):
    """« C'est envoye » : Julien a appuye lui-meme, on garde la trace.

    Sans ca, un message parti par WhatsApp n'existait nulle part : ni dans
    les echanges du club, ni dans sa date de dernier contact.
    """
    from . import envoi
    b = req.body or {}
    club_id = b.get("club_id")
    if not club_id:
        raise ApiError("quel club ?")
    if not db.q1("SELECT id FROM clubs WHERE id = ?", (int(club_id),)):
        raise ApiError("club introuvable", 404)
    envoi.noter_envoi(club_id, (b.get("canal") or "whatsapp"),
                      (b.get("a") or "").strip(), (b.get("objet") or "").strip(),
                      (b.get("corps") or "").strip(), b.get("draft_id"))
    suite = _ranger_apres_envoi(club_id, b.get("tournament_id"))
    return {"ok": True, "range": suite["range"], "prochaine": suite["prochaine"]}


@route("POST", r"/api/envoyer/whatsapp")
def api_envoyer_whatsapp(req):
    """Ouvre la conversation avec le texte, et appuie sur Entree.

    WhatsApp n'a pas d'API gratuite : on passe donc par l'application de
    bureau. L'envoi est reel et ne se rattrape pas. Si WhatsApp ne passe
    pas au premier plan, rien n'est envoye et on le dit — la conversation
    reste ouverte, prete.
    """
    from . import envoi
    b = req.body or {}
    club_id = b.get("club_id")
    tel = (b.get("tel") or "").strip()
    corps = (b.get("corps") or "").strip()

    if club_id:
        club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(club_id),)))
        if not club:
            raise ApiError("club introuvable", 404)
        if club.get("do_not_contact"):
            raise ApiError("ce club a demandé à ne plus être contacté")
        if not tel:
            contact = drafts.best_contact(int(club_id))
            tel = (contact or {}).get("phone") or club.get("phone") or ""
    if tel and not util.is_mobile_fr(tel) and not tel.startswith("+"):
        raise ApiError("WhatsApp ne passe pas sur une ligne fixe")

    try:
        out = envoi.par_whatsapp(tel, corps)
    except envoi.Refus as exc:
        raise ApiError(str(exc))

    envoi.noter_envoi(club_id, "whatsapp", out["a"], "", corps, b.get("draft_id"))
    suite = _ranger_apres_envoi(club_id, b.get("tournament_id"))
    return {"ok": True, "a": out["a"], "range": suite["range"],
            "prochaine": suite["prochaine"]}


@route("GET", r"/api/orphelins")
def api_orphelins(req):
    """Ou en est la recherche des organisateurs."""
    from . import orphelins
    return {"veille": orphelins.dernier_passage(), "reste": orphelins.reste(),
            "par_tour": orphelins.PAR_TOUR, "intervalle": orphelins.INTERVALLE}


@route("POST", r"/api/orphelins/maintenant")
def api_orphelins_maintenant(req):
    from . import orphelins
    try:
        limite = max(1, min(10, int((req.body or {}).get("combien", 3))))
    except (TypeError, ValueError):
        limite = 3
    return orphelins.un_tour(limite)


@route("GET", r"/api/enrichir")
def api_enrichir(req):
    """Ou en est la recherche automatique des coordonnees."""
    from . import enrichir, recherche
    return {"veille": enrichir.dernier_passage(),
            "reste": enrichir.reste(),
            "google": recherche.etat(),
            "par_tour": enrichir.PAR_TOUR,
            "intervalle": enrichir.INTERVALLE}


@route("POST", r"/api/enrichir/maintenant")
def api_enrichir_maintenant(req):
    """Un passage tout de suite, sans attendre le quart d'heure."""
    from . import enrichir
    try:
        limite = max(1, min(10, int((req.body or {}).get("combien", 2))))
    except (TypeError, ValueError):
        limite = 2
    return enrichir.un_tour(limite)


@route("GET", r"/api/recherche/etat")
def api_recherche_etat(req):
    return recherche.etat()


@route("POST", r"/api/tournaments/(\d+)/chercher-contact")
def api_chercher_contact(req, tid):
    """Va LIRE le telephone et le mail sur les pages du club.

    Pas d'IA ici, et c'est deliberе : un modele de langue invente un numero
    plausible quand il ne sait pas, et un numero plausible ne se distingue pas
    d'un vrai. On ne rapporte donc que des suites de chiffres reellement
    presentes sur une page, en disant sur laquelle.

    Rien n'est enregistre : Julien retient ce qu'il veut.
    """
    t = db.row_to_dict(db.q1("SELECT * FROM tournaments WHERE id = ?", (int(tid),)))
    if not t:
        raise ApiError("tournoi introuvable", 404)
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (t.get("club_id"),))) \
        if t.get("club_id") else {}
    from . import numeros
    extra, souci, alerte = _pistes_moteur(dict(club or {}, city=(club or {}).get("city")
                                               or t.get("city")))
    sortie = numeros.chercher(club, t, extra=extra)
    sortie["moteur"] = {"pages": len(extra), "souci": souci, "alerte": alerte}
    return sortie


@route("POST", r"/api/tournaments/(\d+)/chercher-web")
def api_chercher_web(req, tid):
    """Lance la recherche sur le web et rapporte ce qui y figure.

    Julien le faisait a la main : ouvrir Google, lire, recopier. Le moteur
    fait tres bien ce travail — il suffisait de le lancer et de LIRE les
    pages. Deux passes : qui organise, puis les coordonnees de ce club-la.

    Les numeros et les mails sont extraits par util depuis le texte des pages,
    jamais ecrits par un modele. Rien n'est enregistre : Julien retient ce
    qu'il veut.
    """
    t = db.row_to_dict(db.q1("SELECT * FROM tournaments WHERE id = ?", (int(tid),)))
    if not t:
        raise ApiError("tournoi introuvable", 404)
    from . import numeros
    return numeros.fouiller(t)


@route("POST", r"/api/clubs/(\d+)/chercher-contact")
def api_chercher_contact_club(req, cid):
    """Le meme travail, mais pour une organisation sans tournoi.

    Les relais — medias, communautes de parents — n'organisent rien : la
    recherche attachee aux tournois ne pouvait donc pas les servir, et leurs
    fiches restaient sans numero, donc sans WhatsApp. Meme regle qu'ailleurs :
    on lit des pages, on ne devine rien, et on dit d'ou vient chaque chiffre.
    """
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("fiche introuvable", 404)
    from . import numeros
    extra, souci, alerte = _pistes_moteur(club)
    sortie = numeros.chercher(club, None, extra=extra)
    sortie["moteur"] = {"pages": len(extra), "souci": souci, "alerte": alerte}
    return sortie


@route("POST", r"/api/clubs/(\d+)/retenir-contact")
def api_retenir_contact(req, cid):
    """Enregistre le numero ou le mail que Julien a retenu parmi les trouvailles."""
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("fiche introuvable", 404)
    b = req.body or {}
    champs = {}
    if (b.get("phone") or "").strip():
        champs["phone"] = b["phone"].strip()
    if (b.get("email") or "").strip():
        champs["email"] = b["email"].strip()
    if not champs:
        raise ApiError("ni numero ni adresse a retenir")
    champs["updated_at"] = util.now_iso()
    db.update("clubs", int(cid), champs)
    return {"ok": True, "retenu": {k: v for k, v in champs.items() if k != "updated_at"}}


@route("GET", r"/api/tournaments/(\d+)/message")
def api_message_tournoi(req, tid):
    """Le message pour l'organisateur, redige mais PAS enregistre.

    Pre-rediger pour tout le monde remplissait la pile de relecture ; ne
    rediger que pour ce qui entre dans la fenetre de contact laissait 172
    fiches sur 189 sans un mot, et la carte ouverte ne montrait rien. On rend
    donc le texte a la demande : il s'affiche des qu'on regarde la fiche, et
    il ne devient un brouillon que le jour ou Julien s'en sert.
    """
    t = db.row_to_dict(db.q1("SELECT * FROM tournaments WHERE id = ?", (int(tid),)))
    if not t:
        raise ApiError("tournoi introuvable", 404)

    # S'il existe deja un brouillon POUR LE CLUB DE CE TOURNOI, c'est lui qui
    # fait foi : Julien a pu le corriger, on ne va pas le remplacer.
    #
    # Le filtre sur le club n'est pas une precaution theorique. Un tournoi
    # porte volontairement plusieurs brouillons — generate_for_tournament en
    # ecrit un par club voisin a inviter, parfois dix. En prenant simplement
    # le dernier par numero, cette route rendait le message d'un AUTRE club,
    # avec SON numero : on ouvrait WhatsApp sur l'US Pradet en croyant ecrire
    # au Football Arena d'Antibes. Cent trente brouillons sur deux cent
    # vingt-sept etaient dans ce cas.
    club_id = t.get("club_id")
    d = db.q1("SELECT * FROM drafts WHERE tournament_id = ? AND club_id = ? "
              "AND status IN ('draft','approved') ORDER BY id DESC LIMIT 1",
              (int(tid), club_id)) if club_id else None
    if d:
        d = drafts.get_draft(d["id"])
        adr = d.get("to_addr") or ""
        return {"corps": d["body"], "objet": d.get("subject"),
                "canal": d.get("channel"), "destinataire": adr or None,
                "tel": None if "@" in adr else (adr or None),
                "mail": adr if "@" in adr else None,
                "club_id": club_id,
                "draft_id": d["id"], "enregistre": True}

    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (t.get("club_id"),))) \
        if t.get("club_id") else None
    contact = drafts.best_contact(club["id"]) if club else None

    # Deux cent quarante-sept tournois n'ont aucun club rattache : jusqu'ici
    # leur carte repondait « indisponible » et il n'y avait rien a faire avec.
    # Or l'annonce porte souvent tout ce qu'il faut — l'organisateur dans son
    # champ, le telephone et le mail dans sa description. On fabrique donc un
    # destinataire a partir de la fiche elle-meme. Les coordonnees sont lues
    # par util, jamais devinees : la ou il n'y a rien, il n'y a rien.
    if not club:
        texte = " ".join(str(t.get(k) or "") for k in ("description", "venue", "name"))
        tels = util.extract_phones(texte)
        mails = util.extract_emails(texte)
        nom = (t.get("organizer") or "").strip() or (t.get("name") or "").strip()
        if not nom:
            raise ApiError("aucun club rattaché à ce tournoi", 404)
        club = {"id": None, "name": nom, "city": t.get("city"),
                "phone": tels[0] if tels else None,
                "email": mails[0] if mails else None,
                "sport": t.get("sport")}
        # Pas de coordonnees, mais un organisateur NOMME : « Meeting Porsche
        # au Castellet » dit « Porsche Club Motorsport France ». Refuser tout
        # net laissait l'ecran sur « indisponible (404) », alors que le
        # message peut parfaitement s'ecrire — il ne manque qu'a qui
        # l'envoyer, et c'est ce que la recherche web va chercher. On rend
        # donc le texte, en disant franchement qu'il n'a pas de destinataire.
        if not (club["phone"] or club["email"]):
            if not (t.get("organizer") or "").strip():
                raise ApiError(
                    "aucun club rattaché, et l'annonce ne donne ni numéro ni mail", 404)
            modele = drafts.modele_tournoi(t)
            rendu = drafts.render(modele, club, None, t)
            if not rendu:
                raise ApiError("modèle %s introuvable" % modele, 404)
            return {"corps": rendu["body"], "objet": rendu.get("subject"),
                    "canal": rendu["channel"], "destinataire": None,
                    "tel": None, "mail": None, "club_id": None,
                    "organisateur": club["name"], "sans_destinataire": True,
                    "modele": modele, "draft_id": None, "enregistre": False}
    modele = drafts.modele_tournoi(t)
    rendu = drafts.render(modele, club, contact, t)
    if not rendu:
        raise ApiError("modèle %s introuvable" % modele, 404)

    tel = (contact or {}).get("phone") or club.get("phone")
    mail = (contact or {}).get("email") or club.get("email")
    return {"corps": rendu["body"], "objet": rendu.get("subject"),
            "canal": "whatsapp" if (tel and util.is_mobile_fr(tel) and not mail) else rendu["channel"],
            "destinataire": mail or tel, "modele": modele,
            "tel": tel, "mail": mail, "club_id": club.get("id"),
            "draft_id": None, "enregistre": False}


@route("POST", r"/api/tournaments/(\d+)/brouillon-organisateur")
def api_brouillon_organisateur(req, tid):
    """Redige le message pour le club QUI ORGANISE ce tournoi.

    A ne pas confondre avec /drafts, qui ecrit aux clubs des environs. Le
    modele tournoi_avant dit « votre tournoi » : adresse a un club voisin, il
    raconte n'importe quoi. C'est ce qui arrivait tant que la veille livrait
    des tournois sans organisateur rattache.
    """
    t = db.row_to_dict(db.q1("SELECT * FROM tournaments WHERE id = ?", (int(tid),)))
    if not t:
        raise ApiError("tournoi introuvable")
    if not t.get("club_id"):
        raise ApiError("aucun club rattache a ce tournoi")

    deja = db.q1("SELECT id FROM drafts WHERE tournament_id = ? AND club_id = ? "
                 "AND status IN ('draft','approved')", (int(tid), t["club_id"]))
    if deja:
        return {"ok": True, "draft_id": deja["id"], "deja": True}

    d = drafts.create_draft(t["club_id"], "tournoi_avant", tournament_id=int(tid),
                            reason="Organisateur du tournoi %s" % t["name"])
    if not d:
        raise ApiError("message impossible : club en ne-pas-contacter, ou modele absent")
    return {"ok": True, "draft_id": d.get("id"), "deja": False}


@route("POST", r"/api/tournaments/(\d+)/deja-envoye")
def api_tournoi_deja_envoye(req, tid):
    """Un message parti a la main, hors du CRM : on le trace et on programme la suite.

    Sans ca, la relance ne part jamais — ou pire, le club est recontacte comme
    si personne ne lui avait rien ecrit. Le club est cree s'il n'existe pas
    encore : un tournoi trouve par la veille n'en a aucun.
    """
    corps = req.body or {}
    t = db.row_to_dict(db.q1("SELECT * FROM tournaments WHERE id = ?", (int(tid),)))
    if not t:
        raise ApiError("tournoi introuvable")

    quand = (corps.get("date") or "").strip() or util.today().isoformat()
    if len(quand) == 10:
        quand += " 12:00:00"
    canal = (corps.get("channel") or "whatsapp").strip()
    tel = (corps.get("phone") or "").strip()
    nom = (corps.get("club") or "").strip() or t.get("organizer") or t.get("name")

    club_id = t.get("club_id")
    if not club_id:
        club_id, _ = repo.upsert_club({
            "name": nom, "sport": t.get("sport"), "city": t.get("city"),
            "postal_code": t.get("postal_code"), "dept": t.get("dept"),
            "phone": tel or None, "source": t.get("source"), "source_url": t.get("url"),
        })
        db.update("tournaments", int(tid), {"club_id": club_id,
                                            "organizer": t.get("organizer") or nom,
                                            "updated_at": util.now_iso()})
    elif tel:
        club = db.q1("SELECT phone FROM clubs WHERE id = ?", (club_id,))
        if club and not club["phone"]:
            db.update("clubs", club_id, {"phone": tel, "updated_at": util.now_iso()})

    repo.add_interaction(club_id, kind=canal, direction="out",
                         subject=corps.get("subject") or ("%s — envoye a la main" % t["name"]),
                         body=corps.get("body"), occurred_at=quand)

    club = db.q1("SELECT status FROM clubs WHERE id = ?", (club_id,))
    if club and club["status"] == "prospect":
        db.update("clubs", club_id, {"status": "contacte", "updated_at": util.now_iso()})

    # Le brouillon prepare pour ce club n'a plus lieu d'etre : le message est parti.
    db.ex("UPDATE drafts SET status='sent', sent_at=?, updated_at=? "
          "WHERE tournament_id=? AND club_id=? AND status IN ('draft','approved')",
          (quand, util.now_iso(), int(tid), club_id))

    # On tranche aussi le tournoi : on y va, puisqu'on a ecrit.
    db.update("tournaments", int(tid), {"validation": "valide",
                                        "updated_at": util.now_iso()})

    # schedule_sequence rend des identifiants : on relit les echeances posees.
    repo.schedule_sequence(club_id, start=quand)
    taches = db.rows_to_dicts(db.q(
        "SELECT id, title, due_at FROM tasks WHERE club_id = ? AND status = 'pending' "
        "AND kind = 'relance' ORDER BY due_at", (club_id,)))
    aujourdhui = util.today().isoformat()
    en_retard = [x for x in taches if str(x.get("due_at") or "")[:10] <= aujourdhui]
    return {"ok": True, "club_id": club_id, "club": nom,
            "envoye_le": quand[:10], "taches": taches,
            "n_taches": len(taches), "n_en_retard": len(en_retard),
            "prochaine": taches[0]["due_at"] if taches else None}


@route("DELETE", r"/api/tournaments/(\d+)")
def api_tournament_delete(req, tid):
    db.delete("tournaments", int(tid))
    return {"ok": True}


@route("GET", r"/api/tournaments/(\d+)/matches")
def api_tournament_matches(req, tid):
    return drafts.generate_for_tournament(int(tid), radius=req.query.get("radius"),
                                          limit=req.query.get("limit"), dry_run=True)


@route("POST", r"/api/tournaments/(\d+)/drafts")
def api_tournament_drafts(req, tid):
    return drafts.generate_for_tournament(int(tid), radius=req.body.get("radius"),
                                          limit=req.body.get("limit"))


# --------------------------------------------------------------------------
# Sources de veille
# --------------------------------------------------------------------------

@route("GET", r"/api/sources")
def api_sources(req):
    return {"items": db.rows_to_dicts(db.q("SELECT * FROM sources ORDER BY name")),
            "kinds": [{"key": k, "label": v} for k, v in sources.KINDS]}


@route("POST", r"/api/sources")
def api_source_create(req):
    body = req.body
    if not body.get("name") or not body.get("url"):
        raise ApiError("nom et url requis")
    config = body.get("config")
    if isinstance(config, (dict, list)):
        config = json.dumps(config, ensure_ascii=False)
    source_id = db.insert("sources", {
        "name": body["name"], "kind": body.get("kind", "ics"), "url": body["url"],
        "config": config, "sport": body.get("sport"),
        "enabled": 1 if body.get("enabled", True) else 0,
        "created_at": util.now_iso()})
    return {"id": source_id}


@route("PATCH", r"/api/sources/(\d+)")
def api_source_update(req, sid):
    allowed = {"name", "kind", "url", "config", "sport", "enabled"}
    patch = {k: v for k, v in req.body.items() if k in allowed}
    if isinstance(patch.get("config"), (dict, list)):
        patch["config"] = json.dumps(patch["config"], ensure_ascii=False)
    if "enabled" in patch:
        patch["enabled"] = 1 if patch["enabled"] else 0
    db.update("sources", int(sid), patch)
    return db.row_to_dict(db.q1("SELECT * FROM sources WHERE id = ?", (int(sid),)))


@route("DELETE", r"/api/sources/(\d+)")
def api_source_delete(req, sid):
    db.delete("sources", int(sid))
    return {"ok": True}


@route("POST", r"/api/sources/(\d+)/run")
def api_source_run(req, sid):
    return sources.run_source(int(sid))


@route("POST", r"/api/watch/run")
def api_watch_run(req):
    results = sources.run_all()
    # Les annonces fraiches portent souvent le telephone de l'organisateur :
    # on le repeche avant d'ecrire quoi que ce soit, sinon le brouillon nait
    # sans destinataire et rejoint la pile des messages qu'on n'enverra pas.
    enrichis = repo.enrichir_contacts_depuis_annonces()
    generated = drafts.generate_for_new_tournaments() \
        if req.body.get("generate", True) else {"n_drafts": 0}
    return {"sources": results, "enrichis": enrichis, "generated": generated}


@route("POST", r"/api/watch/import")
def api_watch_import(req):
    """Colle direct : un .ics, un CSV ou du HTML sans passer par une URL."""
    text = req.body.get("text") or ""
    kind = req.body.get("kind") or "auto"
    name = req.body.get("source") or "colle a la main"
    if kind == "auto":
        kind = "ics" if "BEGIN:VCALENDAR" in text[:400].upper() else "csv"
    if kind == "ics":
        records = sources.from_ics(text, name)
    elif kind == "csv":
        records = sources.from_csv(text, name)
    elif kind == "rss":
        records = sources.from_rss(text, name)
    else:
        raise ApiError("type non gere pour un collage : %s" % kind)
    created, updated, skipped, _ = sources.save_tournaments(records, name,
                                                         req.body.get("sport"))
    return {"n_created": len(created), "n_updated": updated, "n_skipped": skipped,
            "items": created, "kind": kind}


# --------------------------------------------------------------------------
# Brouillons
# --------------------------------------------------------------------------

@route("GET", r"/api/drafts")
def api_drafts(req):
    return {"items": drafts.list_drafts(req.query.get("status", "draft"))}


@route("GET", r"/api/drafts/(\d+)")
def api_draft_get(req, did):
    draft = drafts.get_draft(int(did))
    if not draft:
        raise ApiError("brouillon introuvable", 404)
    return draft


@route("POST", r"/api/drafts")
def api_draft_create(req):
    body = req.body
    if not body.get("club_id"):
        raise ApiError("club_id requis")
    draft = drafts.create_draft(int(body["club_id"]), body.get("template_key",
                                                               "premier_contact"),
                                contact_id=body.get("contact_id"),
                                tournament_id=body.get("tournament_id"),
                                reason=body.get("reason"), extra=body.get("extra"),
                                created_by=body.get("created_by", "humain"))
    if not draft:
        raise ApiError("brouillon impossible (club en opt-out ou modele inconnu)")
    return draft


# --------------------------------------------------------------------------
# Pense-bete : une idee notee a la volee (« photostudio mobile cannes »)
# devient tout de suite les messages a envoyer, club par club.

# Le premier mot reconnu decide du message ; le reste filtre les clubs.
# On cherche un morceau du mot, pas le mot exact : une note tapee vite
# s'ecrit « photoshtudio » aussi souvent que « photo studio ».
MEMO_MODELES = [
    (("tudio", "shoot"), "studio_club", "Shooting studio mobile — adhérents"),
    (("date", "calendrier", "saison"), "calendrier_mail",
     "Demander les dates de la saison"),
    (("relanc",), "relance_1", "Relance"),
]
MEMO_VIDES = {"photo", "photos", "mobile", "pour", "les", "le", "la", "de", "des",
              "du", "a", "au", "aux", "en", "et", "club", "clubs", "sur", "avec",
              "tous", "toutes", "proposer", "envoyer", "message", "messages"}
MEMO_MAX = 25


def _memo_mots(texte):
    mots = re.findall(r"[a-z0-9]+", util.strip_accents(texte or "").lower())
    modele = None
    reste = []
    for m in mots:
        trouve = next((tpl for cles, tpl, _ in MEMO_MODELES
                       if any(c in m for c in cles)), None)
        if trouve:
            modele = modele or trouve
        elif m not in MEMO_VIDES and len(m) > 1:
            reste.append(m)
    return modele, reste


# ---------------------------------------------------------------------------
# Chercher
# ---------------------------------------------------------------------------
# Le pense-bete ne savait repondre qu'a trois mots — studio, dates, relance —
# et refusait tout le reste. Taper « basket nice » ne donnait rien, alors que
# la reponse etait dans la base. Il cherche donc partout quand il ne reconnait
# pas d'intention : clubs, tournois, conversations, contacts.
CHERCHE_MAX = 8


# « studio », « dates », « relance » disent ce qu'on veut FAIRE, pas ce qu'on
# cherche : « photo studio cannes foot » doit chercher cannes et foot.
MOTS_INTENTION = {"studio", "shooting", "shoot", "date", "dates", "calendrier",
                  "saison", "relance", "relancer", "trouve", "trouver",
                  "cherche", "chercher", "contacter", "demarcher"}


def _mots_cherche(texte):
    vides = MEMO_VIDES | MOTS_INTENTION
    return [m for m in re.findall(r"[a-z0-9]+", util.strip_accents(texte or "").lower())
            if m not in vides and len(m) > 1]


def _combien_dedans(mots, *champs):
    """Combien des mots tapes se trouvent dans ces champs."""
    plat = util.strip_accents(" ".join(str(c or "") for c in champs)).lower()
    return sum(1 for m in mots if m in plat)


def _extrait(texte, mots, largeur=90):
    """Le bout de message ou le mot apparait — pour qu'on reconnaisse l'echange."""
    if not texte:
        return None
    plat = util.strip_accents(texte).lower()
    for m in mots:
        i = plat.find(m)
        if i < 0:
            continue
        a = max(0, i - largeur // 3)
        bout = texte[a:a + largeur].strip()
        return ("…" if a else "") + bout + ("…" if a + largeur < len(texte) else "")
    return None


def _filtrer(lignes, mots, champs):
    """Ceux qui ont tous les mots. A defaut, ceux qui en ont le plus.

    « basket nice » ne rend rien : il n'y a pas de club de basket a Nice. Rendre
    une page vide serait exact et inutile — on montre alors ce qui approche, en
    disant que c'est approchant.
    """
    notes = [(_combien_dedans(mots, *(l.get(c) for c in champs)), l) for l in lignes]
    plein = [l for n, l in notes if n == len(mots)]
    if plein:
        return plein, True
    meilleur = max((n for n, _ in notes), default=0)
    if meilleur == 0:
        return [], True
    return [l for n, l in notes if n == meilleur], False


def _texte_des_messages(chat):
    """Le contenu des derniers messages d'une conversation, en un seul bloc."""
    try:
        msgs = json.loads(chat.get("derniers") or "[]")
    except (TypeError, ValueError):
        return ""
    return " ".join((m.get("texte") or "") for m in msgs if isinstance(m, dict))


# Ce qui ressemble a un identifiant plutot qu'a des mots : un numero, un mail.
# Colle depuis le telephone ou un message, il arrive dans toutes les
# orthographes — « 06 12 34 56 78 », « +33 6 12 34 56 78 », « 0612345678 ».
# La recherche par mots n'en trouvait aucune : elle comparait des chaines.
_RX_MAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _identifiant(texte):
    """(genre, valeur) si le texte EST un numero ou un mail. Sinon (None, None)."""
    brut = (texte or "").strip()
    if not brut:
        return None, None
    m = _RX_MAIL.search(brut)
    if m and len(brut) < 120:
        return "mail", m.group(0).lower()
    chiffres = re.sub(r"\D", "", brut)
    # Au moins six chiffres, et presque que des chiffres : « U13 » n'en est pas un.
    if len(chiffres) >= 6 and len(chiffres) >= len(re.sub(r"[\s+().-]", "", brut)) - 2:
        return "tel", chiffres
    return None, None


def _par_identifiant(genre, valeur):
    """Cherche un numero ou un mail partout ou il peut etre range.

    Pour un numero on compare les CHIFFRES, et sur les huit derniers : un
    meme portable est ecrit +33681..., 0681..., 33681... selon la source.
    """
    groupes = []
    if genre == "tel":
        fin = valeur[-8:]
        comme = "%" + fin
        clubs = db.rows_to_dicts(db.q(
            "SELECT id, name, city, sport, phone, email FROM clubs "
            "WHERE replace(replace(replace(COALESCE(phone,''),' ',''),'.',''),'-','') LIKE ?",
            (comme,)))
        contacts = db.rows_to_dicts(db.q(
            "SELECT ct.id, ct.first_name, ct.last_name, ct.role, ct.phone, "
            "       ct.email, c.name AS club_nom, ct.club_id FROM contacts ct "
            "LEFT JOIN clubs c ON c.id = ct.club_id "
            "WHERE replace(replace(replace(COALESCE(ct.phone,''),' ',''),'.',''),'-','') LIKE ? "
            "   OR replace(replace(replace(COALESCE(ct.phone2,''),' ',''),'.',''),'-','') LIKE ?",
            (comme, comme)))
        chats = db.rows_to_dicts(db.q(
            "SELECT w.id, w.nom, w.phone, w.dernier_le, c.name AS club_nom "
            "FROM wa_chats w LEFT JOIN clubs c ON c.id = w.club_id "
            "WHERE replace(replace(replace(COALESCE(w.phone,''),' ',''),'.',''),'-','') LIKE ? "
            "ORDER BY w.dernier_le DESC", (comme,)))
        rencontres = db.rows_to_dicts(db.q(
            "SELECT id, nom, organisation, role, telephone, ou FROM rencontres "
            "WHERE replace(replace(replace(COALESCE(telephone,''),' ',''),'.',''),'-','') LIKE ?",
            (comme,)))
    else:
        clubs = db.rows_to_dicts(db.q(
            "SELECT id, name, city, sport, phone, email FROM clubs "
            "WHERE lower(COALESCE(email,'')) = ?", (valeur,)))
        contacts = db.rows_to_dicts(db.q(
            "SELECT ct.id, ct.first_name, ct.last_name, ct.role, ct.phone, "
            "       ct.email, c.name AS club_nom, ct.club_id FROM contacts ct "
            "LEFT JOIN clubs c ON c.id = ct.club_id "
            "WHERE lower(COALESCE(ct.email,'')) = ?", (valeur,)))
        chats = []
        rencontres = db.rows_to_dicts(db.q(
            "SELECT id, nom, organisation, role, telephone, ou FROM rencontres "
            "WHERE lower(COALESCE(email,'')) = ?", (valeur,)))

    if clubs:
        groupes.append({"quoi": "clubs", "nom": "Clubs", "n": len(clubs),
                        "items": [{"id": c["id"], "titre": c["name"],
                                   "sous": " · ".join(x for x in (c.get("city"), c.get("sport")) if x)[:70],
                                   "tel": c.get("phone"), "mail": c.get("email"),
                                   "onglet": "clubs"} for c in clubs]})
    if contacts:
        groupes.append({"quoi": "contacts", "nom": "Contacts", "n": len(contacts),
                        "items": [{"id": ct["id"],
                                   "titre": " ".join(x for x in (ct.get("first_name"),
                                                                 ct.get("last_name")) if x) or "Sans nom",
                                   "sous": " · ".join(x for x in (ct.get("role"),
                                                                  ct.get("club_nom")) if x)[:70],
                                   "tel": ct.get("phone"), "mail": ct.get("email"),
                                   "onglet": "clubs"} for ct in contacts]})
    if chats:
        groupes.append({"quoi": "whatsapp", "nom": "Conversations WhatsApp",
                        "n": len(chats),
                        "items": [{"id": w["id"], "titre": w["nom"] or w["phone"],
                                   "sous": " · ".join(x for x in (
                                       w.get("club_nom"),
                                       ("dernier message le " + w["dernier_le"][:10])
                                       if w.get("dernier_le") else None) if x)[:70],
                                   "tel": w.get("phone"), "onglet": "whatsapp"}
                                  for w in chats]})
    if rencontres:
        groupes.append({"quoi": "rencontres", "nom": "Carnet du terrain",
                        "n": len(rencontres),
                        "items": [{"id": r["id"], "titre": r["nom"],
                                   "sous": " · ".join(x for x in (r.get("role"),
                                                                  r.get("organisation"),
                                                                  r.get("ou")) if x)[:70],
                                   "tel": r.get("telephone"), "onglet": "rencontres"}
                                  for r in rencontres]})
    return groupes


def chercher(texte, mots=None):
    """Tout ce qui, dans la base, contient tous les mots tapes.

    Un numero de telephone ou un mail colle dans la barre est reconnu comme
    tel et cherche sur sa valeur, pas sur son orthographe.
    """
    genre, valeur = _identifiant(texte)
    if genre:
        groupes = _par_identifiant(genre, valeur)
        total = sum(g["n"] for g in groupes)
        return {"mots": [valeur], "identifiant": genre, "groupes": groupes,
                "total": total}

    mots = mots if mots is not None else _mots_cherche(texte)
    if not mots:
        return {"mots": [], "groupes": [], "total": 0}

    groupes = []

    clubs = [db.row_to_dict(r) for r in db.q("SELECT * FROM clubs")]
    trouves, exact = _filtrer(clubs, mots, ("name", "city", "sport"))
    if trouves:
        trouves.sort(key=lambda c: (c.get("do_not_contact") or 0, c.get("name") or ""))
        groupes.append({"quoi": "clubs", "nom": "Clubs", "n": len(trouves),
                        "approchant": not exact,
                        "items": [{"id": c["id"], "titre": c["name"],
                                   "sous": " · ".join(x for x in (c.get("city"), c.get("sport")) if x)[:70],
                                   "tel": c.get("phone"), "mail": c.get("email"),
                                   "onglet": "clubs"}
                                  for c in trouves[:CHERCHE_MAX]]})

    tours = [db.row_to_dict(r) for r in db.q(
        "SELECT * FROM tournaments WHERE date(COALESCE(start_date,'2099-01-01')) >= date('now') "
        "ORDER BY start_date")]
    trouves, exact = _filtrer(tours, mots, ("name", "city", "sport", "description"))
    if trouves:
        groupes.append({"quoi": "tournois", "nom": "Dates à venir", "n": len(trouves),
                        "approchant": not exact,
                        "items": [{"id": t["id"], "titre": t["name"],
                                   "sous": " · ".join(x for x in (
                                       t.get("start_date"), t.get("city"),
                                       ("%s km" % t["distance_km"]) if t.get("distance_km") is not None else None)
                                       if x),
                                   "onglet": "tournois"}
                                  for t in trouves[:CHERCHE_MAX]]})

    chats = [db.row_to_dict(r) for r in db.q(
        "SELECT w.*, c.name AS club_nom FROM wa_chats w "
        "LEFT JOIN clubs c ON c.id = w.club_id WHERE w.ecarte = 0 "
        "ORDER BY w.dernier_le DESC")]
    # On fouille aussi le TEXTE des messages, pas seulement le nom de la
    # conversation : « le gars qui parlait du gymnase » ne se retrouve pas
    # autrement, et c'est souvent comme ca qu'on se souvient d'un echange.
    for w in chats:
        w["texte_messages"] = _texte_des_messages(w)
    trouves, exact = _filtrer(chats, mots, ("nom", "club_nom", "resume",
                                            "texte_messages"))
    if trouves:
        groupes.append({"quoi": "conversations", "nom": "Conversations WhatsApp",
                        "n": len(trouves), "approchant": not exact,
                        "items": [{"id": w["id"], "titre": w["nom"],
                                   "sous": " · ".join(x for x in (
                                       w.get("club_nom") or "club inconnu",
                                       (w.get("dernier_le") or "")[:10]) if x),
                                   "extrait": _extrait(w.get("texte_messages"), mots),
                                   "tel": w.get("phone"), "onglet": "whatsapp"}
                                  for w in trouves[:CHERCHE_MAX]]})

    # Ce qu'on a NOTE : le vrai souvenir d'un salon, d'un coup de fil ou d'une
    # rencontre au forum vit la, pas dans le nom d'une fiche. Sans ce tas,
    # « la natation etait interessee au salon » ne se retrouve nulle part.
    notes = [db.row_to_dict(r) for r in db.q(
        "SELECT i.id, i.club_id, i.kind, i.subject, i.body, i.outcome, "
        "       i.occurred_at, c.name AS club_nom, c.city AS club_ville, "
        "       c.sport AS club_sport "
        "FROM interactions i LEFT JOIN clubs c ON c.id = i.club_id "
        "ORDER BY COALESCE(i.occurred_at, i.created_at) DESC")]
    for r in db.q("SELECT id, name, city, sport, notes FROM clubs "
                  "WHERE TRIM(COALESCE(notes,'')) <> ''"):
        notes.append({"id": None, "club_id": r["id"], "kind": "fiche",
                      "subject": "Note sur la fiche", "body": r["notes"],
                      "outcome": None, "occurred_at": None,
                      "club_nom": r["name"], "club_ville": r["city"],
                      "club_sport": r["sport"]})
    trouves, exact = _filtrer(notes, mots, ("club_nom", "club_sport", "club_ville",
                                            "subject", "body", "outcome"))
    if trouves:
        groupes.append({"quoi": "notes", "nom": "Notes et échanges",
                        "n": len(trouves), "approchant": not exact,
                        "items": [{"id": x.get("club_id"),
                                   "titre": x.get("club_nom") or "Sans club",
                                   "sous": " · ".join(y for y in (
                                       x.get("subject"), (x.get("occurred_at") or "")[:10])
                                       if y),
                                   "extrait": _extrait(
                                       " ".join(y for y in (x.get("body"), x.get("outcome")) if y),
                                       mots, 140),
                                   "club_id": x.get("club_id"), "onglet": "clubs"}
                                  for x in trouves[:CHERCHE_MAX]]})

    contacts = [db.row_to_dict(r) for r in db.q(
        "SELECT ct.*, c.name AS club_nom FROM contacts ct "
        "LEFT JOIN clubs c ON c.id = ct.club_id")]
    trouves, exact = _filtrer(contacts, mots,
                              ("first_name", "last_name", "role", "club_nom"))
    if trouves:
        groupes.append({"quoi": "contacts", "nom": "Personnes", "n": len(trouves),
                        "approchant": not exact,
                        "items": [{"id": x["id"],
                                   "titre": " ".join(y for y in (x.get("first_name"),
                                                                 x.get("last_name")) if y) or "—",
                                   "sous": " · ".join(y for y in (x.get("club_nom"),
                                                                  x.get("role")) if y),
                                   "tel": x.get("phone"), "mail": x.get("email"),
                                   "club_id": x.get("club_id"), "onglet": "clubs"}
                                  for x in trouves[:CHERCHE_MAX]]})

    return {"mots": mots, "groupes": groupes,
            "total": sum(g["n"] for g in groupes)}


@route("GET", r"/api/chercher")
def api_chercher(req):
    return chercher(req.query.get("q"))


@route("GET", r"/api/demander")
def api_demander(req):
    """Une question en francais, une reponse appuyee sur la base.

    La barre « Chercher » rendait des listes. Une liste n'est pas une reponse :
    a « qu'est-ce que la natation m'a dit pour le salon ? », on veut la phrase.
    Le modele tourne sur ce Mac et ne voit QUE les fiches trouvees ici — il ne
    sait rien d'autre, et les fiches qui ont servi sont rendues avec la reponse
    pour qu'on puisse toujours aller verifier.
    """
    from . import cerveau
    return cerveau.demander(req.query.get("q"))


@route("GET", r"/api/prospecter")
def api_prospecter(req):
    """Les clubs d'un secteur qui ne sont PAS encore en fiche.

    La recherche ordinaire fouille ce que Julien connait deja ; a « des clubs
    de sport autour de Draguignan » elle repondait donc « rien dans la base »,
    ce qui est vrai et parfaitement inutile. Ici on interroge le repertoire
    officiel des associations (SIRENE/RNA, ouvert, sans cle), et on retire ce
    qui est deja en fiche.

    Aucun telephone n'est rendu : le repertoire n'en publie pas, et on
    n'invente pas. Ils se cherchent ensuite club par club, sur ses pages.
    """
    from . import prospection
    try:
        rayon = max(3, min(60, int(req.query.get("rayon", prospection.RAYON_DEFAUT))))
    except (TypeError, ValueError):
        rayon = prospection.RAYON_DEFAUT
    out = prospection.explorer((req.query.get("ou") or "").strip(), rayon_km=rayon)
    if not out.get("ok"):
        raise ApiError(out.get("error") or "prospection impossible")
    return out


@route("GET", r"/api/ratisser")
def api_ratisser(req):
    """Tout le couloir d'un coup, range par distance de la maison.

    Une ville a la fois ne suffit pas quand la semaine est vide : ce qu'il
    faut alors, c'est tout ce qui existe entre Draguignan et Monaco. Monaco
    n'est pas une commune francaise et ne figure donc pas dans la geographie
    officielle : ses quatre-vingt-onze clubs sont deja en fiche, venus de
    l'annuaire monegasque.
    """
    from . import prospection
    try:
        rayon = max(5, min(40, int(req.query.get("rayon", 15))))
    except (TypeError, ValueError):
        rayon = 15
    villes = [v.strip() for v in (req.query.get("villes") or "").split(",") if v.strip()]
    out = prospection.ratisser(villes or None, rayon_km=rayon)
    quoi = (req.query.get("discipline") or "").strip()
    if quoi:
        out["candidats"] = [c for c in out["candidats"]
                            if (c.get("discipline") or "") == quoi]
        out["filtre"] = quoi
    out["disciplines"] = sorted({c["discipline"] for c in out["candidats"]
                                 if c.get("discipline")})
    out["candidats"] = out["candidats"][:400]
    return out


@route("POST", r"/api/prospecter/retenir")
def api_prospecter_retenir(req):
    """Met un candidat en fiche. Rien n'est envoye, rien n'est redige."""
    b = req.body or {}
    nom = (b.get("nom") or "").strip()
    if not nom:
        raise ApiError("il faut un nom")
    cid, etat = repo.upsert_club({
        "name": nom, "city": (b.get("ville") or "").strip() or None,
        "postal_code": (b.get("code_postal") or "").strip() or None,
        "address": (b.get("adresse") or "").strip() or None,
        "sport": (b.get("sport") or "").strip() or None,
        "source": "Répertoire officiel des associations",
        "source_url": (b.get("fiche") or "").strip() or None,
        "notes": (b.get("note") or "").strip() or None,
    })
    if not cid:
        raise ApiError("fiche impossible")
    return {"ok": True, "club_id": cid, "etat": etat}


@route("GET", r"/api/routines")
def api_routines(req):
    """Les questions qui se reposent toutes seules, chaque nuit."""
    from . import cerveau
    return {"items": cerveau.routines()}


@route("POST", r"/api/routines")
def api_routine_ajouter(req):
    from . import cerveau
    out = cerveau.ajouter_routine((req.body or {}).get("question"))
    if not out.get("ok"):
        raise ApiError(out.get("error") or "routine impossible")
    return out


@route("DELETE", r"/api/routines/(\d+)")
def api_routine_supprimer(req, rid):
    from . import cerveau
    return cerveau.supprimer_routine(int(rid))


@route("POST", r"/api/reperage/chercher")
def api_reperage_chercher(req):
    """La recherche libre du Reperage : « puppy painting a Nice ». Ne rien
    enregistrer ici ; l'ajout passe par POST /api/tournaments, au clic."""
    from . import recherche
    return recherche.chercher((req.body or {}).get("q"))


@route("POST", r"/api/memo")
def api_memo(req):
    texte = req.body.get("text")
    modele, mots = _memo_mots(texte)
    if not modele:
        # Les trois mots-cles n'ont rien reconnu : on demande au modele local
        # de traduire la phrase en intention + mots. Il ne cherche rien et ne
        # decide de rien — c'est la base qui trouve les fiches ensuite. Un
        # modele n'a pas le droit d'inventer un club qui n'existe pas.
        compris = None
        try:
            if ia.disponible():
                compris = ia.comprendre(texte)
        except Exception:
            compris = None
        if compris and compris.get("modele") and compris.get("mots"):
            modele, mots = compris["modele"], compris["mots"]
        else:
            d = chercher(texte, (compris or {}).get("mots"))
            d["mode"] = "recherche"
            d["compris"] = compris
            return d
    raison = next(r for _, tpl, r in MEMO_MODELES if tpl == modele)

    clubs = [db.row_to_dict(r) for r in db.q(
        "SELECT * FROM clubs WHERE do_not_contact = 0 AND relais = 0 "
        "AND status NOT IN ('perdu')")]

    def cle(c, champ):
        return util.strip_accents(str(c.get(champ) or "")).lower()

    # Chaque mot est une ville s'il en designe une, sinon un sport, sinon un
    # morceau de nom : « cannes escrime » = les clubs d'escrime de Cannes.
    filtres = []
    for m in mots:
        for champ in ("city", "sport", "name"):
            if any(m in cle(c, champ) for c in clubs):
                filtres.append((champ, m))
                break
        else:
            filtres.append(("name", m))
    choisis = [c for c in clubs
               if all(m in cle(c, champ) for champ, m in filtres)]
    choisis.sort(key=lambda c: (-(c.get("partenaire") or 0), c.get("priority") or 2,
                                cle(c, "name")))

    cartes, deja, sans_contact = [], [], []
    for c in choisis:
        if len(cartes) >= MEMO_MAX:
            break
        # Le meme message deja prepare : on le ressort au lieu d'en refaire un ;
        # deja envoye : on ne le renvoie pas.
        cles = drafts.variantes(modele)
        precedent = db.q1(
            "SELECT id, status FROM drafts WHERE club_id = ? AND template_key IN (%s) "
            "AND status != 'rejected' ORDER BY id DESC LIMIT 1" % ",".join("?" * len(cles)),
            (c["id"],) + tuple(cles))
        if precedent and precedent["status"] == "sent":
            deja.append(c["name"])
            continue
        if precedent:
            cartes.append(drafts.get_draft(precedent["id"]))
            continue
        d = drafts.create_draft(c["id"], modele, reason=raison, created_by="humain")
        if not d:
            continue
        if not d.get("to_addr"):
            db.delete("drafts", d["id"])
            sans_contact.append({"id": c["id"], "name": c["name"]})
            continue
        cartes.append(d)

    return {"modele": modele, "filtres": [m for _, m in filtres],
            "total": len(choisis), "cartes": cartes,
            "deja_envoye": deja, "sans_contact": sans_contact}


@route("PATCH", r"/api/drafts/(\d+)")
def api_draft_update(req, did):
    return drafts.update_draft(int(did), subject=req.body.get("subject"),
                               body=req.body.get("body"),
                               to_addr=req.body.get("to_addr"),
                               review_note=req.body.get("review_note"))


@route("POST", r"/api/drafts/(\d+)/approve")
def api_draft_approve(req, did):
    return drafts.approve(int(did), req.body.get("note"))


@route("POST", r"/api/drafts/(\d+)/reject")
def api_draft_reject(req, did):
    return drafts.reject(int(did), req.body.get("note"))


@route("POST", r"/api/drafts/(\d+)/sent")
def api_draft_sent(req, did):
    result = drafts.mark_sent(int(did),
                              schedule_followups=req.body.get("schedule_followups", True))
    if isinstance(result, dict) and result.get("error"):
        raise ApiError(result["error"], 409)
    return result


@route("DELETE", r"/api/drafts/(\d+)")
def api_draft_delete(req, did):
    db.delete("drafts", int(did))
    return {"ok": True}


@route("POST", r"/api/drafts/ranger-vieux")
def api_drafts_ranger_vieux(req):
    """Range les brouillons jamais partis et trop vieux. Compte d'abord.

    Sans `pour_de_vrai`, ne touche a rien : on voit combien et depuis quand
    avant de decider. Rien n'est efface — les lignes passent a « abandonne »
    et se reprennent une par une.
    """
    b = req.body or {}
    return drafts.abandonner_les_vieux(
        jours=int(b.get("jours") or 14),
        pour_de_vrai=bool(b.get("pour_de_vrai")))


@route("POST", r"/api/drafts/(\d+)/reprendre")
def api_draft_reprendre(req, did):
    """Remet dans la pile un brouillon range."""
    return {"draft": drafts.reprendre(int(did))}


@route("POST", r"/api/drafts/generate-relances")
def api_generate_relances(req):
    return drafts.generate_for_due_tasks(int(req.body.get("horizon_days", 0)))


# --------------------------------------------------------------------------
# Modeles
# --------------------------------------------------------------------------

@route("GET", r"/api/templates")
def api_templates(req):
    return {"items": db.rows_to_dicts(db.q("SELECT * FROM templates ORDER BY key")),
            "placeholders": PLACEHOLDERS}


PLACEHOLDERS = [
    ["prenom", "prenom du contact (ou 'bonjour')"],
    ["nom", "nom du contact"], ["nom_complet", "prenom + nom"],
    ["role", "fonction du contact"], ["club", "nom du club"],
    ["ville", "ville du club"], ["dept_nom", "nom du departement"],
    ["sport", "discipline"], ["source", "d'ou vient le contact"],
    ["sujet", "sujet evoque sur le forum"], ["pitch", "votre argumentaire (reglages)"],
    ["signature", "votre signature (reglages)"],
    ["tournoi", "nom du tournoi"], ["tournoi_date", "dates en clair"],
    ["tournoi_ville", "ville du tournoi"], ["tournoi_j", "nombre de jours restants"],
    ["tournoi_url", "lien du tournoi"],
    ["my_name", "votre nom"], ["my_org", "votre structure"],
    ["my_email", "votre email"], ["my_phone", "votre telephone"],
]


@route("PUT", r"/api/templates/([\w\-]+)")
def api_template_save(req, key):
    body = req.body
    exists = db.q1("SELECT key FROM templates WHERE key = ?", (key,))
    data = {"label": body.get("label") or key, "channel": body.get("channel", "email"),
            "subject": body.get("subject"), "body": body.get("body") or "",
            "notes": body.get("notes"), "updated_at": util.now_iso()}
    if exists:
        db.ex("UPDATE templates SET label=?, channel=?, subject=?, body=?, notes=?, "
              "updated_at=? WHERE key=?",
              (data["label"], data["channel"], data["subject"], data["body"],
               data["notes"], data["updated_at"], key))
    else:
        data["key"] = key
        db.insert("templates", data)
    return db.row_to_dict(db.q1("SELECT * FROM templates WHERE key = ?", (key,)))


@route("DELETE", r"/api/templates/([\w\-]+)")
def api_template_delete(req, key):
    db.ex("DELETE FROM templates WHERE key = ?", (key,))
    return {"ok": True}


@route("POST", r"/api/templates/([\w\-]+)/preview")
def api_template_preview(req, key):
    club = None
    if req.body.get("club_id"):
        club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?",
                                    (int(req.body["club_id"]),)))
    else:
        club = db.row_to_dict(db.q1("SELECT * FROM clubs ORDER BY updated_at DESC LIMIT 1"))
    contact = drafts.best_contact(club["id"]) if club else None
    tournament = db.row_to_dict(db.q1(
        "SELECT * FROM tournaments WHERE start_date >= date('now') ORDER BY start_date LIMIT 1"))
    if req.body.get("body"):
        ctx = drafts.build_context(club, contact, tournament)
        return {"subject": (req.body.get("subject") or "").format_map(drafts._SafeDict(ctx)),
                "body": req.body["body"].format_map(drafts._SafeDict(ctx))}
    return drafts.render(key, club, contact, tournament) or {}


# --------------------------------------------------------------------------
# Import de contacts
# --------------------------------------------------------------------------

@route("POST", r"/api/import/preview")
def api_import_preview(req):
    body = req.body
    sport = body.get("sport") or db.get_setting("sport")
    if body.get("rows") is not None:
        items = extract.extract_rows(body.get("headers") or [], body["rows"],
                                     mapping=body.get("mapping"),
                                     source=body.get("source"), default_sport=sport)
    else:
        items = extract.extract_any(body.get("text") or "", source=body.get("source"),
                                    source_url=body.get("source_url"),
                                    default_sport=sport, mode=body.get("mode", "auto"))
    for item in items:
        existing = repo.find_club(item["club"].get("name"), item["club"].get("website"),
                                  item["club"].get("email"), item["club"].get("dept"))
        item["existing_club"] = {"id": existing["id"], "name": existing["name"]} \
            if existing else None
        contact = item["contact"]
        dup = repo.find_contact(util.normalize_email(contact.get("email")),
                                util.normalize_phone(contact.get("phone")))
        item["existing_contact"] = {"id": dup["id"],
                                    "name": " ".join(x for x in [dup.get("first_name"),
                                                                 dup.get("last_name")] if x)} \
            if dup else None
        contact["phone_fmt"] = util.format_phone(util.normalize_phone(contact.get("phone")))
    return {"items": items, "n": len(items)}


@route("POST", r"/api/import/commit")
def api_import_commit(req):
    body = req.body
    items = body.get("items") or []
    schedule = bool(body.get("schedule_sequence"))
    stats = {"clubs_created": 0, "clubs_updated": 0, "contacts_created": 0,
             "contacts_updated": 0, "already_known": 0, "skipped": 0, "club_ids": []}
    for item in items:
        club_data = dict(item.get("club") or {})
        contact_data = dict(item.get("contact") or {})
        contact_data.pop("phone_fmt", None)
        club_id, action = repo.upsert_club(club_data)
        if club_id:
            stats["club_ids"].append(club_id)
            if action == "created":
                stats["clubs_created"] += 1
            elif action == "updated":
                stats["clubs_updated"] += 1
        elif not (contact_data.get("email") or contact_data.get("phone")):
            # ni club ni moyen de contact : la ligne n'apporte rien
            stats["skipped"] += 1
            continue
        contact_data["club_id"] = club_id
        contact_id, caction = repo.upsert_contact(contact_data)
        if caction == "created":
            stats["contacts_created"] += 1
        elif caction == "updated":
            stats["contacts_updated"] += 1
        elif caction == "unchanged":
            stats["already_known"] += 1
        if schedule and contact_id:
            repo.schedule_sequence(club_id, contact_id)
    db.log("import", "Import : %d clubs, %d contacts" % (
        stats["clubs_created"], stats["contacts_created"]))
    return stats


# --------------------------------------------------------------------------
# Depot de fichier (Excel / CSV)
# --------------------------------------------------------------------------

MAX_UPLOAD = 12 * 1024 * 1024


def parse_multipart(raw, content_type):
    """Parseur multipart/form-data minimal. Renvoie {champ: valeur|(nom, octets)}."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        raise ApiError("requete multipart sans delimiteur")
    boundary = b"--" + m.group(1).encode("latin-1")
    out = {}
    for part in raw.split(boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        if not _:
            continue
        headers = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', headers)
        if not name:
            continue
        filename = re.search(r'filename="([^"]*)"', headers)
        if filename:
            out[name.group(1)] = (filename.group(1), body)
            out.setdefault("_fichiers", []).append((filename.group(1), body))
        else:
            out[name.group(1)] = body.decode("utf-8", "replace")
    return out


def _decode_text(raw):
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


DEPOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "a-traiter")
DEPOT_OK = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".pdf", ".gif", ".tiff")


def _journal(texte):
    """Trace dans data/serveur.log : c'est ce que je relis quand ca coince."""
    import datetime
    chemin = os.path.join(os.path.dirname(DEPOT_DIR), "data", "serveur.log")
    try:
        with open(chemin, "a") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), texte))
    except OSError:
        pass


@route("POST", r"/api/depot/analyser")
def api_depot_analyser(req):
    """Depouille tout de suite les flyers en attente, sans rien attendre."""
    import traceback
    from . import flyer
    _journal("=== analyse demandee ===")
    try:
        fichiers = [n for n in os.listdir(DEPOT_DIR)
                    if not n.startswith(".") and n != "LISEZ-MOI.txt"
                    and os.path.isfile(os.path.join(DEPOT_DIR, n))]
    except OSError as exc:
        _journal("dossier illisible : %s" % exc)
        raise ApiError("boite de depot illisible : %s" % exc)
    _journal("fichiers en attente : %s" % (fichiers or "aucun"))
    if not fichiers:
        return {"items": [], "soucis": [], "lus": 0}

    try:
        resultats = flyer.depouiller_dossier(DEPOT_DIR)
    except Exception as exc:  # on veut la raison a l'ecran, pas une page blanche
        _journal("PLANTAGE : %s\n%s" % (exc, traceback.format_exc()))
        raise ApiError("l'analyse a echoue : %s" % exc)

    faits, soucis = [], []
    for r in resultats:
        _journal("  %s -> club=%s tournoi=%s brouillon=%s soucis=%s" % (
            r.get("fichier"),
            (r.get("club") or {}).get("nom"),
            (r.get("tournoi") or {}).get("nom"),
            (r.get("brouillon") or {}).get("action"),
            r.get("soucis")))
        if r.get("club") or r.get("tournoi"):
            faits.append(r)
        for s_ in r.get("soucis") or []:
            soucis.append("%s : %s" % (r.get("fichier") or "dossier", s_))
    _journal("=== fin : %d fiche(s), %d souci(s) ===" % (len(faits), len(soucis)))
    return {"items": faits, "soucis": soucis, "lus": len(resultats)}


@route("POST", r"/api/depot/supprimer")
def api_depot_supprimer(req):
    """Retire un document de la boite de depot. Definitif, mais sans consequence :
    le fichier d'origine reste dans vos photos."""
    import os
    nom = os.path.basename((req.body or {}).get("nom") or "")
    if not nom or nom in (".", "..", "LISEZ-MOI.txt"):
        raise ApiError("nom de fichier invalide")
    chemin = os.path.join(DEPOT_DIR, nom)
    if not os.path.isfile(chemin):
        raise ApiError("ce document n'est plus dans la boite")
    try:
        os.remove(chemin)
    except OSError as exc:
        raise ApiError("suppression impossible : %s" % exc)
    return {"ok": True, "nom": nom}


@route("GET", r"/api/depot")
def api_depot_list(req):
    """Ce qui attend dans la boite de depot, et ce qui a deja ete traite."""
    def _lister(chemin):
        try:
            noms = sorted(n for n in os.listdir(chemin)
                          if not n.startswith(".") and n != "LISEZ-MOI.txt"
                          and os.path.isfile(os.path.join(chemin, n)))
        except OSError:
            return []
        return noms
    traites = os.path.join(DEPOT_DIR, "traites")
    return {"en_attente": _lister(DEPOT_DIR), "traites": len(_lister(traites))}


@route("POST", r"/api/depot")
def api_depot_upload(req):
    """Depose un flyer ou une plaquette dans a-traiter/. Rien n'est lu ici :
    l'agent passe toutes les deux heures, lit les documents et remplit le CRM."""
    corps = req.raw_body or {}
    # On prend les fichiers tels que le parseur les a trouves, sans exiger un
    # nom de champ : l'ecran du flyer envoie « fichier », les autres « file ».
    recus = list(corps.get("_fichiers") or [])
    if not recus:
        for cle in ("file", "fichier"):
            v = corps.get(cle)
            if isinstance(v, tuple):
                recus.append(v)
    if not recus:
        raise ApiError("aucun fichier reçu")

    os.makedirs(DEPOT_DIR, exist_ok=True)
    poses, refuses = [], []
    for filename, blob in recus:
        nom = os.path.basename(filename or "document")
        nom = re.sub(r"[^A-Za-z0-9._ -]", "_", nom).strip() or "document"
        if not blob:
            refuses.append("%s : fichier vide" % nom)
            continue
        if len(blob) > MAX_UPLOAD:
            refuses.append("%s : trop lourd (%.1f Mo, maximum %d Mo)"
                           % (nom, len(blob) / 1048576.0, MAX_UPLOAD // 1048576))
            continue
        if not nom.lower().endswith(DEPOT_OK):
            refuses.append("%s : format non pris en charge (jpg, png, heic, pdf)" % nom)
            continue
        cible = os.path.join(DEPOT_DIR, nom)
        base, ext = os.path.splitext(nom)
        n = 2
        while os.path.exists(cible):
            cible = os.path.join(DEPOT_DIR, "%s-%d%s" % (base, n, ext))
            n += 1
        with open(cible, "wb") as f:
            f.write(blob)
        poses.append(os.path.basename(cible))

    # Tout refuse : c'est une erreur, et elle dit laquelle. Un seul passe :
    # on garde ce qui est passe et on nomme le reste.
    if not poses:
        raise ApiError(" ; ".join(refuses) or "aucun fichier reçu")
    return {"ok": True, "noms": poses, "nom": poses[0], "refuses": refuses}


@route("POST", r"/api/depot/depuis-url")
def api_depot_depuis_url(req):
    """Recupere une image dont Julien nous donne l'adresse, et la depose.

    C'est le pont entre un groupe Facebook et l'app : le navigateur reste chez
    lui, avec sa session, et ne nous passe qu'une adresse d'image. Rien n'est
    parcouru ni moissonne — on va chercher exactement le fichier demande.
    """
    url = ((req.body or {}).get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ApiError("adresse invalide")

    import urllib.error
    import urllib.request
    entete = {"User-Agent": "Mozilla/5.0 (Macintosh) prospect-clubs/1.0"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=entete),
                                    timeout=30) as rep:
            type_mime = (rep.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            blob = rep.read(MAX_UPLOAD + 1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise ApiError("image inaccessible : %s" % exc)

    if len(blob) > MAX_UPLOAD:
        raise ApiError("image trop lourde (maximum %d Mo)" % (MAX_UPLOAD // 1048576))
    if not blob:
        raise ApiError("image vide")

    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                  "image/heic": ".heic", "image/heif": ".heif", "image/gif": ".gif",
                  "application/pdf": ".pdf"}
    ext = extensions.get(type_mime)
    if not ext:
        # Certains CDN ne declarent rien d'utile : on retombe sur l'adresse.
        depuis_url = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        ext = depuis_url if depuis_url in (".jpg", ".jpeg", ".png", ".webp", ".heic",
                                           ".heif", ".gif", ".pdf") else None
    if not ext:
        raise ApiError("ce n'est pas une image : %s" % (type_mime or "type inconnu"))

    base = os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0]
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:40].strip("_ ") or "capture"
    nom = base + ext

    os.makedirs(DEPOT_DIR, exist_ok=True)
    cible = os.path.join(DEPOT_DIR, nom)
    n = 2
    while os.path.exists(cible):
        cible = os.path.join(DEPOT_DIR, "%s-%d%s" % (base, n, ext))
        n += 1
    with open(cible, "wb") as f:
        f.write(blob)
    return {"ok": True, "nom": os.path.basename(cible)}


@route("POST", r"/api/import/upload")
def api_import_upload(req):
    """Recoit un .xlsx / .csv / .tsv / .txt et renvoie le tableau + un mappage propose."""
    upload = (req.raw_body or {}).get("file")
    if not isinstance(upload, tuple):
        raise ApiError("aucun fichier recu")
    filename, blob = upload
    if not blob:
        raise ApiError("fichier vide")
    if len(blob) > MAX_UPLOAD:
        raise ApiError("fichier trop lourd (%.1f Mo, maximum %d Mo)"
                       % (len(blob) / 1048576.0, MAX_UPLOAD // 1048576))
    lower = (filename or "").lower()
    sheet = (req.raw_body or {}).get("sheet") or None
    sheets = []

    if lower.endswith(".xls") or blob[:4] == b"\xd0\xcf\x11\xe0":
        raise ApiError("l'ancien format .xls n'est pas lisible : ouvrez le fichier dans "
                       "Excel ou Numbers et faites Enregistrer sous > .xlsx ou .csv")
    if lower.endswith(".numbers"):
        raise ApiError("Numbers n'est pas lisible directement : Fichier > Exporter vers > "
                       "CSV (ou Excel), puis redeposez le fichier")

    if lower.endswith(".xlsx") or lower.endswith(".xlsm") or blob[:2] == b"PK":
        try:
            sheets = xlsx.sheet_names(io.BytesIO(blob))
            _name, headers, rows = xlsx.to_table(io.BytesIO(blob), sheet)
        except xlsx.XlsxError as exc:
            raise ApiError(str(exc))
        except Exception as exc:  # zip corrompu, xml inattendu...
            raise ApiError("classeur illisible : %s" % exc)
        kind = "xlsx"
    else:
        headers, rows = extract.sniff_table(_decode_text(blob))
        kind = "csv"
        if not headers:
            raise ApiError("aucune colonne detectee : le fichier doit avoir une ligne "
                           "d'entetes et un separateur (virgule, point-virgule ou tabulation)")

    mapping = extract.map_headers(headers)
    return {
        "filename": filename, "kind": kind, "sheets": sheets, "sheet": sheet or (sheets[0] if sheets else None),
        "headers": headers, "rows": rows[:5000], "n_rows": len(rows),
        "truncated": len(rows) > 5000,
        "mapping": {str(k): v for k, v in mapping.items()},
        "fields": [{"key": k, "label": l} for k, l in extract.IMPORT_FIELDS],
        "sample": rows[:5],
    }


# --------------------------------------------------------------------------
# Import d'un calendrier personnel
# --------------------------------------------------------------------------

@route("POST", r"/api/calendar/preview")
def api_calendar_preview(req):
    """Lit un calendrier (URL webcal/https, ou contenu colle) et classe chaque
    evenement en piste a contacter ou date a couvrir."""
    body = req.body
    text = body.get("text")
    source = body.get("source") or "calendrier"
    if not text:
        url = (body.get("url") or "").strip()
        if not url:
            raise ApiError("donnez une adresse de calendrier ou collez son contenu")
        try:
            text = sources.fetch(url)
        except ValueError as exc:
            raise ApiError(str(exc))
        except Exception as exc:  # reseau, 404, calendrier prive...
            raise ApiError("calendrier injoignable : %s" % exc)
    if "BEGIN:VCALENDAR" not in (text or "").upper():
        raise ApiError("ce n'est pas un calendrier iCal (BEGIN:VCALENDAR absent)")
    events = ics.parse_ics(text)
    if not events:
        raise ApiError("calendrier lu, mais il ne contient aucun evenement")
    items = extract.extract_calendar(events, source)
    for item in items:
        existing = repo.find_club(item["club"].get("name"), None,
                                  item["club"].get("email"), item["club"].get("dept"))
        item["existing_club"] = {"id": existing["id"], "name": existing["name"]} \
            if existing else None
        phone = util.normalize_phone(item["contact"].get("phone"))
        dup = repo.find_contact(util.normalize_email(item["contact"].get("email")), phone)
        item["existing_contact"] = {"id": dup["id"]} if dup else None
        item["contact"]["phone_fmt"] = util.format_phone(phone)
        fp = sources.fingerprint(item["event"])
        known = db.q1("SELECT id FROM tournaments WHERE fingerprint = ?", (fp,))
        item["existing_event"] = {"id": known["id"]} if known else None
    return {"items": items, "n": len(items),
            "n_leads": sum(1 for i in items if i["kind"] == "lead"),
            "n_events": sum(1 for i in items if i["kind"] == "event")}


@route("POST", r"/api/calendar/commit")
def api_calendar_commit(req):
    """Enregistre la selection : pistes -> clubs/contacts, evenements -> agenda.

    Une piste garde une trace datee de l'echange dans l'historique du club.
    Aucun message n'est prepare ici.
    """
    body = req.body
    items = body.get("items") or []
    stats = {"clubs_created": 0, "clubs_updated": 0, "contacts_created": 0,
             "events_created": 0, "events_known": 0, "already_known": 0, "skipped": 0}
    for item in items:
        kind = item.get("kind")
        if kind == "event":
            created, updated, _skipped, _ = sources.save_tournaments(
                [item.get("event") or {}], item.get("source") or "calendrier")
            stats["events_created"] += len(created)
            if not created:
                stats["events_known"] += 1
            continue
        if kind != "lead":
            stats["skipped"] += 1
            continue
        club_data = dict(item.get("club") or {})
        contact_data = dict(item.get("contact") or {})
        contact_data.pop("phone_fmt", None)
        club_id, action = repo.upsert_club(club_data)
        if not club_id:
            stats["skipped"] += 1
            continue
        if action == "created":
            stats["clubs_created"] += 1
        elif action == "updated":
            stats["clubs_updated"] += 1
        contact_data["club_id"] = club_id
        contact_id, caction = repo.upsert_contact(contact_data)
        if caction == "created":
            stats["contacts_created"] += 1
        elif caction == "unchanged":
            stats["already_known"] += 1
        # Trace datee : l'evenement d'agenda est un echange qui a eu lieu.
        when = item.get("when")
        if when:
            already = db.q1("SELECT id FROM interactions WHERE club_id = ? AND subject = ?",
                            (club_id, item.get("raw")))
            if not already:
                past = util.parse_date(when) and util.parse_date(when) <= util.today()
                repo.add_interaction(
                    club_id, kind="meeting" if past else "note",
                    direction="out", subject=item.get("raw"),
                    body="Depuis le calendrier %s." % (item.get("source") or ""),
                    contact_id=contact_id, occurred_at=when, touch=bool(past))
    db.log("import", "Calendrier : %d club(s), %d evenement(s)"
           % (stats["clubs_created"], stats["events_created"]))
    return stats


# --------------------------------------------------------------------------
# Conversations WhatsApp deja echangees
# --------------------------------------------------------------------------

@route("POST", r"/api/whatsapp/preview")
def api_whatsapp_preview(req):
    """Lit un export 'Exporter la discussion' et propose le club correspondant."""
    body = req.body
    text = body.get("text") or ""
    upload = (req.raw_body or {}).get("file")
    chat_name = body.get("chat_name")
    if isinstance(upload, tuple):
        chat_name = chat_name or upload[0]
        text = _decode_text(upload[1])
    if not text.strip():
        raise ApiError("collez le contenu de l'export, ou deposez le fichier .txt")
    parsed = whatsapp.parse_export(text)
    if not parsed["n"]:
        raise ApiError("aucun message reconnu : est-ce bien un export WhatsApp "
                       "(Discussion > Exporter la discussion > Sans les medias) ?")
    me = body.get("me") or whatsapp.guess_me(parsed["participants"], db.all_settings())
    club_id, why = whatsapp.guess_club(parsed, me, repo.find_club, repo.find_contact,
                                       chat_name)
    club = repo.get_club(club_id) if club_id else None
    parsed["me"] = me
    parsed["club_id"] = club_id
    parsed["club_name"] = club["name"] if club else None
    parsed["match_reason"] = why
    parsed["n_out"] = sum(1 for m in parsed["messages"] if m["author"] == me)
    parsed["n_in"] = parsed["n"] - parsed["n_out"]
    parsed["sample"] = parsed["messages"][:6]
    parsed.pop("messages")
    parsed["text"] = text[:400000]
    return parsed


@route("POST", r"/api/whatsapp/commit")
def api_whatsapp_commit(req):
    """Enregistre la conversation dans l'historique du club et met a jour son statut."""
    body = req.body
    club_id = body.get("club_id")
    if not club_id:
        raise ApiError("choisissez le club auquel rattacher cette conversation")
    club = db.q1("SELECT id, name, status FROM clubs WHERE id = ?", (int(club_id),))
    if not club:
        raise ApiError("club introuvable")
    parsed = whatsapp.parse_export(body.get("text") or "")
    me = body.get("me") or whatsapp.guess_me(parsed["participants"], db.all_settings())
    contact = drafts.best_contact(int(club_id))

    added, skipped, last_out, has_in = 0, 0, None, False
    for msg in parsed["messages"]:
        if not msg["at"]:
            skipped += 1
            continue
        outgoing = (msg["author"] == me)
        if not outgoing:
            has_in = True
        elif not last_out or msg["at"] > last_out:
            last_out = msg["at"]
        # Un import repete ne doit pas dupliquer l'historique.
        exists = db.q1("SELECT id FROM interactions WHERE club_id = ? AND occurred_at = ? "
                       "AND kind = 'whatsapp'", (int(club_id), msg["at"]))
        if exists:
            skipped += 1
            continue
        repo.add_interaction(
            int(club_id), kind="whatsapp",
            direction="out" if outgoing else "in",
            subject="WhatsApp — %s" % msg["author"],
            body=msg["text"], contact_id=(contact or {}).get("id"),
            occurred_at=msg["at"], touch=False)
        added += 1

    now = util.now_iso()
    patch = {"updated_at": now}
    if last_out:
        current = db.q1("SELECT last_contact_at FROM clubs WHERE id = ?",
                        (int(club_id),))["last_contact_at"]
        if not current or last_out > current:
            patch["last_contact_at"] = last_out
    # Un club qui a repondu n'est plus un simple prospect.
    if club["status"] in ("prospect", "contacte"):
        patch["status"] = "repondu" if has_in else "contacte"
    db.update("clubs", int(club_id), patch)
    db.log("whatsapp", "%s : %d message(s) importe(s)" % (club["name"], added))
    return {"ok": True, "club": repo.get_club(int(club_id)), "added": added,
            "skipped": skipped, "status": patch.get("status", club["status"]),
            "last_contact_at": patch.get("last_contact_at")}


@route("GET", r"/api/recherche/etat")
def api_recherche_etat(req):
    return recherche.etat()


@route("POST", r"/api/clubs/enrichir")
def api_clubs_enrichir(req):
    """Repeche les contacts restes dans le texte des annonces importees.

    Puis rend envoyables les brouillons qui n'attendaient que ca.
    """
    bilan = repo.enrichir_contacts_depuis_annonces()
    bilan["brouillons_recolles"] = drafts.recoller_destinataires()
    return bilan


# --------------------------------------------------------------------------
# Le journal WhatsApp : ou en est chaque conversation
# --------------------------------------------------------------------------

@route("POST", r"/api/whatsapp/journal")
def api_whatsapp_journal(req):
    """Recoit l'index des conversations WhatsApp et le raccroche aux clubs.

    Le corps est {"conversations": [{nom, telephone, apercu, quand, sortant,
    non_lus, groupe}]}. Aucun texte de message n'est attendu ni stocke au-dela
    de l'apercu d'une ligne que WhatsApp affiche deja dans sa liste.
    """
    conversations = req.body.get("conversations")
    if not isinstance(conversations, list):
        raise ApiError("attendu : {\"conversations\": [...]}")
    bilan = wa.enregistrer(conversations)
    if req.body.get("rapprocher", True):
        bilan["contacts"] = wa.synchroniser_contacts()
        bilan["brouillons_envoyes"] = wa.rapprocher_brouillons()
        bilan["brouillons_recolles"] = drafts.recoller_destinataires()
    db.log("whatsapp", "Journal : %d conversation(s), %d liee(s) a un club"
           % (bilan["vus"], bilan["lies"]))
    return bilan


@route("GET", r"/api/whatsapp/schema")
def api_whatsapp_schema(req):
    """Diagnostic : structure de la base WhatsApp locale."""
    try:
        return {"tables": wa_local.schema(req.query.get("motif") or "")}
    except wa_local.Inaccessible as exc:
        raise ApiError(str(exc), 409)


@route("POST", r"/api/equipe/numeros")
def api_equipe_numeros(req):
    """Retrouve les numeros manquants des photographes.

    Pas dans le groupe — WhatsApp n'y garde plus les telephones — mais dans
    les conversations en tete a tete, les fiches contacts et le carnet du
    Mac. Rien n'est devine : chaque numero vient d'une ligne lue.
    """
    b = req.body or {}
    return missions.recuperer_numeros(b.get("groupe") or "SnapActions",
                                      ecrire=b.get("ecrire", True))


@route("GET", r"/api/whatsapp/groupes")
def api_whatsapp_groupes(req):
    """Les groupes WhatsApp et le nombre de participants de chacun."""
    try:
        return {"items": wa_local.groupes_connus()}
    except wa_local.Inaccessible as exc:
        raise ApiError(str(exc), 409)


@route("GET", r"/api/whatsapp/membres")
def api_whatsapp_membres(req):
    """Les participants d'un groupe, avec leur numero.

    Rien n'est devine : ce sont les numeros que WhatsApp garde pour ce
    groupe, lus tels quels.
    """
    try:
        return {"items": wa_local.membres(req.query.get("groupe") or "")}
    except wa_local.Inaccessible as exc:
        raise ApiError(str(exc), 409)


@route("GET", r"/api/whatsapp/bases")
def api_whatsapp_bases(req):
    """Quelles bases WhatsApp ce Mac contient, et lesquelles sont lisibles."""
    sortie = {"items": wa_local.inventaire()}
    try:
        sortie["formes_jid"] = wa_local.formes_jid()
        sortie["formes_identifiant"] = wa_local.formes_identifiant()
    except wa_local.Inaccessible as exc:
        sortie["formes_jid"] = {"erreur": str(exc)}
    return sortie


@route("POST", r"/api/whatsapp/relire")
def api_whatsapp_relire(req):
    """Relit le journal WhatsApp dans la base locale du Mac.

    Marche seulement si le processus a l'Acces complet au disque : lance par
    lancer.command, le serveur herite de celui du Terminal.
    """
    try:
        base, conversations = wa_local.lire_journal(req.body.get("base"))
    except wa_local.Inaccessible as exc:
        raise ApiError(str(exc), 409)
    bilan = wa.enregistrer(conversations)
    bilan["base"] = base
    bilan["contacts"] = wa.synchroniser_contacts()
    bilan["brouillons_envoyes"] = wa.rapprocher_brouillons()
    bilan["brouillons_recolles"] = drafts.recoller_destinataires()
    db.log("whatsapp", "Relecture locale : %d conversation(s), %d liee(s)"
           % (bilan["vus"], bilan["lies"]))
    return bilan


@route("GET", r"/api/suivi")
def api_suivi(req):
    """Les dossiers ranges par etape : l'ecran de travail de Julien."""
    d = wa.suivi()
    d["etapes"] = [{"cle": c, "nom": n, "aide": a} for c, n, a in wa.ETAPES]
    return d


@route("POST", r"/api/suivi/(\d+)/evenement")
def api_suivi_evenement(req, chat_id):
    """Cree la date obtenue au telephone.

    Jusqu'ici un tournoi n'existait que s'il venait d'un flyer ou d'une source
    web. Un accord pris de vive voix n'avait nulle part ou se poser : ni J-7,
    ni fichier calendrier, ni rappel. Il en fallait un.
    """
    chat = db.q1("SELECT w.*, c.name AS club_nom, c.city AS club_ville, "
                 "c.postal_code AS club_cp, c.dept AS club_dept, c.sport AS club_sport "
                 "FROM wa_chats w LEFT JOIN clubs c ON c.id = w.club_id "
                 "WHERE w.id = ?", (int(chat_id),))
    if not chat:
        raise ApiError("conversation introuvable", 404)
    if not chat["club_id"]:
        raise ApiError("rattachez d'abord cette conversation a un club")

    debut = util.parse_date(req.body.get("date"))
    if not debut:
        raise ApiError("il faut une date de debut (JJ/MM/AAAA ou AAAA-MM-JJ)")
    fin = util.parse_date(req.body.get("fin")) or debut
    nom = (req.body.get("nom") or "").strip() \
        or "Shoot — %s" % (chat["club_nom"] or "club")
    ville = (req.body.get("ville") or "").strip() or chat["club_ville"]
    cp = (req.body.get("cp") or "").strip() or chat["club_cp"]

    equipes = req.body.get("equipes")
    try:
        equipes = int(equipes) if equipes not in (None, "") else None
    except (TypeError, ValueError):
        equipes = None

    d = None
    if ville or cp:
        try:
            d = geo.depuis_cannes(ville, cp)
        except Exception:
            d = None

    maintenant = util.now_iso()
    tid = db.insert("tournaments", {
        "name": nom,
        "sport": chat["club_sport"],
        "start_date": debut.isoformat(),
        "end_date": fin.isoformat(),
        "city": ville, "postal_code": cp,
        "dept": chat["club_dept"] or util.dept_from_postal(cp),
        "organizer": chat["club_nom"], "club_id": chat["club_id"],
        "source": "Accord telephone", "status": "new",
        "discovered_at": maintenant, "updated_at": maintenant,
        "distance_km": d["km"] if d else None,
        "trajet_min": d["minutes"] if d else None,
        "distance_verdict": d["verdict"] if d else None,
        "nb_equipes": equipes,
        "validation": "oui",
        "description": (req.body.get("details") or "").strip()[:1500] or None,
    })
    repo.add_interaction(chat["club_id"], kind="appel", direction="out",
                         subject="Date convenue : %s" % nom,
                         body=debut.isoformat())
    db.log("suivi", "Evenement cree depuis un accord telephone : %s (%s)"
           % (nom, debut.isoformat()))
    return {"ok": True, "id": tid, "nom": nom, "date": debut.isoformat(),
            "distance_km": d["km"] if d else None}


@route("GET", r"/api/suivi/(\d+)/calendrier.ics")
def api_suivi_ics(req, chat_id):
    """Le fichier a double-cliquer pour poser la date dans le calendrier.

    On ne peut pas ecrire dans le calendrier iCloud publie de Julien — il est
    en lecture seule. Un .ics ouvert par Calendrier fait le meme travail en un
    clic, et c'est lui qui choisit dans quel agenda ca tombe.
    """
    chat = db.q1("SELECT w.*, c.name AS club_nom FROM wa_chats w "
                 "LEFT JOIN clubs c ON c.id = w.club_id WHERE w.id = ?", (int(chat_id),))
    if not chat:
        raise ApiError("conversation introuvable", 404)
    ev = db.q1("SELECT * FROM tournaments WHERE club_id = ? "
               "  AND (start_date IS NULL OR date(start_date) >= date('now')) "
               "ORDER BY start_date LIMIT 1", (chat["club_id"],))
    if not ev or not ev["start_date"]:
        raise ApiError("aucune date connue pour ce club : renseignez le tournoi")

    debut = util.parse_date(ev["start_date"])
    fin = util.parse_date(ev["end_date"]) or debut
    details = [chat["club_nom"] or "", ev["city"] or ""]
    if ev["nb_equipes"]:
        details.append("%s equipes" % ev["nb_equipes"])
    if ev["distance_km"] is not None:
        details.append("%s km" % ev["distance_km"])
    if chat["phone"]:
        details.append("Contact : %s" % chat["phone"])
    if chat["note"]:
        details.append("Note : %s" % chat["note"])

    corps = ics.calendar("SnapActions", ics.vevent(
        "snapactions-suivi-%s@local" % chat["id"],
        "Shoot — %s" % (chat["club_nom"] or ev["name"] or "événement"),
        debut, fin, all_day=True,
        description=" · ".join(x for x in details if x),
        location=ev["venue"] or ev["city"] or None,
        alarm_minutes=60 * 24))
    return {"_raw": corps, "_ctype": "text/calendar; charset=utf-8",
            "_filename": "snapactions-%s.ics" % chat["id"]}


@route("POST", r"/api/suivi/(\d+)/note")
def api_suivi_note(req, chat_id):
    """Le compte rendu d'appel, ecrit ou dicte."""
    texte = wa.poser_note(chat_id, req.body.get("note"))
    return {"ok": True, "note": texte}


@route("POST", r"/api/suivi/(\d+)/etape")
def api_suivi_etape(req, chat_id):
    """Deplace une carte dans une autre colonne, a la main."""
    cle = (req.body.get("etape") or "").strip() or None
    try:
        wa.poser_etape(chat_id, cle)
    except ValueError as exc:
        raise ApiError(str(exc))
    return {"ok": True, "etape": cle}


@route("GET", r"/api/whatsapp/chats")
def api_whatsapp_chats(req):
    filtre = (req.query.get("etat") or "").strip() or "a_rattacher"
    toutes = wa.liste("tout")
    pros = [r for r in toutes if r["pro"]]
    compte = {}
    for r in pros:
        compte[r["etat"]] = compte.get(r["etat"], 0) + 1
    compte["clubs"] = len(pros)
    compte["tout"] = len(toutes)
    compte["a_rattacher"] = sum(1 for r in toutes
                                if not r["club_id"] and r["suggestions"])
    rows = wa.liste(filtre)
    return {"items": rows, "compte": compte, "libelles": wa.LIBELLES}


@route("POST", r"/api/whatsapp/chats/(\d+)/lier")
def api_whatsapp_lier(req, chat_id):
    """Rattache une conversation a un club, ou detache si club_id est nul."""
    chat = db.q1("SELECT * FROM wa_chats WHERE id = ?", (int(chat_id),))
    if not chat:
        raise ApiError("conversation introuvable", 404)
    club_id = req.body.get("club_id")
    if club_id in (None, "", 0):
        db.update("wa_chats", chat["id"], {"club_id": None})
        return {"ok": True, "club_id": None}
    club = repo.get_club(int(club_id))
    if not club:
        raise ApiError("club introuvable", 404)
    db.update("wa_chats", chat["id"], {"club_id": club["id"]})
    # Le numero de la conversation vaut mieux que rien : beaucoup de clubs
    # n'ont aucun contact dans la base.
    if chat["phone"] and not club.get("phone"):
        db.update("clubs", club["id"], {"phone": chat["phone"]})
    contacts = wa.synchroniser_contacts()
    return {"ok": True, "club_id": club["id"], "club": club["name"],
            "contacts": contacts}


@route("POST", r"/api/whatsapp/chats/(\d+)/ecarter")
def api_whatsapp_ecarter(req, chat_id):
    """Une discussion personnelle n'a rien a faire dans la prospection."""
    db.ex("UPDATE wa_chats SET ecarte = 1, updated_at = ? WHERE id = ?",
          (util.now_iso(), int(chat_id)))
    return {"ok": True}


@route("POST", r"/api/whatsapp/chats/(\d+)/club")
def api_whatsapp_creer_club(req, chat_id):
    """Cree le club manquant a partir de la conversation.

    Le vrai carnet d'adresses de Julien est dans WhatsApp : ces clubs-la
    existent deja pour lui, ils manquaient seulement au CRM.
    """
    chat = db.q1("SELECT * FROM wa_chats WHERE id = ?", (int(chat_id),))
    if not chat:
        raise ApiError("conversation introuvable", 404)
    if chat["club_id"]:
        raise ApiError("cette conversation est deja rattachee a un club")
    nom = (req.body.get("nom") or chat["nom"] or "").strip()
    if not nom:
        raise ApiError("il faut un nom de club")
    club_id, action = repo.upsert_club({
        "name": nom,
        "city": req.body.get("ville") or None,
        "sport": req.body.get("sport") or None,
        "phone": chat["phone"] or None,
        "status": "contacte",
        "source": "WhatsApp",
        "notes": "Cree depuis le journal WhatsApp.",
    })
    if not club_id:
        raise ApiError("nom de club refuse")
    db.update("wa_chats", chat["id"], {"club_id": club_id})
    if chat["dernier_le"]:
        repo.add_interaction(club_id, kind="whatsapp",
                             direction="out" if chat["sortant"] else "in",
                             subject="Conversation WhatsApp en cours",
                             occurred_at=chat["dernier_le"])
    return {"ok": True, "club_id": club_id, "club": nom, "action": action}


@route("POST", r"/api/whatsapp/index")
def api_whatsapp_index(req):
    """Recoit le CSV produit par lire-whatsapp.command et le croise avec les clubs.

    Ce CSV ne contient que des numeros et des dates : aucun texte de message.
    """
    upload = (req.raw_body or {}).get("file")
    text = req.body.get("text") or ""
    if isinstance(upload, tuple):
        text = _decode_text(upload[1])
    if not text.strip():
        raise ApiError("deposez le fichier whatsapp-contacts.csv")
    headers, rows = extract.sniff_table(text)
    if not headers:
        raise ApiError("fichier illisible : ce n'est pas le CSV attendu")
    idx = {util.strip_accents(h.strip().lower()): i for i, h in enumerate(headers)}
    if "telephone" not in idx:
        raise ApiError("colonne 'telephone' absente : est-ce bien whatsapp-contacts.csv ?")

    def cell(row, key):
        i = idx.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    trouves, inconnus = [], 0
    for row in rows:
        phone = util.normalize_phone(cell(row, "telephone"))
        if not phone:
            continue
        contact = db.q1(
            "SELECT ct.id, ct.club_id, ct.first_name, ct.last_name, c.name AS club_name, "
            "c.status, c.last_contact_at FROM contacts ct "
            "LEFT JOIN clubs c ON c.id = ct.club_id "
            "WHERE ct.phone = ? OR ct.phone2 = ?", (phone, phone))
        if not contact or not contact["club_id"]:
            inconnus += 1
            continue
        envoi = cell(row, "dernier_envoi")
        recept = cell(row, "derniere_reception")
        if not envoi and not recept:
            continue
        trouves.append({
            "club_id": contact["club_id"],
            "club_name": contact["club_name"],
            "contact_id": contact["id"],
            "contact": " ".join(x for x in [contact["first_name"], contact["last_name"]] if x),
            "phone": phone,
            "phone_fmt": util.format_phone(phone),
            "wa_name": cell(row, "nom"),
            "dernier_envoi": envoi or None,
            "derniere_reception": recept or None,
            "nb_envoyes": cell(row, "nb_envoyes") or "0",
            "nb_recus": cell(row, "nb_recus") or "0",
            "statut_actuel": contact["status"],
            "deja_date": contact["last_contact_at"],
        })
    # Un club peut avoir plusieurs numeros : on garde l'echange le plus recent.
    par_club = {}
    for t in trouves:
        cur = par_club.get(t["club_id"])
        if not cur or (t["dernier_envoi"] or "") > (cur["dernier_envoi"] or ""):
            par_club[t["club_id"]] = t
    items = sorted(par_club.values(), key=lambda x: x["dernier_envoi"] or "", reverse=True)
    return {"items": items, "n": len(items), "n_lignes": len(rows),
            "n_inconnus": inconnus,
            "n_avec_envoi": sum(1 for i in items if i["dernier_envoi"]),
            "n_avec_reponse": sum(1 for i in items if i["derniere_reception"])}


@route("POST", r"/api/whatsapp/index/commit")
def api_whatsapp_index_commit(req):
    """Applique le croisement : date reelle du dernier contact et statut."""
    items = req.body.get("items") or []
    done, skipped = 0, 0
    for it in items:
        club_id = it.get("club_id")
        club = db.q1("SELECT id, name, status, last_contact_at FROM clubs WHERE id = ?",
                     (int(club_id),)) if club_id else None
        if not club:
            skipped += 1
            continue
        envoi = it.get("dernier_envoi")
        recept = it.get("derniere_reception")
        quand = envoi or recept
        if not quand:
            skipped += 1
            continue
        marqueur = "Echange WhatsApp (index local)"
        exists = db.q1("SELECT id FROM interactions WHERE club_id = ? AND subject = ?",
                       (club["id"], marqueur))
        if not exists:
            repo.add_interaction(
                club["id"], kind="whatsapp", direction="out" if envoi else "in",
                subject=marqueur,
                body="%s message(s) envoye(s), %s recu(s). Contenu non importe."
                     % (it.get("nb_envoyes", "?"), it.get("nb_recus", "?")),
                contact_id=it.get("contact_id"), occurred_at=quand, touch=False)
        patch = {"updated_at": util.now_iso()}
        if not club["last_contact_at"] or quand > club["last_contact_at"]:
            patch["last_contact_at"] = quand
        if club["status"] == "prospect":
            patch["status"] = "repondu" if recept else "contacte"
        db.update("clubs", club["id"], patch)
        done += 1
    db.log("whatsapp", "Index WhatsApp : %d club(s) mis a jour" % done)
    return {"ok": True, "n": done, "skipped": skipped}


@route("POST", r"/api/clubs/mark-contacted")
def api_mark_contacted(req):
    """Voie rapide : marquer d'un coup des clubs deja demarches hors de l'outil."""
    ids = req.body.get("club_ids") or []
    when = util.parse_dt(req.body.get("when")) or util.now()
    note = req.body.get("note") or "Contacte hors de l'outil (WhatsApp, telephone)."
    channel = req.body.get("channel") or "whatsapp"
    status = req.body.get("status") or "contacte"
    done = 0
    for club_id in ids:
        club = db.q1("SELECT id, status FROM clubs WHERE id = ?", (int(club_id),))
        if not club:
            continue
        repo.add_interaction(int(club_id), kind=channel, direction="out",
                             subject="Premier contact", body=note,
                             occurred_at=when.isoformat(sep=" "))
        if club["status"] == "prospect":
            db.update("clubs", int(club_id), {"status": status,
                                              "updated_at": util.now_iso()})
        done += 1
    return {"ok": True, "n": done}


# --------------------------------------------------------------------------
# Dates annoncees dans un message
# --------------------------------------------------------------------------

@route("POST", r"/api/dates/extract")
def api_dates_extract(req):
    """Repere les dates d'evenement dans un message recu (WhatsApp, mail, SMS)."""
    text = req.body.get("text") or ""
    if not text.strip():
        raise ApiError("collez le message recu")
    club_id = req.body.get("club_id")
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(club_id),))) \
        if club_id else None
    items = dates_fr.extraire(text)
    for it in items:
        # Lieu : celui du club s'il est connu, sinon ce que dit le message.
        ville = (club or {}).get("city")
        postal = (club or {}).get("postal_code")
        if not ville:
            postal = util.extract_postal(text) or postal
        it["club_id"] = club["id"] if club else None
        it["club_name"] = club["name"] if club else None
        it["city"] = ville
        it["postal_code"] = postal
        it["sport"] = (club or {}).get("sport")
        if club:
            it["nom_complet"] = "%s — %s" % (it["nom"], club["name"])
        else:
            it["nom_complet"] = it["nom"]
        fp = sources.fingerprint({"name": it["nom_complet"], "start_date": it["debut"],
                                  "city": ville or ""})
        it["deja_connu"] = bool(db.q1("SELECT id FROM tournaments WHERE fingerprint = ?",
                                      (fp,)))
    return {"items": items, "n": len(items)}


@route("POST", r"/api/dates/commit")
def api_dates_commit(req):
    """Ajoute les dates retenues a l'agenda — elles partent aussitot dans le flux iCal."""
    items = req.body.get("items") or []
    records, club_ids = [], {}
    for it in items:
        nom = it.get("nom_complet") or it.get("nom")
        if not nom or not it.get("debut"):
            continue
        records.append({
            "name": nom,
            "sport": it.get("sport"),
            "start_date": it.get("debut"),
            "end_date": it.get("fin") or it.get("debut"),
            "city": it.get("city"),
            "postal_code": it.get("postal_code"),
            "club_id": it.get("club_id"),
            "organizer": it.get("club_name"),
            "source": "annonce par le club",
            "description": it.get("extrait"),
            "status": "processed",
        })
        if it.get("club_id"):
            club_ids[it["club_id"]] = True
    created, updated, skipped, _ = sources.save_tournaments(records, "annonce par le club")
    # Trace dans la fiche du club : on sait d'ou vient la date.
    for club_id in club_ids:
        for rec in created:
            if rec.get("club_id") == club_id:
                repo.add_interaction(
                    club_id, kind="note", direction="in",
                    subject="Date annoncee : %s" % rec["name"],
                    body="%s%s\nRepere automatiquement dans un message recu."
                         % (rec["start_date"],
                            (" au " + rec["end_date"]) if rec.get("end_date")
                            and rec["end_date"] != rec["start_date"] else ""),
                    occurred_at=util.now_iso(), touch=False)
    return {"ok": True, "created": len(created), "updated": updated, "skipped": skipped,
            "items": created}


# --------------------------------------------------------------------------
# Missions : appeler les photographes
# --------------------------------------------------------------------------

@route("GET", r"/api/missions")
def api_missions(req):
    return {"items": missions.liste(req.query.get("status")),
            "a_pourvoir": missions.evenements_sans_mission(
                int(req.query.get("horizon", 60))),
            "crews": missions.list_crews()}


@route("POST", r"/api/missions/compose")
def api_mission_compose(req):
    return missions.compose(req.body.get("tournament_id"), req.body.get("crew_id"),
                            req.body.get("extra"))


@route("POST", r"/api/missions")
def api_mission_save(req):
    m = missions.save(req.body)
    if not m:
        raise ApiError("mission non enregistree")
    return m


@route("PATCH", r"/api/missions/(\d+)")
def api_mission_update(req, mid):
    data = dict(req.body)
    data["id"] = int(mid)
    return missions.save(data)


@route("POST", r"/api/missions/(\d+)/posted")
def api_mission_posted(req, mid):
    return missions.marquer_postee(int(mid))


@route("DELETE", r"/api/missions/(\d+)")
def api_mission_delete(req, mid):
    db.delete("missions", int(mid))
    return {"ok": True}


@route("GET", r"/api/equipe")
def api_equipe(req):
    """Tout l'ecran Equipe d'un coup : les dates tenues, les gros plateaux.

    Deux listes, deux gestes differents. Les dates qu'on tient manquent de
    photographes : on appelle le groupe. Les gros plateaux qu'on a reperes et
    ou on n'a personne : on les confie au sourceur, qui cherche sur place.
    """
    # Deux choses sont parties d'ici :
    #
    #   « a_pourvoir » lisait les tournois dont la fiche avait ete validee et
    #   les presentait comme les dates tenues. Les vraies dates viennent du
    #   calendrier : voir /api/equipe/dates.
    #
    #   « recentes » appelait missions.liste(40) a chaque ouverture de la
    #   page. Aucun ecran ne s'en servait.
    return {
        "gros": missions.gros_evenements(
            int(req.query.get("mini", missions.GROS_EQUIPES))),
        "crews": missions.list_crews(),
        "team": missions.list_team(),
        "sourceur": missions.sourceur(),
        "comptes": [c for c in __import__("prospect.comptes", fromlist=["comptes"])
                    .lister() if c.get("actif")],
        "mini": missions.GROS_EQUIPES,
    }


@route("POST", r"/api/equipe/sourceur")
def api_equipe_sourceur(req):
    """Qui va chercher les photographes. Une fiche de l'equipe, ou personne."""
    return {"sourceur": missions.definir_sourceur((req.body or {}).get("team_id"))}


@route("POST", r"/api/equipe/sourceur/message")
def api_equipe_sourceur_message(req):
    """Le texte de la demande, avant de l'envoyer. Rien ne part ici."""
    b = req.body or {}
    return missions.message_sourceur(b.get("ids"), b.get("mot"))


@route("POST", r"/api/equipe/confier")
def api_equipe_confier(req):
    """Confie des événements à un compte : ils apparaissent dans SON espace.

    C'est le pendant de l'envoi WhatsApp : la demande part par message, et
    la liste, elle, vit dans l'espace du sourceur — il la retrouve de chez
    lui sans fouiller une conversation.
    """
    from . import comptes
    b = req.body or {}
    cid = b.get("compte_id")
    if not cid:
        raise ApiError("à quel compte ?")
    qui = comptes.lire(int(cid))
    if not qui or not qui.get("actif"):
        raise ApiError("compte introuvable ou suspendu", 404)
    ids = [int(x) for x in (b.get("ids") or []) if str(x).strip()]
    if not ids:
        raise ApiError("aucun événement choisi")

    poses = missions.confier_au_sourceur(ids, b.get("corps"))
    if poses:
        marques = ",".join("?" for _ in poses)
        db.ex("UPDATE missions SET compte_id = ?, assigned = ?, updated_at = ? "
              "WHERE id IN (%s)" % marques,
              tuple([int(cid), qui.get("nom") or qui["identifiant"],
                     util.now_iso()] + poses))
    db.log("mission", "%d événement(s) confié(s) à %s"
           % (len(poses), qui.get("nom") or qui["identifiant"]))
    return {"ok": True, "confies": len(poses), "a": qui.get("nom")}


@route("POST", r"/api/equipe/sourceur/envoyer")
def api_equipe_sourceur_envoyer(req):
    """Envoie vraiment la demande au sourceur, par WhatsApp, sur un clic.

    Le sourceur est une personne : l'envoi direct marche comme ailleurs. Les
    evenements quittent la liste — quelqu'un s'en occupe.
    """
    from . import envoi
    b = req.body or {}
    ids = b.get("ids") or []
    corps = (b.get("corps") or "").strip()
    if not corps:
        raise ApiError("le message est vide")
    qui = missions.sourceur()
    tel = (b.get("tel") or (qui or {}).get("phone") or "").strip()
    if not tel:
        raise ApiError("aucun numéro pour le sourceur : choisissez-le d'abord")
    try:
        out = envoi.par_whatsapp(tel, corps)
    except envoi.Refus as exc:
        raise ApiError(str(exc))
    poses = missions.confier_au_sourceur(ids, corps)
    return {"ok": True, "a": out["a"], "confies": len(poses)}


@route("POST", r"/api/equipe/event")
def api_equipe_event(req):
    """Prepare l'evenement WhatsApp et ouvre le bon salon. Ne publie rien.

    WhatsApp n'expose aucune API pour creer un evenement de groupe : le
    formulaire « + > Event » se remplit a la main. On met le nom dans le
    presse-papiers, on ouvre le salon, et on rend les autres champs pour que
    l'ecran les affiche. Julien colle et appuie sur Send lui-meme — un
    evenement, dans un groupe, se voit par tout le monde.
    """
    b = req.body or {}
    t = {}
    if b.get("tournament_id"):
        t = db.row_to_dict(db.q1("SELECT * FROM tournaments WHERE id = ?",
                                 (int(b["tournament_id"]),))) or {}
    if not t and not b.get("titre"):
        raise ApiError("quel evenement ?")
    if not t:
        t = {"name": b.get("titre"), "start_date": b.get("starts_at"),
             "city": b.get("lieu")}

    if b.get("crew_id"):
        crew = db.row_to_dict(db.q1("SELECT * FROM crews WHERE id = ?",
                                    (int(b["crew_id"]),)))
    else:
        crew = missions.suggest_crew(t.get("sport"))
    champs = missions.champs_evenement(t, crew)

    ouvert, souci = False, None
    if b.get("ouvrir", True) and champs.get("groupe"):
        try:
            from . import envoi
            envoi.ouvrir_groupe(champs["groupe"], copier=champs["nom"])
            ouvert = True
        except Exception as exc:
            souci = str(exc)
    champs["ouvert"] = ouvert
    champs["souci"] = souci
    return champs


@route("POST", r"/api/equipe/groupe")
def api_equipe_groupe(req):
    """Ouvre le groupe WhatsApp et y colle l'appel. N'envoie pas.

    Dans un groupe, un message part devant tout le monde et ne se reprend
    pas : c'est Julien qui appuie sur Entree, apres avoir lu.
    """
    from . import envoi
    b = req.body or {}
    corps = (b.get("corps") or "").strip()
    if not corps:
        raise ApiError("le message est vide")
    crew_id = b.get("crew_id")
    crew = db.row_to_dict(db.q1("SELECT * FROM crews WHERE id = ?", (int(crew_id),))) \
        if crew_id else None
    nom = (b.get("groupe") or (crew or {}).get("name") or "").strip()
    if not nom:
        raise ApiError("quel groupe ?")
    try:
        out = envoi.vers_groupe(nom, corps)
    except envoi.Refus as exc:
        raise ApiError(str(exc))
    # La mission est enregistree des que le texte est dans le groupe : c'est
    # la que le travail a ete fait, meme si Julien appuie une seconde apres.
    m = missions.save({"tournament_id": b.get("tournament_id"), "crew_id": crew_id,
                       "title": b.get("titre") or "Mission photo",
                       "starts_at": b.get("starts_at"), "place": b.get("lieu"),
                       "body": corps})
    if m:
        missions.marquer_postee(m["id"])
    return {"ok": True, "groupe": out["groupe"], "colle": True,
            "mission_id": (m or {}).get("id")}


@route("GET", r"/api/team")
def api_team(req):
    return {"items": missions.list_team(req.query.get("sport"),
                                        req.query.get("all") != "1"),
            "crews": missions.list_crews()}


@route("POST", r"/api/team")
def api_team_create(req):
    body = req.body
    if not body.get("name"):
        raise ApiError("il faut au moins un nom")
    now = util.now_iso()
    tid = db.insert("team", {
        "name": util.title_name(body["name"]),
        "phone": util.normalize_phone(body.get("phone")),
        "email": util.normalize_email(body.get("email")),
        "role": body.get("role") or "photographe",
        "sports": body.get("sports"), "zones": body.get("zones"),
        "notes": body.get("notes"),
        "active": 1 if body.get("active", True) else 0,
        "created_at": now, "updated_at": now})
    return {"id": tid}


@route("PATCH", r"/api/team/(\d+)")
def api_team_update(req, tid):
    allowed = {"name", "phone", "email", "role", "sports", "zones", "notes", "active"}
    patch = {k: v for k, v in req.body.items() if k in allowed}
    if "phone" in patch:
        patch["phone"] = util.normalize_phone(patch["phone"]) or patch["phone"] or None
    if "active" in patch:
        patch["active"] = 1 if patch["active"] else 0
    patch["updated_at"] = util.now_iso()
    db.update("team", int(tid), patch)
    return db.row_to_dict(db.q1("SELECT * FROM team WHERE id = ?", (int(tid),)))


@route("DELETE", r"/api/team/(\d+)")
def api_team_delete(req, tid):
    db.delete("team", int(tid))
    return {"ok": True}


@route("POST", r"/api/crews")
def api_crew_save(req):
    body = req.body
    if body.get("id"):
        db.update("crews", int(body["id"]), {"name": body.get("name"),
                                             "sports": body.get("sports")})
        return {"id": int(body["id"])}
    if not body.get("name"):
        raise ApiError("nom du groupe requis")
    return {"id": db.insert("crews", {"name": body["name"], "sports": body.get("sports"),
                                      "position": 99, "created_at": util.now_iso()})}


@route("DELETE", r"/api/crews/(\d+)")
def api_crew_delete(req, cid):
    db.delete("crews", int(cid))
    return {"ok": True}


@route("POST", r"/api/clipboard")
def api_clipboard(req):
    """Ecrit dans le presse-papiers du Mac via pbcopy.

    Le navigateur refuse parfois l'acces au presse-papiers (fenetre non
    focalisee, reglages de confidentialite). Le serveur tourne sur la machine
    de l'utilisateur : pbcopy marche a tous les coups.
    """
    import subprocess
    texte = req.body.get("text")
    if texte is None:
        raise ApiError("rien a copier")
    try:
        proc = subprocess.run(["pbcopy"], input=texte.encode("utf-8"),
                              capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApiError("presse-papiers indisponible : %s" % exc)
    if proc.returncode != 0:
        raise ApiError("pbcopy a echoue : %s" % proc.stderr.decode("utf-8", "replace"))
    return {"ok": True, "n": len(texte)}


# Seules ces applications peuvent etre ouvertes depuis l'interface.
APPS_AUTORISEES = {"whatsapp": "WhatsApp", "mail": "Mail", "calendrier": "Calendar"}


@route("POST", r"/api/open-app")
def api_open_app(req):
    """Ouvre une application du Mac (liste blanche) et copie le texte au passage."""
    import subprocess
    cle = (req.body.get("app") or "").lower()
    app = APPS_AUTORISEES.get(cle)
    if not app:
        raise ApiError("application non autorisee : %s" % cle)
    texte = req.body.get("text")
    copie = False
    if texte:
        try:
            subprocess.run(["pbcopy"], input=texte.encode("utf-8"), timeout=5)
            copie = True
        except (OSError, subprocess.SubprocessError):
            copie = False
    try:
        proc = subprocess.run(["open", "-a", app], capture_output=True, timeout=8)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApiError("ouverture impossible : %s" % exc)
    if proc.returncode != 0:
        raise ApiError("%s introuvable sur ce Mac" % app)
    return {"ok": True, "app": app, "copie": copie}


@route("POST", r"/api/ouvrir")
def api_ouvrir(req):
    """Ouvre une adresse dans Chrome, quel que soit le navigateur qui affiche
    l'application.

    window.open() reste prisonnier du navigateur courant : ouvert depuis
    Firefox, tout repartait dans Firefox. Or les sessions WhatsApp et Facebook
    vivent dans Chrome.
    """
    from .__main__ import ouvrir_navigateur
    url = (req.body.get("url") or "").strip()
    if url.lower().startswith("mailto:"):
        return _ouvrir_mail(url)
    if not url.lower().startswith(("http://", "https://")):
        raise ApiError("adresse non geree : %s" % url[:60])
    app = ouvrir_navigateur(url)
    if not app:
        raise ApiError("aucun navigateur Chrome trouve sur ce Mac")
    return {"ok": True, "navigateur": app}


# Un mailto: ne part pas dans Chrome mais dans le logiciel de mail. Et comme
# l'adresse vient d'une page lue sur le web, on verifie sa forme avant de la
# confier a « open » : seul mailto: passe, et seule une adresse qui ressemble
# a une adresse. L'URL est passee en argument, jamais via un shell.
_ADRESSE_MAIL = re.compile(r"^[^\s@<>\"'/\\?&;]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _ouvrir_mail(url):
    import subprocess
    corps = url[len("mailto:"):]
    adresse = urllib.parse.unquote(corps.split("?", 1)[0]).strip()
    if not _ADRESSE_MAIL.match(adresse):
        raise ApiError("adresse mail invalide : %s" % adresse[:60])
    try:
        subprocess.run(["/usr/bin/open", url], check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise ApiError("impossible d'ouvrir le logiciel de mail : %s" % e)
    return {"ok": True, "mail": adresse}


# --------------------------------------------------------------------------
# Groupes Facebook
# --------------------------------------------------------------------------

# Les groupes suivis. Memes identifiants que la liste de l'ecran.
GROUPES_FB = [
    # Les groupes que Julien suit REELLEMENT, releves le 29/10/2026 sur
    # sa page « Vos groupes ». La liste en dur avait vieilli : elle citait
    # des groupes ou il n'etait pas inscrit, donc muets, et ignorait ceux
    # qu'il venait de rejoindre.

    ("Évènements autour du lac d'Annecy", "632949820810176"),
    ("Tournoi/Match FOOT 74", "905935646197251"),
    ("Que faire à Thonon", "702054137578719"),
    ("Soirées / Événements / Sorties & Co sur ANNECY", "692472987454919"),
    ("Tournoi/Match FOOT 73", "710387376298020"),
    ("Tournois de volley et beach-volley en Rhône-Alpes", "352697857419"),
    ("Tournoi de foot 69/01/71", "3001324573485782"),
    ("Tournoi/Match FOOT 69", "707864433224088"),
    ("Matchs amicaux / Tournois / Stages Auvergne-Rhône-Alpes", "660390924520422"),
    ("Tournois de football en SUISSE, France et reste du monde", "888229176089104"),
    ("CSG U6/U7 2026-2027", "1566604310945758"),
    ("Plateaux U6 à U9 — District des FLANDRES", "1839401603021967"),
    ("LES TOURNOIS DE BADMINTON DANS LES HAUTS-DE-FRANCE", "1173569742780080"),
    ("Tournois de Badminton en France", "348945689052045"),
    ("Tournois de Football Feminin", "2959369630819907"),
    ("Tournoi/Match FOOT 85", "817806249083848"),
    ("Tournoi/Match FOOT 14", "267133488324593"),
    ("Tournoi/Match FOOT 64", "775229643369721"),
    ("Tournoi/Match FOOT 49", "765899547379364"),
    ("Tournoi/Match FOOT 40", "277341797233059"),
    ("Tournoi/Match FOOT 92", "450554409593711"),
    ("Tournoi/Match FOOT 59", "275673313956423"),
    ("Tournoi/Match FOOT 45", "284734636612477"),
    ("Tournoi/Match FOOT 71", "281025356753952"),
    ("Tournoi/Match FOOT 78", "454893662514611"),
    ("Tournoi/Match FOOT 65", "460192028439267"),
    ("Tournoi/Match FOOT 67", "130051695716414"),
    ("Tournoi/Match FOOT 27", "538144747567087"),
    ("Tournoi/Match FOOT 77", "927213958015587"),
    ("Tournoi/Match FOOT 84", "1162272080910658"),
    ("Réseau Coach U7 PACA", "1677507692378124"),
    ("Réseau Coach U11 PACA", "182930672635884"),
    ("Tournoi/Match FOOT 48", "434058984467725"),
    ("Tournoi/Match FOOT 01", "807790889812230"),
    ("Tournoi/Match FOOT 68", "142099424482649"),
    ("Tournoi/Match FOOT 03", "303132641148335"),
    ("Tournoi/Match FOOT 24", "188039499464386"),
    ("Tournoi/Match FOOT 62", "287071229477522"),
    ("Tournoi/Match FOOT 81", "348944429764981"),
    ("Tournoi/Match FOOT 16", "256199172823021"),
    ("Tournoi/Match FOOT 63", "484656336046962"),
    ("Tournoi/Match FOOT 05", "345503896799078"),
    ("TOURNOIS & MATCHS AMICAUX BELGIQUE - FRANCE U10 à U15", "1739868489772514"),
    ("Tournois de football U8/U9 région parisienne", "1577681635742600"),
    ("U8 Île-de-France matchs et tournois", "2702270830006841"),
    ("Annonces de Tournois de foot ou matchs amicaux", "619429861529791"),
    ("Tournoi de football U6 à U15 Toulouse", "571224937038576"),
    ("Tournoi/Match FOOT 33", "1396745097385061"),
    ("Tournoi/Match FOOT 13", "2824655944459356"),
    ("Tournoi/Match FOOT 66", "132467412135935"),
    ("Tournoi/Match FOOT 31", "3819324811438831"),
    ("Matchs et tournois U11", "946951162487775"),
    ("Tournoi/Match FOOT 34", "888881948572560"),
    ("Réseau Coach U10 PACA", "336001563843247"),
    ("Match, Tournoi national futsal U8 à U17", "461224628948897"),
    ("Tournoi/Match FOOT 38", "487766788907686"),
    ("Tournoi/Match FOOT 30", "894471404688473"),
    ("Tournoi/Match FOOT 26", "1794050734088034"),
    ("Educateur U6/U7 District ARTOIS", "1836621333076344"),
    ("Tournoi International Football Ascension U9 U11 U13", "1481921012035411"),
    ("FUTSAL HAUTS-DE-FRANCE U6 à U18G", "696575749516897"),
    ("Tournois de foot Ile-de-France", "114709592297863"),
    ("Tournois Football Jeunes HAINAUT et NORD de la France", "1036681199795013"),
    ("Groupe de Tournois Football BELGIQUE", "334584924600096"),
    ("Lyon Events", "953415361421922"),
    ("Idées de sorties dans le Chablais", "2919881338292373"),
    ("Ça bouge à Thonon", "800772398105830"),
    ("Football U9 foot à 8", "725387719522707"),
    ("Réseau Éducateurs de Football (Alpes-Maritimes / PACA)", "240598166459277"),
    ("Tournois Volley Sud-Est", "1874825966113519"),
    ("Les Informations des Tournois France & Europe", "621111809205823"),
    ("Réseau Coach U8 PACA", "522040764935993"),
    ("Le bon coin des tournois", "969326264002358"),
    ("Matchs amicaux / tournois U12", "1199390994172870"),
    ("Réseau Coach U12 PACA", "455204911672202"),
    ("Match amical U8-U9 à 8, U10 à U13 district Est", "1373431126381510"),
    ("Tournois et plateaux U6 U7", "188358915737427"),
    ("Matchs / plateaux / tournois U7 U8 U9", "1669264533357580"),
    ("Match amicaux / plateau / tournoi", "3076363272607335"),
    ("Tournoi Foot PACA", "385898738994736"),
    ("Matchs amicaux, tournois U6 à U13", "448924438641261"),
    ("VOLLEY : Les tournois du 06 et alentours", "209058369494643"),
    ("Tournoi/Match FOOT 83", "132070762115017"),
    ("Réseau Coach U9 PACA", "178768856388067"),
    ("Tournoi/Match FOOT 06", "248248220278380"),

    # Reperes mais pas encore rejoints : le bouton les ouvre dans SON
    # navigateur, il demande l'adhesion lui-meme. Un robot qui rejoint
    # des groupes fait fermer le compte, et c'est le sien.
    ("Tournoi de Football", "342629809940408"),
    ("AS Monaco foot", "308118053101678"),
    ("Monaco & The French Riviera", "282010775325375"),
    ("Tournois 2 FOOT (toute la France)", "915471193354382"),
    ("Tournois national et international", "498967844167417"),
    ("Paris, Hauts-de-France & BELGIQUE U13-U15", "2036051130098178"),
    ("Football matchs et tournois internationaux", "1384541088519330"),
    ("Evenements/tournois U10 a U13", "492050547956767"),
    ("Ecoles de Foot - Ile de France", "756310617843680"),
    ("Tournois en Pays de la Loire", "783563849466059"),
    ("Tournoi en Bretagne", "652298488576905"),
    ("Tournoi de foot dans la Normandie", "110530655649765"),
    ("Educateurs U12-U13 Nord-Pas-de-Calais", "149507328962394"),
    ("Tournoi/Match FOOT Corse", "2532443237056819"),
    ("Tournoi de football BELGIQUE garcons et filles", "1820626038324054"),
    ("TOURNOIS DE MINI FOOT BELGIQUE", "1514224675306435"),
    ("Tournois Futsal BELGIQUE & France", "1740184706929974"),
    ("Tournois de Football dans les Hauts-de-France", "2988142011217915"),
    ("Match amicaux & Tournoi Hauts-de-France 59/62", "1955646184476218"),
    ("Tournois de Foot U6 a U15 France", "2835913233403831"),
]


# Les disciplines que Julien travaille vraiment, et de quoi chercher leurs
# groupes. On ne devine PAS d'identifiants de groupe : un identifiant invente
# ouvre une page morte, ou pire, un groupe qui n'a rien a voir. On ouvre donc
# la recherche de Facebook elle-meme, dans son navigateur ou il est connecte,
# et c'est lui qui voit ce qui existe et qui demande a rejoindre.
CHERCHES_FB = [
    # L'Est-Var. Aucun groupe dedie aux tournois de jeunes n'existe la-bas :
    # verifie le 20/10/2026, on ne trouve que les pages des clubs. « Tournoi/
    # Match FOOT 83 » couvre deja le departement ; ces recherches servent a
    # reperer ce qui se cree, et c'est Julien qui rejoint.
    ("Foot, Fréjus & Saint-Raphaël", "tournoi foot Fréjus Saint-Raphaël"),
    ("Foot, Est-Var", "tournoi football jeunes Est Var Draguignan Fayence"),
    ("District du Var de football", "district Var football tournoi"),
    ("Badminton", "tournoi badminton PACA"),
    ("Basket", "tournoi basket Provence Côte d'Azur"),
    ("Handball", "tournoi handball PACA"),
    ("Volley", "tournoi volley Provence"),
    ("Rugby", "tournoi rugby jeunes Provence"),
    ("Judo & arts martiaux", "tournoi judo PACA"),
    # La boxe, côté enfants : « boxe éducative » est le mot de la fédération
    # pour les catégories jeunes, et ces rencontres remplissent une salle de
    # parents. C'est le créneau, pas les combats pros.
    ("Boxe enfants — éducative", "boxe éducative jeunes PACA compétition"),
    ("Boxe enfants — interclubs", "interclub boxe jeunes Alpes-Maritimes Var"),
    ("Boxe enfants — écoles de boxe", "école de boxe enfants Cannes Nice Antibes"),
    ("Boxe enfants — arts martiaux jeunes", "gala arts martiaux enfants Côte d'Azur"),
    # La boxe, côté adultes : galas et combats amateurs. Une salle pleine,
    # des affiches, et des boxeurs qui achètent leurs photos.
    ("Boxe adultes — galas", "gala de boxe Côte d'Azur Var"),
    ("Boxe adultes — combats amateurs", "combat boxe amateur PACA soirée"),
    ("Boxe adultes — muay thai & kick", "muay thai kickboxing gala Nice Cannes"),
    ("Boxe adultes — savate & française", "savate boxe française compétition PACA"),
    ("Boxe — clubs du 06 et 83", "club de boxe Alpes-Maritimes Var"),
    ("Gym & GR", "compétition gymnastique PACA"),
    ("Danse", "gala concours danse Côte d'Azur"),
    ("Équitation", "concours équitation PACA"),
    ("Natation", "meeting natation PACA"),
    ("Auto & moto", "rassemblement auto moto Provence"),
    ("Course & trail", "trail course à pied Alpes-Maritimes Var"),
    ("Tout sport, Aix & Marseille", "tournoi sport Aix-en-Provence Marseille"),
    # La Belgique : bassin a part, deux heures de Lille, des clubs qui
    # organisent toute l'annee. Le sourceur y cherche des photographes.
    ("Belgique — tournois de foot", "tournoi foot jeunes Belgique"),
    ("Belgique — Hainaut & Namur", "tournoi football Hainaut Namur jeunes"),
    ("Belgique — Flandre & Anvers", "voetbaltornooi jeugd Antwerpen Vlaanderen"),
    ("Belgique — Liège & Luxembourg", "tournoi football Liège province Luxembourg"),
    ("Belgique — tous sports", "tournoi sport jeunes Belgique photographe"),
    # Les plus gros plateaux de France, toutes disciplines : ce sont eux
    # qu'on confie au sourceur, et ils ne sont pas tous sur les agendas.
    ("France — gros tournois jeunes", "grand tournoi international jeunes football France"),
    ("France — badminton, gros tournois", "tournoi national badminton France"),
    ("France — handball, gros plateaux", "grand tournoi handball jeunes France"),
    ("France — basket, gros plateaux", "tournoi international basket jeunes France"),
    ("France — volley & beach", "tournoi national volley beach France"),
    ("France — gymnastique & GR", "championnat national gymnastique France compétition"),
    ("Défilés de mode", "défilé de mode Côte d'Azur"),
    ("Élections de miss", "élection miss comité des fêtes Var Alpes-Maritimes"),
    ("Mini-miss & galas d'école", "mini miss gala école de mode Provence"),
    ("Comités des fêtes", "comité des fêtes Alpes-Maritimes animations"),
    ("Galeries & centres commerciaux", "Cap 3000 Polygone Riviera animations événements"),
    ("Caritatif (Téléthon, Octobre Rose)", "Téléthon Octobre Rose Côte d'Azur soirée gala"),
]


@route("GET", r"/api/groupes")
def api_groupes(req):
    return {
        "items": [{"nom": n, "id": i,
                   "url": "https://www.facebook.com/groups/%s/media" % i}
                  for n, i in GROUPES_FB],
        # A cote des groupes deja repertories : de quoi en trouver d'autres,
        # sport par sport. Le football a tout un ecosysteme de groupes ; les
        # autres disciplines, non — il faut aller voir.
        "cherches": [{"nom": n, "q": q,
                      "url": "https://www.facebook.com/search/groups/?q="
                             + urllib.parse.quote(q)}
                     for n, q in CHERCHES_FB],
    }


@route("POST", r"/api/groupes/ouvrir")
def api_groupes_ouvrir(req):
    """Ouvre les grilles d'affiches dans Chrome.

    On ouvre des pages, on ne les moissonne pas : c'est exactement ce que fait
    un clic sur un favori. Le depouillement reste declenche par vous, depuis
    votre navigation, pour ne pas exposer le compte a une collecte automatisee.
    """
    from .__main__ import ouvrir_navigateur
    voulus = req.body.get("ids")
    liste = [(n, i) for n, i in GROUPES_FB if not voulus or i in voulus]
    ouverts = []
    for nom, gid in liste:
        if ouvrir_navigateur("https://www.facebook.com/groups/%s/media" % gid):
            ouverts.append(nom)
    return {"ok": True, "ouverts": ouverts, "n": len(ouverts)}


def _lieu_du_bloc(bloc):
    """(code postal, commune) d'une annonce.

    Le code postal reste le plus sur. A defaut — et c'est le cas de presque
    toutes les annonces Facebook, qui ecrivent « a la Base Nature de Frejus »
    sans numero — on cherche une commune connue dans le texte : sans lieu, la
    fiche n'a pas de distance et disparait des tiroirs.
    """
    postal = util.extract_postal(bloc)
    ville = None
    if postal:
        for ligne in bloc.split("\n"):
            if postal in ligne:
                reste = ligne.split(postal, 1)[1].strip(" ,-")
                ville = re.split(r"[.,;:!?]", reste)[0].strip()[:40] or None
                break
    if not ville:
        trouve = geo.commune_dans_texte(bloc)
        if trouve:
            ville = trouve.get("commune")
            postal = postal or trouve.get("cp")

    # Dernier mot au referentiel officiel : il connait 35 000 communes contre
    # 261 a notre table, et surtout il refuse de confirmer ce qui n'existe pas.
    # Un « Barastiere » lu de travers sur une affiche ne devient plus un lieu.
    res = lieux.resoudre(ville=ville, code_postal=postal, texte=bloc)
    if res["commune"] and not res["conflit"]:
        return (res["cp"] or postal), res["commune"]
    return postal, ville


# Le sport lu dans l'annonce elle-meme. La capture de groupe estampillait
# « football » tout ce qui passait : les groupes suivis sont des groupes de
# foot, mais du jour ou l'un d'eux parle d'autre chose, la fiche mentait.
SPORTS_CONNUS = [
    ("Football", r"\bfoot\b|football|futsal|u\s?\d{1,2}\b"),
    ("Basket-ball", r"basket"),
    ("Volley-ball", r"volley|beach[- ]?volley"),
    ("Handball", r"\bhand\b|handball"),
    ("Rugby", r"rugby"),
    ("Tennis", r"\btennis\b|padel"),
    ("Voile", r"\bvoile\b|regate|r[ée]gate|nautique|catamaran|optimist"),
    ("Judo", r"\bjudo\b"),
    ("Karaté", r"karat[ée]"),
    ("Boxe", r"\bboxe\b|boxing|muay|kick"),
    ("Escalade", r"escalade|bloc\b"),
    ("Gymnastique", r"gymnastique|\bgym\b|trampoline"),
    ("Danse", r"\bdanse\b|gala de danse"),
    ("Natation", r"natation|piscine"),
    ("Athlétisme", r"athl[ée]tisme|cross\b|trail\b|10\s?km"),
    ("Cyclisme", r"cyclisme|\bvtt\b|\bbmx\b"),
    ("Équitation", r"[ée]quitation|poney|hippique"),
    ("Pétanque", r"p[ée]tanque|boulodrome"),
    ("Tennis de table", r"tennis de table|ping[- ]?pong"),
    ("Badminton", r"badminton"),
    ("Hockey", r"hockey"),
]


def _sport_du_texte(texte):
    """Le sport que l'annonce nomme, ou None. On ne devine pas."""
    plat = util.strip_accents(str(texte or "")).lower()
    for nom, motif in SPORTS_CONNUS:
        if re.search(motif, plat, re.I):
            return nom
    return None


@route("POST", r"/api/groupes/capture")
def api_groupes_capture(req):
    """Transforme des annonces copiees depuis un groupe en fiches de tournoi.

    Le texte arrive du presse-papiers : c'est vous qui avez ouvert la page et
    declenche la copie. Aucune connexion automatisee a Facebook.
    """
    texte = req.body.get("text") or ""
    groupe = (req.body.get("groupe") or "").strip()
    if not texte.strip():
        raise ApiError("rien a lire : copiez d'abord les annonces du groupe")

    source = "Groupe FB %s" % groupe if groupe else "Groupe FB"
    # Un post par bloc separe par une ligne de tirets, comme le produit le
    # bouton de capture ; a defaut, on traite le collage d'un seul tenant.
    blocs = [b.strip() for b in re.split(r"\n-{3,}\n", texte) if b.strip()]
    propositions = []
    for bloc in blocs:
        dates = dates_fr.extraire(bloc)
        if not dates:
            continue
        lien = None
        m = re.search(r"https?://(?:www\.)?facebook\.com/\S+", bloc)
        if m:
            lien = m.group(0).rstrip(").,")
        postal, ville = _lieu_du_bloc(bloc)
        # Le favori ajoute « AFFICHE: <url> » sous le texte du post. On la
        # garde ici et on la telecharge a la validation : l'adresse du CDN
        # de Facebook est signee et expire en quelques heures.
        m_aff = re.search(r"^AFFICHE:\s*(\S+)$", bloc, re.M)
        affiche = m_aff.group(1) if m_aff else None
        for d in dates:
            propositions.append({
                "affiche": affiche,
                "nom": d["nom"],
                "titre_propose": _titre_post(bloc) or d["nom"],
                "debut": d["debut"], "fin": d["fin"],
                "confiance": d["confiance"],
                "annee_devinee": d["annee_devinee"],
                "extrait": d["extrait"],
                "ville": util.title_name(ville) if ville else None,
                "postal_code": postal,
                "dept": util.dept_from_postal(postal),
                "url": lien,
                "telephones": util.extract_phones(bloc),
                "source": source,
                "texte": bloc[:1200],
            })
    return {"items": propositions, "n": len(propositions),
            "blocs": len(blocs), "source": source}


TITRE_POST = re.compile(
    r"^.{0,90}?\b(tournoi\w*|challenge|coupe|cup|open|festival|plateau|criterium)\b.{0,70}",
    re.I | re.M)


# Tournures qui precedent l'intitule reel : « X organise son ... »
AVANT_TITRE = re.compile(
    r"^.*?\b(?:organise|propose|presente|annonce|lance)\s+(?:son|sa|le|la|un|une|leur)\s+",
    re.I)


def _titre_post(bloc):
    """Premiere ligne qui ressemble a un intitule de tournoi, degraissee.

    « Le SC X organise son Tournoi Elite 2026 le samedi 17 octobre. »
    devient « Tournoi Elite 2026 ».
    """
    for ligne in bloc.split("\n"):
        ligne = ligne.strip()
        if not (6 < len(ligne) < 160 and TITRE_POST.match(ligne)):
            continue
        titre = AVANT_TITRE.sub("", ligne)
        # On coupe ce qui suit la date : « ... 2026 le samedi 17 octobre »
        titre = re.split(r"\s+(?:le|les|du|the)\s+(?:\d|lundi|mardi|mercredi|jeudi|"
                         r"vendredi|samedi|dimanche)", titre, 1, re.I)[0]
        titre = titre.strip(" \t.,;:-\u2013\u2014")
        if 6 < len(titre) < 120:
            return titre
    return None


@route("POST", r"/api/groupes/valider")
def api_groupes_valider(req):
    """Enregistre les annonces retenues comme fiches a trancher."""
    items = req.body.get("items") or []
    records = []
    for it in items:
        nom = (it.get("titre_propose") or it.get("nom") or "").strip()
        if not nom or not it.get("debut"):
            continue
        records.append({
            "name": nom[:200], "sport": it.get("sport") or _sport_du_texte(
                " ".join(str(it.get(k) or "") for k in ("titre_propose", "nom", "texte"))),
            "start_date": it.get("debut"),
            "end_date": it.get("fin") or it.get("debut"),
            "city": it.get("ville"), "postal_code": it.get("postal_code"),
            "url": it.get("url"), "description": (it.get("texte") or "")[:1000],
            "source": it.get("source") or "Groupe FB",
        })
    created, updated, skipped, ecartes = sources.save_tournaments(records)

    # L'affiche du post, rapatriee tout de suite. Sans elle on relit une fiche
    # sans jamais pouvoir verifier ce qui etait ecrit dessus — c'est ainsi
    # qu'un tournoi de Vedene est entre en base comme Vidauban.
    affiches = 0
    urls = {}
    for it in items:
        cle_titre = (it.get("titre_propose") or it.get("nom") or "").strip()[:200]
        if cle_titre and it.get("affiche"):
            urls[cle_titre] = it["affiche"]
    for fiche in created:
        url = urls.get((fiche.get("name") or "").strip())
        if url and flyer.rapatrier_affiche(fiche.get("id"), url, prefixe="groupe"):
            affiches += 1

    db.log("groupes", "Capture groupe : %d fiche(s), %d affiche(s), %d hors perimetre"
           % (len(created), affiches, ecartes))
    return {"ok": True, "created": len(created), "updated": updated,
            "skipped": skipped, "hors_perimetre": ecartes,
            "affiches": affiches, "items": created}


# --------------------------------------------------------------------------
# Rassemblements : tout ce qui attire du monde et se vend en photos
# --------------------------------------------------------------------------

@route("GET", r"/api/rassemblements/chasse")
def api_chasse(req):
    """Les pages a ouvrir pour aller chercher des rassemblements."""
    return {"items": rassemblements.liens_de_chasse()}


@route("POST", r"/api/rassemblements/ouvrir")
def api_chasse_ouvrir(req):
    from .__main__ import ouvrir_navigateur
    urls = req.body.get("urls") or []
    if not urls:
        raise ApiError("aucune adresse a ouvrir")
    ouverts = 0
    for u in urls[:12]:          # on n'inonde pas Chrome
        if str(u).startswith(("http://", "https://")) and ouvrir_navigateur(u):
            ouverts += 1
    return {"ok": True, "n": ouverts}


def _titre_annonce(bloc):
    """Le titre d'une annonce d'agenda : sa premiere ligne.

    Les agendas ecrivent le nom en tete puis les details dessous. Une premiere
    ligne courte, sans date ni adresse, est le titre ; sinon on laisse le
    reperage de date nommer l'evenement.
    """
    for ligne in (bloc or "").split("\n"):
        ligne = ligne.strip(" \t-\u2013\u2014")
        if not (4 < len(ligne) < 120):
            continue
        bas = ligne.lower()
        # On ne rejette que si la ligne commence vraiment par une date :
        # « Les Foulées Cannoises » est un titre, « Les 25 et 26 mars » non.
        if re.match(r"^(?:l[ea]s?|du|de|d[eu]s)\s+(?:\d|lundi|mardi|mercredi|jeudi|"
                    r"vendredi|samedi|dimanche)|^\d|^(?:lundi|mardi|mercredi|jeudi|"
                    r"vendredi|samedi|dimanche)\b", bas):
            return None
        if util.extract_postal(ligne) or re.search(r"https?://", ligne):
            return None
        return ligne
    return None


@route("POST", r"/api/rassemblements/capture")
def api_rassemblements_capture(req):
    """Lit des annonces collees et note leur interet photo."""
    texte = req.body.get("text") or ""
    origine = (req.body.get("origine") or "collage").strip()
    if not texte.strip():
        raise ApiError("collez d'abord des annonces")
    blocs = [b.strip() for b in re.split(r"\n-{3,}\n", texte) if b.strip()]
    if len(blocs) == 1:
        # Un collage d'agenda n'a pas de separateurs : on decoupe aux lignes vides.
        blocs = [b.strip() for b in re.split(r"\n\s*\n", texte) if b.strip()]
    propositions = []
    for bloc in blocs:
        classe = rassemblements.classer(bloc)
        if not classe["genre"] or classe["note"] <= 0:
            continue
        dates = dates_fr.extraire(bloc)
        if not dates:
            continue
        postal, ville = _lieu_du_bloc(bloc)
        lien = None
        m = re.search(r"https?://\S+", bloc)
        if m:
            lien = m.group(0).rstrip(").,")
        titre = _titre_annonce(bloc)
        for d in dates:
            propositions.append({
                "nom": titre or d["nom"], "debut": d["debut"], "fin": d["fin"],
                "confiance": d["confiance"], "annee_devinee": d["annee_devinee"],
                "extrait": d["extrait"],
                "genre": classe["genre"], "genre_libelle": classe["libelle"],
                "note_photo": classe["note"], "indices": classe["indices"],
                "ville": util.title_name(ville) if ville else None,
                "postal_code": postal, "dept": util.dept_from_postal(postal),
                "url": lien, "telephones": util.extract_phones(bloc),
                "source": "Rassemblement %s" % origine, "texte": bloc[:1200],
            })
    propositions.sort(key=lambda x: -x["note_photo"])
    return {"items": propositions, "n": len(propositions), "blocs": len(blocs)}


@route("POST", r"/api/rassemblements/valider")
def api_rassemblements_valider(req):
    items = req.body.get("items") or []
    records = []
    for it in items:
        if not it.get("nom") or not it.get("debut"):
            continue
        records.append({
            "name": it["nom"][:200],
            "sport": it.get("genre_libelle"),
            "start_date": it.get("debut"),
            "end_date": it.get("fin") or it.get("debut"),
            "city": it.get("ville"), "postal_code": it.get("postal_code"),
            "url": it.get("url"), "description": (it.get("texte") or "")[:1000],
            "source": it.get("source") or "Rassemblement",
            "genre": it.get("genre"), "genre_libelle": it.get("genre_libelle"),
            "note_photo": it.get("note_photo"),
        })
    created, updated, skipped, ecartes = sources.save_tournaments(records)
    db.log("rassemblements", "Capture : %d fiche(s), %d hors perimetre"
           % (len(created), ecartes))
    return {"ok": True, "created": len(created), "updated": updated,
            "skipped": skipped, "hors_perimetre": ecartes}


@route("GET", r"/api/rassemblements")
def api_rassemblements_liste(req):
    # Ceinture et bretelles : le genre suffit en principe, mais une fiche venue
    # d'une source de veille foot n'a rien a faire ici meme si elle est mal
    # classee — l'ecran doit rester lisible.
    rows = db.rows_to_dicts(db.q(
        "SELECT * FROM tournaments WHERE genre IS NOT NULL AND genre != 'tournoi' "
        "AND COALESCE(sport,'') NOT LIKE '%oot%' "
        "AND COALESCE(source,'') NOT LIKE '%foot%' "
        "AND COALESCE(source,'') NOT LIKE '%Foot%' "
        # Une competition de club, scolaire ou non, releve de la veille d'en
        # haut : la retrouver ici en doublon donnait un ecran de matchs de
        # district la ou on cherche des fetes et des brocantes.
        "AND COALESCE(genre,'') NOT IN ('competition', 'match') "
        "AND name NOT LIKE '%Competition%' AND name NOT LIKE '%Compétition%' "
        "AND name NOT LIKE '%Championnat%' "
        "AND (start_date IS NULL OR date(start_date) >= date('now')) "
        # La date d'abord : un rassemblement tres vendeur mais dans huit mois
        # ne se prepare pas avant celui de samedi.
        "ORDER BY date(start_date), note_photo DESC"))
    return {"items": rows, "n": len(rows)}


# --------------------------------------------------------------------------
# Partenaires : les clubs avec qui on a deja travaille
# --------------------------------------------------------------------------

@route("GET", r"/api/partenaires")
def api_partenaires(req):
    rows = db.rows_to_dicts(db.q(
        "SELECT c.*, "
        "(SELECT COUNT(*) FROM contacts ct WHERE ct.club_id = c.id) AS n_contacts, "
        "(SELECT COUNT(*) FROM tournaments t WHERE t.club_id = c.id "
        " AND date(t.start_date) >= date('now')) AS n_dates "
        "FROM clubs c WHERE c.partenaire = 1 ORDER BY c.name COLLATE NOCASE"))
    for r in rows:
        r["contacts"] = db.rows_to_dicts(db.q(
            "SELECT id, first_name, last_name, role, phone, email "
            "FROM contacts WHERE club_id = ? ORDER BY id", (r["id"],)))
        for ct in r["contacts"]:
            ct["phone_fmt"] = util.format_phone(ct.get("phone"))
        r["prochaine_date"] = db.q1(
            "SELECT name, start_date FROM tournaments WHERE club_id = ? "
            "AND date(start_date) >= date('now') ORDER BY start_date LIMIT 1", (r["id"],))
        if r["prochaine_date"]:
            r["prochaine_date"] = dict(r["prochaine_date"])
        r["temperature"] = repo.temperature(r.get("last_contact_at"))
        # La distance se calcule ici : un partenaire a 19 km ne se traite pas
        # comme un partenaire a 65 km quand il faut caser une journee.
        d = geo.depuis_cannes(r.get("city"), r.get("postal_code")) \
            if (r.get("city") or r.get("postal_code")) else None
        r["km"] = (d or {}).get("km")
        r["trajet_min"] = (d or {}).get("minutes")
        r["distance_verdict"] = (d or {}).get("verdict")
    return {"items": rows, "n": len(rows)}


@route("POST", r"/api/partenaires")
def api_partenaire_ajouter(req):
    """Bascule un club en partenaire, ou en cree un de toutes pieces."""
    body = req.body
    club_id = body.get("club_id")
    if not club_id:
        nom = (body.get("name") or "").strip()
        if not nom:
            raise ApiError("il faut un club existant ou un nom")
        club_id, _ = repo.upsert_club({
            "name": nom, "city": body.get("city"), "sport": body.get("sport"),
            "postal_code": body.get("postal_code"),
            "source": body.get("source") or "partenaire",
        })
        if not club_id:
            raise ApiError("creation impossible")
    db.update("clubs", int(club_id), {
        "partenaire": 1,
        "partenaire_depuis": body.get("depuis"),
        "partenaire_note": body.get("note"),
        "status": body.get("status") or "client",
        "updated_at": util.now_iso()})
    return {"ok": True, "club": repo.get_club(int(club_id))}


@route("DELETE", r"/api/partenaires/(\d+)")
def api_partenaire_retirer(req, cid):
    db.update("clubs", int(cid), {"partenaire": 0, "updated_at": util.now_iso()})
    return {"ok": True}


@route("PATCH", r"/api/partenaires/(\d+)")
def api_partenaire_modifier(req, cid):
    patch = {k: v for k, v in req.body.items()
             if k in ("partenaire_depuis", "partenaire_note", "sport", "city", "notes")}
    patch["updated_at"] = util.now_iso()
    db.update("clubs", int(cid), patch)
    return {"ok": True}


@route("GET", r"/api/visites")
def api_visites(req):
    """Combien de fois chaque ecran a ete ouvert, pour ranger le menu."""
    from . import visites
    return {"comptes": visites.comptes(), "detail": visites.liste()}


@route("POST", r"/api/visites")
def api_visite_noter(req):
    """Les onglets de la plateforme s'annoncent : ils ne passent pas par une page."""
    from . import visites
    ecran = ((req.body or {}).get("ecran") or "").strip()
    return {"ok": visites.noter(ecran)}


@route("GET", r"/api/prospection/villes")
def api_prospection_villes(req):
    """Les villes du couloir, une seule source de verite.

    L'ecran posait ses raccourcis en dur et oubliait la moitie du couloir :
    il les lit maintenant ici, et ajouter une ville a COULOIR suffit.
    """
    from . import prospection
    return {"villes": list(prospection.COULOIR),
            "rayon_defaut": prospection.RAYON_DEFAUT}


@route("POST", r"/api/ajouter-en-phrase")
def api_ajouter_en_phrase(req):
    """« ajoute ERIC AVIRON villefranche en partenaire », et c'est fait.

    Le modele local lit la phrase et en tire des MOTS : un nom, une ville, un
    sport, une personne, un role. Il ne rend ni numero ni mail — ceux-la sont
    extraits du texte brut par util, qui sait reconnaitre un vrai numero
    francais. Un modele n'a pas le droit d'inventer un moyen de contact.

    `verifier` rend ce qui serait cree sans rien ecrire : Julien voit avant.
    """
    from . import ia
    b = req.body or {}
    phrase = (b.get("phrase") or "").strip()
    if not phrase:
        raise ApiError("dites ce qu'il faut ajouter")
    if len(phrase) > 400:
        raise ApiError("c'est trop long pour une phrase")
    if not ia.disponible():
        raise ApiError("le modèle local ne répond pas — vérifiez qu'Ollama tourne", 503)

    lu = ia.fiche_depuis_phrase(phrase)
    if not lu:
        raise ApiError("je n'ai pas compris qui ajouter")

    # Les coordonnees ne viennent JAMAIS du modele : on les lit dans la phrase.
    tels = util.extract_phones(phrase) or []
    mails = util.extract_emails(phrase) or []

    fiche = {
        "name": lu["nom"],
        "city": lu.get("ville") or None,
        "sport": lu.get("sport") or None,
        "phone": tels[0] if tels else None,
        "email": mails[0] if mails else None,
        "source": "dicté",
        "categorie": lu["categorie"],
        "personne": lu.get("personne") or None,
        "role": lu.get("role") or None,
    }

    # str(True) vaut « True » : sans .lower() le booleen JSON passait au
    # travers et la fiche etait creee alors qu'on demandait une simulation.
    if str(b.get("verifier", "")).lower() in ("1", "true", "oui"):
        existe = db.q1("SELECT id, name, city FROM clubs WHERE name_key = ?",
                       (util.norm_key(fiche["name"]),)) if fiche["name"] else None
        fiche["deja"] = dict(existe) if existe else None
        return {"lu": fiche, "phrase": phrase}

    cid, etat = repo.upsert_club({
        "name": fiche["name"], "city": fiche["city"], "sport": fiche["sport"],
        "phone": fiche["phone"], "email": fiche["email"],
        "source": "dicté", "status": "prospect",
    })
    if not cid:
        raise ApiError("création impossible")

    if fiche["categorie"] == "partenaire":
        db.ex("UPDATE clubs SET partenaire = 1, partenaire_depuis = COALESCE("
              "partenaire_depuis, ?) WHERE id = ?", (util.today().isoformat(), cid))
    elif fiche["categorie"] == "relais":
        db.ex("UPDATE clubs SET relais = 1 WHERE id = ?", (cid,))

    # La personne citee devient un contact, avec le numero lu dans la phrase.
    contact_id = None
    if fiche["personne"]:
        bouts = fiche["personne"].split()
        contact_id = db.insert("contacts", {
            "club_id": cid,
            "first_name": bouts[0],
            "last_name": " ".join(bouts[1:]) or None,
            "role": fiche["role"],
            "phone": fiche["phone"],
            "email": fiche["email"],
            "channel": "whatsapp" if (fiche["phone"]
                                      and util.is_mobile_fr(fiche["phone"]))
                       else "email",
            "source": "dicté",
            "created_at": util.now_iso(),
            "updated_at": util.now_iso(),
        })

    db.log("ajout", "Dicté : %s (%s)" % (fiche["name"], fiche["categorie"]))
    return {"ok": True, "club_id": cid, "etat": etat, "contact_id": contact_id,
            "lu": fiche}


@route("GET", r"/api/partenaires/(\d+)/message")
def api_partenaire_message(req, cid):
    """Le mot qu'on enverrait a ce partenaire, rendu sans rien enregistrer.

    La route /relancer cree un brouillon : c'est un acte, pas une lecture.
    Un ecran qui s'ouvre ne doit rien creer — d'ou cette route-ci.
    """
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("partenaire introuvable", 404)
    contact = drafts.best_contact(int(cid))
    modele = ("partenaire_whatsapp"
              if contact and util.is_mobile_fr(contact.get("phone"))
              and not contact.get("email") else "partenaire_relance")
    depuis = club.get("partenaire_depuis")
    rendu = drafts.render(modele, club=club,
                          extra={"depuis": (" depuis %s" % depuis) if depuis else ""})
    return {"objet": rendu.get("subject"), "corps": rendu.get("body"),
            "destinataire": (contact or {}).get("email")
                            or (contact or {}).get("phone")
                            or club.get("email") or club.get("phone"),
            "modele": modele}


@route("POST", r"/api/partenaires/(\d+)/relancer")
def api_partenaire_relancer(req, cid):
    """Prepare un message au ton de quelqu'un qui les connait deja."""
    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (int(cid),)))
    if not club:
        raise ApiError("partenaire introuvable")
    modele = req.body.get("template")
    if not modele:
        contact = drafts.best_contact(int(cid))
        modele = ("partenaire_whatsapp"
                  if contact and util.is_mobile_fr(contact.get("phone"))
                  and not contact.get("email") else "partenaire_relance")
    depuis = club.get("partenaire_depuis")
    extra = {"depuis": (" depuis %s" % depuis) if depuis else ""}
    tid = req.body.get("tournament_id")
    draft = drafts.create_draft(int(cid), modele, tournament_id=tid,
                                reason="Partenaire — %s" % (club.get("partenaire_note")
                                                            or "relance"),
                                extra=extra, created_by="humain")
    if not draft:
        raise ApiError("brouillon impossible (club en opt-out ou modele absent)")
    return draft


@route("POST", r"/api/partenaires/vcards")
def api_partenaires_vcards(req):
    """Lit des cartes de contact WhatsApp et propose de les classer en partenaires."""
    texte = req.body.get("text") or ""
    if "BEGIN:VCARD" not in texte.upper():
        raise ApiError("aucune carte de contact : exportez la discussion "
                       "(Exporter la discussion > Sans les medias) et collez le .txt")
    cartes = whatsapp.parse_vcards(texte)
    if not cartes:
        raise ApiError("cartes illisibles")
    for c in cartes:
        connu = None
        for tel in c["telephones"]:
            connu = repo.find_contact(None, tel)
            if connu:
                break
        if not connu and c["emails"]:
            connu = repo.find_contact(c["emails"][0], None)
        c["existant"] = bool(connu)
        c["club_propose"] = c.get("organisation") or _club_depuis_nom(c["nom"])
        c["telephones_fmt"] = [util.format_phone(t) for t in c["telephones"]]
    return {"items": cartes, "n": len(cartes)}


# Les cartes partagees s'appellent « JOEL DELALANDE President Cap D Ail » :
# le nom, la fonction et le club tiennent dans la meme ligne.
_ROLE_DANS_NOM = re.compile(
    r"\b(pr[ée]sident(?:e)?|secr[ée]taire|tr[ée]sori[eè]r(?:e)?|coach|"
    r"entra[iî]neur|responsable|com|communication|dirigeant)\b", re.I)


def _club_depuis_nom(nom):
    """Devine le club a partir du libelle d'une carte de contact."""
    if not nom:
        return None
    m = _ROLE_DANS_NOM.search(nom)
    if not m:
        return None
    reste = nom[m.end():].strip(" -\u2013\u2014,;")
    return reste or None


@route("POST", r"/api/partenaires/coller")
def api_partenaires_coller(req):
    """Cree des partenaires depuis un collage libre « Nom - numero - club ».

    WhatsApp Mac n'expose pas d'export de discussion : le chemin reel est
    d'ouvrir chaque carte de contact, qui affiche le numero, et de le recopier.
    Cette voie accepte n'importe quelle mise en forme.
    """
    texte = req.body.get("text") or ""
    if not texte.strip():
        raise ApiError("collez des lignes du type « Nom - 06 12 34 56 78 - Club »")
    propositions, groupes = [], 0
    for ligne in texte.split("\n"):
        ligne = ligne.strip(" \t-\u2013\u2014|")
        if not ligne:
            continue
        # Un lien wa.me porte le numero en clair, sans le « + ». On le sort en
        # premier : laisse a extract_phones, une URL est une suite de chiffres
        # collee a du texte, et le garde-fou des longues suites la rejette.
        # Le reste de la ligne (nom, club) continue son chemin normalement.
        tels_lien, ligne = whatsapp.numeros_depuis_liens(ligne)
        tels = list(tels_lien)
        for t in util.extract_phones(ligne):
            if t not in tels:
                tels.append(t)
        mails = util.extract_emails(ligne)
        if not tels and not mails:
            # Une invitation de groupe ne contient aucun numero : il faut le
            # dire, sinon la ligne disparait sans explication.
            if whatsapp.lien_de_groupe(ligne):
                groupes += 1
            continue
        reste = ligne
        for t in tels:
            for forme in (t, util.format_phone(t), t.replace("+33", "0")):
                reste = reste.replace(forme, " ")
        for m in mails:
            reste = reste.replace(m, " ")
        # Le tiret ne coupe que s'il est entoure d'espaces : sans cette regle,
        # « Roquebrune-Cap-Martin » devenait le club « Roquebrune », et
        # « Cap-d'Ail » le club « Cap ». Les noms de communes du 06 et du 83
        # sont pleins de traits d'union, et un club mal nomme fait un doublon.
        morceaux = [x.strip(" \t-\u2013\u2014,;|/")
                    for x in re.split(r"\s[-\u2013\u2014|/]\s|[,;]|\s{2,}", reste)]
        morceaux = [x for x in morceaux if len(x) > 1]
        personne = morceaux[0] if morceaux else None
        club = morceaux[1] if len(morceaux) > 1 else None
        fonction = _role_depuis_nom(ligne)
        if personne:
            personne = _nom_sans_role(personne) or personne
        propositions.append({
            "nom": personne, "club_propose": club or personne,
            "fonction": fonction,
            "telephones": tels, "telephones_fmt": [util.format_phone(t) for t in tels],
            "emails": mails,
            "existant": bool(tels and repo.find_contact(None, tels[0])),
        })
    if not propositions:
        if groupes:
            raise ApiError(
                "ce lien ouvre un groupe WhatsApp, pas une personne : il ne "
                "contient aucun numero. Ouvrez la fiche du contact dans "
                "WhatsApp et copiez son lien wa.me, ou son numero.")
        raise ApiError("aucun numero ni email reconnu dans ce collage")
    return {"items": propositions, "n": len(propositions)}


@route("POST", r"/api/partenaires/vcards/valider")
def api_partenaires_vcards_valider(req):
    """Enregistre les cartes retenues comme partenaires."""
    items = req.body.get("items") or []
    clubs, contacts = 0, 0
    for it in items:
        nom_club = (it.get("club") or it.get("club_propose") or "").strip()
        if not nom_club:
            nom_club = (it.get("nom") or "").strip()
        if not nom_club:
            continue
        club_id, action = repo.upsert_club({
            "name": nom_club, "sport": it.get("sport"), "city": it.get("city"),
            "source": "carte de contact WhatsApp"})
        if not club_id:
            continue
        if action == "created":
            clubs += 1
        db.update("clubs", club_id, {
            "partenaire": 1, "status": "client",
            "partenaire_depuis": it.get("depuis"),
            "partenaire_note": it.get("note"),
            "updated_at": util.now_iso()})
        personne = _nom_sans_role(it.get("nom"))
        tels = it.get("telephones") or []
        cid, caction = repo.upsert_contact({
            "club_id": club_id,
            "first_name": personne.split(" ")[0] if personne else None,
            "last_name": " ".join(personne.split(" ")[1:]) if personne and
                         len(personne.split(" ")) > 1 else None,
            "role": it.get("fonction") or _role_depuis_nom(it.get("nom")),
            "phone": tels[0] if tels else None,
            "phone2": tels[1] if len(tels) > 1 else None,
            "email": (it.get("emails") or [None])[0],
            "source": "carte de contact WhatsApp"})
        if caction == "created":
            contacts += 1
    db.log("partenaires", "Cartes importees : %d club(s), %d contact(s)"
           % (clubs, contacts))
    return {"ok": True, "clubs": clubs, "contacts": contacts}


def _role_depuis_nom(nom):
    m = _ROLE_DANS_NOM.search(nom or "")
    return util.title_name(m.group(0)) if m else None


def _nom_sans_role(nom):
    """« JOEL DELALANDE President Cap D Ail » -> « Joel Delalande »."""
    if not nom:
        return None
    m = _ROLE_DANS_NOM.search(nom)
    avant = nom[:m.start()] if m else nom
    avant = avant.strip(" -\u2013\u2014,;")
    return util.title_name(avant) if avant else None


# --------------------------------------------------------------------------
# Reglages, export, journal
# --------------------------------------------------------------------------

@route("POST", r"/api/mot/reformuler")
def api_mot_reformuler(req):
    """Rend autrement le message qui est dans la case. N'envoie rien.

    Le modele tourne sur ce Mac : rien ne part sur internet, rien n'est
    facture. La regle de la maison ne bouge pas — aucun message ne quitte
    l'application, c'est Julien qui appuie sur envoyer dans WhatsApp.
    """
    corps = req.body or {}
    try:
        return ia.reformuler(corps.get("texte"), corps.get("consigne"),
                             corps.get("contexte"))
    except ia.PasLa as exc:
        raise ApiError(str(exc), 409)
    except ia.Refus as exc:
        raise ApiError(str(exc), 502)


@route("GET", r"/api/mot/reformuler")
def api_mot_reformuler_etat(req):
    """Dit a l'ecran s'il peut proposer le bouton."""
    return ia.etat()


@route("GET", r"/api/settings")
def api_settings(req):
    settings = db.all_settings()

    host = db.get_setting("public_host", req.host or "127.0.0.1:8765")
    token = settings.get("feed_token")
    settings["feeds"] = {
        "all": "http://%s/feed/all.ics?token=%s" % (host, token),
        "relances": "http://%s/feed/relances.ics?token=%s" % (host, token),
        "tournois": "http://%s/feed/tournois.ics?token=%s" % (host, token),
        "webcal": "webcal://%s/feed/all.ics?token=%s" % (host, token),
    }
    settings["statuses"] = [{"key": k, "label": v} for k, v in repo.STATUSES]
    # La cle Google ne redescend jamais en clair dans la page : on en montre
    # juste assez pour savoir qu'elle est posee.
    brute = settings.get("gemini_cle") or ""
    settings["gemini_cle"] = ("…" + brute[-4:]) if len(brute) > 4 else ""
    settings["gemini_pose"] = bool(brute)
    return settings


@route("PUT", r"/api/settings")
def api_settings_save(req):
    for key, value in (req.body or {}).items():
        if key in ("feeds", "statuses", "feed_token", "gemini_pose"):
            continue
        # Le formulaire renvoie la cle masquee : la reenregistrer l'effacerait.
        if key == "gemini_cle":
            texte = (value or "").strip()
            if not texte or texte.startswith("…"):
                continue
            db.set_setting(key, texte)
            continue
        db.set_setting(key, "" if value is None else str(value))
    return api_settings(req)


@route("GET", r"/api/log")
def api_log(req):
    return {"items": db.rows_to_dicts(db.q(
        "SELECT * FROM runlog ORDER BY id DESC LIMIT 100"))}


@route("GET", r"/api/export/(clubs|contacts|tournois)\.csv")
def api_export(req, what):
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    if what == "clubs":
        rows = repo.list_clubs(limit=10000)
        cols = ["id", "name", "sport", "city", "postal_code", "dept", "status", "email",
                "phone", "website", "source", "last_contact_at", "notes"]
    elif what == "contacts":
        rows = repo.list_contacts(limit=10000)
        cols = ["id", "club_name", "first_name", "last_name", "role", "email", "phone_fmt",
                "phone2_fmt", "club_dept", "source", "last_contact_at"]
    else:
        rows = db.rows_to_dicts(db.q("SELECT * FROM tournaments ORDER BY start_date"))
        cols = ["id", "name", "start_date", "end_date", "city", "postal_code", "dept",
                "organizer", "url", "source", "status"]
    writer.writerow(cols)
    for row in rows:
        writer.writerow([row.get(c, "") for c in cols])
    return {"_raw": buf.getvalue(), "_ctype": "text/csv; charset=utf-8",
            "_filename": "%s.csv" % what}


# --------------------------------------------------------------------------
# Flux iCal
# --------------------------------------------------------------------------

def serve_feed(handler, kind, query):
    token = db.get_setting("feed_token")
    if query.get("token") != token:
        handler.send_error(403, "jeton invalide")
        return
    payload = ics.feed(kind).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/calendar; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Content-Disposition", 'inline; filename="%s.ics"' % kind)
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(payload)


# --------------------------------------------------------------------------
# Plomberie HTTP
# --------------------------------------------------------------------------

class Req(object):
    def __init__(self, query, body, host, jeton=None, agent=None):
        self.query = query
        self.raw_body = body
        # Le jeton de session, lu dans le cookie, et le cookie que la route
        # veut poser en retour (connexion, deconnexion).
        self.jeton = jeton
        self.agent = agent
        self.cookie = None
        # body : uniquement les valeurs JSON-serialisables (les fichiers restent
        # accessibles via raw_body).
        self.body = {k: v for k, v in body.items() if not isinstance(v, tuple)} \
            if isinstance(body, dict) else {}
        self.host = host


class Handler(BaseHTTPRequestHandler):
    server_version = "prospect/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("PROSPECT_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers ---------------------------------------------------------
    def _json(self, payload, code=200):
        if isinstance(payload, dict) and "_raw" in payload:
            brut = payload["_raw"]
            raw = brut if isinstance(brut, bytes) else brut.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", payload.get("_ctype", "text/plain"))
            self.send_header("Content-Length", str(len(raw)))
            if payload.get("_filename"):
                self.send_header("Content-Disposition",
                                 'attachment; filename="%s"' % payload["_filename"])
            self.end_headers()
            self.wfile.write(raw)
            return
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if getattr(self, "_cookie_a_poser", None):
            self.send_header("Set-Cookie", self._cookie_a_poser)
            self._cookie_a_poser = None
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _static(self, path):
        rel = posixpath.normpath(path).lstrip("/")
        if rel in ("", "index.html"):
            rel = "index.html"
        # Les images des plaquettes vivent hors du dossier statique, a cote du
        # PDF qu'elles composent. On les sert depuis la, et seulement depuis la.
        if rel.startswith("img-plaquette/"):
            bouts = rel.split("/", 2)
            racine = os.path.abspath(os.path.join(
                RACINE_PROJET, "plaquettes",
                "studio" if len(bouts) > 1 and bouts[1] == "studio" else "", "img"))
            full = os.path.abspath(os.path.join(racine, bouts[-1]))
            if not full.startswith(racine) or not os.path.isfile(full):
                self.send_error(404, "introuvable")
                return
            return self._fichier(full, rel)
        full = os.path.join(STATIC_DIR, rel)
        if not os.path.abspath(full).startswith(os.path.abspath(STATIC_DIR)) \
                or not os.path.isfile(full):
            self.send_error(404, "introuvable")
            return
        # Une ouverture de page, comptee pour ranger le menu par usage. Ne
        # doit jamais empecher la page de s'afficher.
        if rel.endswith(".html"):
            try:
                from . import visites
                visites.noter(rel)
            except Exception:
                pass
        return self._fichier(full, rel)

    def _fichier(self, full, rel):
        ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8", "svg": "image/svg+xml",
                 "ico": "image/x-icon", "png": "image/png", "jpg": "image/jpeg",
                 "jpeg": "image/jpeg", "webp": "image/webp",
                 "woff2": "font/woff2"}.get(rel.rsplit(".", 1)[-1], "text/plain")
        with open(full, "rb") as fh:
            payload = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    # -- dispatch --------------------------------------------------------
    # -- La serrure ---------------------------------------------------------
    #
    # Tant que le CRM n'ecoute que sur 127.0.0.1, il n'a besoin d'aucune
    # serrure : personne d'autre que le Mac ne l'atteint. Des qu'on l'ouvre
    # sur internet pour le sourceur, la question change du tout au tout — la
    # base contient huit cents clubs et six cents conversations.
    #
    # On ne se fie PAS a l'adresse du client pour decider. C'est le piege :
    # derriere un tunnel (Cloudflare, Tailscale, ngrok), la connexion arrive
    # bel et bien de 127.0.0.1, et une serrure fondee sur l'adresse
    # s'ouvrirait toute seule pour le monde entier. On se fie au reglage :
    # si Julien a declare le CRM ouvert, TOUT demande une session.

    LIBRE_OUVERT = (
        "/espace.html", "/espace.css", "/espace.js",
        "/bitchat.css", "/bitchat.js", "/nav.css", "/cartes.css",
        "/img/marque-vert-clair.png", "/img/marque-vert-sombre.png",
    )

    def _jeton_de_session(self):
        brut = self.headers.get("Cookie") or ""
        for bout in brut.split(";"):
            nom, _, val = bout.strip().partition("=")
            if nom == "snapsession":
                return val.strip()
        return None

    def _qui(self):
        try:
            from . import comptes
            return comptes.compte_de_session(self._jeton_de_session())
        except Exception:
            return None

    def _barre_la_route(self, method, path):
        """True si cette requete doit etre refusee. Rend aussi le motif."""
        try:
            ouvert = db.get_setting("ouvert_internet", "0") == "1"
        except Exception:
            # Si on n'arrive pas a lire le reglage, on NE SAIT PAS si le CRM
            # est publie. Dans le doute on verrouille : une serrure qui
            # s'ouvre quand la base tousse n'est pas une serrure.
            #
            # Ce n'est pas theorique — c'est arrive. La connexion du serveur
            # s'est cassee (« database disk image is malformed ») alors qu'un
            # tunnel tournait : chaque lecture echouait, et le CRM s'est
            # retrouve joignable depuis internet sans mot de passe.
            ouvert = True
        if not ouvert:
            # Ferme : on n'ecoute que la maison, rien a verrouiller.
            return None
        if path in self.LIBRE_OUVERT or path.startswith("/img/"):
            return None
        if path in ("/api/connexion", "/api/deconnexion", "/api/moi"):
            return None
        qui = self._qui()
        if not qui:
            return "connexion requise"
        if qui.get("role") == "patron":
            return None
        # Un compte qui n'est pas patron ne voit QUE son espace. Ce n'est pas
        # un filtre d'affichage : les routes du CRM lui sont fermees ici, et
        # changer l'adresse dans la barre ne lui ouvre rien.
        if path.startswith("/api/espace/") or path in self.LIBRE_OUVERT:
            return None
        return "cet espace ne vous est pas ouvert"

    def _dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        feed_match = re.match(r"^/feed/(all|relances|tournois)\.ics$", path)
        if feed_match and method == "GET":
            serve_feed(self, feed_match.group(1), query)
            return

        refus = self._barre_la_route(method, path)
        if refus:
            if path.startswith("/api/"):
                self._json({"error": refus, "connexion": True}, 401)
            else:
                self.send_response(302)
                self.send_header("Location", "/espace.html")
                # Content-Length, et il n'est pas decoratif : on parle
                # HTTP/1.1, donc la connexion reste ouverte. Sans lui le
                # navigateur attend un corps qui ne viendra jamais et la page
                # tourne dans le vide — exactement l'ecran fige, sur CHAQUE
                # page, des que la serrure est mise.
                self.send_header("Content-Length", "0")
                self.end_headers()
            return

        if not path.startswith("/api/"):
            if method == "GET":
                self._static(path if path != "/" else "/simple.html")
            else:
                self.send_error(405, "methode non autorisee")
            return

        ctype = self.headers.get("Content-Type") or ""
        if method == "POST" and ctype.startswith("multipart/form-data"):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD * 2:
                self._json({"error": "fichier trop lourd"}, 413)
                return
            try:
                body = parse_multipart(self.rfile.read(length), ctype)
            except ApiError as exc:
                self._json({"error": exc.message}, exc.code)
                return
        else:
            body = self._read_body() if method in ("POST", "PUT", "PATCH") else {}
        req = Req(query, body, self.headers.get("Host"),
                  jeton=self._jeton_de_session(),
                  agent=self.headers.get("User-Agent"))
        for verb, rx, fn in ROUTES:
            if verb != method:
                continue
            m = rx.match(path)
            if m:
                try:
                    sortie = fn(req, *m.groups())
                    if req.cookie:
                        self._cookie_a_poser = req.cookie
                    self._json(sortie)
                except ApiError as exc:
                    self._json({"error": exc.message}, exc.code)
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)
                return
        self._json({"error": "route inconnue : %s %s" % (method, path)}, 404)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


def serve(host="127.0.0.1", port=8765):
    # La base saura se faire relancer si plus rien ne la lit. Sans ce
    # branchement, elle n'a aucun moyen de redemarrer le processus.
    db.SE_RELANCER = relancer_le_serveur
    db.init()
    db.set_setting("public_host", "%s:%d" % (host, port))
    os.makedirs(EXPORT_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    try:
        from . import comptes
        comptes.init()
        comptes.menage()
    except Exception as exc:
        print("comptes : %s" % exc, flush=True)
    try:
        from . import couverture
        couverture.init()
    except Exception as exc:
        print("couvertures : %s" % exc, flush=True)
    _prechauffer_agenda()
    _veiller_carnet()
    _veiller_bookings()
    _veiller_enrichir()
    _veiller_orphelins()
    return httpd


def _veiller_orphelins():
    """Les tournois sans club : Google cherche qui les organise.

    Une date sans organisateur est une date qu'on voit sans pouvoir ecrire.
    Deux cent quinze sont dans ce cas. Si Google est a plat, le veilleur
    s'arrete poliment et reprendra.
    """
    try:
        from . import orphelins
        orphelins.veiller()
    except Exception:
        pass


def _veiller_enrichir():
    """Les clubs muets deviennent joignables sans qu'on clique.

    Google retrouve leurs pages, util y lit les chiffres. Deux clubs par
    quart d'heure : la recherche ancree met une minute par club, et rien
    ne presse. Si Google n'est pas configure, le veilleur tourne a vide
    sans se plaindre.
    """
    try:
        from . import enrichir
        enrichir.veiller()
    except Exception:
        pass


def _veiller_bookings():
    """Quand quelqu'un dit oui, la date entre au calendrier toute seule.

    Toutes les dix minutes. Trois verrous : l'accord doit etre dans LEUR
    dernier message, la date doit venir de leur accord ou de la proposition
    de Julien, et l'evenement ne doit pas deja figurer au calendrier.
    """
    try:
        from . import booker
        booker.veiller()
    except Exception:
        pass


def _veiller_carnet():
    """Le carnet se releve tout seul, toutes les trois minutes.

    Julien enregistre un numero sur le terrain ; quand il rouvre l'ecran, la
    fiche est deja la. Il n'y a pas de bouton a chercher, pas de scan a
    lancer. Si le carnet est illisible on se tait : ce n'est pas une raison
    pour empecher le serveur de demarrer.
    """
    try:
        from . import carnet
        carnet.veiller()
    except Exception:
        pass


def _prechauffer_agenda():
    """Lit le calendrier en fond des le demarrage.

    Les six secondes d'Apple Events se paient une fois, pendant qu'on ouvre
    encore le navigateur, au lieu d'etre payees par le premier ecran qui
    demande les semaines. Si Calendrier.app n'est pas la, on se tait : ce
    n'est pas une raison pour empecher le serveur de demarrer.
    """
    def lire():
        try:
            agenda.lister_vite()
        except Exception:
            pass
    threading.Thread(target=lire, daemon=True).start()
