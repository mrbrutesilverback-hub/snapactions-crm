/* La page Studio : booker des seances portrait en semaine.
   Meme matiere que le bloc « jours de semaine » des semaines a remplir, mais
   sans plafond et avec la plaquette sous la main. */
const $ = s => document.querySelector(s);
function neud(b, c, t){ const e = document.createElement(b); if (c) e.className = c;
  if (t != null) e.textContent = t; return e; }

let minuteur = null;
function toast(m){ const t = $("#toast"); t.textContent = m; t.hidden = false;
  clearTimeout(minuteur); minuteur = setTimeout(() => { t.hidden = true; }, 3200); }

/* Ouvrir passe par le serveur : WhatsApp vit dans Chrome, pas ici. */
function ouvrirLien(url){
  if (!url) return;
  fetch("/api/ouvrir", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url: url})}).then(r => { if (!r.ok) throw 0; })
    .catch(() => window.open(url, "_blank", "noopener"));
}

function duree(min){
  if (min == null) return "";
  if (min < 60) return min + " min";
  const h = Math.floor(min / 60), m = min % 60;
  return h + " h" + (m ? " " + String(m).padStart(2, "0") : "");
}

const TIROIRS = [
  {cle: "tout",      nom: "Tout",             garde: () => true},
  {cle: "relancer",  nom: "↻ À relancer",  garde: c => c.connu},
  {cle: "demarcher", nom: "✉ À démarcher", garde: c => !c.connu},
  {cle: "proches",   nom: "🚗 À 20 min", garde: c => c.trajet_min != null && c.trajet_min <= 20},
  {cle: "animaux",   nom: "🐾 Animaux", garde: c => c.genre === "animaux"},
];

const P = {items: [], ecartes: 0, tiroir: "tout", ouvert: null};
try { P.tiroir = localStorage.getItem("tiroir-studio") || "tout"; } catch (e) {}

async function charger(){
  try {
    const r = await fetch("/api/studio");
    if (!r.ok) throw new Error("le serveur a refusé (" + r.status + ")");
    const d = await r.json();
    P.items = d.items || [];
    P.ecartes = d.ecartes || 0;
    $("#chapeau").innerHTML = "<b>" + P.items.length + " clubs à portée</b>, à moins de "
      + d.portee + " minutes de route et joignables par téléphone."
      + (P.ecartes ? " <button class='remettre'>" + P.ecartes
         + " mis de côté — tout remettre</button>" : "");
    const rem = document.querySelector("#chapeau .remettre");
    if (rem) rem.onclick = async () => {
      await fetch("/api/studio/reprendre", {method: "POST",
        headers: {"Content-Type": "application/json"}, body: "{}"});
      toast("Fiches remises dans la liste");
      charger();
    };
    peindre();
  } catch (e) {
    $("#chapeau").textContent = "";
    $("#souci").hidden = false;
    $("#souci").textContent = "Impossible de charger la liste : " + e.message;
  }
}

function peindre(){ peindreTiroirs(); peindreListe(); }

function peindreTiroirs(){
  const b = $("#tiroirs"); b.innerHTML = "";
  TIROIRS.forEach(t => {
    const n = P.items.filter(t.garde).length;
    const el = neud("button", "sem" + (t.cle === P.tiroir ? " on" : ""));
    el.type = "button"; el.disabled = !n;
    el.appendChild(neud("span", "num", String(n)));
    el.appendChild(neud("span", "dit", t.nom));
    el.addEventListener("click", () => {
      P.tiroir = t.cle; P.ouvert = null;
      try { localStorage.setItem("tiroir-studio", t.cle); } catch (e) {}
      peindre();
    });
    b.appendChild(el);
  });
}

function peindreListe(){
  const d = $("#liste"); d.innerHTML = "";
  const t = TIROIRS.filter(x => x.cle === P.tiroir)[0] || TIROIRS[0];
  const liste = P.items.filter(t.garde);
  if (!liste.length) { d.appendChild(neud("p", "vide-dit", "Aucun club dans ce tiroir.")); return; }
  const g = neud("div", "pistes");
  liste.forEach(c => g.appendChild(carte(c)));
  d.appendChild(g);
}

