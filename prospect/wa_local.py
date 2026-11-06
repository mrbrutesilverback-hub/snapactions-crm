# -*- coding: utf-8 -*-
"""Lecture du journal WhatsApp directement dans la base locale du Mac.

Aucun navigateur, aucune API Meta : WhatsApp Desktop garde ses conversations
dans un SQLite Core Data, et c'est la machine de Julien. On travaille sur une
copie, en lecture seule ; WhatsApp n'est jamais modifie.

On prend le correspondant, la date du dernier message, qui l'a envoye, le
nombre de non-lus — et les trois derniers messages ecrits. Ces textes-la, il a
fallu s'y resoudre : sans eux l'application sait qui a parle en dernier mais
pas ce qui a ete dit, et « a repondu » ne distingue pas un accord d'un refus.
Rien n'est jamais envoye, et ces textes ne quittent pas ce Mac.

macOS protege ces dossiers (TCC) : le processus qui appelle ce module doit
avoir l'Acces complet au disque. Lance depuis lancer.command, le serveur herite
de celui du Terminal.
"""
import datetime as dt
import glob
import os
import shutil
import sqlite3
import tempfile

# Core Data compte les secondes depuis le 1er janvier 2001.
EPOQUE_APPLE = dt.datetime(2001, 1, 1)
MAISON = os.path.expanduser("~")

# WhatsApp Business d'abord : c'est de la que partent les messages aux clubs.
CANDIDATS = [
    MAISON + "/Library/Group Containers/group.net.whatsapp.WhatsAppSMB.shared/ChatStorage.sqlite",
    MAISON + "/Library/Group Containers/group.net.whatsapp.WhatsAppSMB.shared/Message/ChatStorage.sqlite",
    MAISON + "/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite",
    MAISON + "/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Message/ChatStorage.sqlite",
]


class Inaccessible(Exception):
    """La base existe mais macOS refuse la lecture, ou elle est introuvable."""


def trouver_base():
    for chemin in CANDIDATS:
        if os.path.isfile(chemin):
            return chemin
    trouves = glob.glob(
        MAISON + "/Library/Group Containers/group.net.whatsapp*/**/ChatStorage.sqlite",
        recursive=True)
    return trouves[0] if trouves else None


# WhatsApp glisse des marques de direction invisibles autour des numeros.
INVISIBLES = "\u202a\u202b\u202c\u202d\u202e\u200e\u200f\u2066\u2067\u2068\u2069"


def _propre(texte):
    for c in INVISIBLES:
        texte = (texte or "").replace(c, "")
    return texte.replace("\xa0", " ").strip()


def _numero(jid):
    """Le numero international cache dans un identifiant WhatsApp, ou rien."""
    if not jid:
        return None
    gauche = jid.split("@")[0]
    if not gauche.isdigit() or not (8 <= len(gauche) <= 15):
        return None
    if "@" in jid and not jid.endswith(("@s.whatsapp.net", "@c.us")):
        return None
    return "+" + gauche


def _date(valeur):
    if valeur in (None, 0):
        return None
    try:
        return EPOQUE_APPLE + dt.timedelta(seconds=float(valeur))
    except (TypeError, ValueError, OverflowError):
        return None


def formes_jid(limite=12):
    """Les formes d'identifiant presentes dans la base (diagnostic).

    WhatsApp a plusieurs facons de designer un correspondant selon les
    versions : @s.whatsapp.net, @c.us, et depuis peu @lid, qui masque le
    numero. Savoir laquelle on a dit si on peut esperer un telephone.
    """
    base = trouver_base()
    if not base:
        raise Inaccessible("Base WhatsApp introuvable.")
    dossier = tempfile.mkdtemp(prefix="wa-jid-")
    copie = os.path.join(dossier, "j.sqlite")
    try:
        shutil.copy2(base, copie)
        for suffixe in ("-wal", "-shm"):
            if os.path.exists(base + suffixe):
                shutil.copy2(base + suffixe, copie + suffixe)
        conn = sqlite3.connect(copie)
        compte = {}
        for (jid,) in conn.execute("SELECT ZCONTACTJID FROM ZWACHATSESSION"):
            fin = (jid or "").split("@")[-1] if "@" in (jid or "") else "(sans @)"
            compte[fin] = compte.get(fin, 0) + 1
        conn.close()
    except (OSError, sqlite3.DatabaseError) as exc:
        raise Inaccessible(str(exc))
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    return compte


