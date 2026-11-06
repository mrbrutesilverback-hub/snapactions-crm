# -*- coding: utf-8 -*-
"""Les comptes : qui a le droit d'entrer, et ce qu'il voit.

Jusqu'ici le CRM n'avait aucune serrure, et c'etait defendable : il
n'ecoutait que sur 127.0.0.1, personne d'autre que le Mac mini ne le voyait.
A partir du moment ou il sort sur internet pour que le sourceur s'y connecte,
l'absence de serrure devient le probleme principal : cette base contient huit
cent vingt-deux clubs, six cents conversations WhatsApp et tous les numeros
du carnet.

D'ou deux regles, et elles ne se negocient pas :

  1. Un mot de passe n'est jamais ecrit tel quel. On garde l'empreinte d'un
     PBKDF2 a deux cent mille tours, avec un sel par compte. Meme en lisant
     le fichier de base, on ne retrouve pas le mot de passe.
  2. Un compte qui n'est pas « patron » ne voit QUE ses missions. Ce n'est
     pas un reglage d'affichage : les routes du CRM lui sont fermees, cote
     serveur, et il ne peut pas les atteindre en changeant l'adresse.

Le reste — blocage apres des essais rates, expiration des sessions — sert a
ce qu'une adresse publique ne devienne pas une porte qu'on force a loisir.
"""

import binascii
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets

from . import db, util

TOURS = 200000
ROLES = ("patron", "sourceur", "photographe")

# Au-dela, on fait attendre : six essais en quelques secondes, ce n'est pas
# quelqu'un qui cherche son mot de passe.
ESSAIS_MAX = 6
BLOCAGE_MINUTES = 15
# Une session dort au bout d'un mois. Le sourceur se reconnecte, ce n'est pas
# une page qu'on ouvre dix fois par jour.
SESSION_JOURS = 30


SCHEMA = """
CREATE TABLE IF NOT EXISTS comptes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    identifiant  TEXT NOT NULL UNIQUE,
    nom          TEXT,
    empreinte    TEXT NOT NULL,
    sel          TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'sourceur',
    team_id      INTEGER,
    actif        INTEGER NOT NULL DEFAULT 1,
    cree_le      TEXT NOT NULL,
    vu_le        TEXT,
    essais       INTEGER NOT NULL DEFAULT 0,
    bloque_jusqu TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    jeton     TEXT PRIMARY KEY,
    compte_id INTEGER NOT NULL,
    cree_le   TEXT NOT NULL,
    vu_le     TEXT,
    agent     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_compte ON sessions(compte_id);
"""


def init():
    """Cree les tables si elles manquent. Appele au demarrage."""
    conn = db.connect()
    conn.executescript(SCHEMA)
    conn.commit()


# -- Les mots de passe -------------------------------------------------------

def _empreinte(mot_de_passe, sel):
    return binascii.hexlify(hashlib.pbkdf2_hmac(
        "sha256", (mot_de_passe or "").encode("utf-8"),
        binascii.unhexlify(sel), TOURS)).decode("ascii")


def _solide(mot_de_passe):
    """Un mot de passe trop court se devine. Huit caracteres au minimum."""
    m = mot_de_passe or ""
    if len(m) < 8:
        return "il faut au moins huit caractères"
    if m.isdigit():
        return "pas uniquement des chiffres"
    return None


# -- Les comptes -------------------------------------------------------------

def _plat(identifiant):
    return re.sub(r"[^a-z0-9._-]", "", util.strip_accents(
        str(identifiant or "")).lower().strip())


def creer(identifiant, mot_de_passe, nom=None, role="sourceur", team_id=None):
    """Ouvre un compte. Le mot de passe n'est pas conserve, seule son empreinte."""
    ident = _plat(identifiant)
    if len(ident) < 3:
        raise ValueError("identifiant trop court")
    if role not in ROLES:
        raise ValueError("rôle inconnu : %s" % role)
    souci = _solide(mot_de_passe)
    if souci:
        raise ValueError("mot de passe : %s" % souci)
    if db.q1("SELECT id FROM comptes WHERE identifiant = ?", (ident,)):
        raise ValueError("cet identifiant existe déjà")
    sel = binascii.hexlify(os.urandom(16)).decode("ascii")
    cid = db.insert("comptes", {
        "identifiant": ident, "nom": (nom or identifiant or "").strip() or ident,
        "empreinte": _empreinte(mot_de_passe, sel), "sel": sel,
        "role": role, "team_id": int(team_id) if team_id else None,
        "actif": 1, "cree_le": util.now_iso(),
    })
    db.log("comptes", "compte « %s » créé (%s)" % (ident, role))
    return lire(cid)


