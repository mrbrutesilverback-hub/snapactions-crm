# -*- coding: utf-8 -*-
"""Lecture d'un export de conversation WhatsApp ('Exporter la discussion').

WhatsApp ne donne acces a aucune API pour relire votre historique : ni la Cloud
API de Meta (qui ne voit que ce qui transite par elle), ni rien d'autre.
L'export texte est le seul moyen legitime de recuperer ce qui a deja ete dit.
"""
import datetime as dt
import re

from . import util

# [05/09/2026 10:57:11] Jimmy : message      (iOS)
# 05/09/2026, 10:57 - Jimmy : message        (Android)
# 05/09/2026 a 10:57 - Jimmy : message
LINE_RES = [
    re.compile(r"^\[(?P<d>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{2,4})[,\s]+"
               r"(?P<t>\d{1,2}:\d{2}(?::\d{2})?)\s*(?P<ampm>[AP]M)?\]\s*"
               r"(?P<who>[^:]{1,60}?)\s*:\s(?P<msg>.*)$", re.I),
    re.compile(r"^(?P<d>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{2,4})[,\s]+(?:a |à )?"
               r"(?P<t>\d{1,2}:\d{2}(?::\d{2})?)\s*(?P<ampm>[AP]M)?\s*[-–]\s*"
               r"(?P<who>[^:]{1,60}?)\s*:\s(?P<msg>.*)$", re.I),
]
# Lignes systeme sans auteur ('05/09/2026, 10:57 - Vous avez cree le groupe')
SYSTEM_RE = re.compile(r"^\[?(?P<d>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{2,4})[,\s]+"
                       r"(?P<t>\d{1,2}:\d{2}(?::\d{2})?)[^\w]*(?P<msg>.*)$")

NOISE = re.compile(
    r"end-to-end encrypted|chiffres de bout en bout|a cree le groupe|created group|"
    r"vous a ajout|added you|a rejoint|joined using|changed the subject|"
    r"a change (?:le sujet|l'ic)|Les messages et les appels|"
    r"<M[ée]dias omis>|<Media omitted>|image absente|document omis", re.I)

ATTACH_RE = re.compile(r"<(?:attached|joint)\s*:\s*(?P<f>[^>]+)>|"
                       r"(?P<f2>[\w\-]+\.(?:jpg|jpeg|png|mp4|pdf|opus|webp))\s*"
                       r"\((?:file attached|fichier joint)\)", re.I)


def _clean(text):
    """Retire les marques invisibles que WhatsApp glisse dans ses exports."""
    return (text or "").replace("‎", "").replace("‏", "") \
                       .replace(" ", " ").replace("\xa0", " ")


def _parse_stamp(date_str, time_str, ampm=None):
    date_str = date_str.replace(".", "/").replace("-", "/")
    parts = date_str.split("/")
    if len(parts) != 3:
        return None
    a, b, c = parts
    if len(a) == 4:                       # AAAA/MM/JJ
        cand = [(int(a), int(b), int(c))]
    else:
        y = int(c) + (2000 if len(c) == 2 else 0)
        # JJ/MM/AAAA d'abord (format francais), puis MM/JJ/AAAA
        cand = [(y, int(b), int(a)), (y, int(a), int(b))]
    time_parts = [int(x) for x in time_str.split(":")]
    hour = time_parts[0]
    if ampm:
        if ampm.upper() == "PM" and hour < 12:
            hour += 12
        elif ampm.upper() == "AM" and hour == 12:
            hour = 0
    minute = time_parts[1] if len(time_parts) > 1 else 0
    second = time_parts[2] if len(time_parts) > 2 else 0
    for y, m, d in cand:
        try:
            return dt.datetime(y, m, d, hour, minute, second)
        except ValueError:
            continue
    return None