def schema(motif=""):
    """Tables et colonnes de la base (diagnostic ciblé)."""
    base = trouver_base()
    dossier = tempfile.mkdtemp(prefix="wa-sch-")
    copie = os.path.join(dossier, "s.sqlite")
    try:
        shutil.copy2(base, copie)
        for suffixe in ("-wal", "-shm"):
            if os.path.exists(base + suffixe):
                shutil.copy2(base + suffixe, copie + suffixe)
        conn = sqlite3.connect(copie)
        sortie = {}
        for (nom,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            if motif and motif.lower() not in nom.lower():
                continue
            cols = [c[1] for c in conn.execute("PRAGMA table_info(%s)" % nom)]
            n = conn.execute("SELECT COUNT(*) FROM %s" % nom).fetchone()[0]
            sortie[nom] = {"lignes": n, "colonnes": cols}
        conn.close()
    except (OSError, sqlite3.DatabaseError) as exc:
        raise Inaccessible(str(exc))
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    return sortie


def formes_identifiant():
    """Ce que contient ZCONTACTIDENTIFIER pour les conversations en @lid.

    Depuis que WhatsApp est passe aux identifiants « lid », le jid ne porte
    plus le numero. S'il est ailleurs dans la base, c'est ici qu'il faut
    regarder. On ne renvoie que la forme, jamais un numero.
    """
    base = trouver_base()
    dossier = tempfile.mkdtemp(prefix="wa-id-")
    copie = os.path.join(dossier, "i.sqlite")
    try:
        shutil.copy2(base, copie)
        for suffixe in ("-wal", "-shm"):
            if os.path.exists(base + suffixe):
                shutil.copy2(base + suffixe, copie + suffixe)
        conn = sqlite3.connect(copie)
        formes = {}
        for jid, ident, abid in conn.execute(
                "SELECT ZCONTACTJID, ZCONTACTIDENTIFIER, ZCONTACTABID "
                "FROM ZWACHATSESSION"):
            famille = (jid or "").split("@")[-1]
            if famille != "lid":
                continue
            if not ident:
                forme = "vide"
            elif "@" in ident:
                gauche, droite = ident.split("@", 1)
                forme = ("%d chiffres@%s" % (len(gauche), droite)
                         if gauche.isdigit() else "texte@%s" % droite)
            elif ident.isdigit():
                forme = "%d chiffres" % len(ident)
            else:
                forme = "texte"
            cle = "%s | abid=%s" % (forme, "oui" if abid else "non")
            formes[cle] = formes.get(cle, 0) + 1
        conn.close()
    except (OSError, sqlite3.DatabaseError) as exc:
        raise Inaccessible(str(exc))
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    return formes


def inventaire():
    """Ce que le Mac contient comme bases WhatsApp, et lesquelles sont lisibles.

    Sert au diagnostic : le seul processus qui a le droit de regarder est
    celui qui a l'Acces complet au disque, donc ce releve doit venir d'ici.
    """
    releve = []
    for motif in ("group.net.whatsapp.WhatsAppSMB.shared",
                  "group.net.whatsapp.WhatsApp.shared",
                  "group.net.whatsapp.WhatsAppSMB.private",
                  "group.net.whatsapp.WhatsApp.private"):
        racine = os.path.join(MAISON, "Library", "Group Containers", motif)
        if not os.path.isdir(racine):
            releve.append({"conteneur": motif, "etat": "absent"})
            continue
        try:
            trouves = glob.glob(os.path.join(racine, "**", "ChatStorage.sqlite"),
                                recursive=True)
        except OSError as exc:
            releve.append({"conteneur": motif, "etat": "illisible (%s)" % exc})
            continue
        for chemin in trouves:
            try:
                taille = os.path.getsize(chemin)
                etat = "lisible"
            except OSError:
                taille, etat = None, "refuse par macOS"
            releve.append({"conteneur": motif, "fichier": chemin,
                           "taille": taille, "etat": etat})
        if not trouves:
            releve.append({"conteneur": motif, "etat": "aucune ChatStorage.sqlite"})
    return releve


def qui_nous_a_lances():
    """Le programme responsable de ce processus, remonte de parent en parent.

    macOS n'accorde pas l'Acces complet au disque a un fichier mais au
    programme QUI LANCE, et les enfants en heritent. Savoir qui nous a lances,
    c'est donc savoir pourquoi la lecture passe ou non.
    """
    # Lance par SnapActions.app : la chaine des parents s'arrete a launchd
    # et ne montre que « Python ». Le lanceur se nomme donc lui-meme.
    annonce = (os.environ.get("SNAPACTIONS_LANCEUR") or "").strip()
    if annonce:
        return annonce
    try:
        import subprocess
        pid = os.getpid()
        for _ in range(8):
            r = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=5)
            ligne = (r.stdout or "").strip()
            if not ligne:
                return None
            tete, _, nom = ligne.partition(" ")
            nom = nom.strip()
            if not tete.strip().isdigit():
                return None
            pid = int(tete.strip())
            court = os.path.basename(nom)
            if pid <= 1:
                return court or None
            # Les enveloppes ne decident de rien : on remonte a travers elles.
            if court.lower() in ("bash", "sh", "zsh", "python", "python3",
                                 "login", "sleep", "env"):
                continue
            return court
    except Exception:  # noqa: BLE001 — un diagnostic ne doit jamais planter
        return None
    return None


