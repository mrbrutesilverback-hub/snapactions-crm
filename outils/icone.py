# -*- coding: utf-8 -*-
"""Compose l'icone de l'application : logo blanc sur fond noir.

sips ne sait pas composer : il redimensionne et il complete les bords, mais
il laisse la transparence du logo intacte — l'icone etait donc percee, et le
logo minuscule au milieu d'un carre blanc.

On decode le PNG a la main (zlib est dans la bibliotheque standard), on pose
le logo agrandi sur un fond noir, et on reecrit un PNG opaque. Aucune
dependance, rien a installer.
"""
import struct
import sys
import zlib

FOND = (0, 0, 0)          # noir
PART = 0.78               # part de la largeur occupee par le logo


def lire_png(chemin):
    """Rend (largeur, hauteur, pixels RGBA). Refuse ce qu'il ne sait pas lire."""
    with open(chemin, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("ce n'est pas un PNG")
    i, brut, larg, haut, prof, genre = 8, b"", 0, 0, 0, 0
    palette, alpha_pal = None, None
    while i < len(data):
        taille = struct.unpack(">I", data[i:i + 4])[0]
        nom = data[i + 4:i + 8]
        corps = data[i + 8:i + 8 + taille]
        if nom == b"IHDR":
            larg, haut, prof, genre = struct.unpack(">IIBB", corps[:10])
        elif nom == b"PLTE":
            palette = corps
        elif nom == b"tRNS":
            alpha_pal = corps
        elif nom == b"IDAT":
            brut += corps
        elif nom == b"IEND":
            break
        i += 12 + taille
    if prof != 8:
        raise ValueError("profondeur %d non geree" % prof)
    canaux = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(genre)
    if canaux is None:
        raise ValueError("type de couleur %d non gere" % genre)

    flux = zlib.decompress(brut)
    pas = larg * canaux
    lignes, precedente, p = [], bytearray(pas), 0
    for _ in range(haut):
        filtre = flux[p]; p += 1
        ligne = bytearray(flux[p:p + pas]); p += pas
        for x in range(pas):
            a = ligne[x - canaux] if x >= canaux else 0
            b = precedente[x]
            c = precedente[x - canaux] if x >= canaux else 0
            if filtre == 1:   ligne[x] = (ligne[x] + a) & 255
            elif filtre == 2: ligne[x] = (ligne[x] + b) & 255
            elif filtre == 3: ligne[x] = (ligne[x] + (a + b) // 2) & 255
            elif filtre == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                ligne[x] = (ligne[x] + pred) & 255
        lignes.append(ligne)
        precedente = ligne

    px = bytearray(larg * haut * 4)
    for y in range(haut):
        ligne = lignes[y]
        for x in range(larg):
            s = x * canaux
            if genre == 6:   r, v, b_, a = ligne[s], ligne[s+1], ligne[s+2], ligne[s+3]
            elif genre == 2: r, v, b_, a = ligne[s], ligne[s+1], ligne[s+2], 255
            elif genre == 0: r = v = b_ = ligne[s]; a = 255
            elif genre == 4: r = v = b_ = ligne[s]; a = ligne[s+1]
            else:
                idx = ligne[s]
                r, v, b_ = palette[idx*3], palette[idx*3+1], palette[idx*3+2]
                a = alpha_pal[idx] if alpha_pal and idx < len(alpha_pal) else 255
            d = (y * larg + x) * 4
            px[d], px[d+1], px[d+2], px[d+3] = r, v, b_, a
    return larg, haut, px


def ecrire_png(chemin, larg, haut, rgb):
    def bloc(nom, corps):
        return (struct.pack(">I", len(corps)) + nom + corps
                + struct.pack(">I", zlib.crc32(nom + corps) & 0xFFFFFFFF))
    lignes = bytearray()
    for y in range(haut):
        lignes.append(0)
        lignes += rgb[y * larg * 3:(y + 1) * larg * 3]
    with open(chemin, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(bloc(b"IHDR", struct.pack(">IIBBBBB", larg, haut, 8, 2, 0, 0, 0)))
        f.write(bloc(b"IDAT", zlib.compress(bytes(lignes), 9)))
        f.write(bloc(b"IEND", b""))


def composer(source, sortie, cote=1024):
    lw, lh, lp = lire_png(source)
    # Le logo occupe PART du cote, sans se deformer.
    echelle = min(cote * PART / lw, cote * PART / lh)
    nw, nh = max(1, int(lw * echelle)), max(1, int(lh * echelle))
    ox, oy = (cote - nw) // 2, (cote - nh) // 2

    toile = bytearray(FOND * (cote * cote))
    for y in range(nh):
        sy = int(y / echelle)
        if sy >= lh:
            continue
        for x in range(nw):
            sx = int(x / echelle)
            if sx >= lw:
                continue
            s = (sy * lw + sx) * 4
            a = lp[s + 3]
            if not a:
                continue
            d = ((oy + y) * cote + (ox + x)) * 3
            for k in range(3):
                toile[d + k] = (lp[s + k] * a + toile[d + k] * (255 - a)) // 255
    ecrire_png(sortie, cote, cote, toile)
    return cote


if __name__ == "__main__":
    composer(sys.argv[1], sys.argv[2],
             int(sys.argv[3]) if len(sys.argv) > 3 else 1024)
    print("icone composee : %s" % sys.argv[2])