def lire(compte_id):
    r = db.q1("SELECT id, identifiant, nom, role, team_id, actif, cree_le, vu_le "
              "FROM comptes WHERE id = ?", (int(compte_id),))
    return db.row_to_dict(r) if r else None


def lister():
    return db.rows_to_dicts(db.q(
        "SELECT c.id, c.identifiant, c.nom, c.role, c.team_id, c.actif, "
        "       c.cree_le, c.vu_le, t.name AS equipier, "
        "       (SELECT COUNT(*) FROM missions m WHERE m.compte_id = c.id) AS missions "
        "FROM comptes c LEFT JOIN team t ON t.id = c.team_id "
        "ORDER BY c.actif DESC, c.nom"))


def renommer(compte_id, identifiant=None, nom=None):
    """Change l'identifiant pour entrer, et/ou le nom affiche.

    Le mot de passe ne bouge pas : l'empreinte ne depend que du mot de passe
    et du sel, jamais de l'identifiant. Les sessions ouvertes non plus — on
    renomme quelqu'un, on ne le met pas dehors.
    """
    patch = {}
    if identifiant:
        ident = _plat(identifiant)
        if len(ident) < 3:
            raise ValueError("identifiant trop court")
        autre = db.q1("SELECT id FROM comptes WHERE identifiant = ? AND id <> ?",
                      (ident, int(compte_id)))
        if autre:
            raise ValueError("cet identifiant existe déjà")
        patch["identifiant"] = ident
    if nom is not None:
        patch["nom"] = (nom or "").strip() or None
    if patch:
        db.update("comptes", int(compte_id), patch)
        db.log("comptes", "compte %s renommé : %s" % (compte_id, patch))
    return lire(compte_id)


def changer_mot_de_passe(compte_id, mot_de_passe):
    souci = _solide(mot_de_passe)
    if souci:
        raise ValueError("mot de passe : %s" % souci)
    sel = binascii.hexlify(os.urandom(16)).decode("ascii")
    db.ex("UPDATE comptes SET empreinte = ?, sel = ?, essais = 0, "
          "bloque_jusqu = NULL WHERE id = ?",
          (_empreinte(mot_de_passe, sel), sel, int(compte_id)))
    # Changer de mot de passe ferme les sessions ouvertes ailleurs : c'est
    # tout l'interet d'en changer.
    db.ex("DELETE FROM sessions WHERE compte_id = ?", (int(compte_id),))
    db.log("comptes", "mot de passe changé pour le compte %s" % compte_id)
    return True


def verifier(compte_id, mot_de_passe):
    """Ce mot de passe est-il celui de ce compte ?

    Sans compteur d'essais ni blocage : ce n'est pas une porte d'entree, mais
    la question qu'on pose a quelqu'un DEJA entre, avant de le laisser
    changer son mot de passe. Sinon une session volee suffirait a mettre le
    vrai titulaire dehors.
    """
    r = db.q1("SELECT empreinte, sel FROM comptes WHERE id = ?",
              (int(compte_id),))
    if not r:
        return False
    return hmac.compare_digest(_empreinte(mot_de_passe, r["sel"]),
                               r["empreinte"])


def activer(compte_id, actif=True):
    db.ex("UPDATE comptes SET actif = ? WHERE id = ?",
          (1 if actif else 0, int(compte_id)))
    if not actif:
        db.ex("DELETE FROM sessions WHERE compte_id = ?", (int(compte_id),))
    return lire(compte_id)


def supprimer(compte_id):
    db.ex("DELETE FROM sessions WHERE compte_id = ?", (int(compte_id),))
    db.ex("DELETE FROM comptes WHERE id = ?", (int(compte_id),))
    return True


# -- Entrer et sortir --------------------------------------------------------

def _bloque(c):
    """Le compte est-il encore en pause apres trop d'essais ?"""
    jusqu = c.get("bloque_jusqu")
    if not jusqu:
        return 0
    fin = util.parse_dt(jusqu)
    if not fin:
        return 0
    reste = (fin - dt.datetime.now()).total_seconds()
    return max(0, int(reste))