def _pourquoi_refuse(exc):
    """Le vrai motif du refus, et quoi faire — pas une formule toute faite.

    L'ancien message accusait toujours les Reglages Systeme. Neuf fois sur
    dix l'autorisation est deja cochee : ce qui manque, c'est que le serveur
    tourne SOUS le programme qui la porte. Lance depuis l'application Claude
    ou orphelin de son Terminal, il est aveugle quoi qu'on coche.
    """
    lanceur = qui_nous_a_lances()
    base = "macOS refuse la lecture du journal WhatsApp (%s). " % exc.__class__.__name__
    if lanceur and "terminal" in lanceur.lower():
        return (base + "L'application tourne pourtant depuis Terminal : "
                "l'Acces complet au disque lui manque vraiment. Reglages "
                "Systeme > Confidentialite et securite > Acces complet au "
                "disque, ajoutez Terminal, puis relancez lancer.command.")
    if lanceur and "snapactions.app" in lanceur.lower():
        # Le cas normal depuis que le CRM est une application : il suffit de
        # la cocher une fois, et le Terminal n'est plus necessaire.
        return (base + "SnapActions n'a pas encore l'Acces complet au disque. "
                "Reglages Systeme > Confidentialite et securite > Acces "
                "complet au disque, ajoutez SnapActions, puis relancez "
                "l'application. C'est a faire une seule fois.")
    if lanceur:
        return (base + "L'application a ete lancee par « %s », qui n'a pas "
                "l'Acces complet au disque — et un programme ne transmet que "
                "les droits qu'il a. Lancez plutot SnapActions depuis le "
                "dossier Applications, apres lui avoir donne l'Acces complet "
                "au disque dans les Reglages Systeme." % lanceur)
    return (base + "L'application a perdu le Terminal qui l'avait lancee "
            "(sa fenetre a ete fermee), et avec lui l'Acces complet au "
            "disque. Relancez lancer.command depuis le Finder, et laissez "
            "la fenetre ouverte.")


def lire_journal(base=None):
    """Renvoie les conversations au format attendu par wa.enregistrer()."""
    # Trois messages suffisent a savoir ou on en est : ce qu'on a propose,
    # ce qu'ils ont repondu, et l'eventuelle precision derriere.
    DERNIERS = 3
    base = base or trouver_base()
    if not base:
        raise Inaccessible(
            "Base WhatsApp introuvable : WhatsApp Desktop n'a jamais ete "
            "synchronise sur ce Mac.")

    dossier = tempfile.mkdtemp(prefix="wa-journal-")
    copie = os.path.join(dossier, "journal.sqlite")
    try:
        shutil.copy2(base, copie)
        for suffixe in ("-wal", "-shm"):
            if os.path.exists(base + suffixe):
                shutil.copy2(base + suffixe, copie + suffixe)
    except (PermissionError, OSError) as exc:
        shutil.rmtree(dossier, ignore_errors=True)
        raise Inaccessible(_pourquoi_refuse(exc))

    conn = sqlite3.connect(copie)
    conn.row_factory = sqlite3.Row
    conversations = []
    try:
        # Colonnes lues : correspondant, nom affiche, non-lus, et pour le
        # dernier message sa date et le drapeau « envoye par moi ». Pas de texte.
        requete = """
            SELECT s.Z_PK               AS pk,
                   s.ZCONTACTJID        AS jid,
                   s.ZCONTACTIDENTIFIER AS identifiant,
                   s.ZPARTNERNAME       AS nom,
                   s.ZUNREADCOUNT       AS non_lus,
                   (SELECT m.ZMESSAGEDATE FROM ZWAMESSAGE m
                     WHERE m.ZCHATSESSION = s.Z_PK
                     ORDER BY m.ZMESSAGEDATE DESC LIMIT 1) AS derniere_date,
                   (SELECT m.ZISFROMME FROM ZWAMESSAGE m
                     WHERE m.ZCHATSESSION = s.Z_PK
                     ORDER BY m.ZMESSAGEDATE DESC LIMIT 1) AS de_moi
            FROM ZWACHATSESSION s
        """
        for r in list(conn.execute(requete)):
            quand = _date(r["derniere_date"])
            if not quand:
                continue
            derniers = []
            for m in conn.execute(
                    "SELECT ZTEXT, ZISFROMME, ZMESSAGEDATE FROM ZWAMESSAGE "
                    "WHERE ZCHATSESSION = ? AND ZTEXT IS NOT NULL AND ZTEXT <> '' "
                    "ORDER BY ZMESSAGEDATE DESC LIMIT ?", (r["pk"], DERNIERS)):
                d = _date(m["ZMESSAGEDATE"])
                derniers.append({
                    "de_moi": 1 if m["ZISFROMME"] else 0,
                    "quand": d.strftime("%Y-%m-%d %H:%M") if d else None,
                    "texte": _propre(m["ZTEXT"])[:600],
                })
            derniers.reverse()
            jid = r["jid"] or ""
            groupe = jid.endswith("@g.us")
            # Depuis le passage aux identifiants « lid », le jid ne porte plus
            # le numero : il est reste dans ZCONTACTIDENTIFIER.
            numero = _numero(jid) or _numero(r["identifiant"])
            conversations.append({
                "nom": _propre(r["nom"] or numero or ""),
                "telephone": numero,
                "quand": quand.strftime("%Y-%m-%d"),
                "sortant": 1 if r["de_moi"] else 0,
                "non_lus": int(r["non_lus"] or 0),
                "groupe": 1 if groupe else 0,
                "derniers": derniers,
            })
    except sqlite3.DatabaseError as exc:
        raise Inaccessible("Base illisible (chiffree par WhatsApp ?) : %s" % exc)
    finally:
        conn.close()
        shutil.rmtree(dossier, ignore_errors=True)

    return base, [c for c in conversations if c["nom"]]


