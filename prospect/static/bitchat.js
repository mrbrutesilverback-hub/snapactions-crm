/* Les gestes vivants de bitchat, poses sur le CRM.
   bitchat.css donne les couleurs ; ce fichier donne ce qui bouge ou ce qui
   se calcule, toujours a partir de ce que l'ecran affiche deja. Aucun ecran
   n'est modifie : tout est pose apres coup, et retire quand l'ecran change.

   - Les attentes : le sonar de MeshRadarView devant tout texte d'attente
     (« Lecture… », « Je cherche… »), en grand a la place d'un bloc « … ».
   - L'etat de nos messages, comme DeliveryStatusView : ✓ envoye, ✓✓ bleu
     lu. Le CRM ne lit pas les accuses de WhatsApp ; « lu » ne se pose donc
     que quand ils ont repondu apres — c'est la seule preuve qu'on ait.
     Bitchat le dit lui-meme : un etat affiche a tort est pire que rien.
   - Le direct, comme LiveVoiceBadge : la date d'un evenement qui a lieu
     aujourd'hui pulse en rouge.
   - La voix, comme WaveformView : la dictee en cours montre son onde.
   - Les images, comme BlockRevealImageView : elles arrivent bloc par bloc,
     ligne apres ligne.
   - Les expediteurs, comme Color+Peer : chaque source de la veille a sa
     couleur, toujours la meme. */