def connexion(identifiant, mot_de_passe, agent=None):
    """Rend (jeton, compte) si ça colle. Lève ValueError sinon.

    Le message d'erreur ne dit jamais SI l'identifiant existe : sinon on
    apprend, un essai a la fois, quels comptes sont ouverts.
    """
    ident = _plat(identifiant)
    r = db.q1("SELECT * FROM comptes WHERE identifiant = ?", (ident,))
    c = db.row_to_dict(r) if r else None

    if c:
        reste = _bloque(c)
        if reste:
            raise ValueError("trop d'essais — réessayez dans %d minute(s)"
                             % max(1, reste // 60))

    bon = bool(c) and c.get("actif") and hmac.compare_digest(
        _empreinte(mot_de_passe, c["sel"]), c["empreinte"])

    if not bon:
        if c:
            essais = int(c.get("essais") or 0) + 1
            patch = {"essais": essais}
            if essais >= ESSAIS_MAX:
                patch["bloque_jusqu"] = (
                    dt.datetime.now() + dt.timedelta(minutes=BLOCAGE_MINUTES)
                ).isoformat(" ", "seconds")
                patch["essais"] = 0
                db.log("comptes", "compte « %s » mis en pause : %d essais ratés"
                       % (ident, ESSAIS_MAX))
            db.update("comptes", c["id"], patch)
        raise ValueError("identifiant ou mot de passe incorrect")

    jeton = binascii.hexlify(os.urandom(32)).decode("ascii")
    maintenant = util.now_iso()
    db.insert("sessions", {"jeton": jeton, "compte_id": c["id"],
                           "cree_le": maintenant, "vu_le": maintenant,
                           "agent": (agent or "")[:200]})
    db.update("comptes", c["id"], {"essais": 0, "bloque_jusqu": None,
                                   "vu_le": maintenant})
    db.log("comptes", "connexion de « %s »" % ident)
    return jeton, lire(c["id"])


def compte_de_session(jeton):
    """Le compte derriere ce jeton, ou None. Rafraichit la date de vue."""
    if not jeton or len(jeton) != 64:
        return None
    r = db.q1("SELECT s.jeton, s.cree_le, c.* FROM sessions s "
              "JOIN comptes c ON c.id = s.compte_id "
              "WHERE s.jeton = ? AND c.actif = 1", (jeton,))
    if not r:
        return None
    d = db.row_to_dict(r)
    nee = util.parse_dt(d.get("cree_le"))
    if nee and (dt.datetime.now() - nee).days > SESSION_JOURS:
        db.ex("DELETE FROM sessions WHERE jeton = ?", (jeton,))
        return None
    db.ex("UPDATE sessions SET vu_le = ? WHERE jeton = ?",
          (util.now_iso(), jeton))
    return {"id": d["id"], "identifiant": d["identifiant"], "nom": d["nom"],
            "role": d["role"], "team_id": d["team_id"]}


def deconnexion(jeton):
    if jeton:
        db.ex("DELETE FROM sessions WHERE jeton = ?", (jeton,))
    return True


def menage():
    """Jette les sessions dormantes. Appele au demarrage."""
    limite = (dt.datetime.now() - dt.timedelta(days=SESSION_JOURS)
              ).isoformat(" ", "seconds")
    db.ex("DELETE FROM sessions WHERE cree_le < ?", (limite,))


# ----------------------------------------------------------- la routine

# Le scan de 7 h 30 n'a pas de doigts : il ne peut pas taper un mot de passe.
# Il lui faut pourtant deux routes du serveur — relire WhatsApp et ouvrir les
# grilles de groupes — parce que le serveur, lance depuis le Terminal, porte
# des autorisations macOS que launchd n'a pas.
#
# On ne laisse PAS passer la boucle locale pour autant, et c'est le point
# important : cloudflared tourne sur ce Mac et parle lui aussi a 127.0.0.1.
# Une requete venue d'internet arrive donc avec exactement la meme adresse
# qu'une requete de la maison. « N'autoriser que le local » reviendrait a
# ouvrir le CRM au monde entier en croyant l'avoir ferme.
#
# Une cle, elle, ne se devine pas et ne traverse pas le tunnel. Elle vit dans
# la base, sur le disque du Mac : qui peut la lire pouvait deja tout lire.


def cle_routine(creer=True):
    """La cle du scan quotidien. Fabriquee au premier appel, puis gardee."""
    cle = db.get_setting("cle_routine", "") or ""
    if cle:
        return cle
    if not creer:
        return None
    cle = secrets.token_urlsafe(32)
    db.set_setting("cle_routine", cle)
    db.log("comptes", "Cle de la routine creee")
    return cle


def est_la_routine(presentee):
    """Vrai si l'en-tete presentee est bien celle du scan.

    Comparaison a temps constant : repondre plus vite sur un mauvais premier
    caractere laisserait deviner la cle, un caractere a la fois.
    """
    vraie = cle_routine(creer=False)
    if not vraie or not presentee:
        return False
    return hmac.compare_digest(str(presentee), str(vraie))
