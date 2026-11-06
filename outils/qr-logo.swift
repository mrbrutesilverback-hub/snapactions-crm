// -*- swift -*-
// Un QR code SnapActions, avec la marque au centre.
//
// Poser un logo sur un QR, c'est effacer des modules. Ca ne marche que si le
// code a de quoi les reconstituer : la correction d'erreur. Le QR que MediaDay
// fournit est en niveau M — 15 % de redondance, deja entamee par l'impression,
// l'angle du telephone et la lumiere du gymnase. On ne pose rien dessus.
//
// On refait donc le code a partir du MEME lien, en niveau H (30 %), et on ne
// couvre que le centre, la ou aucun motif de reperage ne se trouve. Puis on
// verifie : l'image ecrite est relue, et relue encore a des tailles de plus en
// plus petites, pour savoir a partir de quelle taille elle cesse d'etre lue.
// Un QR qui ne se relit pas n'est pas ecrit sur le disque.
//
//   swift outils/qr-logo.swift --texte "https://..." --sortie qr.png
//   swift outils/qr-logo.swift --depuis flyers/panneau.png --sortie qr.png
//
// Options : --logo <png>  --taille <px>  --part <0.22>  --correction H|Q|M|L
//           --couleur "#RRGGBB"  --sans-logo  --muet

import Foundation
import CoreImage
import AppKit

// ---------------------------------------------------------------- arguments

func arg(_ nom: String) -> String? {
    let a = CommandLine.arguments
    guard let i = a.firstIndex(of: nom), i + 1 < a.count else { return nil }
    return a[i + 1]
}
func drapeau(_ nom: String) -> Bool { CommandLine.arguments.contains(nom) }

let muet = drapeau("--muet")
func dire(_ s: String) { if !muet { print(s) } }

let racine = FileManager.default.currentDirectoryPath
let cheminLogo = arg("--logo") ?? "\(racine)/prospect/static/img/marque-qr.png"
let taille = Int(arg("--taille") ?? "1400") ?? 1400
let part = Double(arg("--part") ?? "0.22") ?? 0.22
let correction = (arg("--correction") ?? "H").uppercased()
let sansLogo = drapeau("--sans-logo")

guard let sortie = arg("--sortie") else {
    print("ERREUR : il manque --sortie <fichier.png>"); exit(1)
}
guard ["L", "M", "Q", "H"].contains(correction) else {
    print("ERREUR : --correction doit valoir L, M, Q ou H"); exit(1)
}
guard part > 0, part <= 0.30 else {
    print("ERREUR : --part doit rester entre 0 et 0.30 (au-dela, le code devient illisible)")
    exit(1)
}

let ctx = CIContext(options: [.useSoftwareRenderer: false])

// ------------------------------------------------------------------- lecture

/// Relit un QR dans une image. Rend la charge utile, ou nil.
func lireQR(_ image: CIImage) -> String? {
    let det = CIDetector(ofType: CIDetectorTypeQRCode, context: ctx,
                         options: [CIDetectorAccuracy: CIDetectorAccuracyHigh])
    for f in det?.features(in: image) ?? [] {
        if let q = f as? CIQRCodeFeature, let m = q.messageString { return m }
    }
    return nil
}

func lireQRFichier(_ chemin: String) -> String? {
    guard let img = CIImage(contentsOf: URL(fileURLWithPath: chemin)) else { return nil }
    return lireQR(img)
}

// La charge : soit donnee, soit relue dans un QR existant.
var charge: String
if let t = arg("--texte") {
    charge = t
} else if let src = arg("--depuis") {
    guard let lu = lireQRFichier(src) else {
        print("ERREUR : aucun QR lisible dans \(src)"); exit(2)
    }
    charge = lu
    dire("Lu dans \(src) :\n  \(lu)")
} else {
    print("ERREUR : il faut --texte \"...\" ou --depuis <image.png>"); exit(1)
}

// ---------------------------------------------------------------- generation

/// La grille de modules, un pixel par module.
func grilleQR(_ texte: String, _ niveau: String) -> CIImage? {
    guard let d = texte.data(using: .isoLatin1) ?? texte.data(using: .utf8) else { return nil }
    let f = CIFilter(name: "CIQRCodeGenerator")
    f?.setValue(d, forKey: "inputMessage")
    f?.setValue(niveau, forKey: "inputCorrectionLevel")
    return f?.outputImage
}

