# -*- coding: utf-8 -*-
"""Le tunnel : ouvrir le CRM a Boby sans ouvrir le Terminal.

Trois choses se passent quand Julien clique, et dans cet ordre, parce que
l'ordre est la seule chose qui protege la base :

  1. la serrure. Posee AVANT, jamais apres. Un tunnel ouvert sur un CRM sans
     mot de passe donne 822 clubs et 600 conversations a qui trouve l'adresse.
  2. la verification. On interroge le CRM depuis le CRM, sans session, et on
     exige un refus. Si ca repond autre chose, on s'arrete et on referme :
     mieux vaut pas de tunnel qu'un tunnel sur une porte ouverte.
  3. cloudflared. Lance ici, sa sortie lue ligne a ligne pour en extraire
     l'adresse — c'est lui qui la choisit, elle change a chaque fois.

Rien ne demarre tout seul. La fonction ouvrir() n'est appelee que depuis la
route, c'est-a-dire depuis un clic.
"""

import json
import os
import platform
import re
import signal
import subprocess
import tarfile
import threading
import urllib.error
import urllib.request
from collections import deque

from . import db

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER = os.path.join(RACINE, "outils", "bin")
CHEMIN = os.path.join(DOSSIER, "cloudflared")

# L'adresse d'ou vient le binaire. Ecrite ici en clair, pas construite a la
# volee : on doit pouvoir lire d'un coup d'oeil ce que cette machine
# telecharge, et d'ou.
SOURCE = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
          "cloudflared-darwin-%s.tgz")

ADRESSE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# Au-dela, cloudflared n'a pas trouve son adresse et ne la trouvera pas.
ATTENTE_SECONDES = 40

_proc = None
_lignes = deque(maxlen=120)
_adresse = None
_depuis = None
_verrou = threading.Lock()


# -- Ou en est-on ------------------------------------------------------------

def _vivant():
    return _proc is not None and _proc.poll() is None


def _pids_dehors():
    """Les cloudflared qui tournent sans que ce soit nous qui les tenions.

    Le cas arrive pour de vrai : un tunnel lance a la main dans le Terminal.
    Si on l'ignorait, l'ecran afficherait « ferme, personne n'entre » alors
    que le CRM serait publie sur internet. Un ecran qui rassure a tort est
    pire que pas d'ecran du tout.
    """
    try:
        out = subprocess.run(["/usr/bin/pgrep", "-f", "cloudflared tunnel"],
                             capture_output=True, text=True, timeout=8)
    except Exception:
        return []
    nous = _proc.pid if _proc is not None else None
    pids = []
    for bout in (out.stdout or "").split():
        try:
            pid = int(bout)
        except ValueError:
            continue
        if pid != nous and pid != os.getpid():
            pids.append(pid)
    return pids


def _adresse_par_metrics(pid):
    """Demande son adresse a cloudflared lui-meme.

    Il ouvre un petit serveur local et sait repondre ce qu'il publie. C'est
    la seule facon de connaitre l'adresse d'un tunnel qu'on n'a pas lance
    soi-meme — sinon elle n'existe que dans la fenetre du Terminal.
    """
    try:
        out = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN",
                              "-a", "-p", str(pid)],
                             capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    ports = []
    for ligne in (out.stdout or "").splitlines()[1:]:
        bouts = ligne.split()
        if len(bouts) >= 9 and ":" in bouts[8]:
            queue = bouts[8].rsplit(":", 1)[-1]
            if queue.isdigit():
                ports.append(queue)
    for port in dict.fromkeys(ports):
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%s/quicktunnel" % port, timeout=5) as rep:
                nom = json.loads(rep.read().decode("utf-8")).get("hostname")
                if nom:
                    return "https://" + nom
        except Exception:
            continue
    return None


def etat():
    global _adresse
    dehors = [] if _vivant() else _pids_dehors()
    marche = _vivant() or bool(dehors)
    adresse = _adresse if _vivant() else None
    if not adresse and dehors:
        adresse = _adresse_par_metrics(dehors[0])
    return {
        "installe": os.path.isfile(CHEMIN) and os.access(CHEMIN, os.X_OK),
        "en_marche": marche,
        "a_nous": _vivant(),
        "dehors": bool(dehors),
        "adresse": adresse,
        "depuis": _depuis if _vivant() else None,
        "serrure": db.get_setting("ouvert_internet", "0") == "1",
        "source": SOURCE % _puce(),
        "lignes": list(_lignes)[-12:],
    }


def _puce():
    return "arm64" if platform.machine() == "arm64" else "amd64"