def parse_export(text):
    """Renvoie {messages, participants, first, last, n_attachments}."""
    messages = []
    for raw in _clean(text).split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        matched = None
        for rx in LINE_RES:
            m = rx.match(line)
            if m:
                matched = m
                break
        if not matched:
            sysm = SYSTEM_RE.match(line)
            if sysm:
                continue                      # ligne systeme : on ignore
            if messages:                      # suite d'un message multiligne
                messages[-1]["text"] += "\n" + line.strip()
            continue
        stamp = _parse_stamp(matched.group("d"), matched.group("t"),
                             matched.groupdict().get("ampm"))
        body = matched.group("msg").strip()
        who = matched.group("who").strip()
        if NOISE.search(body) or NOISE.search(who):
            continue
        attach = ATTACH_RE.search(body)
        messages.append({
            "at": stamp.isoformat(sep=" ") if stamp else None,
            "author": who,
            "text": body,
            "attachment": (attach.group("f") or attach.group("f2")) if attach else None,
        })

    dated = [m["at"] for m in messages if m["at"]]
    authors = {}
    for m in messages:
        authors[m["author"]] = authors.get(m["author"], 0) + 1
    return {
        "messages": messages,
        "participants": [{"name": k, "n": v}
                         for k, v in sorted(authors.items(), key=lambda x: -x[1])],
        "first": min(dated) if dated else None,
        "last": max(dated) if dated else None,
        "n": len(messages),
        "n_attachments": sum(1 for m in messages if m["attachment"]),
    }


def guess_me(participants, settings):
    """Devine lequel des participants est vous, d'apres les reglages."""
    mine = util.norm_key(settings.get("my_name") or "")
    org = util.norm_key(settings.get("my_org") or "")
    for p in participants:
        key = util.norm_key(p["name"])
        if mine and (mine in key or key in mine):
            return p["name"]
        if org and org in key:
            return p["name"]
    # a defaut : celui qui a le plus ecrit (c'est vous qui prospectez)
    return participants[0]["name"] if participants else None


def guess_club(parsed, me, find_club, find_contact, chat_name=None):
    """Retrouve le club concerne : par telephone, puis par nom du correspondant."""
    others = [p["name"] for p in parsed["participants"] if p["name"] != me]
    blob = " ".join([chat_name or ""] + others)
    for phone in util.extract_phones(blob):
        contact = find_contact(None, phone)
        if contact and contact.get("club_id"):
            return contact["club_id"], "numero %s" % util.format_phone(phone)
    for name in [chat_name] + others:
        if not name:
            continue
        club = find_club(name)
        if club:
            return club["id"], "nom « %s »" % name
    return None, None


# --------------------------------------------------------------------------
# Cartes de contact partagees
# --------------------------------------------------------------------------

VCARD_BLOC = re.compile(r"BEGIN:VCARD(.*?)END:VCARD", re.S | re.I)


def _vcard_champ(bloc, nom):
    """Valeur d'un champ vCard, parametres et pliage compris."""
    motif = re.compile(r"^%s[^:\r\n]*:(.*)$" % nom, re.I | re.M)
    m = motif.search(bloc)
    if not m:
        return None
    valeur = m.group(1)
    # Une ligne vCard peut etre repliee : la suite commence par une espace.
    suite = bloc[m.end():]
    for ligne in suite.split("\n"):
        if ligne[:1] in (" ", "\t"):
            valeur += ligne.strip()
        else:
            break
    return valeur.strip()


