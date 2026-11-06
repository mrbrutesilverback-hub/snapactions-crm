# -*- coding: utf-8 -*-
"""Lecture de classeurs .xlsx sans dependance : un .xlsx est un ZIP de XML.

Gere les chaines partagees, le texte enrichi, les formules, les dates serielles
et les feuilles multiples. Ne lit pas le vieux format binaire .xls.
"""
import datetime as dt
import re
import zipfile
import xml.etree.ElementTree as ET

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# Formats numeriques integres qui designent une date ou une heure.
BUILTIN_DATE_FMT = set(range(14, 23)) | set(range(45, 48)) | {27, 30, 36, 50, 57}
DATE_CHARS = re.compile(r"[ymdhs]", re.I)


class XlsxError(Exception):
    pass


def col_index(ref):
    """'A1' -> 0, 'B7' -> 1, 'AA3' -> 26."""
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return 0
    idx = 0
    for ch in letters.group(1):
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def serial_to_date(value, date1904=False):
    """Convertit un numero de serie Excel en date/heure."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    epoch = dt.datetime(1904, 1, 1) if date1904 else dt.datetime(1899, 12, 30)
    # Excel considere a tort 1900 comme bissextile : les series < 61 sont decalees.
    if not date1904 and number < 61:
        number += 1
    try:
        moment = epoch + dt.timedelta(days=number)
    except OverflowError:
        return None
    if moment.hour or moment.minute:
        return moment
    return moment.date()


def _text_of(node):
    """Texte d'un <si> ou d'un <is>, en recollant les fragments enrichis."""
    parts = []
    for t in node.iter(NS_MAIN + "t"):
        parts.append(t.text or "")
    return "".join(parts)


def _load_shared_strings(zf):
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [_text_of(si) for si in root.findall(NS_MAIN + "si")]


def _load_date_styles(zf):
    """Indices de style (cellXfs) qui correspondent a un format date."""
    try:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    except KeyError:
        return set()
    custom = {}
    fmts = root.find(NS_MAIN + "numFmts")
    if fmts is not None:
        for f in fmts.findall(NS_MAIN + "numFmt"):
            code = f.get("formatCode") or ""
            stripped = re.sub(r'\[[^\]]*\]|"[^"]*"', "", code)
            if DATE_CHARS.search(stripped):
                custom[int(f.get("numFmtId"))] = True
    out = set()
    xfs = root.find(NS_MAIN + "cellXfs")
    if xfs is None:
        return out
    for i, xf in enumerate(xfs.findall(NS_MAIN + "xf")):
        fmt_id = int(xf.get("numFmtId") or 0)
        if fmt_id in BUILTIN_DATE_FMT or custom.get(fmt_id):
            out.add(i)
    return out


def _sheet_paths(zf):
    """[(nom de feuille, chemin dans le zip)] dans l'ordre du classeur."""
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
    except KeyError:
        raise XlsxError("classeur illisible : xl/workbook.xml absent")
    rels = {}
    try:
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            target = rel.get("Target") or ""
            if target.startswith("/"):
                target = target[1:]
            elif not target.startswith("xl/"):
                target = "xl/" + target
            rels[rel.get("Id")] = target.replace("xl/worksheets/../", "xl/")
    except KeyError:
        pass
    out = []
    sheets = wb.find(NS_MAIN + "sheets")
    for i, sheet in enumerate(sheets.findall(NS_MAIN + "sheet") if sheets is not None else []):
        rid = sheet.get(NS_REL + "id")
        path = rels.get(rid) or ("xl/worksheets/sheet%d.xml" % (i + 1))
        if path in zf.namelist():
            out.append((sheet.get("name") or ("Feuille %d" % (i + 1)), path))
    if not out:
        out = [(n.rsplit("/", 1)[-1], n) for n in zf.namelist()
               if n.startswith("xl/worksheets/sheet")]
    return out


def _is_date1904(zf):
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
    except KeyError:
        return False
    pr = wb.find(NS_MAIN + "workbookPr")
    return bool(pr is not None and pr.get("date1904") in ("1", "true"))


def read_sheet(source, sheet=None, max_rows=20000):
    """Renvoie (nom_feuille, lignes) ou lignes est une liste de listes de chaines."""
    with zipfile.ZipFile(source) as zf:
        sheets = _sheet_paths(zf)
        if not sheets:
            raise XlsxError("aucune feuille trouvee dans le classeur")
        chosen = sheets[0]
        if sheet is not None:
            for name, path in sheets:
                if name == sheet or path == sheet:
                    chosen = (name, path)
                    break
        strings = _load_shared_strings(zf)
        date_styles = _load_date_styles(zf)
        date1904 = _is_date1904(zf)
        rows = []
        root = ET.fromstring(zf.read(chosen[1]))
        data = root.find(NS_MAIN + "sheetData")
        for row_el in (data.findall(NS_MAIN + "row") if data is not None else []):
            cells = {}
            for c in row_el.findall(NS_MAIN + "c"):
                value = _cell_value(c, strings, date_styles, date1904)
                if value != "":
                    cells[col_index(c.get("r") or "")] = value
            if cells:
                width = max(cells) + 1
                rows.append([cells.get(i, "") for i in range(width)])
            else:
                rows.append([])
            if len(rows) >= max_rows:
                break
        while rows and not any(rows[-1]):
            rows.pop()
        return chosen[0], rows


def sheet_names(source):
    with zipfile.ZipFile(source) as zf:
        return [name for name, _ in _sheet_paths(zf)]


def _cell_value(c, strings, date_styles, date1904):
    ctype = c.get("t")
    if ctype == "inlineStr":
        is_el = c.find(NS_MAIN + "is")
        return _text_of(is_el) if is_el is not None else ""
    v = c.find(NS_MAIN + "v")
    if v is None or v.text is None:
        return ""
    raw = v.text
    if ctype == "s":
        try:
            return strings[int(raw)]
        except (ValueError, IndexError):
            return ""
    if ctype == "b":
        return "1" if raw == "1" else "0"
    if ctype in ("str", "e"):
        return raw
    style = c.get("s")
    if style is not None and int(style) in date_styles:
        converted = serial_to_date(raw, date1904)
        if converted is not None:
            return converted.isoformat() if not isinstance(converted, dt.datetime) \
                else converted.isoformat(sep=" ")
    # Nombre : on evite la notation scientifique et le '.0' des entiers,
    # sinon un numero de telephone stocke en nombre devient illisible.
    try:
        number = float(raw)
    except ValueError:
        return raw
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return repr(number) if "e" not in raw.lower() else ("%.10f" % number).rstrip("0")


def to_table(source, sheet=None):
    """(nom_feuille, entetes, lignes) — la premiere ligne non vide sert d'entete."""
    name, rows = read_sheet(source, sheet)
    if not rows:
        return name, [], []
    # On saute d'eventuelles lignes de titre avant le vrai entete.
    start = 0
    for i, row in enumerate(rows[:10]):
        filled = [c for c in row if str(c).strip()]
        if len(filled) >= 2:
            start = i
            break
    headers = [str(c).strip() for c in rows[start]]
    body = [r for r in rows[start + 1:] if any(str(c).strip() for c in r)]
    width = len(headers)
    body = [(r + [""] * width)[:width] for r in body]
    return name, headers, body
