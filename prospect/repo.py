"""Couche metier : creation/dedoublonnage, historique, sequences de relance, pipeline."""
import datetime as dt

from . import db, util

STATUSES = [
    ("prospect",   "Prospect"),
    ("contacte",   "Contacte"),
    ("repondu",    "A repondu"),
    ("interesse",  "Interesse"),
    ("rdv",        "RDV pris"),
    ("client",     "Client"),
    ("dormant",    "Dormant"),
    ("perdu",      "Perdu"),
]
ACTIVE_STATUSES = ("prospect", "contacte", "repondu", "interesse", "rdv")
CLOSED_STATUSES = ("client", "dormant", "perdu")

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def quiet_days():
    raw = db.get_setting("quiet_days", "sat,sun") or ""
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def next_open_day(date):
    """Decale la date jusqu'a un jour ouvre selon les reglages."""
    quiet = quiet_days()
    for _ in range(10):
        if WEEKDAY_KEYS[date.weekday()] not in quiet:
            return date
        date += dt.timedelta(days=1)
    return date


def relance_offsets():
    raw = db.get_setting("relance_j", "4,10,21") or ""
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out or [4, 10, 21]


# --------------------------------------------------------------------------
# Clubs
# --------------------------------------------------------------------------

# Domaines qui n'identifient personne : une page Facebook ou une boite Gmail
# est partagee par des milliers de clubs. S'en servir pour dedoublonner fait
# fusionner deux clubs sans rapport.
DOMAINES_PARTAGES = (
    "gmail.com", "googlemail.com", "hotmail.fr", "hotmail.com", "outlook.fr",
    "outlook.com", "orange.fr", "free.fr", "wanadoo.fr", "yahoo.fr", "yahoo.com",
    "laposte.net", "sfr.fr", "icloud.com", "me.com", "live.fr", "bbox.fr", "numericable.fr",
    "facebook.com", "m.facebook.com", "fb.com", "fb.me", "instagram.com",
    "twitter.com", "x.com", "linkedin.com", "youtube.com", "youtu.be", "tiktok.com",
    "helloasso.com", "linktr.ee", "google.com", "docs.google.com", "sites.google.com",
    "forms.gle", "wixsite.com", "wordpress.com", "blogspot.com", "weebly.com",
    "e-monsite.com", "over-blog.com", "jimdo.com", "webnode.fr",
    # Monaco : les adresses grand public de Monaco Telecom. Des dizaines de
    # clubs sans rapport y ont leur boite ; les confondre fusionnait l'escrime,
    # le judo et le tennis dans la fiche de la Societe Nautique.
    "monaco.mc", "libello.com", "monte-carlo.mc", "yahoo.mc",
    "hotmail.it", "libero.it", "gmx.fr", "aol.com", "msn.com",
)


def find_club(name=None, website=None, email=None, dept=None, postal_code=None):
    """Dedoublonnage : nom normalise (+ dept si connu), sinon domaine web/mail."""
    if name:
        key = util.norm_key(name)
        if key:
            if dept:
                row = db.q1("SELECT * FROM clubs WHERE name_key = ? AND dept = ?", (key, dept))
                if row:
                    return dict(row)
            row = db.q1("SELECT * FROM clubs WHERE name_key = ?", (key,))
            if row:
                return dict(row)
    for value in (website, email):
        if not value:
            continue
        # « GMAIL.COM » en capitales passait a cote de la liste ci-dessus.
        dom = (util.email_domain(value) or "").lower() if "@" in str(value) else None
        if not dom:
            dom = str(value).replace("https://", "").replace("http://", "")
            dom = dom.replace("www.", "").split("/")[0].lower()
        if not dom or dom in DOMAINES_PARTAGES:
            continue
        row = db.q1(
            "SELECT * FROM clubs WHERE (website LIKE ? OR email LIKE ?) LIMIT 1",
            ("%" + dom + "%", "%@" + dom))
        if row:
            return dict(row)
    return None