def _hote():
    return db.get_setting("public_host", "127.0.0.1:8765")


# -- Installer ---------------------------------------------------------------

def installer():
    """Pose cloudflared dans outils/bin. Appele au premier clic, jamais seul.

    On passe par curl : sur ce Mac, Python se traine un magasin de certificats
    a lui qui n'est pas toujours garni, alors que curl utilise celui du
    systeme. Un telechargement qui echoue pour une histoire de certificat
    ressemble a une panne de reseau, et on chercherait longtemps.
    """
    os.makedirs(DOSSIER, exist_ok=True)
    url = SOURCE % _puce()
    archive = os.path.join(DOSSIER, "cloudflared-telechargement.tgz")
    try:
        subprocess.run(["/usr/bin/curl", "-fL", "--max-time", "300",
                        "-o", archive, url],
                       check=True, capture_output=True, text=True, timeout=320)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("téléchargement refusé : %s"
                           % (exc.stderr or "").strip()[:200])
    except subprocess.TimeoutExpired:
        raise RuntimeError("téléchargement trop long — réseau ?")

    # On n'extrait pas l'archive en bloc : on va chercher le seul fichier qui
    # nous interesse et on l'ecrit nous-memes. Une archive peut contenir des
    # chemins qui remontent (../..) et extractall les suivrait.
    try:
        with tarfile.open(archive) as tar:
            membre = next((m for m in tar.getmembers()
                           if m.isfile()
                           and os.path.basename(m.name) == "cloudflared"), None)
            if not membre:
                raise RuntimeError("archive inattendue : pas de cloudflared dedans")
            flux = tar.extractfile(membre)
            with open(CHEMIN, "wb") as sortie:
                while True:
                    bout = flux.read(65536)
                    if not bout:
                        break
                    sortie.write(bout)
    finally:
        try:
            os.remove(archive)
        except OSError:
            pass
    os.chmod(CHEMIN, 0o755)
    db.log("tunnel", "cloudflared installé depuis %s" % url)
    return CHEMIN


def version():
    if not os.path.isfile(CHEMIN):
        return None
    try:
        out = subprocess.run([CHEMIN, "--version"], capture_output=True,
                             text=True, timeout=15)
        return (out.stdout or out.stderr or "").strip()[:120] or None
    except Exception:
        return None


# -- La serrure, et sa verification ------------------------------------------

def _poser_la_serrure():
    if not db.q1("SELECT id FROM comptes WHERE role = 'patron' AND actif = 1"):
        raise RuntimeError("créez d'abord VOTRE compte en « patron » : sans lui, "
                           "le CRM demanderait un mot de passe que personne "
                           "n'aurait")
    db.set_setting("ouvert_internet", "1")
    db.log("tunnel", "serrure posée avant l'ouverture du tunnel")


def _la_serrure_tient():
    """Interroge le CRM sans session. On exige un refus.

    C'est la seule verification qui vaille : pas « le reglage est a 1 », mais
    « une requete nue se fait vraiment jeter ».
    """
    url = "http://%s/api/clubs" % _hote()
    try:
        urllib.request.urlopen(url, timeout=10)
    except urllib.error.HTTPError as exc:
        return exc.code == 401, exc.code
    except Exception as exc:
        return False, str(exc)
    return False, 200


# -- Ouvrir et fermer --------------------------------------------------------

def _lire_la_sortie(proc):
    global _adresse
    for ligne in iter(proc.stdout.readline, ""):
        ligne = ligne.rstrip()
        if not ligne:
            continue
        _lignes.append(ligne[-300:])
        if not _adresse:
            trouve = ADRESSE.search(ligne)
            if trouve:
                _adresse = trouve.group(0)
                db.log("tunnel", "adresse publique : %s" % _adresse)