function carte(c){
  const art = neud("article", "piste club");
  const corps = neud("div", "piste-corps");
  corps.appendChild(neud("div", "club-nom", c.name || ""));
  const ou = [c.sport, c.city].filter(Boolean).join(" · ");
  if (ou) corps.appendChild(neud("div", "club-ou", ou.slice(0, 110)));

  const faits = neud("div", "p-faits");
  if (c.trajet_min != null) {
    const r = neud("span", "pave route");
    r.appendChild(neud("b", null, c.trajet_min === 0 ? "sur place" : duree(c.trajet_min)));
    if (c.trajet_min) r.appendChild(neud("i", null, "de route"));
    faits.appendChild(r);
  } else {
    faits.appendChild(neud("span", "etiq", "route inconnue"));
  }
  if (c.partenaire) faits.appendChild(neud("span", "etiq ok", "partenaire"));
  else if (c.status === "client") faits.appendChild(neud("span", "etiq ok", "déjà client"));
  if (c.genre === "animaux") faits.appendChild(neud("span", "etiq animaux", "🐾 animaux"));
  corps.appendChild(faits);
  corps.appendChild(neud("p", "pourquoi", c.pourquoi || ""));
  art.appendChild(corps);

  const barre = neud("div", "p-barre");
  const voir = neud("button", null, P.ouvert === c.id ? "Replier" : "Voir le message");
  voir.type = "button";
  voir.addEventListener("click", () => { P.ouvert = P.ouvert === c.id ? null : c.id; peindreListe(); });
  barre.appendChild(voir);

  if (String(c.phone || "").replace(/\D/g, "")) {
    const tel = neud("button", null, "Appeler");
    tel.type = "button";
    tel.addEventListener("click", () => ouvrirLien("tel:" + c.phone));
    barre.appendChild(tel);
  }

  /* Trois facons de faire disparaitre une fiche. Rien n'est efface : chacune
     revient d'elle-meme, parce qu'un non en octobre peut devenir un oui en
     janvier — et parce qu'une liste de cent trente-quatre fiches qu'on ne peut
     pas vider ne se travaille pas, on la subit. */
  const ecarts = neud("span", "ecarts");
  [["contacte", "\u2713 Contacté", "revient dans 2 mois"],
   ["plus_tard", "\u29d6 Plus tard", "revient dans 2 semaines"],
   ["non", "\u2715 Pas pour moi", "revient dans 1 an"]].forEach(([motif, mot, aide]) => {
    const b = neud("button", "ecart", mot);
    b.type = "button"; b.title = aide;
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await fetch("/api/studio/" + c.id + "/ecarter", {method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({motif: motif})});
        art.classList.add("part");
        toast(mot.slice(2) + " — " + aide);
        setTimeout(charger, 220);
      } catch (e) { b.disabled = false; toast("Impossible de mettre de côté"); }
    });
    ecarts.appendChild(b);
  });
  barre.appendChild(ecarts);
  art.appendChild(barre);
  if (P.ouvert === c.id) art.appendChild(panneau(c));
  return art;
}

/* Un numero utilisable par wa.me, et la regle des lignes fixes — la meme que
   sur le Reperage. Un salon de toilettage sur trois n'a qu'un 04 : ouvrir
   WhatsApp dessus donnait une conversation vide, sans un mot d'explication. */
function numeroWa(tel){
  const brut = String(tel || "").replace(/\D/g, "").replace(/^00/, "");
  const n = /^377/.test(brut) ? brut : brut.replace(/^0/, "33");
  return n.length >= 10 ? n : "";
}
function estFixe(tel){
  const brut = String(tel || "").replace(/\D/g, "").replace(/^00/, "");
  if (/^377/.test(brut)) return /^3779/.test(brut) && brut.length === 11;
  const n = brut.replace(/^0/, "33");
  return /^33[1-59]/.test(n) && n.length === 11;
}