def upsert_club(data, merge=True):
    """Cree ou complete un club. Ne remplace jamais une valeur existante non vide."""
    data = {k: v for k, v in (data or {}).items() if v not in (None, "")}
    name = (data.get("name") or "").strip()
    if not name:
        return None, "skipped"
    postal = data.get("postal_code")
    data["dept"] = data.get("dept") or util.dept_from_postal(postal)
    data["name_key"] = util.norm_key(name)
    data["name"] = name
    # Le numero entre au format E.164, comme pour les contacts. Sans cette
    # ligne les sources ecrivaient « 06 20 64 97 15 » a cote de
    # « +33620649715 » : deux ecritures du meme numero, que rien ne
    # rapproche — 94 fiches etaient dans ce cas le 21/10/2026.
    if data.get("phone"):
        data["phone"] = util.normalize_phone(data["phone"]) or data["phone"]
    if data.get("email"):
        data["email"] = util.normalize_email(data["email"]) or data["email"]

    existing = find_club(name, data.get("website"), data.get("email"), data.get("dept"), postal) \
        if merge else None
    now = util.now_iso()
    if existing:
        patch = {}
        for key, value in data.items():
            if key in ("id", "created_at", "name_key"):
                continue
            if key == "name":
                continue
            if not existing.get(key):
                patch[key] = value
        if patch:
            patch["updated_at"] = now
            db.update("clubs", existing["id"], patch)
        return existing["id"], ("updated" if patch else "unchanged")

    data.setdefault("status", "prospect")
    data.setdefault("country", "FR")
    data["created_at"] = now
    data["updated_at"] = now
    return db.insert("clubs", data), "created"


def set_club_status(club_id, status, note=None):
    patch = {"status": status, "updated_at": util.now_iso()}
    db.update("clubs", club_id, patch)
    if note:
        add_interaction(club_id, kind="note", direction="in", body=note,
                        subject="Statut -> %s" % status)
    if status in CLOSED_STATUSES:
        db.ex("UPDATE tasks SET status='cancelled', updated_at=? "
              "WHERE club_id=? AND status='pending'", (util.now_iso(), club_id))
    return get_club(club_id)


def opt_out(club_id, reason=None):
    """Ne plus contacter : coupe les relances, annule les brouillons en attente."""
    now = util.now_iso()
    db.update("clubs", club_id, {"do_not_contact": 1, "opt_out_at": now,
                                 "opt_out_reason": reason, "status": "perdu",
                                 "updated_at": now})
    db.ex("UPDATE contacts SET do_not_contact=1, opt_out_at=?, updated_at=? WHERE club_id=?",
          (now, now, club_id))
    db.ex("UPDATE tasks SET status='cancelled', updated_at=? WHERE club_id=? AND status='pending'",
          (now, club_id))
    db.ex("UPDATE drafts SET status='rejected', review_note='opt-out', updated_at=? "
          "WHERE club_id=? AND status IN ('draft','approved')", (now, club_id))
    add_interaction(club_id, kind="note", direction="in",
                    subject="Opt-out", body=reason or "Demande a ne plus etre contacte.")


def get_club(club_id):
    row = db.q1("SELECT * FROM clubs WHERE id = ?", (club_id,))
    if not row:
        return None
    club = dict(row)
    club["contacts"] = db.rows_to_dicts(
        db.q("SELECT * FROM contacts WHERE club_id = ? ORDER BY id", (club_id,)))
    club["interactions"] = db.rows_to_dicts(
        db.q("SELECT * FROM interactions WHERE club_id = ? ORDER BY occurred_at DESC LIMIT 50",
             (club_id,)))
    club["tasks"] = db.rows_to_dicts(
        db.q("SELECT * FROM tasks WHERE club_id = ? ORDER BY due_at", (club_id,)))
    club["drafts"] = db.rows_to_dicts(
        db.q("SELECT * FROM drafts WHERE club_id = ? ORDER BY created_at DESC LIMIT 20",
             (club_id,)))
    club["dept_name"] = util.dept_name(club.get("dept"))
    return club


