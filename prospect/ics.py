"""Generation et lecture de fichiers iCalendar (RFC 5545), sans dependance."""
import datetime as dt
import re

from . import db, util

PRODID = "-//prospect-clubs//CRM//FR"


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def esc(text):
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,")
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    return s


def fold(line):
    """Repli a 75 octets, en coupant proprement sur les caracteres UTF-8."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, start = [], 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        out.append(raw[start:end].decode("utf-8"))
        start = end
        limit = 74  # les lignes suivantes commencent par une espace
    return "\r\n ".join(out)


def _stamp(value):
    return value.strftime("%Y%m%dT%H%M%S")


def vevent(uid, summary, start, end=None, all_day=False, description=None, location=None,
           url=None, alarm_minutes=None, categories=None, status=None, transparent=False):
    lines = ["BEGIN:VEVENT", "UID:%s" % uid, "DTSTAMP:%sZ" % _stamp(dt.datetime.utcnow())]
    if all_day:
        d = start.date() if isinstance(start, dt.datetime) else start
        e = end.date() if isinstance(end, dt.datetime) else (end or d)
        lines.append("DTSTART;VALUE=DATE:%s" % d.strftime("%Y%m%d"))
        lines.append("DTEND;VALUE=DATE:%s" % (e + dt.timedelta(days=1)).strftime("%Y%m%d"))
    else:
        s = start if isinstance(start, dt.datetime) else dt.datetime.combine(start, dt.time(9, 0))
        e = end if isinstance(end, dt.datetime) else (s + dt.timedelta(minutes=30))
        lines.append("DTSTART;TZID=Europe/Paris:%s" % _stamp(s))
        lines.append("DTEND;TZID=Europe/Paris:%s" % _stamp(e))
    lines.append("SUMMARY:%s" % esc(summary))
    if description:
        lines.append("DESCRIPTION:%s" % esc(description))
    if location:
        lines.append("LOCATION:%s" % esc(location))
    if url:
        lines.append("URL:%s" % esc(url))
    if categories:
        lines.append("CATEGORIES:%s" % esc(categories))
    if status:
        lines.append("STATUS:%s" % status)
    if transparent:
        lines.append("TRANSP:TRANSPARENT")
    if alarm_minutes is not None and not all_day:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                  "DESCRIPTION:%s" % esc(summary),
                  "TRIGGER:-PT%dM" % int(alarm_minutes), "END:VALARM"]
    elif alarm_minutes is not None and all_day:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                  "DESCRIPTION:%s" % esc(summary),
                  "TRIGGER;RELATED=START:-PT%dH" % max(1, int(alarm_minutes) // 60),
                  "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


VTIMEZONE_PARIS = [
    "BEGIN:VTIMEZONE", "TZID:Europe/Paris",
    "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0200", "TZNAME:CEST",
    "DTSTART:19700329T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
    "BEGIN:STANDARD", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "TZNAME:CET",
    "DTSTART:19701025T030000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
    "END:VTIMEZONE",
]


def calendar(name, events, refresh_minutes=60):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:%s" % PRODID,
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:%s" % esc(name), "NAME:%s" % esc(name),
             "X-WR-TIMEZONE:Europe/Paris",
             "REFRESH-INTERVAL;VALUE=DURATION:PT%dM" % refresh_minutes,
             "X-PUBLISHED-TTL:PT%dM" % refresh_minutes]
    lines += VTIMEZONE_PARIS
    lines += events
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


# --------------------------------------------------------------------------
# Flux
# --------------------------------------------------------------------------

def _club_line(row):
    bits = [row.get("club_name") or ""]
    if row.get("club_dept"):
        bits.append("(%s)" % row["club_dept"])
    return " ".join(b for b in bits if b)


def tasks_events(status="pending", horizon_days=400):
    from . import repo
    events = []
    for t in repo.list_tasks(status=status, horizon_days=horizon_days, limit=2000):
        start = util.parse_dt(t["due_at"])
        if not start:
            continue
        who = " ".join(x for x in [t.get("first_name"), t.get("last_name")] if x)
        desc = []
        if who:
            desc.append("Contact : %s" % who)
        if t.get("contact_email"):
            desc.append("Email : %s" % t["contact_email"])
        if t.get("contact_phone"):
            desc.append("Tel : %s" % util.format_phone(t["contact_phone"]))
        if t.get("notes"):
            desc.append(t["notes"])
        desc.append("Fiche : http://%s/#/clubs/%s" % (
            db.get_setting("public_host", "127.0.0.1:8765"), t["club_id"]))
        events += vevent(
            uid="task-%s@prospect" % t["id"],
            summary="[Relance] %s" % (t["club_name"] or t["title"]),
            start=start, end=start + dt.timedelta(minutes=20),
            description="\n".join(desc),
            categories="Prospection",
            alarm_minutes=15,
            transparent=True,
        )
    return events


def tournaments_events(only_upcoming=True, include_past_days=30):
    where = "1=1"
    params = ()
    if only_upcoming:
        where = "date(start_date) >= date('now', ?)"
        params = ("-%d day" % include_past_days,)
    rows = db.rows_to_dicts(db.q(
        "SELECT t.*, c.name AS club_name FROM tournaments t "
        "LEFT JOIN clubs c ON c.id = t.club_id "
        "WHERE start_date IS NOT NULL AND %s ORDER BY start_date" % where, params))
    events = []
    for t in rows:
        start = util.parse_date(t["start_date"])
        if not start:
            continue
        end = util.parse_date(t["end_date"]) or start
        desc = []
        if t.get("organizer"):
            desc.append("Organisateur : %s" % t["organizer"])
        if t.get("club_name"):
            desc.append("Club en base : %s" % t["club_name"])
        if t.get("level"):
            desc.append("Niveau : %s" % t["level"])
        if t.get("source"):
            desc.append("Source : %s" % t["source"])
        if t.get("description"):
            desc.append(t["description"][:400])
        loc = util.lieu_lisible(t.get("venue"), t.get("city"), t.get("postal_code"))
        events += vevent(
            uid="tournoi-%s@prospect" % t["id"],
            summary="[Tournoi] %s" % t["name"],
            start=start, end=end, all_day=True,
            description="\n".join(desc), location=loc or None,
            url=t.get("url"), categories="Tournoi", alarm_minutes=None,
        )
    return events


def feed(kind="all"):
    if kind == "relances":
        return calendar("Prospection — relances", tasks_events())
    if kind == "tournois":
        return calendar("Prospection — tournois", tournaments_events())
    return calendar("Prospection clubs", tasks_events() + tournaments_events())


# --------------------------------------------------------------------------
# Lecture (pour les sources de type calendrier de federation)
# --------------------------------------------------------------------------

def unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text)


def unesc(value):
    return (value.replace("\\n", "\n").replace("\\N", "\n")
            .replace("\\,", ",").replace("\;", ";").replace("\\\\", "\\"))


def parse_ics(text):
    """Renvoie une liste de dicts VEVENT (cles en minuscules, sans parametres)."""
    events, current = [], None
    for line in unfold(text or "").split("\n"):
        line = line.strip()
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, _, value = line.partition(":")
        name = head.split(";")[0].strip().lower()
        params = {}
        for part in head.split(";")[1:]:
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip().lower()] = v.strip()
        if name in ("dtstart", "dtend"):
            current[name] = _parse_ics_date(value, params)
        else:
            current[name] = unesc(value)
    return events


def _parse_ics_date(value, params):
    value = value.strip()
    if params.get("value", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        try:
            return dt.datetime.strptime(value[:8], "%Y%m%d").date()
        except ValueError:
            return None
    m = re.match(r"(\d{8}T\d{6})", value)
    if m:
        try:
            return dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
        except ValueError:
            return None
    return None