(function () {
  const calme = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- Les attentes -------------------------------------------------------
     Les endroits qui font patienter ecrivent tous un texte termine par « … ».
     Seuls les boutons et les lignes d'etat sont regardes : un nom de sport ou
     un apercu de message coupe par « … » n'est pas une attente. Le scanner
     du Reperage a deja son radar, on le laisse. */
  const HOTES = "button, .btn, .geste, .chapeau, .ia-etat, .hint, .chargement, "
    + ".dit, .avert, .mini-vide, .carte-mot, .etiq-fil, .toast, [data-attente]";

  /* Trois ondes decalees d'un tiers de periode, et le point au centre. Des
     balises a part, et non des <span>, pour qu'aucun querySelector("span")
     d'un ecran ne tombe dessus. */
  const SONAR = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">'
    + '<circle class="bc-onde" cx="8" cy="8" r="7"/>'
    + '<circle class="bc-onde" cx="8" cy="8" r="7"/>'
    + '<circle class="bc-onde" cx="8" cy="8" r="7"/>'
    + '<circle class="bc-point" cx="8" cy="8" r="1.3"/></svg>';

  const finit = s => /…\s*$/.test(s || "");

  /* Le texte qui compte : tout le texte de l'element, ou sa premiere ligne
     quand une precision la suit (« Lecture du calendrier… <span>quelques
     secondes</span> »). */
  function attend(hote) {
    if (finit(hote.textContent)) return true;
    for (const n of hote.childNodes) {
      if (n.nodeType === 1 && n.localName === "bc-sonar") continue;
      return n.nodeType === 3 && finit(n.textContent);
    }
    return false;
  }

  function regler(hote) {
    if (!hote.isConnected || hote.closest(".scouter")) return;
    const deja = Array.prototype.find.call(hote.children, e => e.localName === "bc-sonar");
    if (attend(hote)) {
      hote.classList.add("bc-attente");
      if (!deja) {
        const s = document.createElement("bc-sonar");
        s.innerHTML = SONAR;
        hote.insertBefore(s, hote.firstChild);
      }
    } else if (deja || hote.classList.contains("bc-attente")) {
      hote.classList.remove("bc-attente");
      if (deja) deja.remove();
    }
  }

  /* ---- L'etat de nos messages --------------------------------------------
     Les fils sont dans l'ordre (le plus ancien d'abord). Sur les fiches de
     relance, le dernier message est sorti du fil et affiche avant lui ;
     l'echange plus ancien est replie dessous. */
  const COCHE = '<path d="M3 8.6l3.1 3.1L13 4.8" fill="none" stroke="currentColor" '
    + 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>';
  const ROND_COCHE = '<circle cx="8" cy="8" r="7.2" fill="currentColor"/>'
    + '<path d="M4.6 8.3l2.3 2.3 4.6-4.8" fill="none" style="stroke:var(--bg)" '
    + 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>';
  const glyphe = d => '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">' + d + '</svg>';

  function luApres(el) {
    for (let s = el.nextElementSibling; s; s = s.nextElementSibling) {
      if (s.matches(".bulle.eux, .msg.lui")) return true;
    }
    const replie = el.parentElement && el.parentElement.closest("details.avant");
    if (replie) {
      const dernier = replie.previousElementSibling;
      if (dernier && dernier.matches(".msg.lui")) return true;
    }
    return false;
  }

  /* L'heure en fin de ligne, grise et petite : « … message [14:32] ». Un
     message d'un autre jour porte aussi sa date : « [23/10 14:32] ». Les
     ecrans la posent dans data-quand (« 2026-10-23 14:32 »). */
  function heureLisible(q) {
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(q || "");
    if (!m) return "";
    const auj = new Date();
    const memeJour = +m[1] === auj.getFullYear() && +m[2] === auj.getMonth() + 1
      && +m[3] === auj.getDate();
    const h = m[4] ? m[4] + ":" + m[5] : "";
    if (memeJour) return h || "aujourd’hui";
    return m[3] + "/" + m[2] + (h ? " " + h : "");
  }

  function heures(racine) {
    racine.querySelectorAll(".bulle[data-quand], .msg[data-quand]").forEach(m => {
      if (Array.prototype.some.call(m.children, e => e.localName === "bc-heure")) return;
      const texte = heureLisible(m.dataset.quand);
      if (!texte) return;
      const h = document.createElement("bc-heure");
      h.textContent = "[" + texte + "]";
      const etat = Array.prototype.find.call(m.children, e => e.localName === "bc-etat");
      m.insertBefore(h, etat || null);
    });
  }

  function etats(racine) {
    heures(racine);
    racine.querySelectorAll(".bulle.moi, .msg.moi").forEach(m => {
      const lu = luApres(m);
      const deja = Array.prototype.find.call(m.children, e => e.localName === "bc-etat");
      if (deja && deja.classList.contains(lu ? "lu" : "envoye")) return;
      const e = deja || document.createElement("bc-etat");
      e.className = lu ? "lu" : "envoye";
      e.setAttribute("role", "img");
      e.title = lu ? "Lu : ils ont répondu après" : "Envoyé, pas encore de réponse";
      e.setAttribute("aria-label", e.title);
      e.innerHTML = lu ? glyphe(ROND_COCHE) + glyphe(ROND_COCHE)
                       : glyphe(COCHE);
      if (!deja) m.appendChild(e);
    });
  }

  /* ---- La couleur d'un expediteur ------------------------------------------
     Color+Peer : chaque pair de bitchat a sa teinte, tiree d'un hachage djb2
     de son identifiant, en evitant l'orange (reserve a soi). Ici, chaque
     source de la veille sur les fiches du Reperage, et chaque contact ou club
     nomme en tete de carte. Deux teintes par nom,
     une par theme, que bitchat.css choisit. En sombre, la clarte ne descend
     pas sous 58 %, et sous 70 % pour les bleus et les violets : un bleu
     sature sur du noir ne se lisait pas. */
  function teintePair(graine, sombre) {
    let h = 5381;
    for (let i = 0; i < graine.length; i++) h = ((h << 5) + h + graine.charCodeAt(i)) >>> 0;
    let teinte = (h % 1000) / 1000;
    if (Math.abs(teinte - 30 / 360) < 0.06) teinte = (teinte + 0.25) % 1;
    const sAlea = ((h >>> 17) & 0x3FF) / 1023, bAlea = ((h >>> 27) & 0x1F) / 31;
    const s = Math.min(1, Math.max(0.5, (sombre ? 0.8 : 0.7) + (sAlea - 0.5) * 0.2));
    const v = Math.min(1, Math.max(0.35, (sombre ? 0.75 : 0.45) + (bAlea - 0.5) * (sombre ? 0.16 : 0.14)));
    let l = v * (1 - s / 2);
    const sl = l === 0 || l === 1 ? 0 : (v - l) / Math.min(l, 1 - l);
    if (sombre) l = Math.max(l, teinte > 0.55 && teinte < 0.82 ? 0.7 : 0.58);
    return "hsl(" + Math.round(teinte * 360) + " " + Math.round(sl * 100) + "% " + Math.round(l * 100) + "%)";
  }
  /* Les sources de la veille, et les gens : contacts, clubs en tete de carte. */
  const PAIRS = ".f-source, .gens h3, .acte button.qui, #attendent .item .t, h2.qui, .carte-club button.ouvrir, .club-nom";
  function pairs(racine) {
    racine.querySelectorAll(PAIRS).forEach(el => {
      const graine = el.textContent.trim().toLowerCase();
      if (!graine || el.dataset.bcPair === graine) return;
      el.dataset.bcPair = graine;
      el.style.setProperty("--pair-s", teintePair(graine, true));
      el.style.setProperty("--pair-c", teintePair(graine, false));
    });
  }

  /* ---- Les categories d'age ------------------------------------------------
     « U11 à 8 » : l'age, puis le format de jeu. L'age passe en bloc inverse
     (bitchat.css), le format reste dedans, plus discret. Le texte de la
     categorie ne change pas — seule sa mise en forme. */
  const AGE = /^(U\s?\d{1,2}|baby|mini|poussin|pupille|benjamin|minime|cadet|junior|[eé]cole)/i;
  function ages(racine) {
    racine.querySelectorAll(".f-cat:not([data-bc-age])").forEach(c => {
      const texte = c.textContent.trim();
      c.dataset.bcAge = "";
      if (!AGE.test(texte)) return;
      c.classList.add("bc-age");
      /* Les petits, de U6 a U10 : les parents achetent le plus de photos.
         Ceux-la scintillent. */
      const u = /^U\s?(\d{1,2})/i.exec(texte);
      if (u && +u[1] >= 6 && +u[1] <= 10) c.classList.add("bc-petits");
      /* L'age dans sa propre balise (c'est lui qui scintille), le format
         de jeu dans la sienne. Le texte lu par la page reste « U11 à 8 ». */
      const m = /^(U\s?\d{1,2})(\s.+)?$/i.exec(texte);
      if (!m) return;
      const age = document.createElement("bc-u");
      age.textContent = m[1];
      c.textContent = "";
      c.appendChild(age);
      if (m[2]) {
        const f = document.createElement("bc-format");
        f.textContent = m[2];
        c.appendChild(f);
      }
    });
  }

  /* ---- Le direct ------------------------------------------------------- */
  /* « aujourd'hui » en toutes lettres (Reperage, Semaines), ou « J-0 » sur
     les cartes de Tournois. */
  const AUJOURDHUI = /^(aujourd['’]hui|J[-‑]0)$/i;
  function direct(racine) {
    racine.querySelectorAll(".f-reste, .pave i, .rep.date i").forEach(i => {
      i.classList.toggle("bc-direct", AUJOURDHUI.test(i.textContent.trim()));
    });
  }

  /* ---- La voix --------------------------------------------------------- */
  function voix() {
    document.querySelectorAll("#noteDicter").forEach(b => {
      const onde = b.querySelector("bc-voix");
      if (b.classList.contains("ecoute")) {
        if (!onde) {
          const v = document.createElement("bc-voix");
          v.setAttribute("aria-hidden", "true");
          v.innerHTML = "<bc-barre></bc-barre>".repeat(5);
          b.appendChild(v);
        }
      } else if (onde) {
        onde.remove();
      }
    });
  }

  /* ---- Les images, bloc par bloc ------------------------------------------
     24 colonnes sur 16 lignes, revelees dans l'ordre de lecture en moins
     d'une seconde : un masque fait des lignes deja pleines, plus le debut de
     la ligne en cours. Les logos et les vignettes restent tels quels. */
  const COLS = 24, LIGNES = 16, DUREE = 700;
  function reveler(img) {
    if (calme || img.dataset.bcRevele || img.closest("#menu, .blason, .rond")) return;
    const W = img.clientWidth, H = img.clientHeight;
    if (W < 120 || H < 80) return;
    img.dataset.bcRevele = "1";
    const bw = W / COLS, bh = H / LIGNES, total = COLS * LIGNES;
    const s = img.style;
    const t0 = performance.now();
    function pas(t) {
      /* L'horodatage d'une image peut preceder t0 de quelques millisecondes :
         sans le plancher, les tailles du masque deviendraient negatives et
         l'image s'afficherait d'un coup. */
      const n = Math.min(total, Math.ceil((Math.max(0, t - t0) / DUREE) * total));
      const ligne = Math.floor(n / COLS), col = n % COLS;
      const plein = "linear-gradient(#000, #000)";
      s.webkitMaskImage = s.maskImage = plein + ", " + plein;
      s.webkitMaskRepeat = s.maskRepeat = "no-repeat";
      s.webkitMaskSize = s.maskSize = W + "px " + (ligne * bh) + "px, " + (col * bw) + "px " + bh + "px";
      s.webkitMaskPosition = s.maskPosition = "0 0, 0 " + (ligne * bh) + "px";
      if (n < total) requestAnimationFrame(pas);
      else s.webkitMaskImage = s.maskImage = s.webkitMaskSize = s.maskSize
        = s.webkitMaskPosition = s.maskPosition = s.webkitMaskRepeat = s.maskRepeat = "";
    }
    requestAnimationFrame(pas);
  }
  document.addEventListener("load", e => {
    if (e.target && e.target.localName === "img") reveler(e.target);
  }, true);

  /* ---- Le guet -------------------------------------------------------------
     Les changements arrivent par rafales (une liste de trois cents fiches) :
     on note ce qui a bouge et on regle tout une fois, juste apres. */
  let hotes = new Set(), racines = new Set(), prevu = false;
  function noter(n) {
    const el = n.nodeType === 1 ? n : n.parentElement;
    if (!el) return;
    const hote = el.closest(HOTES);
    if (hote) hotes.add(hote);
    racines.add(el);
  }
  function vider() {
    prevu = false;
    const h = hotes, r = racines;
    hotes = new Set();
    racines = new Set();
    h.forEach(regler);
    r.forEach(el => {
      if (!el.isConnected) return;
      /* Un fil change d'un bloc : on regarde depuis son conteneur, pour que
         la reponse arrivee apres un de nos messages le passe a « lu ». */
      const zone = el.closest(".fil, .avant, .dossier, .carte, .acte") || el;
      etats(zone);
      direct(zone);
      pairs(zone);
      ages(zone);
    });
    voix();
  }
  const guet = new MutationObserver(recs => {
    recs.forEach(r => {
      if (r.target.closest && r.target.closest("bc-sonar, bc-etat, bc-voix")) return;
      noter(r.target);
      r.addedNodes.forEach(n => {
        if (n.nodeType !== 1 || /^bc-/.test(n.localName)) return;
        if ((n.textContent || "").indexOf("…") >= 0) {
          if (n.matches(HOTES)) hotes.add(n);
          n.querySelectorAll(HOTES).forEach(h => hotes.add(h));
        }
      });
    });
    if ((hotes.size || racines.size) && !prevu) {
      prevu = true;
      /* Un minuteur et non une image d'animation : une page masquee (onglet
         en arriere-plan, fenetre cachee) suspend ces dernieres, et rien
         n'aurait ete pose avant son retour. */
      setTimeout(vider, 0);
    }
  });

  function demarrer() {
    document.querySelectorAll(HOTES).forEach(h => {
      if ((h.textContent || "").indexOf("…") >= 0) regler(h);
    });
    etats(document.body);
    direct(document.body);
    pairs(document.body);
    ages(document.body);
    voix();
    Array.prototype.forEach.call(document.images, img => {
      if (img.complete && img.naturalWidth) reveler(img);
    });
    guet.observe(document.body, {subtree: true, childList: true, characterData: true});
  }
  if (document.body) demarrer();
  else document.addEventListener("DOMContentLoaded", demarrer);
})();