def list_clubs(search=None, status=None, dept=None, tag=None, limit=500, offset=0,
               order="updated_at DESC"):
    where, params = ["1=1"], []
    if search:
        like = "%" + search.strip() + "%"
        where.append("(c.name LIKE ? OR c.city LIKE ? OR c.email LIKE ? OR c.notes LIKE ? "
                     "OR EXISTS (SELECT 1 FROM contacts ct WHERE ct.club_id=c.id AND "
                     "(ct.first_name LIKE ? OR ct.last_name LIKE ? OR ct.email LIKE ? "
                     "OR ct.phone LIKE ?)))")
        params += [like] * 8
    if status:
        where.append("c.status = ?")
        params.append(status)
    if dept:
        where.append("c.dept = ?")
        params.append(dept)
    if tag:
        where.append("c.tags LIKE ?")
        params.append("%" + tag + "%")
    sql = ("SELECT c.*, "
           "(SELECT COUNT(*) FROM contacts ct WHERE ct.club_id=c.id) AS n_contacts, "
           "(SELECT MIN(due_at) FROM tasks t WHERE t.club_id=c.id AND t.status='pending') "
           "  AS next_due, "
           "(SELECT COUNT(*) FROM drafts d WHERE d.club_id=c.id AND d.status='draft') "
           "  AS n_drafts "
           "FROM clubs c WHERE %s ORDER BY %s LIMIT ? OFFSET ?" % (" AND ".join(where), order))
    rows = db.rows_to_dicts(db.q(sql, tuple(params) + (limit, offset)))
    for r in rows:
        r["dept_name"] = util.dept_name(r.get("dept"))
        r["temperature"] = temperature(r.get("last_contact_at"))
    return rows


def temperature(last_contact_at):
    """Chaud / tiede / froid selon l'anciennete du dernier contact."""
    d = util.parse_dt(last_contact_at)
    if not d:
        return "neuf"
    age = (util.today() - d.date()).days
    if age <= 7:
        return "chaud"
    if age <= 30:
        return "tiede"
    return "froid"


# --------------------------------------------------------------------------
# Contacts
# --------------------------------------------------------------------------

def find_contact(email=None, phone=None, club_id=None, first_name=None, last_name=None):
    if email:
        row = db.q1("SELECT * FROM contacts WHERE email = ?", (email,))
        if row:
            return dict(row)
    if phone:
        row = db.q1("SELECT * FROM contacts WHERE phone = ? OR phone2 = ?", (phone, phone))
        if row:
            return dict(row)
    if club_id and (first_name or last_name):
        row = db.q1(
            "SELECT * FROM contacts WHERE club_id = ? AND "
            "LOWER(COALESCE(first_name,'')) = ? AND LOWER(COALESCE(last_name,'')) = ?",
            (club_id, (first_name or "").lower(), (last_name or "").lower()))
        if row:
            return dict(row)
    return None


def upsert_contact(data, merge=True):
    data = {k: v for k, v in (data or {}).items() if v not in (None, "")}
    data["email"] = util.normalize_email(data.get("email"))
    data["phone"] = util.normalize_phone(data.get("phone"))
    data["phone2"] = util.normalize_phone(data.get("phone2"))
    data = {k: v for k, v in data.items() if v not in (None, "")}
    if data.get("phone2") == data.get("phone"):
        data.pop("phone2", None)
    if not (data.get("email") or data.get("phone") or data.get("last_name")
            or data.get("first_name")):
        return None, "skipped"
    for key in ("first_name", "last_name"):
        if data.get(key):
            data[key] = util.title_name(data[key])

    existing = find_contact(data.get("email"), data.get("phone"), data.get("club_id"),
                            data.get("first_name"), data.get("last_name")) if merge else None
    now = util.now_iso()
    if existing:
        patch = {}
        for key, value in data.items():
            if key in ("id", "created_at"):
                continue
            if not existing.get(key):
                patch[key] = value
        if patch:
            patch["updated_at"] = now
            db.update("contacts", existing["id"], patch)
        return existing["id"], ("updated" if patch else "unchanged")
    data["created_at"] = now
    data["updated_at"] = now
    return db.insert("contacts", data), "created"