# -- Les membres des groupes -------------------------------------------------
#
# WhatsApp garde, pour chaque groupe, la liste de ses participants avec leur
# numero : c'est la seule facon de retrouver le telephone d'un photographe
# qu'on n'a jamais eu en fiche. On ne lit QUE ce que la base contient — aucun
# numero n'est devine, reconstitue ni complete.

def membres(motif_groupe=""):
    """Les participants des groupes, avec leur numero. [] si rien ne colle.

    Rend [{groupe, nom, prenom, tel, admin, actif}]. Le nom est celui que le
    carnet d'adresses du Mac donne au participant ; quand il n'y en a pas,
    il reste vide et seul le numero est rendu.
    """
    base = trouver_base()
    dossier = tempfile.mkdtemp(prefix="wa-membres-")
    copie = os.path.join(dossier, "m.sqlite")
    try:
        shutil.copy2(base, copie)
        for suffixe in ("-wal", "-shm"):
            if os.path.exists(base + suffixe):
                shutil.copy2(base + suffixe, copie + suffixe)
        conn = sqlite3.connect(copie)
        conn.row_factory = sqlite3.Row
        lignes = conn.execute(
            "SELECT s.ZPARTNERNAME AS groupe, m.ZCONTACTNAME AS nom, "
            "       m.ZFIRSTNAME AS prenom, m.ZMEMBERJID AS jid, "
            "       m.ZISADMIN AS admin, m.ZISACTIVE AS actif "
            "FROM ZWAGROUPMEMBER m "
            "LEFT JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION "
            "ORDER BY s.ZPARTNERNAME, m.ZCONTACTNAME").fetchall()
        sortie = []
        for r in lignes:
            groupe = _propre(r["groupe"] or "")
            if motif_groupe and motif_groupe.lower() not in groupe.lower():
                continue
            tel = _numero(r["jid"])
            if not tel:
                continue
            sortie.append({
                "groupe": groupe or "(groupe sans nom)",
                "nom": _propre(r["nom"] or "") or None,
                "prenom": _propre(r["prenom"] or "") or None,
                "tel": tel,
                "admin": bool(r["admin"]), "actif": bool(r["actif"]),
            })
        conn.close()
    except (OSError, sqlite3.DatabaseError) as exc:
        raise Inaccessible(_pourquoi_refuse(exc))
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    return sortie


def groupes_connus():
    """Les groupes et combien de participants chacun porte."""
    compte = {}
    for m in membres():
        compte[m["groupe"]] = compte.get(m["groupe"], 0) + 1
    return sorted(({"groupe": g, "membres": n} for g, n in compte.items()),
                  key=lambda x: -x["membres"])