function panneau(c){
  const m = neud("div", "mot");
  const ta = document.createElement("textarea");
  ta.id = "mot-studio-" + c.id;
  ta.value = "Je rédige\u2026";
  ta.disabled = true;
  m.appendChild(ta);

  /* Le texte vient des modeles, pas d'ici. Cet ecran en ecrivait un a lui,
     « Semaines a remplir » en ecrivait un autre, et les deux avaient perdu en
     route la galerie intelligente, la selection remise au club et la seance
     deja faite au Basket Cannes Olympique — c'est-a-dire tout ce qui fait
     dire oui. Un seul endroit decide maintenant : la table des modeles, que
     Julien peut relire et corriger dans les reglages. */
  let objet = "";
  fetch("/api/studio/" + c.id + "/message").then(r => r.json()).then(d => {
    if (d.error) throw new Error(d.error);
    ta.value = d.corps || "";
    ta.disabled = false;
    objet = d.objet || "";
  }).catch(e => {
    ta.value = "Message indisponible : " + (e.message || "");
  });

  /* La meme barre que partout ailleurs : le modele tourne sur ce Mac. */
  const ia = document.createElement("div");
  ia.innerHTML = Reform.barre("mot-studio-" + c.id,
    [c.name, c.city].filter(Boolean).join(" \u00b7 "));
  m.appendChild(ia.firstElementChild);

  const g = neud("div", "gestes");
  const num = numeroWa(c.phone);
  const fixe = !!num && estFixe(c.phone);
  const lire = () => (document.getElementById("mot-studio-" + c.id) || {}).value || "";

  /* Le bouton ouvrait « wa.me » dans le navigateur : il fallait encore
     appuyer sur Entree dans WhatsApp, et on croyait avoir envoye. Il envoie
     maintenant pour de bon, comme partout ailleurs. */
  const wa = neud("button", fixe ? "fixe" : "env-direct",
                  fixe ? "WhatsApp \u2014 ligne fixe" : "\u25b6 Envoyer sur WhatsApp");
  wa.type = "button"; wa.disabled = !num || fixe;
  if (fixe) wa.title = c.phone + " est une ligne fixe : WhatsApp n\u2019y passera pas.";
  wa.addEventListener("click", () => {
    if (!window.Envoyer) { toast("Module d\u2019envoi absent"); return; }
    Envoyer.direct({tel: c.phone, corps: lire(), club_id: c.id}, wa);
  });
  g.appendChild(wa);

  const cop = neud("button", null, "Copier le texte");
  cop.type = "button";
  cop.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(lire()); toast("Message copié"); }
    catch (e) { toast("Copie refusée par le navigateur"); }
  });
  g.appendChild(cop);

  const pl = neud("button", null, "📎 Copier la plaquette");
  pl.type = "button";
  pl.addEventListener("click", () => copierPlaquette(pl));
  g.appendChild(pl);

  if (c.email) {
    /* L'objet venait d'ici, en dur, et disait « vos adhérents » meme a un
       salon de toilettage. Il vient maintenant du modele, comme le corps. */
    const ml = neud("button", (!num || fixe) ? "primaire" : null,
                    (!num || fixe) ? "Envoyer par mail" : "Par mail");
    ml.type = "button";
    ml.addEventListener("click", () => ouvrirLien("mailto:" + c.email
      + "?subject=" + encodeURIComponent(objet || "Séance photo studio")
      + "&body=" + encodeURIComponent(lire())));
    g.appendChild(ml);
  }
  m.appendChild(g);
  m.appendChild(neud("p", "avert", "Relisez avant d’envoyer. Rien ne part d’ici."));
  setTimeout(() => Reform.brancher(m), 0);
  return m;
}

async function copierPlaquette(bouton){
  const avant = bouton ? bouton.textContent : null;
  if (bouton) { bouton.disabled = true; bouton.textContent = "Copie…"; }
  try {
    const r = await fetch("/api/plaquette/copier", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({quelle: "studio"})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "refus");
    toast("Plaquette dans le presse-papiers — ⌘V dans WhatsApp");
  } catch (e) { toast(e.message || "Copie impossible"); }
  if (bouton) { bouton.disabled = false; bouton.textContent = avant; }
}

$("#copierPlaquette").onclick = () => copierPlaquette($("#copierPlaquette"));

/* Les vignettes : montrer le rendu plutot que le promettre. */
fetch("/api/plaquette/vignettes?quelle=studio").then(r => r.json()).then(d => {
  const b = $("#vignettes");
  (d.images || []).slice(0, 7).forEach(src => {
    const i = document.createElement("img");
    i.src = src; i.alt = "";   // sept vignettes : rien a differer
    b.appendChild(i);
  });
}).catch(() => {});

charger();
