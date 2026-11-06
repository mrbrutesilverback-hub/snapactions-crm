"""Acces SQLite : schema, migrations, helpers de requete."""
import datetime as _dt
import os
import secrets
import sqlite3
import threading

from . import util

DB_PATH = os.environ.get(
    "PROSPECT_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "crm.db"),
)

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS clubs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    name_key        TEXT,
    sport           TEXT,
    city            TEXT,
    postal_code     TEXT,
    dept            TEXT,
    country         TEXT DEFAULT 'FR',
    website         TEXT,
    email           TEXT,
    phone           TEXT,
    address         TEXT,
    status          TEXT NOT NULL DEFAULT 'prospect',
    priority        INTEGER DEFAULT 2,
    source          TEXT,
    source_url      TEXT,
    notes           TEXT,
    tags            TEXT,
    do_not_contact  INTEGER NOT NULL DEFAULT 0,
    opt_out_at      TEXT,
    opt_out_reason  TEXT,
    last_contact_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clubs_dept   ON clubs(dept);
CREATE INDEX IF NOT EXISTS idx_clubs_status ON clubs(status);
CREATE INDEX IF NOT EXISTS idx_clubs_key    ON clubs(name_key);

CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id         INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    first_name      TEXT,
    last_name       TEXT,
    role            TEXT,
    email           TEXT,
    phone           TEXT,
    phone2          TEXT,
    handle          TEXT,
    channel         TEXT DEFAULT 'email',
    notes           TEXT,
    source          TEXT,
    source_url      TEXT,
    do_not_contact  INTEGER NOT NULL DEFAULT 0,
    opt_out_at      TEXT,
    last_contact_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_club  ON contacts(club_id);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone);