def get_contact(contact_id):
    row = db.q1("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    return dict(row) if row else None


def list_contacts(search=None, club_id=None, has_phone=None, limit=1000):
    where, params = ["1=1"], []
    if club_id:
        where.append("ct.club_id = ?")
        params.append(club_id)
    if search:
        like = "%" + search.strip() + "%"
        where.append("(ct.first_name LIKE ? OR ct.last_name LIKE ? OR ct.email LIKE ? "
                     "OR ct.phone LIKE ? OR ct.role LIKE ? OR c.name LIKE ?)")
        params += [like] * 6
    if has_phone == "1":
        where.append("ct.phone IS NOT NULL AND ct.phone != ''")
    elif has_phone == "0":
        where.append("(ct.phone IS NULL OR ct.phone = '')")
    rows = db.rows_to_dicts(db.q(
        "SELECT ct.*, c.name AS club_name, c.dept AS club_dept, c.status AS club_status "
        "FROM contacts ct LEFT JOIN clubs c ON c.id = ct.club_id "
        "WHERE %s ORDER BY ct.updated_at DESC LIMIT ?" % " AND ".join(where),
        tuple(params) + (limit,)))
    for r in rows:
        r["phone_fmt"] = util.format_phone(r.get("phone"))
        r["phone2_fmt"] = util.format_phone(r.get("phone2"))
        r["is_mobile"] = util.is_mobile_fr(r.get("phone"))
    return rows


# --------------------------------------------------------------------------
# Interactions
# --------------------------------------------------------------------------

def add_interaction(club_id, kind="note", direction="out", subject=None, body=None,
                    contact_id=None, occurred_at=None, outcome=None, touch=True):
    now = util.now_iso()
    row_id = db.insert("interactions", {
        "club_id": club_id, "contact_id": contact_id, "kind": kind, "direction": direction,
        "subject": subject, "body": body, "outcome": outcome,
        "occurred_at": occurred_at or now, "created_at": now,
    })
    if touch and kind in ("email", "call", "sms", "whatsapp", "meeting", "visit"):
        db.ex("UPDATE clubs SET last_contact_at = ?, updated_at = ? WHERE id = ?",
              (occurred_at or now, now, club_id))
        if contact_id:
            db.ex("UPDATE contacts SET last_contact_at = ?, updated_at = ? WHERE id = ?",
                  (occurred_at or now, now, contact_id))
    return row_id


# --------------------------------------------------------------------------
# Taches / relances
# --------------------------------------------------------------------------

def add_task(club_id, title, due_at, contact_id=None, notes=None, kind="relance",
             draft_id=None, sequence=None, step=0):
    now = util.now_iso()
    return db.insert("tasks", {
        "club_id": club_id, "contact_id": contact_id, "draft_id": draft_id,
        "title": title, "notes": notes, "kind": kind,
        "due_at": due_at, "status": "pending", "sequence": sequence, "step": step,
        "created_at": now, "updated_at": now,
    })


def schedule_sequence(club_id, contact_id=None, start=None, sequence="standard",
                      replace=True):
    """Programme la cadence de relance (J+4 / J+10 / J+21 par defaut)."""
    club = db.q1("SELECT name, do_not_contact FROM clubs WHERE id = ?", (club_id,))
    if not club or club["do_not_contact"]:
        return []
    if replace:
        db.ex("UPDATE tasks SET status='cancelled', updated_at=? "
              "WHERE club_id=? AND status='pending' AND kind='relance'",
              (util.now_iso(), club_id))
    base = util.parse_dt(start).date() if start else util.today()
    created = []
    labels = ["Relance 1", "Relance 2", "Relance 3", "Relance 4", "Relance 5"]
    for i, offset in enumerate(relance_offsets()):
        due = next_open_day(base + dt.timedelta(days=offset))
        title = "%s — %s" % (labels[i] if i < len(labels) else "Relance %d" % (i + 1),
                             club["name"])
        created.append(add_task(club_id, title,
                                dt.datetime.combine(due, dt.time(9, 0)).isoformat(sep=" "),
                                contact_id=contact_id, kind="relance",
                                sequence=sequence, step=i + 1))
    return created


def complete_task(task_id, outcome=None):
    now = util.now_iso()
    task = db.q1("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        return None
    db.update("tasks", task_id, {"status": "done", "done_at": now, "updated_at": now})
    if outcome:
        add_interaction(task["club_id"], kind="note", direction="out",
                        subject=task["title"], body=outcome, contact_id=task["contact_id"])
    return dict(db.q1("SELECT * FROM tasks WHERE id = ?", (task_id,)))


def snooze_task(task_id, days=7):
    task = db.q1("SELECT due_at FROM tasks WHERE id = ?", (task_id,))
    if not task:
        return None
    base = util.parse_dt(task["due_at"]) or util.now()
    new_due = next_open_day(max(base.date(), util.today()) + dt.timedelta(days=int(days)))
    db.update("tasks", task_id, {
        "due_at": dt.datetime.combine(new_due, dt.time(9, 0)).isoformat(sep=" "),
        "status": "pending", "updated_at": util.now_iso()})
    return dict(db.q1("SELECT * FROM tasks WHERE id = ?", (task_id,)))


def list_tasks(status="pending", horizon_days=None, club_id=None, limit=500):
    where, params = ["1=1"], []
    if status and status != "all":
        where.append("t.status = ?")
        params.append(status)
    if club_id:
        where.append("t.club_id = ?")
        params.append(club_id)
    if horizon_days is not None:
        limit_date = (util.today() + dt.timedelta(days=int(horizon_days))).isoformat()
        where.append("date(t.due_at) <= ?")
        params.append(limit_date)
    rows = db.rows_to_dicts(db.q(
        "SELECT t.*, c.name AS club_name, c.dept AS club_dept, c.status AS club_status, "
        "ct.first_name, ct.last_name, ct.email AS contact_email, ct.phone AS contact_phone "
        "FROM tasks t LEFT JOIN clubs c ON c.id=t.club_id "
        "LEFT JOIN contacts ct ON ct.id=t.contact_id "
        "WHERE %s ORDER BY t.due_at LIMIT ?" % " AND ".join(where), tuple(params) + (limit,)))
    for r in rows:
        r["when"] = util.human_delta(r["due_at"])
        r["overdue"] = bool(util.parse_dt(r["due_at"])
                            and util.parse_dt(r["due_at"]).date() < util.today()
                            and r["status"] == "pending")
    return rows


# --------------------------------------------------------------------------
# Tableau de bord
# --------------------------------------------------------------------------

def enrichir_contacts_depuis_annonces(limite_partage=3):
    """Recupere telephones et emails restes dans le texte des annonces.

    Cent trente-sept clubs n'avaient aucun moyen d'etre joints alors que le
    numero de l'organisateur dormait dans la description du tournoi qu'on
    avait deja importee. On ne touche qu'aux clubs sans aucun contact : une
    valeur saisie a la main n'est jamais ecrasee.

    Un numero qui revient chez trois clubs differents est un standard ou un
    prestataire, pas un contact de club : on le laisse de cote.
    """
    lignes = db.q("SELECT t.id, t.url, t.description, t.club_id "
                  "FROM tournaments t JOIN clubs c ON c.id = t.club_id "
                  "WHERE COALESCE(t.description,'') <> '' "
                  "  AND COALESCE(c.phone,'') = '' AND COALESCE(c.email,'') = ''")

    partage = {}
    for r in lignes:
        for tel in util.extract_phones(r["description"]):
            partage.setdefault(tel, set()).add(r["club_id"])
    banals = {t for t, clubs in partage.items() if len(clubs) >= limite_partage}

    clubs_faits, tels, mails = set(), 0, 0
    for r in lignes:
        if r["club_id"] in clubs_faits:
            continue
        numeros = [t for t in util.extract_phones(r["description"]) if t not in banals]
        # Un mobile se prete au WhatsApp, c'est par la que Julien travaille.
        numeros.sort(key=lambda t: (not util.is_mobile_fr(t),))
        courriels = util.extract_emails(r["description"])
        if not numeros and not courriels:
            continue

        patch = {}
        if numeros:
            patch["phone"] = numeros[0]
            tels += 1
        if courriels:
            patch["email"] = courriels[0]
            mails += 1
        patch["updated_at"] = util.now_iso()
        db.update("clubs", r["club_id"], patch)

        upsert_contact({
            "club_id": r["club_id"],
            "first_name": "Organisateur",
            "role": "contact annonce",
            "phone": numeros[0] if numeros else None,
            "email": courriels[0] if courriels else None,
            "channel": "whatsapp" if numeros else "email",
            "source": "Annonce du tournoi",
            "source_url": r["url"] or None,
        })
        clubs_faits.add(r["club_id"])

    if clubs_faits:
        db.log("enrichissement",
               "%d club(s) joignables grace au texte des annonces "
               "(%d telephone(s), %d email(s))" % (len(clubs_faits), tels, mails))
    return {"clubs": len(clubs_faits), "telephones": tels, "emails": mails,
            "ecartes": sorted(banals)}


def dashboard():
    today_iso = util.today().isoformat()
    week_iso = (util.today() + dt.timedelta(days=7)).isoformat()
    counts = {r["status"]: r["n"] for r in db.q(
        "SELECT status, COUNT(*) AS n FROM clubs GROUP BY status")}
    stats = {
        "clubs": db.q1("SELECT COUNT(*) AS n FROM clubs")["n"],
        "contacts": db.q1("SELECT COUNT(*) AS n FROM contacts")["n"],
        "with_phone": db.q1("SELECT COUNT(*) AS n FROM contacts "
                            "WHERE phone IS NOT NULL AND phone != ''")["n"],
        "by_status": [{"key": k, "label": lbl, "n": counts.get(k, 0)} for k, lbl in STATUSES],
        "tasks_overdue": db.q1("SELECT COUNT(*) AS n FROM tasks WHERE status='pending' "
                               "AND date(due_at) < ?", (today_iso,))["n"],
        "tasks_today": db.q1("SELECT COUNT(*) AS n FROM tasks WHERE status='pending' "
                             "AND date(due_at) = ?", (today_iso,))["n"],
        "tasks_week": db.q1("SELECT COUNT(*) AS n FROM tasks WHERE status='pending' "
                            "AND date(due_at) <= ?", (week_iso,))["n"],
        "drafts_pending": db.q1("SELECT COUNT(*) AS n FROM drafts WHERE status='draft'")["n"],
        # Un brouillon sans destinataire n'est pas un message en attente,
        # c'est un club a retrouver : les deux chiffres ne se melangent pas.
        "drafts_ready": db.q1("SELECT COUNT(*) AS n FROM drafts WHERE status='draft' "
                              "AND COALESCE(to_addr,'') <> ''")["n"],
        "drafts_blocked": db.q1("SELECT COUNT(*) AS n FROM drafts WHERE status='draft' "
                                "AND COALESCE(to_addr,'') = ''")["n"],
        "drafts_approved": db.q1("SELECT COUNT(*) AS n FROM drafts "
                                 "WHERE status='approved'")["n"],
        "tournaments_new": db.q1("SELECT COUNT(*) AS n FROM tournaments WHERE status='new' "
                                 "AND (start_date IS NULL OR start_date >= ?)",
                                 (today_iso,))["n"],
        "tournaments_upcoming": db.q1("SELECT COUNT(*) AS n FROM tournaments "
                                      "WHERE start_date >= ?", (today_iso,))["n"],
        "no_contact_info": db.q1(
            "SELECT COUNT(*) AS n FROM clubs c WHERE NOT EXISTS "
            "(SELECT 1 FROM contacts ct WHERE ct.club_id=c.id AND "
            "(ct.email IS NOT NULL OR ct.phone IS NOT NULL)) "
            "AND (c.email IS NULL AND c.phone IS NULL)")["n"],
        "cold": db.q1("SELECT COUNT(*) AS n FROM clubs WHERE status IN "
                      "('contacte','repondu','interesse') AND (last_contact_at IS NULL "
                      "OR date(last_contact_at) < date('now','-30 day'))")["n"],
        "opted_out": db.q1("SELECT COUNT(*) AS n FROM clubs WHERE do_not_contact=1")["n"],
    }
    return stats


# ---------------------------------------------------------------------------
# Rendre a chaque tournoi son organisateur
# ---------------------------------------------------------------------------
# Deux cent quarante-sept tournois n'ont aucun club rattache : leurs cartes
# affichent « Message indisponible ». Et quand le CRM rattachait tout seul, il
# decoupait le NOM DU TOURNOI sur un tiret et fabriquait un club fantome —
# douze fiches a fusionner, un numero envoye au mauvais club.
#
# Le modele local sait repondre a « qui organise ca ? » sans tomber dans le
# piege : « Challenge Julien Eutrope 2026 U13 - La Seyne-sur-Mer » n'a pas
# d'organisateur nommé, et La Seyne est une ville, pas un club. On ne cree donc
# un club que s'il nomme vraiment une structure, et on prefere toujours un club
# qui existe deja.
def reparer_organisateurs(limite=40, creer=False):
    from . import ia, util
    if not ia.disponible():
        return {"traites": 0, "raison": "modele local indisponible"}

    # On commence par les noms qui ont une chance de nommer quelqu'un : « du »,
    # « de l' », « de la ». Un « Trail des Maures » n'a pas d'organisateur dans
    # son nom, et on ne va pas payer trois secondes pour l'apprendre.
    lignes = db.q(
        "SELECT id, name, city FROM tournaments "
        "WHERE club_id IS NULL AND TRIM(COALESCE(name,'')) <> '' "
        "  AND (name LIKE '% du %' OR name LIKE '% de l%' OR name LIKE '% des %' "
        "       OR name LIKE '%FC %' OR name LIKE '%US %' OR name LIKE '%AS %') "
        "ORDER BY date(COALESCE(start_date,'2099-01-01')) LIMIT ?", (int(limite),))

    bilan = {"traites": 0, "rattaches": 0, "crees": 0, "sans_organisateur": 0}
    for t in lignes:
        bilan["traites"] += 1
        d = ia.organisateur(t["name"])
        nom = (d or {}).get("organisateur")
        if not nom:
            bilan["sans_organisateur"] += 1
            continue
        cle = util.norm_key(nom)
        club = db.q1("SELECT * FROM clubs WHERE name_key = ?", (cle,))
        if club:
            db.update("tournaments", t["id"], {"club_id": club["id"]})
            bilan["rattaches"] += 1
            continue
        if not creer:
            continue
        club_id, _ = upsert_club({"name": nom,
                                  "city": (d or {}).get("ville") or t["city"],
                                  "source": "organisateur lu dans le nom du tournoi"})
        db.update("tournaments", t["id"], {"club_id": club_id})
        bilan["crees"] += 1
    return bilan