guard let grille = grilleQR(charge, correction) else {
    print("ERREUR : le texte n'a pas pu etre encode"); exit(3)
}

// CIQRCodeGenerator rend l'image avec une marge d'un module. On la retire pour
// poser la notre, de quatre modules — celle que la norme demande.
let brut = grille.extent
let modulesAvecMarge = Int(brut.width.rounded())
let bordNatif = 1
let modules = modulesAvecMarge - 2 * bordNatif
let marge = 4
let total = modules + 2 * marge

// Un module = un nombre ENTIER de pixels, sinon le scanner voit des bords flous.
let parModule = max(1, Int((Double(taille) / Double(total)).rounded()))
let cote = total * parModule

dire("Code : version a \(modules) modules, correction \(correction), "
     + "\(parModule) px par module, image \(cote)x\(cote).")

// ------------------------------------------------------------------- dessin

func couleur(_ hexa: String?) -> NSColor {
    guard var h = hexa else { return .black }
    h = h.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
    guard h.count == 6, let v = UInt32(h, radix: 16) else { return .black }
    return NSColor(srgbRed: CGFloat((v >> 16) & 255) / 255.0,
                   green: CGFloat((v >> 8) & 255) / 255.0,
                   blue: CGFloat(v & 255) / 255.0, alpha: 1)
}
let encre = couleur(arg("--couleur"))

/// Le rectangle d'encre du logo : on jette la marge blanche du fichier.
func cadreUtile(_ cg: CGImage) -> CGRect {
    let l = cg.width, h = cg.height
    guard let donnees = CFDataGetBytePtr(cg.dataProvider?.data) else {
        return CGRect(x: 0, y: 0, width: l, height: h)
    }
    let parLigne = cg.bytesPerRow
    let parPixel = cg.bitsPerPixel / 8
    var x0 = l, y0 = h, x1 = -1, y1 = -1
    for y in 0..<h {
        for x in 0..<l {
            let p = y * parLigne + x * parPixel
            let a = parPixel >= 4 ? Int(donnees[p + 3]) : 255
            let r = Int(donnees[p]), v = Int(donnees[p + 1]), b = Int(donnees[p + 2])
            // De l'encre : opaque, et pas blanc.
            if a > 40 && (r + v + b) / 3 < 220 {
                if x < x0 { x0 = x }; if x > x1 { x1 = x }
                if y < y0 { y0 = y }; if y > y1 { y1 = y }
            }
        }
    }
    if x1 < 0 { return CGRect(x: 0, y: 0, width: l, height: h) }
    return CGRect(x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1)
}