CREATE TABLE IF NOT EXISTS interactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id     INTEGER REFERENCES clubs(id) ON DELETE CASCADE,
    contact_id  INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL DEFAULT 'note',
    direction   TEXT NOT NULL DEFAULT 'out',
    subject     TEXT,
    body        TEXT,
    outcome     TEXT,
    occurred_at TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inter_club ON interactions(club_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id     INTEGER REFERENCES clubs(id) ON DELETE CASCADE,
    contact_id  INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    draft_id    INTEGER REFERENCES drafts(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    notes       TEXT,
    kind        TEXT DEFAULT 'relance',
    due_at      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    done_at     TEXT,
    sequence    TEXT,
    step        INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_due    ON tasks(status, due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_club   ON tasks(club_id);

CREATE TABLE IF NOT EXISTS tournaments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    sport         TEXT,
    level         TEXT,
    start_date    TEXT,
    end_date      TEXT,
    city          TEXT,
    postal_code   TEXT,
    dept          TEXT,
    venue         TEXT,
    organizer     TEXT,
    club_id       INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    url           TEXT,
    source        TEXT,
    external_id   TEXT,
    fingerprint   TEXT UNIQUE,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'new',
    discovered_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tourn_start ON tournaments(start_date);
CREATE INDEX IF NOT EXISTS idx_tourn_dept  ON tournaments(dept);

CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id       INTEGER REFERENCES clubs(id) ON DELETE CASCADE,
    contact_id    INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    tournament_id INTEGER REFERENCES tournaments(id) ON DELETE SET NULL,
    channel       TEXT NOT NULL DEFAULT 'email',
    to_addr       TEXT,
    subject       TEXT,
    body          TEXT NOT NULL,
    template_key  TEXT,
    reason        TEXT,
    status        TEXT NOT NULL DEFAULT 'draft',
    created_by    TEXT DEFAULT 'agent',
    review_note   TEXT,
    created_at    TEXT NOT NULL,
    approved_at   TEXT,
    sent_at       TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status, created_at DESC);

CREATE TABLE IF NOT EXISTS templates (
    key        TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    channel    TEXT NOT NULL DEFAULT 'email',
    subject    TEXT,
    body       TEXT NOT NULL,
    notes      TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    url         TEXT,
    config      TEXT,
    sport       TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_status TEXT,
    last_count  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone       TEXT,
    email       TEXT,
    role        TEXT DEFAULT 'photographe',
    sports      TEXT,
    zones       TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    sports      TEXT,
    notes       TEXT,
    position    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS missions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER REFERENCES tournaments(id) ON DELETE SET NULL,
    crew_id       INTEGER REFERENCES crews(id) ON DELETE SET NULL,
    title         TEXT NOT NULL,
    starts_at     TEXT,
    place         TEXT,
    body          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',
    assigned      TEXT,
    created_at    TEXT NOT NULL,
    posted_at     TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status, starts_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS wa_chats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cle         TEXT UNIQUE NOT NULL,
    nom         TEXT NOT NULL,
    phone       TEXT,
    club_id     INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    groupe      INTEGER NOT NULL DEFAULT 0,
    apercu      TEXT,
    dernier_le  TEXT,
    sortant     INTEGER,
    non_lus     INTEGER NOT NULL DEFAULT 0,
    ecarte      INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    derniers    TEXT,
    note_le     TEXT,
    etape_auto  TEXT,
    etape_main  TEXT,
    etape_le    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wa_club ON wa_chats(club_id);

CREATE TABLE IF NOT EXISTS journee_ecarts (
    cle      TEXT PRIMARY KEY,
    jusquau  TEXT NOT NULL,
    motif    TEXT,
    pose_le  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runlog (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT,
    message    TEXT,
    created_at TEXT NOT NULL
);
"""


def _ecarter_le_journal():
    """Met de cote un journal WAL desynchronise, sans rien detruire.

    Le 1er novembre, l'application a refuse de demarrer pendant des heures
    sur « database disk image is malformed » : le fichier etait parfaitement
    sain — c'est son journal d'ecriture qui ne lui correspondait plus, a
    force d'arrets brutaux. Toute connexion neuve retombait dessus, donc
    rouvrir ne servait a rien et l'application restait morte.

    On DEPLACE le journal, on ne l'efface pas : s'il contenait quelque chose,
    il reste sur le disque, dates, et on peut y revenir.
    """
    import shutil
    quand = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    ecartes = []
    for suffixe in ("-wal", "-shm"):
        chemin = DB_PATH + suffixe
        if os.path.exists(chemin):
            cible = "%s.casse-%s%s" % (DB_PATH, quand, suffixe)
            try:
                shutil.move(chemin, cible)
                ecartes.append(os.path.basename(cible))
            except OSError:
                pass
    return ecartes


def _ouvrir_vraiment():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect():
    """Connexion par thread (le serveur HTTP est multi-thread).

    Si l'ouverture echoue sur une corruption, c'est presque toujours le
    journal : on l'ecarte et on reessaie une fois. L'application repart au
    lieu de rester bloquee jusqu'a ce que quelqu'un vienne la depanner.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        try:
            conn = _ouvrir_vraiment()
        except sqlite3.DatabaseError as exc:
            if not _connexion_a_jeter(exc):
                raise
            ecartes = _ecarter_le_journal()
            conn = _ouvrir_vraiment()      # si ca casse encore, c'est reel
            try:
                conn.execute("PRAGMA integrity_check").fetchone()
                log("base", "journal d'écriture écarté (%s) : la base est saine, "
                            "l'application repart" % ", ".join(ecartes or ["rien"]))
            except sqlite3.DatabaseError:
                pass
        _local.conn = conn
    return conn


# Colonnes ajoutees apres coup. Elles ont longtemps ete posees a la main
# directement dans la base : une reinstallation repartait donc sans elles et
# l'application tombait au premier SELECT. On les declare ici, et init() les
# ajoute si elles manquent.
COLONNES_TARDIVES = {
    "tournaments": [
        # 1 = le lieu est corrobore par la fiche elle-meme (code postal, ou nom
        # de commune present dans le texte). 0 = il repose sur une transcription
        # que personne n'a recoupee. NULL = pas encore examine.
        ("lieu_verifie", "INTEGER"),
        # Temps de route depuis Hyeres, le second point d'appui. Calcule a
        # l'entree en base : le recalculer a chaque affichage demandait une
        # requete reseau par fiche et faisait expirer la page.
        ("hyeres_min", "INTEGER"),
        # Le nom du fichier de l'affiche, range dans a-traiter/traites/.
        # Sans ce lien, l'image etait conservee mais introuvable : on relisait
        # une fiche sans jamais pouvoir verifier ce qui etait ecrit dessus.
        ("affiche", "TEXT"),
        # Quand on a demande son organisateur, et ce que ca a donne.
        ("orga_cherche_le", "TEXT"),
        ("orga_cherche_etat", "TEXT"),
        ("orga_cherche_essais", "INTEGER"),
        # « pour_moi » ou « reseau » : qui ira shooter cette date.
        ("portee", "TEXT"),
    ],
    "missions": [
        # A quel compte cette mission est confiee. Sans ce lien, l'espace du
        # sourceur n'aurait aucun moyen de savoir ce qui est a lui.
        ("compte_id", "INTEGER"),
    ],
    "clubs": [
        # Quand on a demande ses pages a Google. Sans cette date, le
        # veilleur repasserait eternellement sur les memes clubs muets.
        ("contact_cherche_le", "TEXT"),
        ("contact_cherche_etat", "TEXT"),
        ("contact_cherche_essais", "INTEGER"),
        ("partenaire", "INTEGER NOT NULL DEFAULT 0"),
        ("partenaire_depuis", "TEXT"),
        ("partenaire_note", "TEXT"),
        ("logo", "TEXT"),          # l'extension du fichier stocke, ou NULL
        # Un relais n'est pas un club : il n'organise pas de tournoi, il parle
        # aux parents. Kidiklik Cote d'Azur revendique 870 000 visiteurs et
        # 37 300 familles sur les reseaux — c'est exactement le public qui
        # achete les photos. On les range donc a part, mais dans la meme table
        # que les clubs, pour qu'ils heritent de tout ce qui existe deja :
        # messages, relances, suivi des echanges, recherche de contact.
        ("relais", "INTEGER NOT NULL DEFAULT 0"),
        ("relais_type", "TEXT"),        # media | communaute | institutionnel
        ("relais_role", "TEXT"),        # ce que ca apporte, en une phrase
        ("relais_audience", "TEXT"),    # l'audience annoncee, telle qu'annoncee
        ("relais_priorite", "INTEGER NOT NULL DEFAULT 0"),
        # La raison d'ecrire a CELUI-LA, ecrite comme une phrase. Le
        # descriptif ne peut pas en tenir lieu : « je vous ecris parce que le
        # plus gros media sorties enfants de la region » ne veut rien dire.
        ("relais_accroche", "TEXT"),
    ],
    "wa_chats": [
        # Une phrase qui dit ou en est le dossier, ecrite par le modele local
        # a partir des derniers messages. Elle est STOCKEE : la calculer a
        # chaque ouverture de page couterait trois secondes par carte.
        ("resume", "TEXT"),
        ("resume_le", "TEXT"),
        ("resume_source", "TEXT"),   # l'empreinte du fil resume, pour ne pas refaire
        # La reponse proposee, ecrite par le modele local d'apres tout le fil.
        # Meme regle que le resume : stockee avec l'empreinte du fil, refaite
        # seulement quand la conversation bouge.
        ("reponse_ia", "TEXT"),
        ("reponse_source", "TEXT"),
        # Le tri des numeros ramasses sur le terrain : « booker » (quelqu'un
        # qui peut donner du travail), « photographe » (du metier, a recruter),
        # « non » (sans suite). Vide tant que Julien n'a pas tranche.
        ("tri", "TEXT"),
        ("tri_le", "TEXT"),
    ],
}


def _balayer_orphelins(conn):
    """Les lignes qui pointent vers un club disparu.

    Les cles etrangeres sont actives et la cascade fonctionne pour le CRM.
    Mais une operation coupee en plein vol — le serveur redemarre tout seul
    quand le code change — a laisse trois taches derriere un club qui
    n'existait plus. Elles ne se voyaient nulle part : les ecrans joignent
    sur clubs, donc elles etaient invisibles et eternelles.
    """
    for table in ("tasks", "drafts", "contacts", "interactions"):
        try:
            conn.execute(
                "DELETE FROM %s WHERE club_id IS NOT NULL "
                "AND club_id NOT IN (SELECT id FROM clubs)" % table)
        except Exception:
            pass
    for table in ("tournaments", "wa_chats", "rencontres"):
        try:
            conn.execute(
                "UPDATE %s SET club_id = NULL WHERE club_id IS NOT NULL "
                "AND club_id NOT IN (SELECT id FROM clubs)" % table)
        except Exception:
            pass


def _completer_colonnes(conn):
    for table, colonnes in COLONNES_TARDIVES.items():
        existantes = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        for nom, declaration in colonnes:
            if nom not in existantes:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s"
                             % (table, nom, declaration))


def init():
    conn = connect()
    conn.executescript(SCHEMA)
    _completer_colonnes(conn)
    _balayer_orphelins(conn)
    conn.commit()
    if not get_setting("feed_token"):
        set_setting("feed_token", secrets.token_urlsafe(18))
    for key, default in DEFAULT_SETTINGS.items():
        if get_setting(key) is None:
            set_setting(key, default)
    seed_templates()
    seed_crews()
    return conn


DEFAULT_SETTINGS = {
    "my_name": "",
    "my_org": "",
    "my_email": "",
    "my_phone": "",
    "my_signature": "",
    "sport": "",
    "pitch": "",
    "relance_j": "4,10,21",          # cadence de relance en jours
    "quiet_days": "sat,sun",          # jours ou on ne programme pas de relance
    "auto_draft": "1",                # generer des brouillons automatiquement
    "auto_send": "0",                 # verrou : jamais d'envoi automatique
    "match_radius": "neighbours",     # same_dept | neighbours | region | all
    "lead_days_min": "10",            # fenetre de contact avant un tournoi
    "lead_days_max": "75",
}


# --------------------------------------------------------------------------
# Helpers generiques
# --------------------------------------------------------------------------

# Le 9 octobre, toute l'application a repondu « database disk image is
# malformed » sur chaque appel, alors que le fichier etait parfaitement sain :
# PRAGMA integrity_check disait « ok » et les 446 tournois se lisaient sans
# broncher avec l'outil sqlite3. C'etait la connexion du serveur qui etait
# cassee, pas les donnees — un redemarrage a tout remis d'aplomb.
#
# La cause la plus plausible : /api/redemarrer relance par os.execv, qui
# remplace le processus EN CONSERVANT les descripteurs ouverts, dont ceux de
# SQLite et de son fichier de memoire partagee (-shm). La routine de 7 h 30
# appelle ce redemarrage tous les matins ; au bout de quelques jours, le
# nouveau processus retombe sur un etat partage incoherent.
#
# Deux reponses : fermer les connexions avant de relancer (voir web.py), et
# ici, savoir se rattraper. Une base vraiment corrompue le redira au second
# essai ; une connexion fatiguee, elle, repart.

_CORRUPTION = ("malformed", "not a database", "disk image", "database is locked")


def _connexion_a_jeter(exc):
    return any(m in str(exc).lower() for m in _CORRUPTION)


def _rouvrir():
    """Jette la connexion de ce thread et en ouvre une neuve."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None
    return connect()


def fermer_toutes():
    """Ferme la connexion de ce thread, avant un redemarrage par execv.

    On ne peut fermer que la sienne — les autres threads ont la leur — mais
    c'est celle du thread qui relance, et le checkpoint du journal WAL qui
    l'accompagne suffit a laisser le fichier dans un etat propre.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        return False
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError:
        pass
    try:
        conn.close()
    except Exception:
        pass
    _local.conn = None
    return True


def ranger_avant_de_partir():
    """Vide le journal dans la base avant que le processus s'arrete.

    C'est la cause de la panne : l'application etait tuee en plein travail,
    le journal restait a moitie ecrit, et la base suivante ne le reconnaissait
    plus. Un checkpoint a l'arret suffit a ce que le fichier soit toujours
    complet et le journal toujours vide.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        try:
            conn = _ouvrir_vraiment()
        except sqlite3.DatabaseError:
            return False
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    except sqlite3.DatabaseError:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None
    return True


def sauvegarder(garde=7):
    """Une copie datee de la base, et on garde les sept dernieres.

    Il n'en existait aucune. Le 1er novembre, l'application a refuse sa base
    pendant des heures : elle s'est averee saine, mais si elle ne l'avait pas
    ete, huit cents clubs, treize cents dates et toutes les conversations
    partaient avec. Une copie par jour coute quatre megaoctets.

    On passe par l'API de sauvegarde de SQLite, pas par une copie de fichier :
    elle sait prendre une image coherente d'une base en cours d'ecriture.
    """
    dossier = os.path.join(os.path.dirname(DB_PATH), "sauvegardes")
    os.makedirs(dossier, exist_ok=True)
    cible = os.path.join(dossier, "crm-%s.db" % _dt.date.today().isoformat())
    try:
        source = connect()
        copie = sqlite3.connect(cible)
        with copie:
            source.backup(copie)
        copie.close()
    except (sqlite3.DatabaseError, OSError) as exc:
        log("base", "sauvegarde impossible : %s" % exc)
        return None

    # On ne garde que les plus recentes : une sauvegarde de trois semaines
    # ne sert plus a rien, et le dossier grossirait sans fin.
    try:
        vieilles = sorted(n for n in os.listdir(dossier)
                          if n.startswith("crm-") and n.endswith(".db"))
        for n in vieilles[:-int(garde)]:
            os.remove(os.path.join(dossier, n))
    except OSError:
        pass
    return cible


# Pose par le serveur au demarrage : comment se relancer. Voir plus bas
# pourquoi c'est parfois le seul remede.
SE_RELANCER = None
_derniere_relance = [0.0]


def _tenter(action):
    """Execute, et recommence une fois sur une connexion neuve si besoin."""
    try:
        return action(connect())
    except sqlite3.DatabaseError as exc:
        if not _connexion_a_jeter(exc):
            raise          # une vraie erreur SQL ne se reessaie pas
        log_silencieux = getattr(_local, "_deja_rouvert", False)
        _local._deja_rouvert = True
        try:
            out = action(_rouvrir())
        except sqlite3.DatabaseError as encore:
            # Une connexion neuve ne lit toujours pas : ce n'est donc pas la
            # connexion. Dans un processus, toutes partagent le meme fichier
            # de memoire partagee (-shm) ; s'il est casse, en rouvrir dix
            # n'y change rien. Seul un processus neuf repart proprement.
            #
            # Sans ca, le CRM passe la nuit a repondre « database disk image
            # is malformed » sur chaque page, alors que le fichier est
            # parfaitement sain — c'est arrive, et il a fallu venir le
            # relancer a la main.
            if _connexion_a_jeter(encore) and SE_RELANCER:
                import time
                if time.time() - _derniere_relance[0] > 120:
                    _derniere_relance[0] = time.time()
                    try:
                        print("Base illisible meme sur une connexion neuve : "
                              "je relance le serveur.", flush=True)
                        SE_RELANCER()
                    except Exception:
                        pass
            raise
        finally:
            _local._deja_rouvert = log_silencieux
        return out


def _lire(sql, params, une_seule):
    """Lit, puis RELACHE la lecture. Le « puis » n'est pas un detail.

    En WAL, une lecture commencee et jamais finie fige la vue de la base
    pour cette connexion : elle continue de voir le fichier tel qu'il etait
    au debut de cette lecture. q1() ne prenait qu'une ligne et abandonnait
    le curseur en route — la lecture restait ouverte, et la connexion
    gardait sa photo.

    Tant que le serveur est seul a ecrire, ca ne se voit pas : chaque
    ecriture passe par la meme connexion et rafraichit tout. Mais qu'un
    autre programme ecrive dans la base — une commande lancee dans le
    Terminal — et le serveur ne le voit jamais. C'est comme ca qu'un compte
    renomme et son mot de passe refait sont restes invisibles, avec un
    ecran de connexion qui refusait le bon mot de passe sans meme compter
    l'essai.
    """
    def faire(c):
        cur = c.execute(sql, params)
        try:
            return cur.fetchone() if une_seule else cur.fetchall()
        finally:
            cur.close()
    return _tenter(faire)


def q(sql, params=()):
    return _lire(sql, params, False)


def q1(sql, params=()):
    return _lire(sql, params, True)


def ex(sql, params=()):
    def faire(conn):
        cur = conn.execute(sql, params)
        conn.commit()
        return cur
    return _tenter(faire)


def insert(table, data):
    data = {k: v for k, v in data.items() if v is not None}
    cols = ", ".join(data)
    marks = ", ".join("?" for _ in data)
    cur = ex("INSERT INTO %s (%s) VALUES (%s)" % (table, cols, marks), tuple(data.values()))
    return cur.lastrowid


def update(table, row_id, data):
    data = dict(data)
    data.pop("id", None)
    if not data:
        return 0
    sets = ", ".join("%s = ?" % k for k in data)
    cur = ex("UPDATE %s SET %s WHERE id = ?" % (table, sets), tuple(data.values()) + (row_id,))
    return cur.rowcount


def delete(table, row_id):
    return ex("DELETE FROM %s WHERE id = ?" % table, (row_id,)).rowcount


def row_to_dict(row):
    return dict(row) if row is not None else None


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def get_setting(key, default=None):
    row = q1("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    ex("INSERT INTO settings (key, value) VALUES (?, ?) "
       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def all_settings():
    return {r["key"]: r["value"] for r in q("SELECT key, value FROM settings")}


def log(kind, message):
    insert("runlog", {"kind": kind, "message": message, "created_at": util.now_iso()})


# --------------------------------------------------------------------------
# Templates par defaut
# --------------------------------------------------------------------------

DEFAULT_TEMPLATES = [
    {
        "key": "premier_contact",
        "label": "Premier contact (suite forum)",
        "channel": "email",
        "subject": "{club} — suite à nos échanges sur {source}",
        "body": """Bonjour {prenom},

On a échangé sur {source} à propos de {sujet}. Je me permets de revenir vers vous.

{pitch}

Auriez-vous 15 minutes cette semaine ou la suivante pour en parler ? Je m'adapte à vos horaires.

{signature}""",
        "notes": "À envoyer dans les 48 h après l'échange forum, tant que c'est frais.",
    },
    {
        "key": "relance_1",
        "label": "Relance 1 — courte",
        "channel": "email",
        "subject": "Re: {club}",
        "body": """Bonjour {prenom},

Je remonte mon message, au cas où il serait passé à la trappe.

Toujours partant pour 15 minutes ? Si le timing n'est pas bon, dites-le moi simplement, je repasserai plus tard dans la saison.

{signature}""",
        "notes": "Courte, sans reproche, avec une porte de sortie explicite.",
    },
    {
        "key": "relance_2",
        "label": "Relance 2 — dernière",
        "channel": "email",
        "subject": "Re: {club}",
        "body": """Bonjour {prenom},

Dernière relance de ma part, promis — je ne veux pas encombrer votre boîte.

Si le sujet revient sur la table du côté de {club}, vous avez mes coordonnées. Bonne saison à vous.

{signature}""",
        "notes": "Ferme la boucle proprement. Le club passe en 'dormant' ensuite.",
    },
    {
        "key": "tournoi_avant",
        "label": "Tournoi à venir — accroche contextuelle",
        "channel": "email",
        "subject": "{tournoi} — un coup de main pour l'organisation ?",
        "body": """Bonjour {prenom},

J'ai vu passer {tournoi} : {tournoi_date}, à {tournoi_ville}. C'est dans {tournoi_j} jours.

{pitch}

Si l'organisation vous prend du temps cette année, autant en parler avant que le rush ne commence. 15 minutes suffisent pour voir si ça colle.

{signature}""",
        "notes": "Déclencheur : un tournoi détecté dans la zone du club. Idéal 3 à 8 semaines avant.",
    },
    {
        "key": "tournoi_aviron",
        "label": "Aviron et voile — sur l'eau",
        "channel": "email",
        "subject": "{tournoi_intitule} — une équipe photo sur l'eau ?",
        "body": """Bonjour,

Nous sommes photographes sportifs et développons actuellement SnapActions.

Nous serions intéressés pour couvrir votre {tournoi_intitule}, {tournoi_jour}.

Le principe est simple : nous photographions {nautique_qui} tout au long de {aviron_epreuve}, au départ, sur le parcours et à l'arrivée. Ensuite, chacun peut retrouver facilement ses photos grâce à notre galerie intelligente.

L'organisation n'a aucun frais : le service est financé par les participants qui souhaitent acheter leurs photos.

De votre côté, vous recevez une sélection de 30 à 40 photos, selon la taille de l'événement, libres d'utilisation pour la communication de votre club.

Vous trouverez notre plaquette en pièce jointe pour découvrir le concept et son fonctionnement.

Si vous êtes ouverts à accueillir une équipe de photographes sur l'événement, nous serions ravis d'échanger avec vous et de vous présenter rapidement notre fonctionnement. L'idéal, si c'est envisageable, serait que je monte avec vous sur un bateau de sécurité pendant {aviron_epreuve} : c'est de là que se font les meilleures images, au plus près des équipages.

Sportivement,
{my_name}
{my_org}""",
        "notes": "Le message de Julien pour l'aviron de mer. « {aviron_epreuve} » devient randonnée, régate, course ou épreuve selon la date. L'aviron en salle (indoor, au sol) garde le message ordinaire.",
    },
    {
        "key": "relais_premier",
        "label": "Relais — médias et communautés de parents",
        "channel": "email",
        "subject": "{club} — des photos et un jeu-concours pour vos lecteurs ?",
        "body": """Bonjour,

Je suis Julien, photographe sportif sur la Côte d'Azur. Je développe SnapActions : nous couvrons les tournois et les événements sportifs des clubs de la région, et les parents retrouvent ensuite facilement les photos de leur enfant grâce à notre galerie intelligente.

Je vous écris parce que {relais_pourquoi}

Ce que je peux vous apporter, concrètement :

– des photos libres de droits pour vos articles et vos réseaux, prises sur les événements que nous couvrons ;
– un jeu-concours pour votre audience, avec une séance photo offerte à gagner ;
– un relais de vos propres contenus auprès des clubs que nous suivons.

Si le principe vous intéresse, dites-moi simplement comment vous préférez travailler avec un partenaire comme nous — et je m'adapte.

Vous trouverez notre plaquette en pièce jointe.

Bien à vous,
{my_name}
{my_org}
{my_phone}""",
        "notes": "Premier contact avec un média, une communauté de parents ou un relais institutionnel. « {relais_pourquoi} » se remplit tout seul selon le type de relais. Ce ne sont pas des clubs : on ne leur propose pas une couverture, on leur propose un échange de visibilité.",
    },
    {
        "key": "tournoi_apres",
        "label": "Après le tournoi — retour d'expérience",
        "channel": "email",
        "subject": "{tournoi} — comment ça s'est passé ?",
        "body": """Bonjour {prenom},

{tournoi} vient de se terminer, j'espère que ça s'est bien passé pour {club}.

C'est souvent juste après un tournoi qu'on voit ce qui a coincé côté organisation. Si vous voulez en discuter à froid, je suis dispo.

{signature}""",
        "notes": "À envoyer 3 à 7 jours après. Le club a encore les irritants en tête.",
    },
    {
        "key": "sms_relance",
        "label": "SMS — relance courte",
        "channel": "sms",
        "subject": None,
        "body": """Bonjour {prenom}, {my_name} ({my_org}). Je vous ai écrit au sujet de {club}, sans certitude que le mail soit bien arrivé. Auriez-vous 15 min cette semaine ? Merci !""",
        "notes": "Uniquement si le contact a donné son numéro lui-même. Jamais en premier contact.",
    },
    {
        "key": "studio_club",
        "label": "Shooting studio mobile — adhérents du club",
        "channel": "email",
        "subject": "{club} : un shooting photo studio pour vos adhérents ?",
        "body": """Bonjour {prenom},

Nous sommes photographes sportifs et développons actuellement SnapActions.

Nous venons d'installer notre studio photo mobile au gymnase du Basket Cannes Olympique, pendant les entraînements : les joueurs passent devant le fond les uns après les autres, sans bousculer la séance, et plusieurs parents ont déjà commandé leurs tirages. Nous aimerions proposer la même chose à {club}.

Le principe est simple : nous venons avec notre studio (fond, éclairage) en semaine, sur un créneau d'entraînement, et nous photographions {studio_qui}, ainsi que les groupes. Les familles retrouvent ensuite facilement les photos grâce à notre galerie intelligente.

Aucun frais pour le club : le service est financé par les adhérents et les familles qui souhaitent acheter leurs photos.

Le club reçoit de son côté une sélection de photos libres d'utilisation pour sa communication : site, réseaux, affiches.

Vous trouverez notre plaquette en pièce jointe afin de découvrir le concept et son fonctionnement.

Si l'idée vous plaît, nous serions ravis d'échanger avec vous et de convenir d'un soir d'entraînement qui vous arrange.

Sportivement,
{my_name}
{my_org}""",
        "notes": "Shooting studio mobile des adhérents, en semaine au gymnase pendant l'entraînement (pas une compétition). Référence : la séance au gymnase du Basket Cannes Olympique (Céline), où des parents ont commandé des tirages. « {studio_qui} » s'adapte au sport du club. Sans email mais avec un portable, part en WhatsApp (studio_whatsapp).",
    },
    {
        "key": "studio_whatsapp",
        "label": "Shooting studio mobile — WhatsApp",
        "channel": "whatsapp",
        "subject": None,
        "body": """Bonjour {prenom}, {moi}. {rencontre}

On installe notre studio photo directement {studio_lieu} pendant un entraînement : chacun passe à son tour devant le fond, sans couper la séance. On photographie {studio_qui}, plus les photos de groupe.

Aucun frais pour le club : les familles achètent les photos si elles le souhaitent, et vous recevez une sélection pour votre com (site, réseaux, affiches).

On vient de le faire au Basket Cannes Olympique et des parents ont déjà commandé leurs tirages. Est-ce qu'on pourrait caler ça la semaine prochaine ? Si oui, quels jours et quels horaires vous arrangent ?""",
        "notes": "Premier message studio pour un contact dont on a le portable : club croisé au forum (la phrase d'accroche le rappelle) ou jamais contacté. Un club déjà en discussion reçoit studio_rdv à la place.",
    },
    {
        "key": "studio_rdv",
        "label": "Shooting studio — caler la date (déjà en discussion)",
        "channel": "whatsapp",
        "subject": None,
        "body": """Bonjour {prenom}, {moi} pour les photos. On en avait parlé pour faire un shooting {studio_lieu} : est-ce qu'on peut prévoir ça la semaine prochaine ? Si oui, quels seraient les meilleurs jours et horaires pour vous ?""",
        "notes": "Le message de Julien pour un club qui a déjà répondu ou qu'on a vu : pas de présentation, on cale le jour. Ajouter à la main avec qui on s'était vus (« avec Jimmy de mon asso »).",
    },
    {
        "key": "saison_club",
        "label": "Club qui publie toute sa saison",
        "channel": "whatsapp",
        "subject": "{club} — votre saison {sport}",
        "body": """Bonjour {prenom}, {moi}.

J'ai vu que vous publiiez tout votre calendrier de la saison : {saison_combien} dates jusqu'en {saison_fin}, avec les championnats et le gala. C'est rare, et ça change tout pour nous — on peut s'organiser à l'avance plutôt que de courir après chaque date.

Ce qu'on fait : on photographie les gymnastes pendant la compétition, et chaque famille retrouve ensuite facilement les photos de son enfant dans une galerie. Aucun frais pour le club — ce sont les familles qui achètent si elles le souhaitent — et vous recevez une sélection libre d'utilisation pour votre communication.

Plutôt que de vous écrire à chaque date, est-ce qu'on peut en caler deux ou trois ensemble ? Je pense surtout à {saison_prochaine} et au gala.

Dites-moi ce qui vous arrangerait.""",
        "notes": "Pour un club dont la saison entière est publiée — AISA GR Côte d'Azur annonce 17 dates jusqu'en juin, La Crau judo publie tous ses déplacements. Un calendrier complet vaut mieux qu'une date : on vend la saison, pas la journée. Les champs saison_* sont passés en extra par l'appelant, jamais devinés.",
    },
    {
        "key": "relance_forum",
        "label": "Relance — club rencontré sur un stand",
        "channel": "whatsapp",
        "subject": "{club} — on s'est vus au forum",
        "body": """Bonjour {prenom}, {moi}. On s'est croisés au forum des associations début septembre, je vous avais écrit pour les photos.

Je sais que la rentrée a dû vous occuper — je remonte juste le message avant de passer à autre chose.

En ce moment j'installe le studio {studio_lieu}, en semaine sur un créneau d'entraînement : chacun passe devant le fond sans couper la séance, aucun frais pour le club, et vous recevez une sélection pour votre communication. J'ai des créneaux dans les deux semaines qui viennent.

Si ça ne vous intéresse pas, dites-le moi simplement — je ne reviendrai pas vous déranger. Bonne saison à vous !""",
        "notes": "Pour les 73 clubs rencontrés aux forums de Saint-Laurent-du-Var et de Cagnes le 5 septembre 2026, à qui un premier message est parti et qui n'ont jamais répondu. On rappelle où l'on s'est vus — c'est vrai et vérifiable —, on propose une date, et on donne une porte de sortie : sans elle, le silence continue et on ne sait jamais si c'est non.",
    },
    {
        "key": "studio_animaux",
        "label": "Shooting studio mobile — salon de toilettage, club canin",
        "channel": "email",
        "subject": "{club} : un studio photo pour vos clients et leurs chiens ?",
        "body": """Bonjour {prenom},

Nous sommes photographes et développons actuellement SnapActions.

Nous installons un studio photo mobile chez nos partenaires, sur une journée : les clients passent devant le fond avec leur animal, sans rien changer à leur rendez-vous. Nous aimerions proposer la même chose à {club}.

Le principe est simple : nous venons avec notre studio (fond, éclairage) sur une journée qui vous arrange, et nous photographions chaque animal en portrait, seul et avec son maître. Les clients retrouvent ensuite facilement leurs photos grâce à notre galerie intelligente.

Aucun frais pour vous : le service est financé par les clients qui souhaitent acheter leurs photos.

Vous recevez de votre côté une sélection de photos libres d'utilisation pour votre vitrine et vos réseaux.

Vous trouverez notre plaquette en pièce jointe afin de découvrir le concept et son fonctionnement.

Si l'idée vous plaît, nous serions ravis d'échanger avec vous et de convenir d'une journée qui vous arrange.

Cordialement,
{my_name}
{my_org}""",
        "notes": "Même offre que studio_club, mais pour un salon de toilettage, un club canin ou un centre équestre. On n'y dit pas « photographes sportifs » ni « le club » : ce serait faux, et ça s'entend. La sortie n'est pas « Sportivement » pour la même raison.",
    },
    {
        "key": "studio_animaux_whatsapp",
        "label": "Studio animaux — WhatsApp",
        "channel": "whatsapp",
        "subject": None,
        "body": """Bonjour {prenom}, {moi}. {rencontre}

On installe notre studio photo directement chez vous, sur une journée : chaque client passe devant le fond avec son animal, sans rien changer à son rendez-vous. On photographie chaque animal en portrait, seul et avec son maître.

Aucun frais pour vous : les clients achètent leurs photos s'ils le souhaitent, et vous recevez une sélection pour votre vitrine et vos réseaux.

Est-ce qu'on pourrait caler une journée prochainement ? Si oui, quel jour vous arrangerait ?""",
        "notes": "Version WhatsApp de studio_animaux. Attention : la plupart des salons de toilettage n'ont qu'un fixe, sur lequel WhatsApp ne passe pas — le CRM le signale avant le clic.",
    },
]


DEFAULT_CREWS = [
    ("Général", ""),
    ("Basket", "basket"),
    ("Foot", "football"),
    ("Danse", "danse"),
    ("Voiles", "voile,nautique"),
    ("Rafting", "rafting"),
    ("Cannes", ""),
]


def seed_crews():
    if q1("SELECT id FROM crews LIMIT 1"):
        return
    for i, (name, sports) in enumerate(DEFAULT_CREWS):
        insert("crews", {"name": name, "sports": sports, "position": i,
                         "created_at": util.now_iso()})


def seed_templates():
    for tpl in DEFAULT_TEMPLATES:
        exists = q1("SELECT key FROM templates WHERE key = ?", (tpl["key"],))
        if not exists:
            row = dict(tpl)
            row["updated_at"] = util.now_iso()
            insert("templates", row)