def parse_vcards(texte):
    """Lit les cartes de contact d'un export WhatsApp.

    Partager un contact dans WhatsApp insere une vCard dans l'export texte.
    C'est le seul moyen de recuperer un numero depuis une carte partagee :
    a l'ecran, la carte n'affiche que le nom.
    """
    out = []
    for bloc in VCARD_BLOC.findall(_clean(texte or "")):
        nom = _vcard_champ(bloc, "FN")
        if not nom:
            n = _vcard_champ(bloc, "N")
            if n:
                morceaux = [p.strip() for p in n.split(";") if p.strip()]
                nom = " ".join(reversed(morceaux[:2])) if morceaux else None
        telephones, seen = [], set()
        for m in re.finditer(r"^TEL[^:\r\n]*:(.+)$", bloc, re.I | re.M):
            num = util.normalize_phone(m.group(1))
            if num and num not in seen:
                seen.add(num)
                telephones.append(num)
        emails = []
        for m in re.finditer(r"^EMAIL[^:\r\n]*:(.+)$", bloc, re.I | re.M):
            mail = util.normalize_email(m.group(1))
            if mail and mail not in emails:
                emails.append(mail)
        org = _vcard_champ(bloc, "ORG")
        titre = _vcard_champ(bloc, "TITLE")
        if not (nom or telephones or emails):
            continue
        out.append({
            "nom": (nom or "").strip().strip(";"),
            "telephones": telephones,
            "emails": emails,
            "organisation": (org or "").split(";")[0].strip() or None,
            "fonction": (titre or "").strip() or None,
        })
    return out


# ---------------------------------------------------------------------------
# Les liens WhatsApp
# ---------------------------------------------------------------------------
# Depuis le telephone, « Partager le contact > Copier le lien » et le bouton
# « Discuter » d'une page de club donnent une adresse wa.me. Le numero y est
# en clair, sans le +, et c'est souvent la seule forme qu'on a sous la main :
# WhatsApp pour Mac n'exporte pas les discussions, et une carte de contact
# affichee a l'ecran ne montre pas le numero.
#
#   https://wa.me/33612345678
#   https://wa.me/33612345678?text=Bonjour
#   https://api.whatsapp.com/send?phone=%2B33612345678
#   https://web.whatsapp.com/send/?phone=33612345678&text=...
#   whatsapp://send?phone=33612345678
#
# Attention a chat.whatsapp.com : c'est une invitation a un GROUPE, et elle ne
# contient aucun numero. On la reconnait pour pouvoir le dire, plutot que de
# laisser l'utilisateur croire que son lien n'a pas ete lu.
# Un lien se prend en entier (jusqu'au prochain espace), puis on y cherche le
# numero : sinon le « ?text=Bonjour » colle derriere resterait dans le nom.
LIEN_WA = re.compile(
    r"(?:https?://)?(?:www\.)?(?:wa\.me|api\.whatsapp\.com|web\.whatsapp\.com)/\S*"
    r"|whatsapp://\S*",
    re.I)

# « wa.me/33612345678 » ou « ...?phone=+33612345678 » (le + s'ecrit %2B dans une URL).
_NUM_DANS_LIEN = re.compile(r"(?:phone=\+?|wa\.me/)(\d{8,20})", re.I)

LIEN_GROUPE_WA = re.compile(
    r"(?:https?://)?(?:chat\.whatsapp\.com|wa\.me/g/)[^\s]*", re.I)


def numeros_depuis_liens(texte):
    """Renvoie (numeros, texte_sans_les_liens) pour un morceau de texte.

    Le texte rendu est debarrasse des adresses, pour que ce qui reste puisse
    servir de nom et de club sans trainer une URL au milieu.
    """
    if not texte:
        return [], texte or ""
    reste = texte
    numeros, vus = [], set()
    for m in LIEN_WA.finditer(texte):
        lien = m.group(0)
        reste = reste.replace(lien, " ")
        brut = lien.replace("%2B", "+").replace("%20", "")
        trouve = _NUM_DANS_LIEN.search(brut)
        if not trouve:
            continue                      # lien de groupe, ou adresse sans numero
        num = util.normalize_phone("+" + trouve.group(1))
        if num and num not in vus:
            vus.add(num)
            numeros.append(num)
    return numeros, reste


def lien_de_groupe(texte):
    """Vrai si le texte porte une invitation de groupe (donc aucun numero)."""
    if not texte:
        return False
    return bool(LIEN_GROUPE_WA.search(texte))
