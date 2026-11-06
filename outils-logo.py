# -*- coding: utf-8 -*-
"""Fabrique la variante claire du logo depuis le PNG transparent, sans dependance.

Le PNG est en RGBA 8 bits non entrelace : on peut le decoder, transformer les
pixels puis le reencoder avec zlib seul.
"""
import struct
import sys
import zlib


def lire_png(chemin):
    raw = open(chemin, "rb").read()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "pas un PNG"
    pos, idat, ihdr = 8, b"", None
    while pos < len(raw):
        ln = struct.unpack(">I", raw[pos:pos + 4])[0]
        typ = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", data)
        elif typ == b"IDAT":
            idat += data
        pos += 12 + ln
    w, h, depth, ctype, comp, filt, inter = ihdr
    assert depth == 8 and ctype == 6 and inter == 0, "format non gere"
    return w, h, bytearray(zlib.decompress(idat))


def defiltrer(w, h, brut):
    """Annule les filtres de ligne PNG (0=None 1=Sub 2=Up 3=Average 4=Paeth)."""
    bpp, stride = 4, w * 4
    out = bytearray(h * stride)
    pos = 0
    for y in range(h):
        f = brut[pos]; pos += 1
        ligne = brut[pos:pos + stride]; pos += stride
        deb = y * stride
        prec = out[deb - stride:deb] if y else bytearray(stride)
        for x in range(stride):
            a = out[deb + x - bpp] if x >= bpp else 0
            b = prec[x]
            c = prec[x - bpp] if x >= bpp else 0
            v = ligne[x]
            if f == 1:
                v += a
            elif f == 2:
                v += b
            elif f == 3:
                v += (a + b) // 2
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                v += a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            out[deb + x] = v & 0xFF
    return out


def _est_couleur(r, g, b):
    return max(r, g, b) - min(r, g, b) >= 46


def eclaircir(w, h, px):
    """Le trait noir devient blanc, le bleu et le rouge sont gardes.

    Exception : la bande tricolore. Sur une ligne qui contient du bleu ou du
    rouge, le blanc est celui du drapeau — on le laisse blanc au lieu de
    l'inverser en noir.
    """
    stride = w * 4
    lignes_drapeau = set()
    for y in range(h):
        deb = y * stride
        for x in range(0, stride, 4):
            r, g, b, a = px[deb + x:deb + x + 4]
            if a > 200 and _est_couleur(r, g, b):
                lignes_drapeau.add(y)
                break
    for y in range(h):
        deb = y * stride
        drapeau = y in lignes_drapeau
        for x in range(0, stride, 4):
            i = deb + x
            r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
            if a == 0 or _est_couleur(r, g, b):
                continue                          # transparent, bleu ou rouge
            if drapeau and r > 160:
                continue                          # blanc du drapeau : conserve
            px[i] = 255 - r
            px[i + 1] = 255 - g
            px[i + 2] = 255 - b
    return px


def ecrire_png(chemin, w, h, px):
    brut = bytearray()
    stride = w * 4
    for y in range(h):
        brut.append(0)                            # filtre None
        brut += px[y * stride:(y + 1) * stride]

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    with open(chemin, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(bytes(brut), 9)))
        fh.write(chunk(b"IEND", b""))


src, dst = sys.argv[1], sys.argv[2]
w, h, brut = lire_png(src)
px = eclaircir(w, h, defiltrer(w, h, brut))
ecrire_png(dst, w, h, px)
print("écrit : %s (%dx%d)" % (dst, w, h))