def ouvrir():
    """Serrure, verification, tunnel. Rend l'etat, adresse comprise."""
    global _proc, _adresse, _depuis
    with _verrou:
        if _vivant():
            return etat()

        # Un tunnel lance a la main tourne encore : on ne va pas en ouvrir un
        # second a cote. On reprend celui-la, sans le tuer — l'adresse qu'il
        # publie marche deja et quelqu'un l'a peut-etre.
        dehors = _pids_dehors()
        if dehors:
            _poser_la_serrure()
            return etat()

        _poser_la_serrure()
        tient, vu = _la_serrure_tient()
        if not tient:
            db.set_setting("ouvert_internet", "0")
            db.log("tunnel", "ouverture annulée : le CRM a répondu %s sans "
                             "mot de passe" % vu)
            raise RuntimeError(
                "la serrure ne tient pas : sans mot de passe, le CRM répond "
                "%s au lieu de 401. Rien n'a été ouvert." % vu)

        if not (os.path.isfile(CHEMIN) and os.access(CHEMIN, os.X_OK)):
            installer()

        _lignes.clear()
        _adresse = None
        _proc = subprocess.Popen(
            [CHEMIN, "tunnel", "--no-autoupdate", "--url",
             "http://%s" % _hote()],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        threading.Thread(target=_lire_la_sortie, args=(_proc,),
                         daemon=True).start()

        attendu = threading.Event()
        for _ in range(ATTENTE_SECONDES * 4):
            if _adresse or not _vivant():
                break
            attendu.wait(0.25)

        if not _adresse:
            fermer(enlever_serrure=True)
            raise RuntimeError(
                "cloudflared n'a pas donné d'adresse en %d secondes. "
                "La serrure a été enlevée, rien n'est ouvert.\n%s"
                % (ATTENTE_SECONDES, "\n".join(list(_lignes)[-4:])))

        _depuis = db_maintenant()
        db.set_setting("tunnel_adresse", _adresse)
        return etat()


def db_maintenant():
    from . import util
    return util.now_iso()


def fermer(enlever_serrure=True):
    """Ferme le tunnel. Enleve la serrure par defaut : plus de porte dehors,
    plus besoin de mot de passe a la maison."""
    global _proc, _adresse, _depuis
    with _verrou:
        if _vivant():
            _proc.terminate()
            try:
                _proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _proc.kill()
            db.log("tunnel", "tunnel fermé")
        # Fermer doit vraiment fermer. Un tunnel lance depuis le Terminal
        # n'est pas le notre, mais il publie le meme CRM : le laisser debout
        # ferait d'un bouton « Fermer l'accès » un mensonge.
        for pid in _pids_dehors():
            try:
                os.kill(pid, signal.SIGTERM)
                db.log("tunnel", "tunnel lancé à la main fermé (pid %d)" % pid)
            except OSError:
                pass
        _proc = None
        _adresse = None
        _depuis = None
        db.set_setting("tunnel_adresse", "")
        if enlever_serrure:
            db.set_setting("ouvert_internet", "0")
            db.log("tunnel", "serrure enlevée")
        return etat()


def au_revoir(garder=False):
    """Appele quand le CRM s'arrete ou se relance.

    garder=True, c'est une RELANCE (execv) : on lache la main sur le tunnel
    sans le tuer. Le processus suivant le retrouvera tout seul — _pids_dehors
    le voit, _adresse_par_metrics lui demande son adresse — et saura le
    fermer. L'adresse reste donc valable d'un bout a l'autre.

    Sans ca, la moindre mise a jour du code coupait le sourceur et obligeait
    a lui renvoyer une nouvelle adresse. On a fait l'aller-retour trois fois
    dans la meme heure.

    On ferme NOTRE tunnel : sans ca, un execv nous remplace, l'enfant survit
    sans personne pour le tenir et publie une adresse que le CRM ne sait plus
    fermer.

    Un tunnel lance a la main dans le Terminal, lui, on n'y touche pas : il
    appartient a sa fenetre, pas a nous. Et surtout on laisse la serrure en
    place — ce tunnel-la publie encore le CRM, l'enlever ouvrirait une porte
    sans mot de passe sur internet le temps du redemarrage.
    """
    global _proc, _adresse, _depuis
    if not _vivant():
        return
    if garder:
        db.log("tunnel", "relance du CRM : le tunnel reste ouvert")
        _proc = None
        _adresse = None
        _depuis = None
        return
    # Retenu AVANT de lacher _proc : juste apres l'avoir tue, le processus
    # peut encore apparaitre une fraction de seconde dans la liste du
    # systeme. Sans ce garde-fou, on croit qu'un tunnel etranger tourne
    # encore et on laisse la serrure mise — le CRM reclame alors un mot de
    # passe au prochain demarrage alors que plus rien n'est publie. C'est
    # arrive, et il a fallu venir l'enlever a la main.
    pid_a_nous = _proc.pid
    try:
        _proc.terminate()
        try:
            _proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _proc.kill()
        db.log("tunnel", "tunnel fermé (le CRM s'arrête)")
        _proc = None
        _adresse = None
        _depuis = None
        db.set_setting("tunnel_adresse", "")
        if not [p for p in _pids_dehors() if p != pid_a_nous]:
            db.set_setting("ouvert_internet", "0")
            db.log("tunnel", "serrure enlevée (plus rien n'est publié)")
    except Exception:
        pass