func dessiner() -> NSBitmapImageRep? {
    guard let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: cote, pixelsHigh: cote,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return nil }

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let g = NSGraphicsContext.current!
    g.imageInterpolation = .none

    NSColor.white.setFill()
    NSBezierPath(rect: NSRect(x: 0, y: 0, width: cote, height: cote)).fill()

    // La grille, sans la marge native, agrandie sans lissage.
    let utile = brut.insetBy(dx: CGFloat(bordNatif), dy: CGFloat(bordNatif))
    guard let cgGrille = ctx.createCGImage(grille, from: utile) else {
        NSGraphicsContext.restoreGraphicsState(); return nil
    }
    let zone = NSRect(x: marge * parModule, y: marge * parModule,
                      width: modules * parModule, height: modules * parModule)
    // CIQRCodeGenerator rend les modules en NOIR sur fond transparent : on
    // dessine la forme, puis on la teinte si une couleur a ete demandee.
    g.cgContext.saveGState()
    g.cgContext.interpolationQuality = .none
    g.cgContext.draw(cgGrille, in: zone)
    if encre != .black {
        g.cgContext.setBlendMode(.sourceIn)
        encre.setFill()
        NSBezierPath(rect: zone).fill()
    }
    g.cgContext.restoreGState()

    // La marque, au centre.
    if !sansLogo {
        let viseLargeur = Double(modules * parModule) * part
        // Arrondi au module : le pave blanc tombe sur la grille, pas entre deux.
        let modulesLogo = max(3, Int((viseLargeur / Double(parModule)).rounded()))
        let largeurPave = modulesLogo * parModule
        let centre = cote / 2
        let pave = NSRect(x: centre - largeurPave / 2, y: centre - largeurPave / 2,
                          width: largeurPave, height: largeurPave)
        NSColor.white.setFill()
        NSBezierPath(roundedRect: pave, xRadius: CGFloat(parModule),
                     yRadius: CGFloat(parModule)).fill()

        if let dl = NSImage(contentsOfFile: cheminLogo),
           let cgl = dl.cgImage(forProposedRect: nil, context: nil, hints: nil) {
            let utileLogo = cadreUtile(cgl)
            guard let rogne = cgl.cropping(to: utileLogo) else {
                NSGraphicsContext.restoreGraphicsState(); return nil
            }
            // Le logo respire : il ne touche pas le bord du pave.
            let dedans = pave.insetBy(dx: CGFloat(parModule) * 1.2,
                                      dy: CGFloat(parModule) * 1.2)
            let r = Double(rogne.width) / Double(rogne.height)
            var l = dedans.width, h = dedans.height
            if r > 1 { h = l / CGFloat(r) } else { l = h * CGFloat(r) }
            let place = NSRect(x: dedans.midX - l / 2, y: dedans.midY - h / 2,
                               width: l, height: h)
            g.cgContext.interpolationQuality = .high
            g.cgContext.draw(rogne, in: place)
        } else {
            NSGraphicsContext.restoreGraphicsState()
            print("ERREUR : logo illisible — \(cheminLogo)")
            exit(4)
        }
    }

    NSGraphicsContext.restoreGraphicsState()
    return rep
}

guard let rep = dessiner(), let png = rep.representation(using: .png, properties: [:]) else {
    print("ERREUR : le dessin a echoue"); exit(5)
}

// -------------------------------------------------------------- verification
// Rien n'est ecrit tant que l'image ne s'est pas relue elle-meme.

guard let relu = CIImage(data: png), let trouve = lireQR(relu) else {
    print("REFUSE : le QR produit ne se relit pas. Rien n'a ete ecrit.")
    print("         Essayez --part plus petit (0.14) ou --correction H.")
    exit(6)
}
guard trouve == charge else {
    print("REFUSE : le QR relu ne donne pas le meme lien. Rien n'a ete ecrit.")
    exit(7)
}

/// A quelle taille le code cesse-t-il d'etre lu ? Un telephone tenu a un metre
/// d'un panneau A5 voit a peu pres 200 px de QR.
func plusPetiteTailleLue(_ image: CIImage) -> Int? {
    var derniere: Int? = nil
    for large in [900, 760, 640, 540, 460, 400, 350, 300, 260, 220, 190, 160, 140, 120, 100] {
        let f = CGFloat(large) / image.extent.width
        var essai = image.transformed(by: CGAffineTransform(scaleX: f, y: f))
        // Un telephone tenu a la main ne rend pas des pixels nets : il tremble,
        // il met au point a peu pres, le papier n'est pas plan. On floute donc
        // legerement avant de relire, sinon le chiffre annonce est trop beau.
        if let flou = CIFilter(name: "CIGaussianBlur",
                               parameters: [kCIInputImageKey: essai,
                                            kCIInputRadiusKey: 0.8]),
           let f2 = flou.outputImage {
            essai = f2.cropped(to: essai.extent)
        }
        // On repasse par un bitmap : c'est ce que voit un appareil photo.
        guard let cg = ctx.createCGImage(essai, from: essai.extent) else { continue }
        if lireQR(CIImage(cgImage: cg)) == charge { derniere = large } else { break }
    }
    return derniere
}

let seuil = plusPetiteTailleLue(relu)

do {
    try png.write(to: URL(fileURLWithPath: sortie))
} catch {
    print("ERREUR : ecriture impossible — \(error.localizedDescription)"); exit(8)
}

dire("Relu : identique au lien d'origine.")
if let s = seuil {
    dire("Lisible jusqu'a \(s) px de cote.")
} else {
    dire("Lisible a 600 px, pas teste en dessous.")
}
dire("Ecrit : \(sortie)")
