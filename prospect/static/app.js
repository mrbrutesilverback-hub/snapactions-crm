'use strict';

/* ---------- outils ---------- */
const $  = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function api(path, opts) {
  const o = Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {});
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const ct = r.headers.get('Content-Type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
  return data;
}

/* Elle etait appelee a trois endroits sans avoir jamais ete ecrite : les
   filtres des ecrans Clubs et Tournois ne reagissaient donc a rien, en
   silence. */
function debounce(fn, delai) {
  let t;
  return function () {
    const args = arguments, moi = this;
    clearTimeout(t);
    t = setTimeout(() => fn.apply(moi, args), delai || 260);
  };
}

let toastTimer;
function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, bad ? 5200 : 2600);
}

function modal(html) {
  $('#modalBody').innerHTML = html;
  $('#modal').hidden = false;
}
function closeModal() { $('#modal').hidden = true; }
$('#modalClose').onclick = closeModal;
$('#modal').onclick = e => { if (e.target.id === 'modal') closeModal(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

const STATUS_LABEL = {};
const TEMP = { neuf: ['Jamais contacté', ''], chaud: ['Chaud', 'on'],
               tiede: ['Tiède', ''], froid: ['Froid', 'warn'] };

function frDate(iso) {
  if (!iso) return '';
  const d = new Date(String(iso).replace(' ', 'T'));
  if (isNaN(d)) return iso;
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: '2-digit' });
}

async function copy(text) {
  // 1) l'API du navigateur, qui échoue si la fenêtre n'a pas le focus
  try {
    await navigator.clipboard.writeText(text);
    toast('Copié'); return true;
  } catch (e) { /* on tente la suite */ }
  // 2) la vieille méthode, qui marche sans permission
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:-2000px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    if (ok) { toast('Copié'); return true; }
  } catch (e) { /* on tente la suite */ }
  // 3) pbcopy, côté serveur : le serveur tourne sur ce Mac
  try {
    await api('/api/clipboard', { method: 'POST', body: { text } });
    toast('Copié'); return true;
  } catch (e) {
    toast('Copie impossible : ' + e.message, true); return false;
  }
}

let picked = new Set();

/* ---------- navigation ---------- */
const LOADERS = {};
function show(tab) {
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  $$('.tab').forEach(s => s.classList.toggle('on', s.id === 'tab-' + tab));
  location.hash = '#/' + tab;
  if (LOADERS[tab]) LOADERS[tab]();
}
$$('#tabs button').forEach(b => b.onclick = () => show(b.dataset.tab));

/* ================= AUJOURD'HUI ================= */
LOADERS.today = async function () {
  const d = await api('/api/dashboard');
  $('#setupWarn').hidden = !!d.settings_ready;

  /* Trois chiffres qui commandent une action, pas neuf qui comptent un stock.
     Savoir qu'on a 195 clubs n'a jamais fait avancer une prospection. */
  /* Une reponse est la seule chose qui fasse vraiment avancer un dossier :
     elle passe donc devant les stocks de brouillons et de relances. */
  let suivi = {colonnes: []}, etapes = {};
  try {
    suivi = await api('/api/suivi');
    (suivi.colonnes || []).forEach(c => etapes[c.cle] = c.n);
  } catch (e) {}

  const k = [
    ['wa', etapes.a_traiter || 0, 'attendent votre réponse', 'suivi',
     (etapes.a_traiter || 0) > 0],
    ['appel', etapes.a_appeler || 0, 'à appeler maintenant', 'suivi',
     (etapes.a_appeler || 0) > 0],
    ['relance', etapes.a_relancer || 0, 'relancer par message', 'suivi',
     (etapes.a_relancer || 0) > 0],
    ['dt', etapes.conclu || 0, 'dates bloquées à pourvoir', 'missions',
     (etapes.conclu || 0) > 0],
  ];

  /* Le menu de gauche n'a plus d'entree « Suivi », donc plus ce compteur :
     sans cette garde, l'ecran s'arretait ici et restait vide. */
  const badgeSuivi = $('#suiviBadge');
  if (badgeSuivi) {
    badgeSuivi.hidden = !(etapes.a_traiter || 0);
    badgeSuivi.textContent = etapes.a_traiter || 0;
  }
  $('#kpis').innerHTML = k.map(([id, n, lbl, tab, alert]) =>
    `<div class="kpi act${alert ? ' alert' : ''}" data-go="${tab}">
       <b>${n || 0}</b><span>${lbl}</span></div>`).join('');
  $$('#kpis .kpi').forEach(e => e.onclick = () => show(e.dataset.go));

  chargerJournee();
  chargerMesDates();
  chargerATrancher();
  chargerAttendent(suivi);


  const log = await api('/api/log');
  $('#log').innerHTML = log.items.slice(0, 5).map(l =>
    `<div class="item"><div class="body"><div class="s">${frDate(l.created_at)} —
     ${esc(l.message)}</div></div></div>`).join('') || '<div class="empty">Vide.</div>';
};
/* Le point du jour. Une liste, pas un tableau de bord : chaque carte porte un
   verbe, la raison d'etre la aujourd'hui, et les gestes qui la font avancer.
   Ce qui deborde du plafond est annonce en bas, sans etre affiche — le but est
   justement qu'on puisse finir la liste.

   Et pour qu'on puisse la finir, encore faut-il pouvoir dire qu'une ligne est
   faite : la page promettait « elles remonteront quand celles-ci seront
   faites » sans jamais offrir le bouton. Les memes douze cartes revenaient
   donc indefiniment et les cent autres n'arrivaient jamais. */

/* Le geste principal depend de la famille : on repond par ecrit, mais on
   rappelle par telephone, et un tournoi ne se tranche que sur sa fiche. */
const GESTE_PREMIER = {
  1: 'wa', 2: 'wa', 3: 'wa', 4: 'tel', 5: 'ouvrir',
  6: 'wa', 7: 'wa', 8: 'ouvrir', 9: 'wa',
};

/* « 5 déc. » se lit d'un coup d'oeil, « 2026-12-05 » non. */
function jourCourt(iso) {
  if (!iso) return '';
  const d = new Date(String(iso).replace(' ', 'T') + (String(iso).length <= 10 ? 'T12:00:00' : ''));
  if (isNaN(d)) return String(iso);
  return d.toLocaleDateString('fr-FR', {weekday: 'short', day: 'numeric', month: 'short'});
}

function compteARebours(j) {
  if (j == null) return '';
  if (j < 0) return 'passé';
  if (j === 0) return "aujourd'hui";
  if (j === 1) return 'demain';
  return 'dans ' + j + ' j';
}

/* Les trois reperes que Julien regarde avant tout le reste : quand, combien de
   route, combien d'equipes. Ils etaient noyes dans la phrase d'explication. */
function reperes(a) {
  const out = [];
  if (a.date) {
    const j = a.jours_avant;
    const ton = j == null ? '' : j <= 7 ? ' urgent' : j <= 21 ? ' bientot' : '';
    out.push(`<span class="rep date${ton}"><b>${esc(jourCourt(a.date))}</b>
      <i>${esc(compteARebours(j))}</i></span>`);
  }
  if (a.trajet_min != null && a.trajet_min > 0) {
    /* Sur Reperage les kilometres passent devant et la duree suit : c'est
       dans cet ordre que Julien lit la ligne, on ne l'inverse pas ici. */
    out.push(`<span class="rep route">${a.distance_km != null
      ? '<b>' + Math.round(a.distance_km) + ' km</b><i>' + esc(duree(a.trajet_min)) + '</i>'
      : '<b>' + esc(duree(a.trajet_min)) + '</b>'}</span>`);
  }
  if (a.nb_equipes) {
    out.push(`<span class="rep equipes"><b>${a.nb_equipes}</b><i>équipes</i></span>`);
  }
  return out.length ? `<div class="reperes">${out.join('')}</div>` : '';
}

/* Les tiroirs du point du jour, calques sur ceux de Reperage : on choisit
   d'abord ce qu'on vient faire, on lit les cartes ensuite. Douze cartes sans
   filtre, c'est une liste qu'on subit. */
const TIROIRS_JOUR = [
  {cle: 'tout', nom: 'Tout', garde: () => true},
  {cle: 'repondre', nom: 'Répondre', garde: a => a.famille === 1},
  {cle: 'appeler', nom: 'Appeler', garde: a => a.famille === 4},
  {cle: 'caler', nom: 'Caler une date', garde: a => a.famille === 2 || a.famille === 3},
  {cle: 'trancher', nom: 'Trancher', garde: a => a.famille === 5},
];
let tiroirJour = 'tout';
let actesDuJour = [];

/* « Une a la fois » : douze cartes posees ensemble, c'est une liste qu'on
   subit et qu'on repousse. Une seule carte a l'ecran, c'est une decision a
   prendre maintenant. Le mode se retient d'une fois sur l'autre, et « Tout
   voir » reste la pour embrasser la journee d'un coup d'oeil. */
let modeJour = 'une';
try { modeJour = localStorage.getItem('mode-jour') || 'une'; } catch (e) {}
let iJour = 0;            // ou l'on en est quand on les prend une par une

function reglerModeJour(mode) {
  modeJour = mode;
  try { localStorage.setItem('mode-jour', mode); } catch (e) {}
  iJour = 0;
  peindreJournee();
}

function peindreTiroirsJour() {
  const boite = $('#tiroirsJour');
  if (!boite) return;
  boite.innerHTML = TIROIRS_JOUR.map(t => {
    const n = actesDuJour.filter(t.garde).length;
    return `<button class="t-${t.cle}${t.cle === tiroirJour ? ' on' : ''}"
      type="button" data-tiroir="${t.cle}"${n ? '' : ' disabled'}>
      <span class="n">${n}</span><span class="nom">${esc(t.nom)}</span></button>`;
  }).join('');
  $$('#tiroirsJour [data-tiroir]').forEach(b => b.onclick = () => {
    if (b.dataset.tiroir === tiroirJour) return;
    tiroirJour = b.dataset.tiroir;
    iJour = 0;
    peindreTiroirsJour();
    peindreJournee();
  });
}

async function chargerJournee() {
  const boite = $('#journee');
  let d;
  try { d = await api('/api/journee'); }
  catch (e) { boite.innerHTML = '<div class="empty">Indisponible.</div>'; return; }

  peindreFraicheurWa(d.whatsapp);
  $('#nJournee').textContent = d.actions.length || '';
  $('#journeeSous').textContent = d.chaud
    ? d.chaud + (d.chaud > 1 ? ' urgences' : ' urgence') + ' en tête'
    : 'dans l’ordre, du plus coûteux à laisser filer';

  if (!d.actions.length) {
    boite.innerHTML = '<div class="empty">Rien qui presse aujourd’hui. '
      + 'Profitez-en pour appeler un club au hasard.</div>';
    return;
  }

  actesDuJour = d.actions;
  window.__journee = d;
  peindreTiroirsJour();
  peindreJournee();
}

/* Les compteurs de cet ecran sortent du journal WhatsApp. Quand il cesse
   d'etre relu, ils ne disparaissent pas — ils vieillissent en silence. Le
   bandeau porte l'age et le geste qui le repare, parce que l'information
   etait jusqu'ici enterree dans un fichier de log que personne n'ouvre. */
function peindreFraicheurWa(f) {
  const b = $('#waVieux');
  if (!b) return;
  if (!f || !f.vieux) { b.hidden = true; return; }
  b.hidden = false;
  b.innerHTML = '<b>Chiffres périmés.</b> ' + esc(f.dit || '')
    + ' <button class="relire-wa">Relire WhatsApp maintenant</button>';
  b.querySelector('.relire-wa').onclick = async function () {
    this.disabled = true;
    const avant = this.textContent;
    this.textContent = 'Lecture…';
    try {
      const r = await api('/api/whatsapp/relire', {method: 'POST', body: {}});
      toast((r.vus || 0) + ' conversations relues');
      chargerJournee();
    } catch (e) {
      /* Le refus de macOS n'est pas une panne : c'est une autorisation qui
         manque, et il faut dire laquelle plutot que de lacher un code. */
      this.disabled = false;
      this.textContent = avant;
      toast(e.message || 'Relecture impossible', true);
    }
  };
}

function peindreJournee() {
  const boite = $('#journee');
  const d = window.__journee || {actions: [], reste: 0};
  const t = TIROIRS_JOUR.filter(x => x.cle === tiroirJour)[0] || TIROIRS_JOUR[0];
  const liste = d.actions.filter(t.garde);
  if (!liste.length) {
    boite.innerHTML = '<div class="empty">Rien dans ce tiroir.</div>';
    return;
  }

  /* En mode « une a la fois », on n'affiche que celle du moment. Le reste de
     la fonction ne change pas : c'est la meme carte, seule. */
  if (iJour >= liste.length) iJour = 0;
  const montrees = modeJour === 'une' ? [liste[iJour]] : liste;
  boite.classList.toggle('seule', modeJour === 'une');

  boite.innerHTML = barreMode(liste) + montrees.map(a => {
    const num = String(a.tel || '').replace(/[^0-9]/g, '');
    const premier = GESTE_PREMIER[a.famille] || 'ouvrir';

    /* Tout est un bouton ; seule l'intensite change. Le geste qui fait
       avancer le dossier est en tete, les autres suivent en gris. */
    const gestes = [];
    if (a.tel && premier === 'wa') {
      /* Le texte est lu AU MOMENT DU CLIC, pas a la construction de la carte :
         entre les deux, Julien a pu corriger le message ou le reformuler.
         Sans ?text=, WhatsApp s'ouvrait sur une conversation vide — il fallait
         revenir copier le message a la main. */
      gestes.push(`<button class="geste maitre faire" data-wa="${a.rang}"
        data-num="${num}" data-club="${a.club_id || ''}"
        ><span class="ico">💬</span>Envoyer</button>`);
      gestes.push(`<a class="geste appel icone" href="tel:${esc(a.tel)}"
        title="Appeler ${esc(a.tel)}"><span class="ico">📞</span></a>`);
    } else if (a.tel && premier === 'tel') {
      gestes.push(`<a class="geste maitre appel faire" href="tel:${esc(a.tel)}">
        <span class="ico">📞</span>Appeler</a>`);
      gestes.push(`<button class="geste icone" data-wa="${a.rang}" data-num="${num}"
        data-club="${a.club_id || ''}"
        title="Envoyer sur WhatsApp"><span class="ico">💬</span></button>`);
    } else {
      gestes.push(`<button class="geste maitre ouvre faire" data-go="${esc(a.onglet)}">
        <span class="ico">↗</span>${premier === 'ouvrir' && a.famille === 5
          ? 'Trancher' : 'Ouvrir la fiche'}</button>`);
    }
    /* Rayer la ligne : « Fait » la masque jusqu'a demain, l'horloge une
       semaine. Rien n'est efface — si l'action n'a pas reellement avance,
       elle revient, et la liste ne peut donc pas mentir. */
    gestes.push(`<button class="geste discret rayer" data-rayer="fait"
      data-cle="${esc(a.cle)}" title="Fait — la ligne repart, et laisse monter la suivante">
      <span class="ico">✓</span>Fait</button>`);
    /* L'horloge 🕗 se dessine en blanc plein : dans la barre elle faisait un
       trou blanc plutot qu'un bouton. Le sablier se lit comme « plus tard ». */
    gestes.push(`<button class="geste discret icone" data-rayer="plus_tard"
      data-cle="${esc(a.cle)}" aria-label="Plus tard"
      title="Plus tard — à revoir dans une semaine">
      <span class="ico">⏳</span></button>`);

    /* Le fil et le mot propose s'affichent tels quels : un volet a deplier
       de plus, c'etait un clic avant de savoir de quoi on parle, sur chacune
       des douze cartes. La carte dit tout, on decide sans rien ouvrir. */
    /* Une ligne qui dit ou en est le dossier, avant le fil brut : on la lit en
       une seconde, et le detail reste dessous pour verifier. */
    const resume = a.resume
      ? `<p class="resume-fil"><span class="ico">\u2261</span>${esc(a.resume)}</p>`
      : '';
    const fil = (a.echange || []).map(m =>
      `<div class="bulle ${m.de_moi ? 'moi' : 'eux'}" data-quand="${esc(m.heure || '')}">
         <span class="qui">${m.de_moi ? 'Vous' : 'Eux'}</span>
         ${esc(m.texte).replace(/\n/g, '<br>')}</div>`).join('');
    const mot = a.proposition ? `
      <div class="propose">
        <div class="titre">${esc(a.proposition_titre || 'Message proposé')}
          <button class="geste discret" data-copier="${a.rang}">
            <span class="ico">⧉</span>Copier</button></div>
        <textarea class="mot" id="mot-${a.rang}" rows="7">${esc(a.proposition)}</textarea>
        ${Reform.barre('mot-' + a.rang,
          [a.qui, a.tournoi, a.ville].filter(Boolean).join(' · '))}
        <p class="hint">Relisez avant d’envoyer. Rien ne part d’ici.</p>
      </div>` : '';
    const detail = (fil || mot)
      ? `<div class="detail">${resume}${fil}${mot}</div>` : '';

    /* L'etiquette ne repete plus le titre : sur « Trancher », le nom du
       tournoi EST le nom de la carte, et l'etiquette faisait doublon. */
    const memeNom = (a.tournoi || '').trim() === (a.qui || '').trim();
    const contexte = [
      a.tournoi && !memeNom ? `<span class="etiq">${esc(a.tournoi)}</span>` : '',
      a.tel ? '' : '<span class="etiq creux">pas de numéro</span>',
    ].filter(Boolean).join('');

    const sousTitre = [a.quoi, a.ville].filter(Boolean).map(esc).join(' · ');

    return `<article class="acte ${esc(a.urgence)}" data-cle="${esc(a.cle)}">
      <header>
        <span class="rang">${a.rang}</span>
        <span class="blason" style="--teinte:${teinte(a.qui || '')}">${
          picto(a.sport || a.qui) || `<span class="ini">${esc(initiales(a.qui || ''))}</span>`}</span>
        <div class="quoi">
          <button class="qui" data-go="${esc(a.onglet)}">${esc(a.qui || '')}</button>
          <div class="verbe">${sousTitre}</div>
        </div>
      </header>
      <p class="pourquoi">${esc(a.pourquoi || '')}</p>
      ${reperes(a)}
      ${contexte ? `<div class="etiqs">${contexte}</div>` : ''}
      ${detail}
      <footer>${gestes.join('')}</footer>
    </article>`;
  }).join('')
    + (modeJour === 'une' ? pieUneParUne(liste, d) : pieDeListe(d));

  $$('#journee [data-mode]').forEach(b => b.onclick = () => reglerModeJour(b.dataset.mode));
  const passer = $('#journee .passer');
  if (passer) passer.onclick = () => { iJour += 1; peindreJournee(); };
  const revenir = $('#journee .revenir');
  if (revenir) revenir.onclick = () => { iJour -= 1; peindreJournee(); };
  $$('#journee [data-go]').forEach(b => b.onclick = () => show(b.dataset.go));
  $$('#journee [data-copier]').forEach(b => b.onclick = e => {
    e.preventDefault();
    copy($('#mot-' + b.dataset.copier).value);
  });
  $$('#journee [data-rayer]').forEach(b => b.onclick = e => {
    e.preventDefault();
    rayerLigne(b.dataset.cle, b.dataset.rayer, b);
  });
  $$('#journee [data-wa]').forEach(b => b.onclick = async e => {
    e.preventDefault();
    const champ = $('#mot-' + b.dataset.wa);
    const texte = champ ? (champ.value || '').trim() : '';
    /* Sans texte il n'y a rien a envoyer : on ouvre la conversation. Avec
       un texte, le message part pour de bon — plus de « collé, à vous de
       presser Entrée », qui faisait croire a un envoi qui n'avait pas eu
       lieu. */
    if (!texte || !window.Envoyer) {
      ouvrirDehors('https://wa.me/' + b.dataset.num);
      return;
    }
    await Envoyer.direct({tel: b.dataset.num, corps: texte,
                          club_id: b.dataset.club || null}, b);
  });
  Reform.brancher($('#journee'));
  const rappel = $('#journee .rappel');
  if (rappel) rappel.onclick = () => toutRemettre();
}

/* La barre qui dit ou l'on en est, et qui laisse reprendre la vue d'ensemble. */
function barreMode(liste) {
  const une = modeJour === 'une';
  const compteur = une
    ? `<b>${iJour + 1}</b> sur ${liste.length}`
    : `<b>${liste.length}</b> à traiter`;
  return `<div class="mode-jour">
    <span class="ou">${compteur}</span>
    ${une ? `<span class="jauge"><i style="width:${
        Math.round(((iJour + 1) / liste.length) * 100)}%"></i></span>` : ''}
    <span class="grow"></span>
    <button class="bascule${une ? ' on' : ''}" data-mode="une">Une à la fois</button>
    <button class="bascule${une ? '' : ' on'}" data-mode="tout">Tout voir</button>
  </div>`;
}

/* Sous la carte unique : ce qui vient apres, et de quoi sauter sans rien
   rayer — passer n'est pas « fait », et la ligne doit rester entiere. */
function pieUneParUne(liste, d) {
  const suivante = liste[iJour + 1];
  const bouts = [];
  if (iJour > 0) bouts.push('<button class="revenir">← la précédente</button>');
  if (suivante) {
    bouts.push(`<button class="passer">Passer →</button>`
      + `<span class="apres">ensuite : ${esc(suivante.qui || '')}</span>`);
  } else if (liste.length > 1) {
    bouts.push('<button class="passer">Revenir à la première ↺</button>');
  }
  const reste = d.reste > 0
    ? `<span class="apres"><b>${d.reste}</b> autre${d.reste > 1 ? 's' : ''} en attente derrière</span>`
    : '';
  return `<div class="acte-reste une">${bouts.join(' ')} ${reste}</div>`;
}

function pieDeListe(d) {
  const bouts = [];
  if (d.reste > 0) {
    bouts.push(`<b>${d.reste}</b> autre${d.reste > 1 ? 's' : ''} en attente. `
      + 'Elles remontent dès que celles-ci sont rayées.');
  }
  if (d.rayees > 0) {
    /* Un clic de travers sur « Fait » doit pouvoir se defaire : le compteur
       est lui-meme le bouton qui remet tout dans la liste. */
    bouts.push(`${d.rayees} ligne${d.rayees > 1 ? 's' : ''} rayée${d.rayees > 1 ? 's' : ''} `
      + `aujourd’hui — <button class="rappel">tout remettre</button>.`);
  }
  return bouts.length ? `<div class="acte-reste">${bouts.join(' ')}</div>` : '';
}

/* Rayer une ligne : la carte s'efface tout de suite, puis la liste se
   recharge pour faire monter celle qui attendait derriere. */
async function rayerLigne(cle, motif, bouton) {
  const carte = bouton.closest('.acte');
  bouton.disabled = true;
  try {
    await api('/api/journee/rayer', {method: 'POST', body: {cle, motif}});
    if (carte) carte.classList.add('part');
    toast(motif === 'fait' ? 'Rayée — elle revient demain si rien n’a bougé'
                           : 'Remise à dans une semaine');
    setTimeout(chargerJournee, 220);
  } catch (e) {
    bouton.disabled = false;
    toast(e.message || 'Impossible de rayer cette ligne', true);
  }
}

async function toutRemettre() {
  try {
    const r = await api('/api/journee/reprendre', {method: 'POST', body: {}});
    toast((r.remises || 0) + ' ligne(s) remise(s) dans la liste');
    chargerJournee();
  } catch (e) { toast(e.message || 'Impossible de remettre les lignes', true); }
}


/* ================= RELAIS ================= */
/* Medias, communautes de parents, relais institutionnels. Ils ne figurent pas
   dans Clubs parce qu'on ne leur demande pas la meme chose : un club donne
   acces a un terrain, un relais donne acces a des parents. Le message n'est
   pas le meme, la relance non plus. */
LOADERS.relais = chargerRelais;

const RELAIS_PICTO = {media: '📰', communaute: '👪', institutionnel: '🏛'};

async function chargerRelais() {
  let d;
  try { d = await api('/api/relais'); }
  catch (e) {
    $('#relaisListe').innerHTML = '<div class="empty">Indisponible.</div>';
    return;
  }
  $('#nRelais').textContent = d.total || '';
  const badge = $('#relaisBadge');
  badge.hidden = !d.a_contacter;
  badge.textContent = d.a_contacter;

  if (!d.items.length) {
    $('#relaisListe').innerHTML = '<div class="empty">Aucun relais enregistré.</div>';
    return;
  }

  /* Par famille, et dans chaque famille les prioritaires d'abord : c'est
     l'ordre dans lequel on les appelle, pas l'ordre alphabetique. */
  $('#relaisListe').innerHTML = d.familles.filter(f => f.n).map(f => {
    const dedans = d.items.filter(r => r.relais_type === f.cle);
    return `<h3 style="margin-top:22px">${RELAIS_PICTO[f.cle] || ''} ${esc(f.nom)}
      <span class="n">${f.n}</span></h3>
      <div class="part-grille">${dedans.map(carteRelais).join('')}</div>`;
  }).join('');

  $$('#relaisListe [data-msg]').forEach(b => b.onclick = () => messageRelais(b.dataset.msg));
  $$('#relaisListe [data-site]').forEach(b => b.onclick = () => ouvrirDehors(b.dataset.site));
  $$('#relaisListe [data-chercher]').forEach(b =>
    b.onclick = () => chercherContactRelais(b.dataset.chercher, b));
}

function carteRelais(r) {
  const chaud = r.relais_priorite >= 3;
  const num = String(r.phone || '').replace(/\D/g, '');
  const contacte = r.last_contact_at
    ? '<span class="tag on">contacté le ' + frDate(r.last_contact_at) + '</span>'
    : '<span class="tag warn">jamais contacté</span>';

  const gestes = [
    r.phone ? `<a class="geste maitre faire" target="_blank" rel="noopener"
       href="https://wa.me/${num}"><span class="ico">💬</span>WhatsApp</a>` : '',
    r.phone ? `<a class="geste appel icone" href="tel:${esc(r.phone)}"
       aria-label="Appeler" title="Appeler ${esc(r.phone)}"><span class="ico">📞</span></a>` : '',
    `<button class="geste${r.phone ? '' : ' maitre'}" data-msg="${r.id}">
       <span class="ico">✉</span>${r.phone ? 'Le message' : 'Voir le message'}</button>`,
    `<button class="geste trouver" data-chercher="${r.id}">
       <span class="ico">🔎</span>Trouver le numéro</button>`,
    r.website ? `<button class="geste discret" data-site="${esc(r.website)}">
       <span class="ico">↗</span>Leur site</button>` : '',
  ].filter(Boolean).join('');

  return `<article class="part${chaud ? ' chaude' : ''}">
    <header>
      <span class="rond" style="--teinte:${teinte(r.name)}">
        <span class="picto">${RELAIS_PICTO[r.relais_type] || '📰'}</span></span>
      <div class="titre">
        <b>${esc(r.name)}</b>
        ${r.relais_audience ? '<div class="lieu">' + esc(r.relais_audience) + '</div>' : ''}
      </div>
      ${chaud ? '<span class="etiq-prio">à contacter en priorité</span>' : ''}
    </header>
    ${r.relais_role ? '<p class="pourquoi">' + esc(r.relais_role) + '</p>' : ''}
    <div class="etiquettes">${contacte}</div>
    ${r.notes ? `<details class="detail"><summary>
        <span class="geste discret"><span class="ico">ℹ</span>Ce qu'on sait d'eux
          <span class="chev">▶</span></span></summary>
        <p class="pourquoi" style="margin-top:9px">${esc(r.notes)}</p></details>` : ''}
    <footer class="bas">${gestes}</footer>
    <div class="trouvailles" id="trouve-${r.id}" hidden></div>
  </article>`;
}

/* Lire les coordonnees sur LEURS pages, puis retenir celle qu'on garde.
   Sans numero, la carte n'avait pas de bouton WhatsApp et le relais restait
   injoignable : c'est exactement le travail que fait deja le Reperage sur les
   tournois, il manquait seulement ici. Aucune IA : chaque chiffre affiche
   vient d'une page qu'on nomme, et Julien choisit. */
async function chercherContactRelais(id, bouton) {
  const boite = $('#trouve-' + id);
  bouton.disabled = true;
  const mot = bouton.querySelector('span:last-child');
  const avant = mot.textContent;
  mot.textContent = 'Je cherche…';
  boite.hidden = false;
  boite.innerHTML = '<p class="hint">Lecture de leurs pages…</p>';
  let d;
  try { d = await api('/api/clubs/' + id + '/chercher-contact', {method: 'POST', body: {}}); }
  catch (e) {
    boite.innerHTML = '<p class="hint">Échec : ' + esc(e.message) + '</p>';
    bouton.disabled = false; mot.textContent = avant; return;
  }
  bouton.disabled = false; mot.textContent = avant;

  const tels = (d.trouves || []).filter(x => x.numero);
  const mails = (d.trouves || []).filter(x => x.email);
  if (!tels.length && !mails.length) {
    const lues = (d.vues || []).filter(v => v.lue).length;
    boite.innerHTML = '<p class="hint">Rien trouvé sur ' + lues
      + ' page(s) lue(s). Leur contact passe sans doute par un formulaire.</p>';
    return;
  }
  boite.innerHTML =
    (tels.length ? '<p class="hint">Numéros lus sur leurs pages :</p>' : '')
    + tels.map(x => `<button class="trouve${x.mobile ? ' mobile' : ''}"
         data-retenir="${id}" data-tel="${esc(x.numero)}">
         <b>${esc(x.format)}</b>
         <span>${x.mobile ? 'mobile · ' : 'fixe · '}${esc(x.source)}</span></button>`).join('')
    + (mails.length ? '<p class="hint">Adresses lues :</p>' : '')
    + mails.map(x => `<button class="trouve mail"
         data-retenir="${id}" data-mail="${esc(x.email)}">
         <b>${esc(x.email)}</b><span>${esc(x.source)}</span></button>`).join('');

  $$('#trouve-' + id + ' [data-retenir]').forEach(b => b.onclick = async () => {
    const corps = b.dataset.tel ? {phone: b.dataset.tel} : {email: b.dataset.mail};
    try {
      await api('/api/clubs/' + id + '/retenir-contact', {method: 'POST', body: corps});
      toast(b.dataset.tel ? 'Numéro retenu — WhatsApp est actif sur la fiche'
                          : 'Adresse retenue');
      await chargerRelais();
    } catch (e) { toast(e.message || 'Échec', true); }
  });
}

function ouvrirDehors(url) {
  fetch('/api/ouvrir', {method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({url})})
    .then(r => { if (!r.ok) throw new Error('refus'); })
    .catch(() => window.open(url, '_blank', 'noopener'));
}

/* Le mot est rendu a la demande : le preparer pour les douze d'avance
   remplirait la pile de relecture sans qu'aucun ne parte. */
async function messageRelais(id) {
  modal('<h2>Un instant…</h2>');
  let m;
  try { m = await api('/api/relais/' + id + '/message'); }
  catch (e) { modal('<h2>Échec</h2><p class="hint">' + esc(e.message) + '</p>'); return; }
  modal(`<h2>${esc(m.objet || 'Message')}</h2>
    <div class="form">
      <label>Objet <input id="rObjet" value="${esc(m.objet || '')}"></label>
      <label>Message <textarea id="rCorps" rows="16">${esc(m.corps || '')}</textarea></label>
    </div>
    <div class="mini-acts" style="padding:14px 0 0">
      <button class="geste maitre" id="rCopier"><span class="ico">⧉</span>Copier</button>
      <button class="geste piece" id="rPlaquette"><span class="ico">📎</span>Plaquette</button>
      ${m.destinataire && m.destinataire.indexOf('@') > 0
        ? `<button class="geste appel" id="rMail"><span class="ico">✉</span>Par mail</button>` : ''}
      <button class="geste discret" id="rFermer">Fermer</button>
    </div>
    <p class="hint" style="margin-top:10px">Relisez avant d’envoyer. Rien ne part d’ici.</p>`);
  $('#rCopier').onclick = () => copy($('#rCorps').value);
  $('#rFermer').onclick = closeModal;
  $('#rPlaquette').onclick = async () => {
    try { await api('/api/plaquette/copier', {method: 'POST', body: {}});
          toast('Plaquette dans le presse-papiers — Cmd+V dans le mail'); }
    catch (e) { toast('Plaquette : ' + e.message, true); }
  };
  const mail = $('#rMail');
  if (mail) mail.onclick = () => {
    const lien = 'mailto:' + m.destinataire
      + '?subject=' + encodeURIComponent($('#rObjet').value)
      + '&body=' + encodeURIComponent($('#rCorps').value);
    fetch('/api/ouvrir', {method: 'POST', headers: {'Content-Type': 'application/json'},
                          body: JSON.stringify({url: lien})})
      .then(r => { if (!r.ok) throw new Error('refus'); })
      .catch(() => { location.href = lien; });
  };
}

/* Les jours ou Julien sera sur le terrain. C'est le seul bloc de la page qui
   parle de travail deja acquis : il passe avant tout le reste. */
LOADERS.dates = chargerMesDates;
$('#nouvelleDate').onclick = () => formulaireDate(null);

/* Les semaines creuses. Une semaine sans date est une semaine sans chiffre
   d'affaires, et on s'en apercoit d'ordinaire trop tard pour demarcher. On
   rapproche donc l'agenda et la veille, qui vivaient chacun de leur cote. */
const CREUX_TON = {vide: 'vide', we_libre: 'tiede', leger: 'leger', plein: 'plein'};

async function chargerCreuses() {
  let d;
  try { d = await api('/api/semaines'); }
  catch (e) { $('#creuses').innerHTML = ''; $('#creuxTitre').hidden = true; return; }

  const aVoir = (d.semaines || []).filter(s => s.etat !== 'plein' && s.n_pistes);
  $('#creuxTitre').hidden = !aVoir.length;
  $('#nCreuses').textContent = d.n_creuses || '';
  if (!aVoir.length) {
    $('#creuses').innerHTML = (d.semaines || []).length
      ? '<div class="empty">Toutes vos semaines sont pourvues. Rien à combler.</div>' : '';
    return;
  }

  $('#creuses').innerHTML = aVoir.map(s => {
    const du = frDate(s.lundi), au = frDate(s.dimanche);
    const pistes = s.pistes.map(p => {
      const route = p.trajet_min != null
        ? '<b>' + duree(p.trajet_min) + '</b>'
          + (p.distance_km != null ? '<i>' + Math.round(p.distance_km) + ' km</i>' : '')
        : '<i>route inconnue</i>';
      return `<button class="piste" data-piste="${p.id}">
        <span class="p-jour">${esc(jourCourt(p.jour))}${p.week_end ? '' : ' <em>semaine</em>'}</span>
        <span class="p-nom">${esc(p.name || '')}</span>
        <span class="p-ou">${esc(p.city || '')}</span>
        <span class="p-route">${route}</span>
        ${p.nb_equipes ? '<span class="p-eq">' + p.nb_equipes + ' éq.</span>' : ''}
        ${p.lieu_verifie === 0 ? '<span class="p-doute">lieu à confirmer</span>' : ''}
      </button>`;
    }).join('');
    const reste = s.n_pistes - s.pistes.length;
    return `<section class="creux ${CREUX_TON[s.etat] || ''}">
      <header>
        <div>
          <b>Semaine ${s.numero} — du ${esc(du)} au ${esc(au)}</b>
          <div class="creux-etat">${esc(s.dit)}${s.n_cale
            ? ' · ' + s.n_cale + (s.n_cale > 1 ? ' dates posées' : ' date posée') : ''}</div>
        </div>
        <span class="creux-compte">${s.n_pistes} piste${s.n_pistes > 1 ? 's' : ''}${
          s.proches ? ' · <b>' + s.proches + ' à moins de 45 min</b>' : ''}</span>
      </header>
      ${s.cale.length ? '<p class="creux-deja">Déjà : '
        + s.cale.map(c => esc(c.titre)).join(' · ') + '</p>' : ''}
      <div class="pistes">${pistes}</div>
      ${reste > 0 ? '<p class="hint">' + reste + ' autre'
        + (reste > 1 ? 's' : '') + ' à portée, dans Repérage.</p>' : ''}
    </section>`;
  }).join('');

  /* Cliquer une piste emmene sur sa fiche de tournoi : c'est la que le
     message se prepare et que le contact se cherche. */
  $$('#creuses [data-piste]').forEach(b => b.onclick = () => {
    show('tournois');
    setTimeout(() => {
      const l = document.querySelector('#tab-tournois [data-tournoi="' + b.dataset.piste + '"]');
      if (l) l.scrollIntoView({block: 'center', behavior: 'smooth'});
    }, 400);
  });
}

async function chargerMesDates() {
  let d;
  try { d = await api('/api/agenda'); }
  catch (e) { d = { items: [], erreur: e.message }; }

  /* Quand Calendrier ne repond pas, on retombe sur le lien publie : on voit
     toujours ses dates, mais on previent qu'elles ne sont plus modifiables. */
  const avis = $('#datesAvis');
  avis.hidden = !d.avertissement;
  if (d.avertissement) avis.textContent = d.avertissement;
  $('#nouvelleDate').disabled = d.modifiable === false;
  $('#datesSous').textContent = d.modifiable === false
    ? 'lecture seule — Calendrier ne répond pas'
    : 'cliquez une date pour la modifier — elle change dans Calendrier';

  if (d.erreur || !d.items.length) {
    $('#mesDates').innerHTML = `<div class="empty">${esc(d.erreur
      || 'Aucune date à venir. Créez-en une avec « Nouvelle date ».')}</div>`;
    $('#nDates').textContent = '';
    return;
  }
  $('#nDates').textContent = d.total;
  const proches = d.items.filter(e => e.jours <= 7).length;
  const badge = $('#datesBadge');
  badge.hidden = !proches;
  badge.textContent = proches;

  $('#mesDates').innerHTML = d.items.map(e => {
    /* Le compte a rebours est l'information qu'on lit en premier : combien de
       jours avant d'etre sur le terrain. Il est donc ecrit en grand. */
    const quand = e.jours === 0
      ? '<b class="mot">Aujourd\'hui</b>'
      : e.jours === 1 ? '<b class="mot">Demain</b>'
      : '<b class="chiffre">' + e.jours + '</b><span class="unite">jours</span>';
    const lieu = [e.city, e.venue].filter(Boolean)[0] || '';
    const heure = (!e.journee && e.debut) ? e.debut.slice(11, 16) : '';
    return `<article class="date${e.jours <= 3 ? ' proche' : ''}${e.jours <= 7 ? ' semaine' : ''}${
        e.uid ? ' ouvrable' : ''}" ${e.uid ? 'data-uid="' + esc(e.uid) + '"' : ''}
        ${e.uid ? 'tabindex="0" role="button" title="Modifier cette date"' : ''}>
      <div class="quand">${quand}</div>
      <div class="jour">${frDate(e.start_date)}${heure ? ' · ' + heure : ''}</div>
      <div class="quoi">${esc(e.name || '')}</div>
      ${lieu ? '<div class="ou">' + esc(lieu) + '</div>' : ''}
      ${e.distance_km != null ? '<div class="km">' + e.distance_km + ' km</div>' : ''}
    </article>`;
  }).join('');

  const ouvrir = el => {
    const e = d.items.filter(x => x.uid === el.dataset.uid)[0];
    if (e) formulaireDate(e);
  };
  $$('#mesDates .date.ouvrable').forEach(el => {
    el.onclick = () => ouvrir(el);
    el.onkeydown = ev => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); ouvrir(el); }
    };
  });
}

/* Creer ou modifier une date. Elle part dans Calendrier du Mac, qui la
   remonte sur iCloud et sur le telephone : il n'y a donc qu'un seul agenda,
   et pas une copie de plus a tenir a jour. */
function formulaireDate(e) {
  const neuf = !e || !e.uid;
  const dep = (e && e.debut) || '';
  const arr = (e && e.fin) || '';
  const auj = new Date();
  const parDefaut = auj.getFullYear() + '-'
    + String(auj.getMonth() + 1).padStart(2, '0') + '-'
    + String(auj.getDate()).padStart(2, '0');

  modal(`<h2>${neuf ? 'Nouvelle date' : 'Modifier la date'}</h2>
    <div class="form">
      <label>Intitulé
        <input id="dTitre" value="${esc((e && e.name) || '')}"
               placeholder="Tournoi U11 — AS Cannes"></label>
      <label class="chk"><input type="checkbox" id="dJournee"
        ${e && e.journee ? 'checked' : ''}> Toute la journée</label>
      <div class="deux">
        <label>Début <input type="date" id="dJour1"
          value="${esc(dep.slice(0, 10) || parDefaut)}"></label>
        <label>Heure <input type="time" id="dH1"
          value="${esc(dep.slice(11, 16) || '09:00')}"></label>
      </div>
      <div class="deux">
        <label>Fin <input type="date" id="dJour2"
          value="${esc(arr.slice(0, 10) || dep.slice(0, 10) || parDefaut)}"></label>
        <label>Heure <input type="time" id="dH2"
          value="${esc(arr.slice(11, 16) || '18:00')}"></label>
      </div>
      <label>Lieu <input id="dLieu" value="${esc((e && (e.venue || e.city)) || '')}"
             placeholder="Cannes"></label>
      <label>Notes <textarea id="dNotes" rows="3">${esc((e && e.notes) || '')}</textarea></label>
    </div>
    <div class="mini-acts" style="padding:14px 0 0">
      <button class="geste maitre" id="dOk">
        <span class="ico">✓</span>${neuf ? 'Créer' : 'Enregistrer'}</button>
      ${neuf ? '' : `<button class="geste" id="dSuppr">
        <span class="ico">🗑</span>Supprimer</button>`}
      <button class="geste discret" id="dAnnuler">Annuler</button>
    </div>
    <p class="hint" style="margin-top:10px">Écrit dans Calendrier du Mac,
      calendrier « SnapActions ». La synchronisation iCloud suit.</p>`);

  /* Les heures disparaissent sur une journee entiere : les laisser visibles
     laisserait croire qu'elles comptent encore. */
  const majJournee = () => {
    const tout = $('#dJournee').checked;
    [$('#dH1'), $('#dH2')].forEach(h => h.closest('label').hidden = tout);
  };
  $('#dJournee').onchange = majJournee;
  majJournee();

  $('#dAnnuler').onclick = closeModal;

  $('#dOk').onclick = async () => {
    const tout = $('#dJournee').checked;
    const titre = $('#dTitre').value.trim();
    if (!titre) { toast('Il faut un intitulé', true); return; }
    const j1 = $('#dJour1').value, j2 = $('#dJour2').value || $('#dJour1').value;
    if (!j1) { toast('Il faut une date de début', true); return; }
    const debut = tout ? j1 + 'T00:00' : j1 + 'T' + ($('#dH1').value || '09:00');
    const fin   = tout ? j2 + 'T23:59' : j2 + 'T' + ($('#dH2').value || '18:00');
    if (fin < debut) { toast('La fin est avant le début', true); return; }

    const corps = {titre, debut, fin, journee: tout,
                   lieu: $('#dLieu').value.trim(), notes: $('#dNotes').value};
    $('#dOk').disabled = true;
    $('#dOk').querySelector('span:last-child') ||
      ($('#dOk').textContent = 'Un instant…');
    try {
      if (neuf) await api('/api/agenda', {method: 'POST', body: corps});
      else await api('/api/agenda/' + encodeURIComponent(e.uid),
                     {method: 'PATCH', body: corps});
      closeModal();
      toast(neuf ? 'Date créée dans Calendrier' : 'Date modifiée dans Calendrier');
      await chargerMesDates();
    } catch (err) {
      $('#dOk').disabled = false;
      toast(err.message || 'Échec de l’enregistrement', true);
    }
  };

  const suppr = $('#dSuppr');
  if (suppr) suppr.onclick = async () => {
    /* Supprimer une date de tournage ne se rattrape pas d'un clic : on
       nomme ce qu'on s'apprete a effacer avant de le faire. */
    if (!confirm('Supprimer « ' + (e.name || '') + ' » du '
                 + frDate(e.start_date) + ' ?\n\nElle disparaîtra de Calendrier '
                 + 'et de tous vos appareils.')) return;
    suppr.disabled = true;
    try {
      await api('/api/agenda/' + encodeURIComponent(e.uid), {method: 'DELETE'});
      closeModal();
      toast('Date supprimée');
      await chargerMesDates();
    } catch (err) {
      suppr.disabled = false;
      toast(err.message || 'Échec de la suppression', true);
    }
  };
}

/* Les discussions ou la balle est dans notre camp. Trois suffisent : le reste
   est dans Suivi, et un tableau de bord qui liste cinquante lignes ne fait
   prendre aucune decision. */
function chargerAttendent(suivi) {
  const cols = suivi.colonnes || [];
  const rep = (cols.find(c => c.cle === 'a_traiter') || {cartes: []}).cartes;
  const app = (cols.find(c => c.cle === 'a_appeler') || {cartes: []}).cartes;
  /* Ceux qu'il faut appeler passent devant : un silence coute plus cher
     qu'une reponse qui attend. */
  const col = {cartes: app.concat(rep)};
  $('#nAttendent').textContent = col.cartes.length || '';
  if (!col.cartes.length) {
    $('#attendent').innerHTML = '<div class="empty">Personne n\'attend de réponse.</div>';
    return;
  }
  $('#attendent').innerHTML = col.cartes.slice(0, 5).map(c => {
    const dernier = (c.derniers || []).filter(m => !m.de_moi).slice(-1)[0];
    const ev = c.evenement;
    return `<div class="item">
      <div class="body">
        <div class="t">${esc(c.club || '')}</div>
        <div class="s">${c.etape === 'a_appeler'
            ? '<span class="tag warn">à appeler</span> ' : ''}${[c.personne, c.jours == null ? '' :
            (c.jours === 0 ? "aujourd'hui" : c.jours === 1 ? 'hier' : 'il y a ' + c.jours + ' j')]
            .filter(Boolean).map(esc).join(' · ')}
          ${ev && ev.start_date ? ' · <b>' + esc(ev.name || '') + '</b> ' + frDate(ev.start_date) : ''}</div>
        ${dernier ? '<div class="s cite">« ' + esc((dernier.texte || '').slice(0, 110)) + ' »</div>' : ''}
      </div>
      <div class="acts">
        ${c.etape === 'a_appeler' && c.phone
          ? `<a class="mini danger" href="tel:${c.phone}">Appeler</a>`
          : (c.phone ? `<a class="mini primary" target="_blank" rel="noopener"
             href="https://wa.me/${c.phone.replace(/\D/g, '')}">Répondre</a>` : '')}
      </div></div>`;
  }).join('');
}

/* Un tournoi qui a lieu demain a 250 km ne se decide pas : il est perdu.
   On ne montre que ce sur quoi on peut encore agir — assez loin dans le temps
   pour proposer une couverture, assez pres pour que la route se justifie. */
const FENETRE_MIN = 10, FENETRE_MAX = 75, ROUTE_MAX = 300;

/* « 0 h 17 » ne se lit pas. En dessous d'une heure, on parle en minutes. */
function duree(min) {
  if (min == null) return '';
  if (min < 60) return min + ' min';
  const h = Math.floor(min / 60), m = min % 60;
  return m ? h + ' h ' + String(m).padStart(2, '0') : h + ' h';
}

async function chargerATrancher() {
  let items = [];
  try { items = ((await api('/api/tournaments/a-valider')) || {}).items || []; }
  catch (e) { items = []; }

  const auj = new Date();
  const jours = t => t.start_date
    ? Math.round((new Date(t.start_date + 'T12:00:00') - auj) / 86400000) : null;

  const utiles = items.filter(t => {
    const j = jours(t);
    return t.trajet_min != null && t.trajet_min <= ROUTE_MAX
        && j != null && j >= FENETRE_MIN && j <= FENETRE_MAX;
  });
  // Le plus pres d'abord : c'est la route qui decide, pas le calendrier.
  utiles.sort((a, b) => (a.distance_km || 99999) - (b.distance_km || 99999));

  $('#nTrancher').textContent = utiles.length || '';
  if (!utiles.length) {
    $('#aTrancher').innerHTML = '<div class="empty">Rien à décider : '
      + 'aucun tournoi à portée dans les dix à soixante-quinze jours.</div>';
    return;
  }
  $('#aTrancher').innerHTML = utiles.slice(0, 10).map(t => {
    const v = String(t.distance_verdict || '').toLowerCase();
    const cls = v === 'proche' ? 'on' : (v === 'correct' ? 'on' : 'warn');
    return `<div class="item" data-tournoi="${t.id}">
      <div class="body">
        <div class="t">${esc(t.club_name || t.name)}</div>
        <div class="s">${frDate(t.start_date)} · <b>J-${jours(t)}</b>${
            t.city ? ' · ' + esc(t.city) : ''}
          · <span class="tag ${cls}">${t.distance_km} km</span>
          ${t.trajet_min ? ' · ' + duree(t.trajet_min) : ''}
          ${t.nb_equipes ? ' · ' + t.nb_equipes + ' équipes' : ''}</div>
      </div>
      <div class="acts">
        <button class="mini" data-act="oui">On y va</button>
        <button class="mini" data-act="non">On passe</button>
      </div></div>`;
  }).join('');

  $$('#aTrancher [data-tournoi]').forEach(row => {
    const id = row.dataset.tournoi;
    $$('button[data-act]', row).forEach(b => b.onclick = async () => {
      try {
        await api('/api/tournaments/' + id, { method: 'PATCH',
          body: { validation: b.dataset.act === 'oui' ? 'valide' : 'ecarte' } });
        toast(b.dataset.act === 'oui' ? 'Retenu' : 'Écarté');
        chargerATrancher();
      } catch (e) { toast(e.message, true); }
    });
  });
}

function taskRow(t) {
  const who = [t.first_name, t.last_name].filter(Boolean).join(' ');
  return `<div class="item${t.overdue ? ' late' : ''}" data-task="${t.id}"
    data-club="${t.club_id}">
    <div class="body">
      <div class="t">${esc(t.club_name || t.title)}</div>
      <div class="s">${esc(t.title)} · ${esc(t.when)}${who ? ' · ' + esc(who) : ''}
      ${t.contact_phone ? ' · <a href="tel:' + esc(t.contact_phone) + '">appeler</a>' : ''}</div>
    </div>
    <div class="acts">
      <button class="mini" data-act="open">Fiche</button>
      <button class="mini" data-act="snooze">+7 j</button>
      <button class="mini" data-act="done">Fait</button>
    </div></div>`;
}

function bindTaskRows(root) {
  $$('[data-task]', root).forEach(row => {
    const id = row.dataset.task;
    $$('button[data-act]', row).forEach(b => b.onclick = async () => {
      try {
        if (b.dataset.act === 'open') return openClub(row.dataset.club);
        if (b.dataset.act === 'snooze') await api(`/api/tasks/${id}/snooze`,
          { method: 'POST', body: { days: 7 } });
        if (b.dataset.act === 'done') await api(`/api/tasks/${id}/done`, { method: 'POST' });
        toast('Mis à jour'); LOADERS.today();
      } catch (e) { toast(e.message, true); }
    });
  });
}

const btnRelances = $('#genRelances');
if (btnRelances) btnRelances.onclick = async () => {
  try {
    const r = await api('/api/drafts/generate-relances', { method: 'POST', body: {} });
    toast(r.n ? `${r.n} brouillon(s) prêt(s) à relire` : 'Rien de nouveau à préparer');
    LOADERS.today();
  } catch (e) { toast(e.message, true); }
};

$('#runWatch').onclick = async (ev) => {
  ev.target.disabled = true; ev.target.textContent = 'Veille en cours…';
  try {
    const r = await api('/api/watch/run', { method: 'POST', body: { generate: true } });
    const nouv = r.sources.reduce((a, s) => a + (s.n_created || 0), 0);
    const echecs = r.sources.filter(s => !s.ok);
    toast(`${nouv} tournoi(s), ${r.generated.n_drafts || 0} brouillon(s)` +
      (echecs.length ? ` — ${echecs.length} source(s) en échec` : ''), echecs.length > 0);
    LOADERS.today();
  } catch (e) { toast(e.message, true); }
  ev.target.disabled = false; ev.target.textContent = 'Lancer la veille';
};

/* ================= CLUBS ================= */
/* Une carte par club plutot qu'une ligne de tableau : sur 103 clubs, la
   colonne « Lieu » se lisait de haut en bas sans qu'on voie jamais qu'une
   ville en concentre quinze. Le regroupement le rend evident. */

/* Le champ sport porte parfois toute une plaquette : « Tae Kwon Do (art
   martial coreen) - enfants des 3 ans, ados... ». On n'en garde que la tete,
   sinon chaque club devient son propre groupe. */
function sportCourt(sport) {
  const tete = String(sport || '').split(/[(;,]| - | – /)[0].trim();
  return tete.length > 30 ? tete.slice(0, 29).trim() + '…' : tete;
}

const GROUPES = {
  ville: {label: 'ville', cle: c => (c.city || '').trim() || 'Ville inconnue'},
  sport: {label: 'sport', cle: c => sportCourt(c.sport) || 'Sport non renseigné'},
  dept:  {label: 'département',
          cle: c => c.dept ? (c.dept + ' — ' + (c.dept_name || '')).trim().replace(/ —\s*$/, '')
                           : 'Département inconnu'},
};

LOADERS.clubs = async function () {
  const p = new URLSearchParams({
    q: $('#clubSearch').value, status: $('#clubStatus').value,
    dept: $('#clubDept').value, order: $('#clubOrder').value });
  const d = await api('/api/clubs?' + p);

  /* Ce que l'onglet Contacts permettait de voir — qui est joignable — devient
     un filtre ici, puisque les coordonnees sont sur les cartes. */
  const joignable = $('#clubJoignable').value;
  if (joignable) {
    const aQqun = c => (c.contacts || []).some(x => x.phone || x.phone2 || x.email)
                       || c.phone || c.email;
    d.items = d.items.filter(c => joignable === '1' ? aQqun(c) : !aQqun(c));
  }

  if (!d.items.length) {
    $('#clubList').innerHTML = '<div class="empty">Aucun club. Passez par l\'onglet Import.</div>';
    return;
  }

  const groupe = GROUPES[$('#clubGroupe').value];
  let html = '';
  if (!groupe) {
    html = '<div class="cartes">' + d.items.map(carteClub).join('') + '</div>';
  } else {
    const paquets = new Map();
    d.items.forEach(c => {
      const k = groupe.cle(c);
      if (!paquets.has(k)) paquets.set(k, []);
      paquets.get(k).push(c);
    });
    // Les gros paquets d'abord : c'est la ou il y a du travail.
    const ordre = [...paquets.entries()].sort((a, b) =>
      b[1].length - a[1].length || a[0].localeCompare(b[0], 'fr'));
    html = ordre.map(([nom, clubs]) => `
      <section class="paquet">
        <h3>${esc(nom)}<span class="n">${clubs.length}</span></h3>
        <div class="cartes">${clubs.map(carteClub).join('')}</div>
      </section>`).join('');
  }
  $('#clubList').innerHTML = html;

  /* Julien cliquait au milieu de la carte et rien ne s'ouvrait : seul le
     titre reagissait. Toute la carte ouvre la fiche, sauf les boutons. */
  $$('#clubList .carte-club').forEach(el => el.onclick = ev => {
    if (ev.target.closest('a, input, button.mini')) return;
    openClub(el.dataset.club);
  });
  $$('#clubList input.pick').forEach(cb => cb.onchange = () => {
    const id = +cb.dataset.id;
    if (cb.checked) picked.add(id); else picked.delete(id);
    refreshBulk();
  });
};

/* La carte d'un club sert a une chose : joindre quelqu'un. Elle portait
   « Contacts : 2 » sans dire lesquels, et il fallait ouvrir une fiche pour
   trouver un numero. Maintenant les gens sont dessus, avec leur telephone
   sous le pouce — l'onglet Contacts n'a plus de raison d'etre. */
function ligneContact(ct) {
  const nom = [ct.first_name, ct.last_name].filter(Boolean).join(' ').trim();
  const tel = ct.phone || ct.phone2;
  // Sans prenom, le role tient lieu de nom : « Contact — Contact WhatsApp »
  // ne dit rien de plus que « Contact WhatsApp ».
  const titre = nom || ct.role || 'Contact';
  const role = nom && ct.role ? ' <em>' + esc(ct.role) + '</em>' : '';
  return `<li>
    <span class="nom">${esc(titre)}${role}</span>
    <span class="gestes">
      ${tel ? `<a class="mini" href="tel:${esc(tel)}" title="Appeler ${esc(tel)}">Appeler</a>
              <a class="mini" target="_blank" rel="noopener"
                 href="https://wa.me/${tel.replace(/\D/g, '')}">WhatsApp</a>` : ''}
      ${ct.email ? `<a class="mini" href="mailto:${esc(ct.email)}">Mail</a>` : ''}
      ${!tel && !ct.email ? '<span class="mute">aucune coordonnée</span>' : ''}
    </span>
  </li>`;
}

/* Une identite visuelle pour chaque club, sans inventer son blason.

   Aucun de vos clubs n'a de site exploitable : il n'y a donc aucun logo a
   telecharger, et aller en chercher un par une recherche sur le nom finirait
   par coller l'embleme d'un autre club sur la carte. A la place, chaque club
   recoit une pastille qui lui est propre : une couleur deduite de son nom —
   donc toujours la meme, d'un affichage a l'autre — un pictogramme de sa
   discipline, et ses initiales. Un vrai logo, quand il y en a un, la remplace. */
const PICTOS = [
  [/futsal|football am|foot|soccer/i, '\u26bd'],
  [/basket/i, '\ud83c\udfc0'],
  [/volley/i, '\ud83c\udfd0'],
  [/hand/i, '\ud83e\udd3e'],
  [/rugby/i, '\ud83c\udfc9'],
  [/tennis de table|ping/i, '\ud83c\udfd3'],
  [/tennis|padel/i, '\ud83c\udfbe'],
  [/voile|yacht|nautique|kayak|pagaie|aviron|canoe/i, '\u26f5'],
  [/natation|piscine|plongee/i, '\ud83c\udfca'],
  [/ski|snowboard|glisse/i, '\ud83c\udfbf'],
  [/danse|k-?pop|spectacle|magie/i, '\ud83d\udc83'],
  [/gym|trampoline/i, '\ud83e\udd38'],
  [/judo|karate|taekwondo|tae kwon|aikido|lutte|krav|self-?defense|muay|boxe|boxing|arts? martia/i, '\ud83e\udd4b'],
  [/escalade|grimpe/i, '\ud83e\uddd7'],
  [/equitation|poney|cheval/i, '\ud83d\udc0e'],
  [/moto|trial|auto|drift|karting/i, '\ud83c\udfc1'],
  [/velo|cyclis|bmx|vtt/i, '\ud83d\udeb4'],
  [/athle|course|trail|running|marathon/i, '\ud83c\udfc3'],
  [/hockey/i, '\ud83c\udfd1'],
  [/petanque|boule/i, '\ud83c\udfb1'],
  [/tir|archer/i, '\ud83c\udfaf'],
  [/golf/i, '\u26f3'],
  [/echecs|jeu/i, '\ud83c\udfb2'],
  [/scout|nature|camp|rando/i, '\u26fa'],
  [/handisport|adapte/i, '\u267f'],
  [/sabre laser|escrime/i, '\ud83e\udd3a'],
  [/musique|chant|chorale|theatre/i, '\ud83c\udfad'],
];

function picto(sport) {
  for (const [rx, emo] of PICTOS) if (rx.test(sport || '')) return emo;
  return '';
}

/* La meme chaine donne toujours la meme teinte : la carte d'un club garde sa
   couleur d'une session a l'autre, ce qui aide a le reconnaitre de loin. */
function teinte(texte) {
  let h = 0;
  for (let i = 0; i < (texte || '').length; i++) h = (h * 31 + texte.charCodeAt(i)) % 360;
  return h;
}

function blason(c) {
  if (c.logo) return `<img src="/api/logos/${c.id}" alt="" loading="lazy">`;
  const p = picto(c.sport);
  return p ? `<span class="picto">${p}</span>`
           : `<span class="ini">${esc(initiales(c.name))}</span>`;
}

function carteClub(c) {
  const [tl, tc] = TEMP[c.temperature] || ['', ''];
  const lieu = [c.city, c.dept].filter(Boolean).join(' · ');
  const gens = c.contacts || [];
  const gene = [c.phone, c.email].filter(Boolean);

  return `<article class="carte-club${c.do_not_contact ? ' stop' : ''}"
      data-club="${c.id}">
    <input type="checkbox" class="pick" data-id="${c.id}" ${picked.has(c.id) ? 'checked' : ''}
      title="Sélectionner">
    <header>
      <span class="blason" style="--teinte:${teinte(c.name)}">${blason(c)}</span>
      <div class="titre">
        <button class="ouvrir" data-club="${c.id}">${esc(c.name)}</button>
        <div class="lieu">${[sportCourt(c.sport), lieu].filter(Boolean).map(esc).join(' · ')}</div>
      </div>
    </header>
    <div class="etiquettes">
      ${c.ou_en_est ? '<span class="tag on">' + esc(c.ou_en_est) + '</span>'
                    : '<span class="tag">' + esc(STATUS_LABEL[c.status] || c.status) + '</span>'}
      ${c.last_contact_at ? '<span class="tag ' + tc + '">' + esc(tl) + ' '
        + frDate(c.last_contact_at) + '</span>' : ''}
      ${c.do_not_contact ? '<span class="tag bad">ne pas contacter</span>' : ''}
    </div>
    ${gens.length
      ? '<ul class="gens">' + gens.slice(0, 4).map(ligneContact).join('')
        + (gens.length > 4 ? '<li class="autres">+ ' + (gens.length - 4)
           + ' autre(s)</li>' : '') + '</ul>'
      : (gene.length
          ? `<ul class="gens"><li>
              <span class="nom">Contact du club</span>
              <span class="gestes">
                ${c.phone ? `<a class="mini" href="tel:${esc(c.phone)}">Appeler</a>
                  <a class="mini" target="_blank" rel="noopener"
                     href="https://wa.me/${c.phone.replace(/\D/g, '')}">WhatsApp</a>` : ''}
                ${c.email ? `<a class="mini" href="mailto:${esc(c.email)}">Mail</a>` : ''}
              </span></li></ul>`
          : '<div class="injoignable">Aucun contact — club injoignable</div>')}
  </article>`;
}

async function openClub(id) {
  const c = await api('/api/clubs/' + id);
  const statusOpts = Object.entries(STATUS_LABEL).map(([k, v]) =>
    `<option value="${k}"${c.status === k ? ' selected' : ''}>${esc(v)}</option>`).join('');
  modal(`
    <h2 style="font-size:19px">${esc(c.name)}</h2>
    <div class="toolbar">
      <select id="mStatus">${statusOpts}</select>
      ${c.city ? '<span class="tag">' + esc(c.city) + '</span>' : ''}
      ${c.dept ? '<span class="tag">' + esc(c.dept_name) + '</span>' : ''}
      ${c.website ? '<a class="tag" target="_blank" rel="noopener" href="' + esc(c.website) + '">site</a>' : ''}
      <span class="grow"></span>
      <button class="mini" id="mDraft">Écrire</button>
      <button class="mini" id="mStudio">Proposer un shooting studio</button>
      <button class="mini" id="mSeq">Programmer relances</button>
      <button class="mini danger" id="mOptout">Ne plus contacter</button>
    </div>
    ${c.do_not_contact ? '<div class="banner">Ce club a demandé à ne plus être contacté.</div>' : ''}
    <div class="split">
      <div>
        <h3>Contacts</h3>
        <div class="list">${c.contacts.map(ct => `<div class="item"><div class="body">
          <div class="t">${esc([ct.first_name, ct.last_name].filter(Boolean).join(' ') || '—')}
            ${ct.role ? '<span class="tag">' + esc(ct.role) + '</span>' : ''}</div>
          <div class="s">${ct.email ? '<a href="mailto:' + esc(ct.email) + '">' + esc(ct.email) + '</a>' : ''}
          ${ct.phone ? ' · <a href="tel:' + esc(ct.phone) + '">' + esc(ct.phone) + '</a>' : ''}</div>
        </div></div>`).join('') || '<div class="empty">Aucun contact.</div>'}</div>
        <h3>Ajouter une note</h3>
        <textarea id="mNote" rows="3" placeholder="Ce qui s'est dit…"></textarea>
        <div class="toolbar" style="margin-top:6px">
          <select id="mKind"><option value="note">Note</option><option value="call">Appel</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="email">Email</option><option value="meeting">RDV</option></select>
          <button class="mini primary" id="mNoteSave">Enregistrer</button>
        </div>

        <h3>Message reçu → dates</h3>
        <p class="hint" style="margin:0 0 6px">Collez sa réponse : les dates annoncées
          partent dans votre calendrier.</p>
        <textarea id="mReply" rows="4"
          placeholder="« Notre prochain tournoi sera le weekend du 10/11 octobre… »"></textarea>
        <div class="toolbar" style="margin-top:6px">
          <button class="mini primary" id="mDates">Repérer les dates</button>
        </div>
        <div id="mDatesResult"></div>
      </div>
      <div>
        <h3>Relances programmées</h3>
        <div class="list">${c.tasks.filter(t => t.status === 'pending').map(t =>
          `<div class="item"><div class="body"><div class="s">${frDate(t.due_at)} — ${esc(t.title)}</div>
           </div></div>`).join('') || '<div class="empty">Aucune.</div>'}</div>
        <h3>Historique</h3>
        <div class="list">${c.interactions.map(i => `<div class="item"><div class="body">
          <div class="s">${frDate(i.occurred_at)} · ${esc(i.kind)}${i.subject ? ' · ' + esc(i.subject) : ''}</div>
          ${i.body ? '<div class="raw">' + esc(i.body.slice(0, 400)) + '</div>' : ''}
        </div></div>`).join('') || '<div class="empty">Rien pour l\'instant.</div>'}</div>
      </div>
    </div>`);

  $('#mStatus').onchange = async e => {
    await api(`/api/clubs/${id}/status`, { method: 'POST', body: { status: e.target.value } });
    toast('Statut mis à jour'); LOADERS.clubs();
  };
  $('#mNoteSave').onclick = async () => {
    const body = $('#mNote').value.trim();
    if (!body) return;
    await api(`/api/clubs/${id}/interactions`, { method: 'POST',
      body: { kind: $('#mKind').value, body } });
    toast('Noté'); openClub(id);
  };
  $('#mDates').onclick = async () => {
    const text = $('#mReply').value.trim();
    if (!text) return toast('Collez d\'abord le message', true);
    try {
      const d = await api('/api/dates/extract', { method: 'POST', body: { text, club_id: +id } });
      if (!d.n) {
        $('#mDatesResult').innerHTML = '<div class="empty">Aucune date repérée.</div>';
        return;
      }
      $('#mDatesResult').innerHTML = `<div class="list" style="margin-top:8px">` +
        d.items.map((it, i) => `<div class="item">
          <input type="checkbox" class="dpick" data-i="${i}" ${it.deja_connu ? '' : 'checked'}>
          <div class="body">
            <div class="t">${esc(it.nom)}
              <span class="tag ${it.confiance >= 90 ? 'on' : 'warn'}">${it.confiance}% sûr</span>
              ${it.annee_devinee ? '<span class="tag">année déduite</span>' : ''}
              ${it.deja_connu ? '<span class="tag warn">déjà au calendrier</span>' : ''}</div>
            <div class="s">${frDate(it.debut)}${it.fin !== it.debut ? ' → ' + frDate(it.fin) : ''}</div>
            <div class="raw">« ${esc(it.extrait)} »</div>
          </div></div>`).join('') +
        `</div><div class="toolbar" style="margin-top:8px">
          <button class="mini primary" id="mDatesSave">Ajouter au calendrier</button></div>`;
      $('#mDatesSave').onclick = async () => {
        const keep = $$('#mDatesResult input.dpick').filter(c => c.checked)
          .map(c => d.items[+c.dataset.i]);
        if (!keep.length) return toast('Rien de coché', true);
        const r = await api('/api/dates/commit', { method: 'POST', body: { items: keep } });
        toast(`${r.created} date(s) ajoutée(s) au calendrier`);
        openClub(id);
      };
    } catch (e) { toast(e.message, true); }
  };
  $('#mSeq').onclick = async () => {
    await api(`/api/clubs/${id}/sequence`, { method: 'POST', body: {} });
    toast('Relances programmées'); openClub(id);
  };
  $('#mOptout').onclick = async () => {
    const reason = prompt('Motif (facultatif) :');
    if (reason === null) return;
    await api(`/api/clubs/${id}/optout`, { method: 'POST', body: { reason } });
    toast('Club retiré de la prospection'); closeModal(); LOADERS.clubs();
  };
  // Le shooting studio se fait en semaine, sur un entrainement : c'est ce qui
  // remplit les jours libres entre deux week-ends de tournois.
  $('#mStudio').onclick = async () => {
    try {
      const d = await api('/api/drafts', { method: 'POST',
        body: { club_id: +id, template_key: 'studio_club', created_by: 'humain',
                reason: 'Shooting studio mobile — adhérents' } });
      closeModal();
      montrerMessage(d.id, 'Shooting studio — relisez avant d’envoyer');
    } catch (e) { toast(e.message, true); }
  };
  $('#mDraft').onclick = async () => {
    const tpls = await api('/api/templates');
    const key = prompt('Modèle :\n' + tpls.items.map(t => '· ' + t.key).join('\n'),
      'premier_contact');
    if (!key) return;
    try {
      const d = await api('/api/drafts', { method: 'POST',
        body: { club_id: +id, template_key: key, created_by: 'humain' } });
      closeModal();
      montrerMessage(d.id, 'Message préparé — relisez avant d\u2019envoyer');
    } catch (e) { toast(e.message, true); }
  };
}

$('#newClub').onclick = () => {
  modal(`<h2>Nouveau club</h2><div class="form">
    <label>Nom <input id="nName"></label>
    <label>Ville <input id="nCity"></label>
    <label>Code postal <input id="nPc"></label>
    <label>Email <input id="nMail"></label>
    <label>Téléphone <input id="nPhone"></label>
    <label>Site <input id="nWeb"></label>
    <label>Source <input id="nSrc" placeholder="Forum…"></label>
    <button class="primary" id="nSave">Créer</button></div>`);
  $('#nSave').onclick = async () => {
    try {
      await api('/api/clubs', { method: 'POST', body: {
        name: $('#nName').value, city: $('#nCity').value, postal_code: $('#nPc').value,
        email: $('#nMail').value, phone: $('#nPhone').value, website: $('#nWeb').value,
        source: $('#nSrc').value } });
      closeModal(); toast('Club créé'); LOADERS.clubs();
    } catch (e) { toast(e.message, true); }
  };
};

/* ================= CONTACTS ================= */
LOADERS.contacts = async function () {
  const p = new URLSearchParams({ q: $('#contactSearch').value,
    has_phone: $('#contactPhone').value });
  const d = await api('/api/contacts?' + p);
  if (!d.items.length) {
    $('#contactList').innerHTML = '<div class="empty">Aucun contact.</div>'; return;
  }
  $('#contactList').innerHTML = `<table><thead><tr>
    <th>Nom</th><th>Rôle</th><th>Club</th><th>Email</th><th>Téléphone</th>
    <th>Dernier contact</th></tr></thead><tbody>` + d.items.map(c => `<tr>
    <td>${esc([c.first_name, c.last_name].filter(Boolean).join(' ') || '—')}</td>
    <td>${esc(c.role || '')}</td>
    <td class="click" data-club="${c.club_id || ''}">${esc(c.club_name || '')}</td>
    <td>${c.email ? '<a href="mailto:' + esc(c.email) + '">' + esc(c.email) + '</a>' : ''}</td>
    <td>${c.phone ? '<a href="tel:' + esc(c.phone) + '">' + esc(c.phone_fmt) + '</a>' +
        (c.is_mobile ? ' <span class="tag">mobile</span>' : '') : ''}</td>
    <td>${frDate(c.last_contact_at)}</td></tr>`).join('') + '</tbody></table>';
  $$('#contactList td.click').forEach(td => {
    if (td.dataset.club) td.onclick = () => openClub(td.dataset.club);
  });
};
/* Les filtres de l'ecran Clubs n'etaient branches sur rien : on pouvait
   changer la ville ou le statut, la liste ne bougeait pas. */
['clubSearch', 'clubJoignable', 'clubStatus', 'clubDept', 'clubOrder', 'clubGroupe']
  .forEach(id => {
    const el = $('#' + id);
    if (!el) return;
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input',
                        debounce(() => LOADERS.clubs()));
  });

/* ================= TOURNOIS ================= */
/* Les tournois ne sont pas un catalogue : ce sont des decisions qui expirent.
   La page les range donc par ce qui commande le choix — peut-on y aller — et
   affiche le trajet en clair. Un tournoi a 5 h de route ne doit pas avoir la
   meme tete qu'un tournoi a vingt minutes. */
const ZONES_AIDE = {
  jour:     ['Faisable dans la journée', 'Aller-retour dans la journée — le cœur du métier.'],
  bassin:   ['Autres bassins — Lyon, Toulouse',
             'Loin, mais vous y allez : ça se décide au cas par cas.'],
  ailleurs: ['Ailleurs', 'Gardé pour mémoire. Rien ne s\u2019écrit tout seul ici.'],
};

function ligneTournoi(t) {
  const reste = t.jours_avant;
  let echeance = '', classe = '';
  if (t.a_soi) { echeance = 'votre date'; classe = 'soi'; }
  else if (reste === null || reste === undefined) { echeance = 'date inconnue'; }
  else if (reste < 0) { echeance = 'passé'; classe = 'passe'; }
  else if (t.in_window) { echeance = 'à décider avant le ' + frDate(t.decision_avant); classe = 'ouvert'; }
  else if (reste < 10) { echeance = 'trop tard pour démarcher'; classe = 'tard'; }
  else { echeance = 'trop tôt, revenez le ' + frDate(t.decision_avant); classe = 'tot'; }

  const nom = t.url
    ? `<a target="_blank" rel="noopener" href="${esc(t.url)}">${esc(t.name)}</a>`
    : esc(t.name);
  return `<tr>
    <td>${nom}${t.n_drafts ? ' <span class="tag on">' + t.n_drafts + ' message(s)</span>' : ''}
      <div class="s">${esc(t.city || 'lieu à préciser')}${t.nb_equipes ? ' · ' + t.nb_equipes + ' équipes' : ''}</div></td>
    <td>${frDate(t.start_date)}${t.end_date && t.end_date !== t.start_date ? ' → ' + frDate(t.end_date) : ''}</td>
    <td class="trajet">${t.lieu_verifie === 0
      ? `<span class="doute" title="${esc(t.lieu_phrase || 'Lieu non corroboré — la distance peut être fausse.')}">lieu à confirmer</span>`
      : esc(t.trajet || '—')}</td>
    <td><span class="ech ${classe}">${esc(echeance)}</span></td>
    <td><button class="mini" data-match="${t.id}">Clubs concernés</button></td></tr>`;
}

LOADERS.tournois = async function () {
  const p = new URLSearchParams({ q: $('#tourSearch').value, dept: $('#tourDept').value,
    upcoming: $('#tourUpcoming').checked ? '1' : '0' });
  const d = await api('/api/tournaments?' + p);
  if (!d.items.length) {
    $('#tourList').innerHTML = '<div class="empty">Aucun tournoi. ' +
      'Ajoutez une source de veille dans Réglages, ou collez un calendrier.</div>';
    return;
  }
  /* Neuf fiches sur dix sont du football, alors que les dates reellement
     travaillees sont du basket, de la voile, de la danse. Le filtre les
     separe avant le regroupement par distance, qui reste l'axe principal. */
  const disc = $('#tourDiscipline').value;
  if (disc) {
    const foot = t => /\bfoot|football|futsal|\bu\s?\d{1,2}\b/i
      .test([t.sport, t.name, t.categories].join(' '));
    d.items = d.items.filter(t => disc === 'foot' ? foot(t) : !foot(t));
  }

  /* Des cartes, plus un tableau : une ligne de tableau ne porte ni le message,
     ni la route, ni le nombre d'equipes de facon lisible — et c'est sur ces
     trois-la qu'on decide. Meme langage que le Reperage et le Studio. */
  const html = ['jour', 'bassin', 'ailleurs'].map(z => {
    const dedans = d.items.filter(t => t.zone === z);
    if (!dedans.length) return '';
    const [titre, aide] = ZONES_AIDE[z];
    const replie = z === 'ailleurs' ? '' : ' open';
    return `<details class="zone z-${z}"${replie}>
      <summary><b>${titre}</b><span class="n">${dedans.length}</span>
        <span class="sous">${aide}</span></summary>
      <div class="tournois">${dedans.map(carteTournoi).join('')}</div></details>`;
  }).join('');
  $('#tourList').innerHTML = html;
  $('#tourList').className = 'cartes-tournois';
  $$('#tourList button[data-match]').forEach(b => b.onclick = () => showMatches(b.dataset.match));
  $$('#tourList [data-mot]').forEach(b => b.onclick = () => ouvrirMotTournoi(b.dataset.mot, b));
};

/* Une carte de tournoi : les trois faits qui decident — la date, la route, le
   nombre d'equipes — puis le message, qu'on deplie quand on veut ecrire. */
function carteTournoi(t) {
  const reste = t.jours_avant;
  let quand = '', ton = '';
  if (t.a_soi) { quand = 'votre date'; ton = 'soi'; }
  else if (reste === null || reste === undefined) { quand = 'date inconnue'; }
  else if (reste < 0) { quand = 'passé'; ton = 'passe'; }
  else if (t.in_window) { quand = 'à décider avant le ' + frDate(t.decision_avant); ton = 'ouvert'; }
  else if (reste < 10) { quand = 'trop tard pour démarcher'; ton = 'tard'; }
  else { quand = 'trop tôt, revenez le ' + frDate(t.decision_avant); ton = 'tot'; }

  const dates = frDate(t.start_date)
    + (t.end_date && t.end_date !== t.start_date ? ' → ' + frDate(t.end_date) : '');

  const reperes = [
    `<span class="rep date${reste != null && reste >= 0 && reste <= 21 ? ' urgent' : ''}">
       ${esc(dates)}${reste != null && reste >= 0 ? '<i>J-' + reste + '</i>' : ''}</span>`,
    t.distance_km != null
      ? `<span class="rep route"><b>${t.distance_km} km</b>${
          t.trajet_min != null ? '<i>' + dureeFr(t.trajet_min) + '</i>' : ''}</span>` : '',
    t.nb_equipes
      ? `<span class="rep equipes"><b>${t.nb_equipes}</b><i>équipes</i></span>` : '',
  ].filter(Boolean).join('');

  return `<article class="carte-tournoi ${ton}" data-id="${t.id}">
    <div class="ct-nom">${t.url
      ? `<a target="_blank" rel="noopener" href="${esc(t.url)}">${esc(t.name)}</a>`
      : esc(t.name)}</div>
    <div class="ct-ou">${esc([t.sport, t.city || 'lieu à préciser'].filter(Boolean).join(' · '))}</div>
    <div class="ct-reperes">${reperes}</div>
    <div class="ct-etiqs">
      <span class="etiq ${ton}">${esc(quand)}</span>
      ${t.lieu_verifie === 0 ? '<span class="etiq doute">lieu à confirmer</span>' : ''}
      ${t.n_drafts ? `<span class="etiq on">${t.n_drafts} message(s)</span>` : ''}
    </div>
    <div class="ct-barre">
      <button class="mini" data-mot="${t.id}">Voir le message</button>
      ${t.club_id ? `<button class="mini" data-match="${t.id}">Clubs à proposer</button>` : ''}
    </div>
    <div class="ct-mot" id="ct-mot-${t.id}"></div>
  </article>`;
}

function dureeFr(min) {
  if (min == null) return '';
  if (min < 60) return min + ' min';
  const h = Math.floor(min / 60), m = min % 60;
  return h + ' h' + (m ? ' ' + String(m).padStart(2, '0') : '');
}

/* Le message est rendu a la demande : le preparer pour deux cents fiches
   remplirait la pile de relecture sans qu'aucun ne parte. */
async function ouvrirMotTournoi(tid, bouton) {
  const boite = $('#ct-mot-' + tid);
  if (!boite) return;
  if (boite.innerHTML) {
    boite.innerHTML = '';
    bouton.textContent = 'Voir le message';
    return;
  }
  bouton.textContent = 'Replier';
  boite.innerHTML = '<p class="hint">Rédaction…</p>';
  try {
    const m = await api('/api/tournaments/' + tid + '/message');
    const num = String(m.destinataire || '').replace(/[^0-9+]/g, '');
    const estTel = /^\+?\d{8,}$/.test(num);
    boite.innerHTML = `
      <textarea id="mot-t-${tid}" rows="9">${esc(m.corps || '')}</textarea>
      ${Reform.barre('mot-t-' + tid, m.objet || '')}
      <div class="ct-gestes">
        ${estTel ? `<button class="mini primary" data-wa-t="${tid}"
          data-num="${num.replace(/\D/g, '')}" data-club="${m.club_id || ''}"
          >▶ Envoyer sur WhatsApp</button>` : ''}
        <button class="mini" data-copier-t="${tid}">Copier le texte</button>
        ${(m.destinataire || '').indexOf('@') > 0
          ? `<button class="mini" data-mail-t="${tid}"
              data-dest="${esc(m.destinataire)}"
              data-objet="${esc(m.objet || '')}">Par mail</button>` : ''}
      </div>
      <p class="hint">Relisez avant d’envoyer. Rien ne part d’ici.</p>`;
    Reform.brancher(boite);
    const lire = () => ($('#mot-t-' + tid) || {}).value || '';
    const wa = $('[data-wa-t]', boite);
    if (wa) wa.onclick = async () => {
      if (!window.Envoyer) {
        ouvrirDehors('https://wa.me/' + wa.dataset.num
          + '?text=' + encodeURIComponent(lire()));
        return;
      }
      await Envoyer.direct({tel: wa.dataset.num, corps: lire(),
                            club_id: wa.dataset.club || null,
                            tournament_id: tid}, wa);
    };
    const cp = $('[data-copier-t]', boite);
    if (cp) cp.onclick = () => copy(lire());
    const ml = $('[data-mail-t]', boite);
    if (ml) ml.onclick = () => ouvrirDehors('mailto:' + ml.dataset.dest
      + '?subject=' + encodeURIComponent(ml.dataset.objet)
      + '&body=' + encodeURIComponent(lire()));
  } catch (e) {
    boite.innerHTML = `<p class="hint">Message indisponible : ${esc(e.message)}</p>`;
  }
}
['tourSearch', 'tourDept'].forEach(id =>
  $('#' + id).addEventListener('input', debounce(LOADERS.tournois)));
$('#tourUpcoming').onchange = LOADERS.tournois;
$('#tourDiscipline').onchange = LOADERS.tournois;

async function showMatches(tid) {
  const d = await api(`/api/tournaments/${tid}/matches`);
  const t = d.tournament;
  modal(`<h2>${esc(t.name)}</h2>
    <p class="hint">${frDate(t.start_date)} · ${esc(t.city || '')} —
      fenêtre de contact : <b>${esc(d.window)}</b>
      ${d.in_window ? '' : ' (hors fenêtre : la génération sera refusée)'}</p>
    <div class="list">${d.clubs.map(c => `<div class="item"><div class="body">
      <div class="t">${esc(c.name)}</div>
      <div class="s">${esc(c.city || '')} · ${esc(c.match_reason)}</div></div></div>`).join('')
      || '<div class="empty">Aucun club correspondant. Élargissez le rayon dans Réglages.</div>'}</div>
    ${d.clubs.length ? `<div class="toolbar" style="margin-top:14px">
      <button class="primary" id="genDrafts">Préparer ${d.clubs.length} message(s)</button>
      <span class="hint" style="margin:0">Ils partiront en relecture, pas en envoi.</span></div>` : ''}`);
  const btn = $('#genDrafts');
  if (btn) btn.onclick = async () => {
    try {
      const r = await api(`/api/tournaments/${tid}/drafts`, { method: 'POST', body: {} });
      closeModal();
      toast(`${r.n} message(s) préparé(s) — ils remontent dans le point du jour`);
      show('today');
    } catch (e) { toast(e.message, true); }
  };
}

$('#newTournoi').onclick = () => {
  modal(`<h2>Nouveau tournoi</h2><div class="form">
    <label>Nom <input id="tName"></label>
    <label>Début <input id="tStart" type="date"></label>
    <label>Fin <input id="tEnd" type="date"></label>
    <label>Ville <input id="tCity"></label>
    <label>Code postal <input id="tPc"></label>
    <label>Lien <input id="tUrl"></label>
    <button class="primary" id="tSave">Créer</button></div>`);
  $('#tSave').onclick = async () => {
    try {
      await api('/api/tournaments', { method: 'POST', body: {
        name: $('#tName').value, start_date: $('#tStart').value, end_date: $('#tEnd').value,
        city: $('#tCity').value, postal_code: $('#tPc').value, url: $('#tUrl').value } });
      closeModal(); toast('Ajouté'); LOADERS.tournois();
    } catch (e) { toast(e.message, true); }
  };
};

/* Les brouillons existent toujours — ils alimentent le message propose dans
   les cartes du jour. Ce qui a disparu, c'est la pile a trier : un message
   qu'on vient de preparer s'ouvre ici, seul, et se referme. */
async function montrerMessage(draftId, titre) {
  try {
    const d = await api('/api/drafts/' + draftId);
    modal(`<h2>${esc(titre || 'Message préparé')}</h2>` + draftCard(d));
    bindDraft(d);
  } catch (e) { toast(e.message, true); }
}

function draftCard(d) {
  const who = [d.first_name, d.last_name].filter(Boolean).join(' ');
  const ro = (d.status === 'sent');
  return `<div class="draft${d.status === 'approved' ? ' appr' : ''}" id="d${d.id}">
    <div class="head">
      <span class="who">${esc(d.club_name || '—')}</span>
      <span class="tag">${esc(d.channel)}</span>
      <span class="tag ${d.status === 'approved' ? 'on' : d.status === 'rejected' ? 'bad' : ''}">
        ${esc({draft:'à relire',approved:'validé',sent:'envoyé',rejected:'écarté'}[d.status] || d.status)}</span>
      ${d.created_by === 'agent' ? '<span class="tag">rédigé par l\'agent</span>' : ''}
      ${d.whatsapp ? '<span class="tag on">WhatsApp possible</span>' : ''}
      <span class="grow"></span>
      <span class="why">${esc(d.reason || '')}</span>
    </div>
    <div class="why" style="margin-bottom:8px">
      À : ${esc(who)} ${d.to_addr ? '&lt;' + esc(d.to_addr) + '&gt;' : '<b>— aucune adresse —</b>'}
      ${d.tournament_name ? ' · déclencheur : ' + esc(d.tournament_name) : ''}</div>
    ${d.channel === 'email'
      ? `<input class="subj" data-f="subject" value="${esc(d.subject || '')}" ${ro ? 'readonly' : ''}>`
      : ''}
    <textarea data-f="body" ${ro ? 'readonly' : ''}>${esc(d.body)}</textarea>
    <div class="acts">
      ${d.status === 'draft' ? `<button class="primary" data-a="approve">Valider</button>
        <button data-a="reject">Écarter</button>` : ''}
      ${d.status === 'approved' ? `${d.to_addr && d.to_addr.includes('@') ? '<button data-a="mailto">Ouvrir dans Mail</button>' : ''}
        ${d.whatsapp ? '<button data-a="wa">Ouvrir dans WhatsApp</button>' : ''}
        <button data-a="copy">Copier</button>
        <button class="primary" data-a="sent">Marqué envoyé</button>` : ''}
      ${d.status === 'rejected' ? '<button data-a="approve">Récupérer</button>' : ''}
      ${!ro ? '<button data-a="save">Enregistrer les modifs</button>' : ''}
      <span class="grow"></span>
      <button class="mini" data-a="club">Fiche club</button>
      ${!ro ? '<button class="mini danger" data-a="del">Supprimer</button>' : ''}
    </div></div>`;
}

function bindDraft(d) {
  const root = $('#d' + d.id);
  const val = f => { const e = $(`[data-f="${f}"]`, root); return e ? e.value : null; };
  const save = () => api('/api/drafts/' + d.id, { method: 'PATCH',
    body: { subject: val('subject'), body: val('body') } });
  $$('button[data-a]', root).forEach(b => b.onclick = async () => {
    const a = b.dataset.a;
    try {
      if (a === 'club') return openClub(d.club_id);
      if (a === 'copy') return copy((val('subject') ? val('subject') + '\n\n' : '') + val('body'));
      if (a === 'save') { await save(); return toast('Enregistré'); }
      if (a === 'wa') {
        await save();
        const fresh = await api('/api/drafts/' + d.id);
        if (!fresh.whatsapp) return toast('Pas de mobile sur ce contact', true);
        window.open(fresh.whatsapp, '_blank');
        return toast('WhatsApp ouvert — relisez puis envoyez vous-même');
      }
      if (a === 'mailto') {
        await save();
        const fresh = await api('/api/drafts/' + d.id);
        if (!fresh.mailto) return toast('Pas d\'adresse email sur ce contact', true);
        location.href = fresh.mailto;
        return;
      }
      if (a === 'approve') { await save(); await api(`/api/drafts/${d.id}/approve`,
        { method: 'POST', body: {} }); toast('Validé — prêt à envoyer'); }
      if (a === 'reject') { const note = prompt('Pourquoi ? (facultatif)');
        if (note === null) return;
        await api(`/api/drafts/${d.id}/reject`, { method: 'POST', body: { note } });
        toast('Écarté'); }
      if (a === 'sent') {
        if (!confirm('Confirmer que vous avez bien envoyé ce message ?\n' +
          'Les relances seront programmées automatiquement.')) return;
        await api(`/api/drafts/${d.id}/sent`, { method: 'POST',
          body: { schedule_followups: true } });
        toast('Tracé, relances programmées');
      }
      if (a === 'del') { if (!confirm('Supprimer ce brouillon ?')) return;
        await api('/api/drafts/' + d.id, { method: 'DELETE' }); toast('Supprimé'); }
      closeModal();
      chargerJournee();
    } catch (e) { toast(e.message, true); }
  });
}

/* ================= IMPORT ================= */
let importItems = [];
let uploaded = null;   // { headers, rows, mapping, fields, sheets, filename }

/* --- dépôt de fichier --- */
const dz = $('#dropzone');
$('#pickFile').onclick = () => $('#fileInput').click();
dz.onclick = e => { if (e.target === dz || e.target.tagName === 'B') $('#fileInput').click(); };
['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); dz.classList.add('hot');
}));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => {
  e.preventDefault(); if (ev === 'dragleave' && dz.contains(e.relatedTarget)) return;
  dz.classList.remove('hot');
}));
dz.addEventListener('drop', e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) upload(f);
});
$('#fileInput').onchange = e => { if (e.target.files[0]) upload(e.target.files[0]); };

async function upload(file, sheet) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  if (sheet) fd.append('sheet', sheet);
  $('#fileInfo').hidden = false;
  $('#fileInfo').innerHTML = `<div class="banner">Lecture de ${esc(file.name)}…</div>`;
  try {
    const r = await fetch('/api/import/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    uploaded = Object.assign({}, d, { file });
    renderMapping();
    await previewFromMapping();
  } catch (e) {
    $('#fileInfo').innerHTML = `<div class="banner">${esc(e.message)}</div>`;
    toast(e.message, true);
  }
}

function renderMapping() {
  const d = uploaded;
  const opts = f => ['<option value="">— ignorer —</option>'].concat(
    d.fields.map(x => `<option value="${x.key}"${f === x.key ? ' selected' : ''}>${esc(x.label)}</option>`)
  ).join('');
  const sheetSel = (d.sheets && d.sheets.length > 1)
    ? `<label class="chk">Feuille&nbsp;<select id="sheetSel">${d.sheets.map(n =>
        `<option${n === d.sheet ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></label>` : '';
  const mapped = Object.values(d.mapping).filter(Boolean).length;
  $('#fileInfo').hidden = false;
  $('#fileInfo').innerHTML = `
    <div class="toolbar">
      <b>${esc(d.filename)}</b>
      <span class="tag">${d.n_rows} ligne(s)</span>
      <span class="tag">${d.headers.length} colonne(s)</span>
      <span class="tag ${mapped ? 'on' : 'warn'}">${mapped} colonne(s) reconnue(s)</span>
      ${d.truncated ? '<span class="tag warn">tronqué à 5000 lignes</span>' : ''}
      ${sheetSel}
      <span class="grow"></span>
      <button class="mini" id="dropAgain">Autre fichier</button>
    </div>
    <p class="hint">Vérifiez à quoi correspond chaque colonne. Ce qui est sur « ignorer » ne sera pas importé.</p>
    <div class="mapgrid">${d.headers.map((h, i) => {
      const f = d.mapping[String(i)] || '';
      const ex = (d.sample.find(r => r[i] != null && String(r[i]).trim()) || [])[i] || '';
      return `<div class="mapcol${f ? '' : ' off'}">
        <div class="h" title="${esc(h)}">${esc(h || 'colonne ' + (i + 1))}</div>
        <select data-col="${i}">${opts(f)}</select>
        <div class="ex" title="${esc(ex)}">${ex ? 'ex : ' + esc(ex) : ''}</div></div>`;
    }).join('')}</div>`;

  $$('#fileInfo select[data-col]').forEach(sel => sel.onchange = async () => {
    uploaded.mapping[sel.dataset.col] = sel.value;
    sel.closest('.mapcol').classList.toggle('off', !sel.value);
    await previewFromMapping();
  });
  const ss = $('#sheetSel');
  if (ss) ss.onchange = () => upload(uploaded.file, ss.value);
  $('#dropAgain').onclick = () => {
    uploaded = null; importItems = [];
    $('#fileInfo').hidden = true; $('#importResult').innerHTML = '';
    $('#importCommit').hidden = true; $('#fileInput').value = '';
  };
}

async function previewFromMapping() {
  if (!uploaded) return;
  try {
    const d = await api('/api/import/preview', { method: 'POST', body: {
      headers: uploaded.headers, rows: uploaded.rows, mapping: uploaded.mapping,
      source: $('#importSource').value || uploaded.filename } });
    importItems = d.items;
    renderImport();
  } catch (e) { toast(e.message, true); }
}

$('#importPreview').onclick = async () => {
  const text = $('#importText').value.trim();
  if (!text) return toast('Collez quelque chose d\'abord', true);
  try {
    const d = await api('/api/import/preview', { method: 'POST', body: {
      text, source: $('#importSource').value, source_url: $('#importUrl').value,
      mode: $('#importMode').value } });
    importItems = d.items;
    renderImport();
  } catch (e) { toast(e.message, true); }
};

function renderImport() {
  const box = $('#importResult');
  $('#importCommit').hidden = !importItems.length;
  if (!importItems.length) {
    box.innerHTML = '<div class="empty">Rien de repérable. Essayez le mode « Texte libre ».</div>';
    return;
  }
  box.innerHTML = `<p class="hint">${importItems.length} fiche(s) repérée(s).
    Décochez ce que vous ne voulez pas, corrigez au besoin.</p>
    <div class="table"><table><thead><tr><th></th><th>Club</th><th>Contact</th>
    <th>Email</th><th>Téléphone</th><th>Lieu</th><th>Déjà en base ?</th></tr></thead><tbody>` +
    importItems.map((it, i) => `<tr>
      <td><input type="checkbox" class="imp" data-i="${i}" checked></td>
      <td><input data-i="${i}" data-p="club" data-k="name" value="${esc(it.club.name || '')}" size="22"></td>
      <td><input data-i="${i}" data-p="contact" data-k="first_name" value="${esc(it.contact.first_name || '')}" size="9">
          <input data-i="${i}" data-p="contact" data-k="last_name" value="${esc(it.contact.last_name || '')}" size="10">
          ${it.contact.role ? '<div class="tag">' + esc(it.contact.role) + '</div>' : ''}</td>
      <td><input data-i="${i}" data-p="contact" data-k="email" value="${esc(it.contact.email || '')}" size="20"></td>
      <td><input data-i="${i}" data-p="contact" data-k="phone" value="${esc(it.contact.phone || '')}" size="13">
          ${it.contact.phone_fmt ? '<div class="tag">' + esc(it.contact.phone_fmt) + '</div>' : ''}</td>
      <td><input data-i="${i}" data-p="club" data-k="city" value="${esc(it.club.city || '')}" size="10">
          <input data-i="${i}" data-p="club" data-k="postal_code" value="${esc(it.club.postal_code || '')}" size="5"></td>
      <td>${it.existing_club ? '<span class="tag warn">club connu</span>' : ''}
          ${it.existing_contact ? '<span class="tag warn">contact connu</span>' : ''}
          ${!it.existing_club && !it.existing_contact ? '<span class="tag on">nouveau</span>' : ''}</td>
    </tr>`).join('') + '</tbody></table></div>';

  $$('#importResult input[data-k]').forEach(inp => inp.oninput = () => {
    importItems[+inp.dataset.i][inp.dataset.p][inp.dataset.k] = inp.value;
  });
}

$('#importCommit').onclick = async () => {
  const keep = $$('#importResult input.imp').filter(c => c.checked).map(c => importItems[+c.dataset.i]);
  if (!keep.length) return toast('Rien de coché', true);
  try {
    const r = await api('/api/import/commit', { method: 'POST', body: {
      items: keep, schedule_sequence: $('#importSeq').checked } });
    const bits = [`${r.clubs_created} club(s) et ${r.contacts_created} contact(s) créés`];
    if (r.clubs_updated + r.contacts_updated) bits.push(`${r.clubs_updated + r.contacts_updated} complété(s)`);
    if (r.already_known) bits.push(`${r.already_known} déjà en base`);
    toast(bits.join(', '));
    importItems = []; uploaded = null;
    $('#importResult').innerHTML = ''; $('#importText').value = '';
    $('#importCommit').hidden = true; $('#fileInfo').hidden = true; $('#fileInput').value = '';
  } catch (e) { toast(e.message, true); }
};

/* ================= REGLAGES ================= */
LOADERS.reglages = async function () {
  const s = await api('/api/settings');
  $$('#settingsForm [name]').forEach(el => {
    const v = s[el.name];
    if (el.type === 'checkbox') el.checked = (v === '1');
    else if (v != null) el.value = v;
  });
  $('#feeds').innerHTML = [
    ['Tout (relances + tournois)', s.feeds.all],
    ['Relances seules', s.feeds.relances],
    ['Tournois seuls', s.feeds.tournois],
  ].map(([l, u]) => `<div class="feed"><b>${esc(l)}</b><code>${esc(u)}</code>
    <button class="mini" data-copy="${esc(u)}">Copier</button></div>`).join('');
  $$('#feeds button[data-copy]').forEach(b => b.onclick = () => copy(b.dataset.copy));

  const src = await api('/api/sources');
  $('#sourceList').innerHTML = src.items.map(s2 => `<div class="item">
    <div class="body"><div class="t">${esc(s2.name)}
      <span class="tag">${esc(s2.kind)}</span>
      ${s2.enabled ? '' : '<span class="tag bad">désactivée</span>'}</div>
      <div class="s mono">${esc((s2.url || '').slice(0, 64))}</div>
      ${s2.last_status ? '<div class="s">' + frDate(s2.last_run_at) + ' — ' +
        esc(s2.last_status) + '</div>' : ''}</div>
    <div class="acts"><button class="mini" data-run="${s2.id}">Tester</button>
      <button class="mini danger" data-del="${s2.id}">×</button></div></div>`).join('')
    || '<div class="empty">Aucune source. La veille ne trouvera rien.</div>';
  $$('#sourceList button[data-run]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    try { const r = await api(`/api/sources/${b.dataset.run}/run`, { method: 'POST', body: {} });
      toast(r.ok ? r.status : r.error, !r.ok); LOADERS.reglages();
    } catch (e) { toast(e.message, true); b.disabled = false; }
  });
  $$('#sourceList button[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('Supprimer cette source ?')) return;
    await api('/api/sources/' + b.dataset.del, { method: 'DELETE' }); LOADERS.reglages();
  });

  const tpl = await api('/api/templates');
  $('#templateList').innerHTML = tpl.items.map(t => `<div class="item"><div class="body">
    <div class="t">${esc(t.label)} <span class="tag">${esc(t.channel)}</span></div>
    <div class="s mono">${esc(t.key)}</div></div>
    <div class="acts"><button class="mini" data-tpl="${esc(t.key)}">Modifier</button></div></div>`).join('');
  $$('#templateList button[data-tpl]').forEach(b =>
    b.onclick = () => editTemplate(tpl.items.find(t => t.key === b.dataset.tpl), tpl.placeholders));
};

$('#saveSettings').onclick = async () => {
  const body = {};
  $$('#settingsForm [name]').forEach(el => {
    body[el.name] = el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value;
  });
  try { await api('/api/settings', { method: 'PUT', body }); toast('Réglages enregistrés'); }
  catch (e) { toast(e.message, true); }
};

function editTemplate(t, placeholders) {
  modal(`<h2>${esc(t.label)}</h2>
    <div class="form">
      <label>Objet <input id="tplSubj" value="${esc(t.subject || '')}"></label>
      <label>Corps <textarea id="tplBody" rows="13">${esc(t.body)}</textarea></label>
      <label>Note interne <input id="tplNote" value="${esc(t.notes || '')}"></label>
    </div>
    <p class="hint" style="margin-top:10px">Champs disponibles :
      ${placeholders.map(([k, d]) => `<code class="tag" title="${esc(d)}">{${esc(k)}}</code>`).join(' ')}</p>
    <div class="toolbar"><button class="primary" id="tplSave">Enregistrer</button>
      <button id="tplPrev">Aperçu réel</button></div>
    <div id="tplPreview"></div>`);
  $('#tplSave').onclick = async () => {
    await api('/api/templates/' + t.key, { method: 'PUT', body: {
      label: t.label, channel: t.channel, subject: $('#tplSubj').value,
      body: $('#tplBody').value, notes: $('#tplNote').value } });
    toast('Modèle enregistré'); closeModal(); LOADERS.reglages();
  };
  $('#tplPrev').onclick = async () => {
    const r = await api(`/api/templates/${t.key}/preview`, { method: 'POST',
      body: { subject: $('#tplSubj').value, body: $('#tplBody').value } });
    $('#tplPreview').innerHTML = `<h3>Aperçu</h3>
      <div class="raw" style="max-height:none">${esc(r.subject || '')}\n\n${esc(r.body || '')}</div>`;
  };
}

$('#newSource').onclick = () => {
  modal(`<h2>Nouvelle source de veille</h2><div class="form">
    <label>Nom <input id="sName" placeholder="Calendrier ligue AURA"></label>
    <label>Type <select id="sKind">
      <option value="ics">Calendrier iCal (.ics)</option>
      <option value="rss">Flux RSS / Atom</option>
      <option value="json">API JSON</option>
      <option value="csv">CSV en ligne</option>
      <option value="html">Page HTML (motif regex)</option></select></label>
    <label>URL <input id="sUrl" placeholder="https://…"></label>
    <label>Discipline (facultatif) <input id="sSport"></label>
    <label>Config JSON (JSON/HTML seulement)
      <textarea id="sConf" rows="4" placeholder='{"root":"data.items"}'></textarea></label>
    <button class="primary" id="sSave">Ajouter</button></div>
    <p class="hint" style="margin-top:10px">Astuce : commencez par un .ics de fédération ou de
    ligue, c'est le format le plus fiable. Pour une page HTML, la config attend un motif regex
    à groupes nommés, ex&nbsp;: <code class="tag">{"pattern":"&lt;h3&gt;(?P&lt;name&gt;[^&lt;]+)"}</code></p>`);
  $('#sSave').onclick = async () => {
    let config = $('#sConf').value.trim();
    if (config) { try { config = JSON.parse(config); }
      catch (e) { return toast('Config JSON invalide', true); } }
    try {
      await api('/api/sources', { method: 'POST', body: {
        name: $('#sName').value, kind: $('#sKind').value, url: $('#sUrl').value,
        sport: $('#sSport').value, config: config || null } });
      closeModal(); toast('Source ajoutée'); LOADERS.reglages();
    } catch (e) { toast(e.message, true); }
  };
};

$('#watchPasteBtn').onclick = async () => {
  const text = $('#watchPaste').value.trim();
  if (!text) return;
  try {
    const r = await api('/api/watch/import', { method: 'POST', body: { text } });
    toast(`${r.n_created} tournoi(s) ajouté(s), ${r.n_skipped} déjà connu(s)`);
    $('#watchPaste').value = '';
  } catch (e) { toast(e.message, true); }
};

/* ================= démarrage ================= */
(async function init() {
  const s = await api('/api/settings');
  s.statuses.forEach(st => { STATUS_LABEL[st.key] = st.label; });
  $('#clubStatus').innerHTML = '<option value="">Tous statuts</option>' +
    s.statuses.map(st => `<option value="${st.key}">${esc(st.label)}</option>`).join('');
  const tab = (location.hash.match(/^#\/(\w+)/) || [])[1] || 'today';
  // « #/clubs/club/30 » : la page Chercher ouvre directement une fiche.
  const fiche = (location.hash.match(/\/club\/(\d+)/) || [])[1];
  guetterRattacher();
  show(tab);
  if (fiche) openClub(fiche);
})();

/* --- import d'un calendrier personnel --- */
let calItems = [];

$('#calLoad').onclick = async (ev) => {
  const url = $('#calUrl').value.trim();
  if (!url) return toast('Collez d\'abord un lien de calendrier', true);
  ev.target.disabled = true; ev.target.textContent = 'Lecture…';
  try {
    const d = await api('/api/calendar/preview', { method: 'POST', body: {
      url, source: $('#calName').value.trim() || 'calendrier' } });
    calItems = d.items;
    renderCal(d);
  } catch (e) { toast(e.message, true); }
  ev.target.disabled = false; ev.target.textContent = 'Lire';
};

function renderCal(d) {
  const box = $('#calResult');
  if (!calItems.length) { box.innerHTML = '<div class="empty">Rien à importer.</div>'; return; }
  box.innerHTML = `<p class="hint">${d.n} événement(s) : ${d.n_leads} piste(s),
    ${d.n_events} date(s). Corrigez ce qui est mal classé — « ignorer » n'écrit rien.</p>
    <div class="table"><table><thead><tr><th>Classer</th><th>Quand</th><th>Nom</th>
    <th>Ville</th><th>Téléphone</th><th>Déjà en base ?</th></tr></thead><tbody>` +
    calItems.map((it, i) => `<tr>
      <td><select data-cal="${i}">
        <option value="lead"${it.kind === 'lead' ? ' selected' : ''}>Piste (club)</option>
        <option value="event"${it.kind === 'event' ? ' selected' : ''}>Date (agenda)</option>
        <option value="skip">Ignorer</option></select></td>
      <td>${frDate(it.when)}</td>
      <td><input data-cal-name="${i}" value="${esc(it.club.name || '')}" size="26">
        <div class="ex">${esc(it.raw.slice(0, 46))}</div></td>
      <td>${esc(it.club.city || '')} ${it.club.dept ? '<span class="tag">' + esc(it.club.dept) + '</span>' : ''}</td>
      <td>${it.contact.phone ? '<span class="tag on">' + esc(it.contact.phone_fmt || it.contact.phone) + '</span>' : ''}</td>
      <td>${it.existing_club ? '<span class="tag warn">club connu</span>' : ''}
          ${it.existing_event ? '<span class="tag warn">date connue</span>' : ''}
          ${!it.existing_club && !it.existing_event ? '<span class="tag on">nouveau</span>' : ''}</td>
    </tr>`).join('') + `</tbody></table></div>
    <div class="toolbar" style="margin-top:12px">
      <button class="primary" id="calCommit">Enregistrer</button>
      <span class="hint" style="margin:0">Les pistes gardent une trace datée dans l'historique du club.</span>
    </div>`;

  $$('#calResult select[data-cal]').forEach(sel => sel.onchange = () => {
    calItems[+sel.dataset.cal].kind = sel.value;
  });
  $$('#calResult input[data-cal-name]').forEach(inp => inp.oninput = () => {
    const it = calItems[+inp.dataset.calName];
    it.club.name = inp.value; it.event.name = inp.value;
  });
  $('#calCommit').onclick = async () => {
    const keep = calItems.filter(i => i.kind !== 'skip');
    if (!keep.length) return toast('Tout est sur « ignorer »', true);
    try {
      const r = await api('/api/calendar/commit', { method: 'POST', body: { items: keep } });
      const bits = [];
      if (r.clubs_created) bits.push(`${r.clubs_created} club(s)`);
      if (r.events_created) bits.push(`${r.events_created} date(s)`);
      if (r.contacts_created) bits.push(`${r.contacts_created} contact(s)`);
      if (r.events_known || r.already_known) bits.push(`${r.events_known + r.already_known} déjà connu(s)`);
      toast(bits.length ? bits.join(', ') + ' enregistré(s)' : 'Rien de nouveau');
      calItems = []; $('#calResult').innerHTML = '';
    } catch (e) { toast(e.message, true); }
  };
}

/* --- import d'une conversation WhatsApp déjà échangée --- */
let waParsed = null;
const waDrop = $('#waDrop');
$('#waPick').onclick = () => $('#waFile').click();
waDrop.onclick = e => { if (e.target === waDrop || e.target.tagName === 'B') $('#waFile').click(); };
['dragenter', 'dragover'].forEach(ev => waDrop.addEventListener(ev, e => {
  e.preventDefault(); waDrop.classList.add('hot');
}));
['dragleave', 'drop'].forEach(ev => waDrop.addEventListener(ev, e => {
  e.preventDefault(); waDrop.classList.remove('hot');
}));
waDrop.addEventListener('drop', e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) waUpload(f);
});
$('#waFile').onchange = e => { if (e.target.files[0]) waUpload(e.target.files[0]); };

async function waUpload(file) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  fd.append('chat_name', file.name.replace(/\.txt$/i, '').replace(/^Discussion WhatsApp avec /i, ''));
  $('#waResult').innerHTML = '<div class="banner">Lecture…</div>';
  try {
    const r = await fetch('/api/whatsapp/preview', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    waParsed = d;
    await renderWa();
  } catch (e) {
    $('#waResult').innerHTML = `<div class="banner">${esc(e.message)}</div>`;
  }
}

async function renderWa() {
  const d = waParsed;
  const clubs = (await api('/api/clubs?limit=500')).items;
  $('#waResult').innerHTML = `
    <div class="toolbar" style="margin-top:12px">
      <span class="tag on">${d.n} message(s)</span>
      <span class="tag">${d.n_out} envoyé(s)</span>
      <span class="tag">${d.n_in} reçu(s)</span>
      ${d.n_attachments ? `<span class="tag">${d.n_attachments} pièce(s) jointe(s)</span>` : ''}
      <span class="tag">${frDate(d.first)} → ${frDate(d.last)}</span>
    </div>
    <div class="toolbar">
      <label class="chk">Vous êtes&nbsp;<select id="waMe">${d.participants.map(p =>
        `<option${p.name === d.me ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
      <label class="chk">Club&nbsp;<select id="waClub"><option value="">— à choisir —</option>
        ${clubs.map(c => `<option value="${c.id}"${c.id === d.club_id ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}
      </select></label>
      ${d.match_reason ? `<span class="hint" style="margin:0">reconnu par ${esc(d.match_reason)}</span>` : ''}
    </div>
    <div class="item"><div class="body">${d.sample.map(m => `<div class="wa-msg${m.author === d.me ? ' out' : ''}">
      <span class="w">${frDate(m.at)} · ${esc(m.author.slice(0, 14))}</span>
      <span class="t">${esc((m.text || '').slice(0, 90))}</span></div>`).join('')}
      ${d.n > d.sample.length ? `<div class="s">… et ${d.n - d.sample.length} de plus</div>` : ''}
    </div></div>
    <div class="toolbar" style="margin-top:10px">
      <button class="primary" id="waCommit">Enregistrer dans l'historique</button>
      <span class="hint" style="margin:0">Aucun message n'est envoyé — on enregistre ce qui a déjà eu lieu.</span>
    </div>`;

  $('#waMe').onchange = e => { waParsed.me = e.target.value; };
  $('#waCommit').onclick = async () => {
    const clubId = $('#waClub').value;
    if (!clubId) return toast('Choisissez le club', true);
    try {
      const r = await api('/api/whatsapp/commit', { method: 'POST', body: {
        club_id: +clubId, me: $('#waMe').value, text: waParsed.text } });
      toast(r.added ? `${r.added} message(s) ajouté(s) — club « ${r.status} »`
                    : `Déjà importé (${r.skipped} message(s) connus)`);
      waParsed = null; $('#waResult').innerHTML = ''; $('#waFile').value = '';
    } catch (e) { toast(e.message, true); }
  };
}

/* --- marquage groupé des clubs déjà démarchés --- */
function refreshBulk() {
  $('#bulkBar').hidden = picked.size === 0;
  $('#bulkCount').textContent = picked.size;
}
$('#bulkClear').onclick = () => {
  picked.clear(); $$('#clubList input.pick').forEach(c => { c.checked = false; }); refreshBulk();
};
$('#bulkMark').onclick = async () => {
  if (!picked.size) return;
  try {
    const r = await api('/api/clubs/mark-contacted', { method: 'POST', body: {
      club_ids: [...picked], when: $('#bulkWhen').value || undefined,
      channel: $('#bulkChannel').value } });
    toast(`${r.n} club(s) marqué(s) comme contactés`);
    picked.clear(); refreshBulk(); LOADERS.clubs();
  } catch (e) { toast(e.message, true); }
};

/* --- thème clair / sombre / système --- */
/* Le libelle du theme prenait soixante pixels de barre pour une chose qu'on
   regle une fois. Une pastille ronde suffit : le mot reste dans l'infobulle. */
const THEMES = [
  ['system', 'Système', '◐'],
  ['light', 'Clair', '☀'],
  ['dark', 'Sombre', '☾'],
];
function currentTheme() {
  try { return localStorage.getItem('theme') || 'system'; } catch (e) { return 'system'; }
}
function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === 'system') delete root.dataset.theme;
  else root.dataset.theme = mode;
  try {
    if (mode === 'system') localStorage.removeItem('theme');
    else localStorage.setItem('theme', mode);
  } catch (e) { /* navigation privée : le thème vaut pour cette session */ }
  const choix = THEMES.find(t => t[0] === mode) || THEMES[0];
  const btn = $('#themeBtn');
  btn.textContent = choix[2];
  btn.title = 'Thème : ' + choix[1] + ' — cliquez pour changer';
}
$('#themeBtn').onclick = () => {
  const i = THEMES.findIndex(t => t[0] === currentTheme());
  applyTheme(THEMES[(i + 1) % THEMES.length][0]);
};
applyTheme(currentTheme());

/* --- croisement avec l'index des conversations WhatsApp --- */
let waIdx = null;
const waIdxDrop = $('#waIdxDrop');
$('#waIdxPick').onclick = () => $('#waIdxFile').click();
waIdxDrop.onclick = e => {
  if (e.target === waIdxDrop || e.target.tagName === 'B') $('#waIdxFile').click();
};
['dragenter', 'dragover'].forEach(ev => waIdxDrop.addEventListener(ev, e => {
  e.preventDefault(); waIdxDrop.classList.add('hot');
}));
['dragleave', 'drop'].forEach(ev => waIdxDrop.addEventListener(ev, e => {
  e.preventDefault(); waIdxDrop.classList.remove('hot');
}));
waIdxDrop.addEventListener('drop', e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) waIdxUpload(f);
});
$('#waIdxFile').onchange = e => { if (e.target.files[0]) waIdxUpload(e.target.files[0]); };

async function waIdxUpload(file) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  $('#waIdxResult').innerHTML = '<div class="banner">Croisement…</div>';
  try {
    const r = await fetch('/api/whatsapp/index', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    waIdx = d;
    renderWaIdx();
  } catch (e) {
    $('#waIdxResult').innerHTML = `<div class="banner">${esc(e.message)}</div>`;
  }
}

function renderWaIdx() {
  const d = waIdx;
  if (!d.n) {
    $('#waIdxResult').innerHTML = `<div class="empty">Aucun numéro du fichier ne correspond
      à un contact du CRM (${d.n_lignes} ligne(s) lues).</div>`;
    return;
  }
  $('#waIdxResult').innerHTML = `
    <div class="toolbar" style="margin-top:12px">
      <span class="tag on">${d.n} club(s) reconnu(s)</span>
      <span class="tag">${d.n_avec_envoi} où vous avez écrit</span>
      <span class="tag">${d.n_avec_reponse} qui ont répondu</span>
      ${d.n_inconnus ? `<span class="tag">${d.n_inconnus} numéro(s) hors CRM</span>` : ''}
    </div>
    <div class="table"><table><thead><tr><th></th><th>Club</th><th>Numéro</th>
      <th>Dernier envoi</th><th>Réponse</th><th>Deviendra</th></tr></thead><tbody>` +
    d.items.map((it, i) => `<tr>
      <td class="pick"><input type="checkbox" class="waidx" data-i="${i}" checked></td>
      <td>${esc(it.club_name)}</td>
      <td>${esc(it.phone_fmt)}</td>
      <td>${frDate(it.dernier_envoi)}</td>
      <td>${it.derniere_reception ? frDate(it.derniere_reception) : '—'}</td>
      <td><span class="tag ${it.derniere_reception ? 'on' : ''}">${
        it.statut_actuel === 'prospect'
          ? (it.derniere_reception ? 'a répondu' : 'contacté')
          : esc(it.statut_actuel)}</span></td>
    </tr>`).join('') + `</tbody></table></div>
    <div class="toolbar" style="margin-top:12px">
      <button class="primary" id="waIdxCommit">Mettre à jour ces clubs</button>
      <span class="hint" style="margin:0">Seules les dates et les statuts changent.</span>
    </div>`;

  $('#waIdxCommit').onclick = async () => {
    const keep = $$('#waIdxResult input.waidx').filter(c => c.checked)
      .map(c => waIdx.items[+c.dataset.i]);
    if (!keep.length) return toast('Rien de coché', true);
    try {
      const r = await api('/api/whatsapp/index/commit', { method: 'POST', body: { items: keep } });
      toast(`${r.n} club(s) mis à jour`);
      waIdx = null; $('#waIdxResult').innerHTML = ''; $('#waIdxFile').value = '';
    } catch (e) { toast(e.message, true); }
  };
}

/* ================= OU J'EN SUIS =================
   Un annuaire de conversations ne dit pas quoi faire. Ici chaque discussion
   est un dossier : le club, la personne, l'evenement dont on parle, les deux
   derniers messages, et le geste suivant. Range par etape, parce que ce qui
   compte c'est de voir d'un coup ce qui attend une reponse. */

/* Trois tons seulement : ce qui attend une reponse, ce qui est gagne, ce qui
   pourrit. Le reste reste neutre pour ne pas crier partout. */
const ETAPE_TON = {a_traiter: 'chaud', a_caler: 'appel', a_appeler: 'appel',
                   conclu: 'ok', a_relancer: 'tarde', en_attente: '',
                   a_contacter: '', sans_suite: 'froid'};

let suiviEtape = 'a_traiter';
let suiviCache = null;

LOADERS.suivi = async function () {
  const d = suiviCache = await api('/api/suivi');
  $('#nSuivi').textContent = d.total || '';

  const chauds = (d.colonnes.find(c => c.cle === 'a_traiter') || {}).n || 0;
  const badge = $('#suiviBadge');
  if (badge) {
    badge.hidden = !chauds;
    badge.textContent = chauds;
  }

  /* Une etape a la fois. Six colonnes qui descendent a l'infini, on ne voit
     jamais le bas de la premiere ; ici on choisit ce qu'on traite, et les
     dossiers tiennent dans une grille qu'on embrasse d'un coup d'oeil. */
  if (!d.colonnes.some(c => c.cle === suiviEtape && c.n)) {
    const plein = d.colonnes.find(c => c.n);
    suiviEtape = plein ? plein.cle : 'a_traiter';
  }

  $('#suiviEtapes').innerHTML = d.colonnes.map(c =>
    `<button class="wa-etat${c.cle === suiviEtape ? ' on' : ''}${
      ETAPE_TON[c.cle] ? ' t-' + ETAPE_TON[c.cle] : ''}"
      data-etape="${c.cle}"${c.n ? '' : ' disabled'}>
      <b>${c.n}</b><span>${esc(c.nom)}</span></button>`).join('');
  $$('#suiviEtapes .wa-etat').forEach(b => b.onclick = () => {
    suiviEtape = b.dataset.etape;
    dessinerSuivi();
  });

  dessinerSuivi();

  const frais = d.colonnes.reduce((m, c) => c.cartes.reduce(
    (n, x) => (x.dernier_le && x.dernier_le > n ? x.dernier_le : n), m), '');
  $('#suiviMaj').textContent = frais ? 'dernier échange le ' + frDate(frais) : '';

  $('#suiviRelire').onclick = async function () {
    this.disabled = true;
    const avant = this.textContent;
    this.textContent = 'Lecture…';
    try {
      const r = await api('/api/whatsapp/relire', {method: 'POST', body: {}});
      toast(r.vus + ' conversations relues');
      await LOADERS.suivi();
    } catch (e) { toast(e.message, true); }
    this.disabled = false;
    this.textContent = avant;
  };
};

function dessinerSuivi() {
  const d = suiviCache;
  if (!d) return;
  $$('#suiviEtapes .wa-etat').forEach(b =>
    b.classList.toggle('on', b.dataset.etape === suiviEtape));
  const col = d.colonnes.find(c => c.cle === suiviEtape) || {cartes: [], aide: ''};
  $('#suiviAide').textContent = col.aide || '';
  $('#suiviCartes').innerHTML = col.cartes.length
    ? col.cartes.map(carteSuivi).join('')
    : '<div class="empty">Rien dans cette étape.</div>';
  brancherSuivi(d.etapes);
}

function carteSuivi(c) {
  const ev = c.evenement;
  const quand = c.jours == null ? '' : (c.jours === 0 ? "aujourd'hui"
    : c.jours === 1 ? 'hier' : 'il y a ' + c.jours + ' j');

  let bandeau = '';
  if (ev) {
    const j = ev.start_date ? Math.round(
      (new Date(ev.start_date + 'T12:00:00') - new Date()) / 86400000) : null;
    bandeau = `<div class="evt${j != null && j <= 21 ? ' urgent' : ''}">
      ${esc(ev.name || 'Événement')}
      <span>${ev.start_date ? frDate(ev.start_date) : ''}${
        j != null ? ' · J-' + j : ''}${
        ev.nb_equipes ? ' · ' + ev.nb_equipes + ' équipes' : ''}${
        ev.distance_km == null ? ''
          : (ev.distance_km === 0 ? ' · sur place' : ' · ' + ev.distance_km + ' km')}</span>
    </div>`;
  }

  /* Un seul message a l'ouverture : le dernier. L'echange complet se deplie,
     sinon chaque carte fait trois cents pixels de haut. */
  const msgs = c.derniers || [];
  const der = msgs[msgs.length - 1];
  const fil = der
    ? `<p class="msg ${der.de_moi ? 'moi' : 'lui'}" data-quand="${esc(der.quand || '')}">
         <b>${der.de_moi ? 'Vous' : 'Lui'}</b> ${esc((der.texte || '').slice(0, 150))}${
           (der.texte || '').length > 150 ? '…' : ''}</p>`
    : '<p class="msg vide">Aucun message écrit — seulement des pièces jointes.</p>';
  const avant = msgs.length > 1
    ? `<details class="avant"><summary>Voir l'échange</summary>
        ${msgs.slice(0, -1).map(m => `<p class="msg ${m.de_moi ? 'moi' : 'lui'}" data-quand="${esc(m.quand || '')}">
          <b>${m.de_moi ? 'Vous' : 'Lui'}</b> ${esc(m.texte || '')}</p>`).join('')}
       </details>` : '';

  /* Un club qui n'a pas repondu par ecrit ne repondra pas au prochain ecrit.
     La carte le dit franchement et met le numero sous le pouce. */
  let appel = '';
  if (c.etape === 'a_appeler' && c.phone) {
    const pourquoi = c.jours_avant != null && c.jours_avant <= 21
      ? 'l\'événement est dans ' + c.jours_avant + ' jours'
      : 'sans réponse depuis ' + c.jours + ' jours';
    appel = `<a class="appel" href="tel:${c.phone}">
      <b>Passez à l'appel maintenant</b>
      <span>${esc(pourquoi)} · ${esc(c.phone)}</span></a>`;
  }

  /* Une date prise au telephone n'est pas encore une date : elle doit partir
     au calendrier et se confirmer par ecrit. La carte porte les deux gestes. */
  let suite = '';
  if (c.etape === 'conclu' || c.etape === 'a_caler') {
    const rappel = c.calage_le
      ? 'À rappeler le ' + frDate(c.calage_le)
        + (c.jours_avant != null ? ' · J-7' : '')
      : '';
    suite = `<div class="suite${c.etape === 'a_caler' ? ' urgent' : ''}">
      <b>${c.etape === 'a_caler'
          ? 'Calez le concret : heure, accès, nombre d\'équipes'
          : 'Accord pris — reste à sécuriser'}</b>
      ${rappel ? '<span>' + esc(rappel) + '</span>' : ''}</div>`;
  }

  const note = c.note
    ? `<div class="note" data-note="${esc(c.note)}">
         <b>Note${c.note_le ? ' · ' + frDate(c.note_le.slice(0, 10)) : ''}</b>
         ${esc(c.note)}</div>`
    : '<div class="note" data-note="" hidden></div>';

  const wa = c.phone ? 'https://wa.me/' + c.phone.replace(/\D/g, '') : '';
  return `<article class="dossier" data-id="${c.id}" data-club="${c.club_id}">
    <div class="qui">
      <b>${esc(c.club || '')}</b>
      ${c.posee_main ? '<span class="tag">classé main</span>' : ''}
    </div>
    <div class="sous">${[c.personne, c.ville, quand].filter(Boolean).map(esc).join(' · ')}</div>
    ${bandeau}
    ${appel}
    ${suite}
    ${note}
    <div class="fil">${fil}${avant}</div>
    <div class="gestes">
      ${wa ? `<a class="mini primary" href="${wa}" target="_blank" rel="noopener">WhatsApp</a>` : ''}
      ${c.phone ? `<a class="mini" href="tel:${c.phone}">Appeler</a>` : ''}
      <button class="mini" data-a="noter">${c.note ? 'Modifier la note' : 'Note d\'appel'}</button>
      ${c.mot ? `<button class="mini" data-a="copier"
        data-texte="${esc(c.mot)}">${esc(c.mot_nom)}</button>` : ''}
      ${c.mot && c.evenement ? `<a class="mini" href="/api/suivi/${c.id}/calendrier.ics"
        download>Au calendrier</a>` : ''}
      ${!c.evenement ? '<button class="mini" data-a="evenement">Créer la date</button>' : ''}
      ${!c.mot && c.brouillon ? `<button class="mini" data-a="copier"
        data-texte="${esc(c.brouillon.body || '')}">Copier</button>` : ''}
      <button class="mini" data-a="deplacer">Classer…</button>
    </div>
  </article>`;
}

/* La dictee : Chrome sait transcrire le francais sans rien installer. C'est
   la seule facon realiste de saisir un compte rendu en sortant d'un appel. */
function dictaphone(zone, bouton) {
  const Moteur = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Moteur) { bouton.disabled = true; bouton.title = 'Dictée indisponible ici'; return; }
  let moteur = null, acquis = '';
  bouton.onclick = () => {
    if (moteur) { moteur.stop(); return; }
    moteur = new Moteur();
    moteur.lang = 'fr-FR';
    moteur.continuous = true;
    moteur.interimResults = true;
    acquis = zone.value ? zone.value.replace(/\s*$/, '') + ' ' : '';
    moteur.onresult = ev => {
      let encours = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) acquis += t + ' '; else encours += t;
      }
      zone.value = (acquis + encours).replace(/\s+/g, ' ').trimStart();
    };
    moteur.onerror = ev => {
      toast(ev.error === 'not-allowed'
        ? 'Micro refusé : autorisez-le dans Chrome' : 'Dictée : ' + ev.error, true);
    };
    moteur.onend = () => {
      moteur = null;
      bouton.classList.remove('ecoute');
      bouton.textContent = '🎤 Dicter';
    };
    moteur.start();
    bouton.classList.add('ecoute');
    bouton.textContent = '⏹ Arrêter';
    zone.focus();
  };
}

function brancherSuivi(etapes) {
  $$('#suiviCartes .dossier').forEach(carte => {
    const id = carte.dataset.id;

    const cop = carte.querySelector('[data-a="copier"]');
    if (cop) cop.onclick = async () => {
      try {
        await navigator.clipboard.writeText(cop.dataset.texte || '');
        toast('Message copié — collez-le dans WhatsApp');
      } catch (e) { toast('Copie impossible : ' + e.message, true); }
    };

    carte.querySelector('[data-a="noter"]').onclick = () => {
      const actuelle = (carte.querySelector('.note') || {}).dataset;
      modal(`<h2>Note d'appel</h2>
        <p class="hint">Ce qui s'est dit au téléphone. Personne d'autre ne le verra.</p>
        <textarea id="noteTexte" rows="7" style="width:100%"
          placeholder="Rappelé le président, il veut un devis pour 40 photos…"></textarea>
        <div class="toolbar" style="margin-top:12px">
          <button id="noteDicter" class="mini">🎤 Dicter</button>
          <button class="primary" id="noteOk">Enregistrer</button>
          <button id="noteVider">Effacer la note</button>
        </div>`);
      const zone = $('#noteTexte');
      zone.value = (actuelle && actuelle.note) || '';
      zone.focus();
      dictaphone(zone, $('#noteDicter'));
      const envoyer = async texte => {
        try {
          await api(`/api/suivi/${id}/note`, {method: 'POST', body: {note: texte}});
          closeModal();
          toast(texte ? 'Note enregistrée' : 'Note effacée');
          await LOADERS.suivi();
        } catch (e) { toast(e.message, true); }
      };
      $('#noteOk').onclick = () => envoyer(zone.value);
      $('#noteVider').onclick = () => envoyer('');
    };

    const evb = carte.querySelector('[data-a="evenement"]');
    if (evb) evb.onclick = () => {
      const club = (carte.querySelector('.qui b') || {}).textContent || '';
      modal(`<h2>Créer la date</h2>
        <p class="hint">L'accord pris au téléphone devient un événement :
          rappel à J-7, fichier calendrier, distance calculée.</p>
        <label class="champ">Ce que c'est
          <input id="evNom" placeholder="Tournoi U11, Koh-Lanta, gala…"></label>
        <div class="deux">
          <label class="champ">Date <input id="evDate" type="date"></label>
          <label class="champ">Fin (si plusieurs jours)
            <input id="evFin" type="date"></label>
        </div>
        <div class="deux">
          <label class="champ">Commune <input id="evVille"></label>
          <label class="champ">Équipes <input id="evEq" type="number" min="0"></label>
        </div>
        <div class="toolbar" style="margin-top:14px">
          <button class="primary" id="evOk">Créer</button>
        </div>`);
      $('#evNom').value = 'Shoot — ' + club;
      $('#evDate').focus();
      $('#evOk').onclick = async () => {
        if (!$('#evDate').value) return toast('Il faut une date', true);
        try {
          const r = await api(`/api/suivi/${id}/evenement`, {method: 'POST', body: {
            nom: $('#evNom').value, date: $('#evDate').value, fin: $('#evFin').value,
            ville: $('#evVille').value, equipes: $('#evEq').value}});
          closeModal();
          toast('Date créée' + (r.distance_km != null ? ' · ' + r.distance_km + ' km' : ''));
          await LOADERS.suivi();
        } catch (e) { toast(e.message, true); }
      };
    };

    carte.querySelector('[data-a="deplacer"]').onclick = () => {
      modal(`<h2>Classer ce dossier</h2>
        <div class="list">${etapes.map(e =>
          `<div class="item"><div class="body"><div class="t">${esc(e.nom)}</div>
            <div class="s">${esc(e.aide)}</div></div>
            <div class="actions"><button class="mini" data-e="${e.cle}">Ici</button></div>
          </div>`).join('')}
          <div class="item"><div class="body"><div class="t">Laisser l'application décider</div>
            <div class="s">L'étape se recalcule au fil des messages.</div></div>
            <div class="actions"><button class="mini" data-e="">Auto</button></div></div>
        </div>`);
      $$('#modal button[data-e]').forEach(x => x.onclick = async () => {
        try {
          await api(`/api/suivi/${id}/etape`, {method: 'POST', body: {etape: x.dataset.e}});
          closeModal();
          await LOADERS.suivi();
        } catch (e) { toast(e.message, true); }
      });
    };
  });
}

/* ================= WHATSAPP =================
   Le carnet d'adresses reel de Julien vit dans WhatsApp Business, pas ici.
   Cet ecran dit ou en est chaque conversation et permet de la raccrocher au
   club correspondant : c'est ce qui manquait pour que le CRM sache quelque
   chose de vrai sur les clubs deja contactes. */

const WA_ORDRE = ['a_lire', 'a_repondu', 'a_relancer', 'en_attente', 'inconnu'];
const WA_CLASSE = {a_lire: 'warn', a_repondu: 'on', a_relancer: 'warn',
                   en_attente: '', inconnu: ''};
/* Par defaut on ne montre que les conversations qui touchent un club : la
   lecture locale ramene aussi tout le carnet personnel. */
let waFiltre = 'a_rattacher';

/* L'onglet Rattacher ne s'affiche que s'il y a des conversations a relier.
   Zero, il disparait ; une seule, il revient avec son compte. On interroge le
   serveur au demarrage pour ne pas dependre de l'ouverture de l'onglet. */
function majOngletRattacher(n) {
  const b = $('#ongletRattacher');
  if (!b) return;
  /* On garde l'onglet visible tant qu'on est dessus, sinon il disparaitrait
     sous les doigts au moment ou la derniere conversation est rattachee. */
  b.hidden = !n && !b.classList.contains('on');
  const p = $('#waBadge');
  p.hidden = !n;
  p.textContent = n || '';
}

async function guetterRattacher() {
  try {
    const d = await api('/api/whatsapp/chats?etat=a_rattacher');
    majOngletRattacher((d.compte || {}).a_rattacher || 0);
  } catch (e) { /* pas de reseau local : l'onglet reste cache */ }
}

LOADERS.whatsapp = async function () {
  const d = await api('/api/whatsapp/chats' + (waFiltre ? '?etat=' + waFiltre : ''));
  const compte = d.compte || {};
  $('#nWa').textContent = compte.clubs || '';

  const badge = $('#waBadge');
  const chauds = (compte.a_lire || 0) + (compte.a_repondu || 0);
  badge.hidden = !chauds;
  badge.textContent = chauds;
  majOngletRattacher(compte.a_rattacher || 0);

  const onglets = [['a_rattacher', 'À rattacher', compte.a_rattacher || 0],
                   ['clubs', 'Liées à un club', compte.clubs || 0]].concat(
    WA_ORDRE.filter(k => compte[k]).map(k => [k, d.libelles[k], compte[k]]),
    [['tout', 'Tout le carnet', compte.tout || 0]]);
  $('#waEtats').innerHTML = onglets.map(([k, nom, n]) =>
    `<button class="wa-etat${k === waFiltre ? ' on' : ''}${WA_CLASSE[k] ? ' ' + WA_CLASSE[k] : ''}"
      data-etat="${k}"><b>${n}</b><span>${esc(nom)}</span></button>`).join('');

  $$('#waEtats .wa-etat').forEach(b => b.onclick = () => {
    waFiltre = b.dataset.etat; LOADERS.whatsapp();
  });

  $('#waListe').innerHTML = d.items.length ? d.items.map(waLigne).join('')
    : '<div class="empty">Aucune conversation dans ce filtre.</div>';
  waBrancher();

  const frais = d.items.map(c => c.updated_at).sort().pop();
  $('#waMaj').textContent = frais ? 'Journal relu le ' + frDate(frais.slice(0, 10)) : '';

  $('#waRelire').onclick = async function () {
    this.disabled = true;
    const avant = this.textContent;
    this.textContent = 'Lecture…';
    try {
      const r = await api('/api/whatsapp/relire', {method: 'POST', body: {}});
      toast(r.vus + ' conversations relues, ' + r.lies + ' rattachée(s)');
      LOADERS.whatsapp();
    } catch (e) { toast(e.message, true); }
    this.disabled = false;
    this.textContent = avant;
  };
};

function waLigne(c) {
  const age = c.jours == null ? '' : (c.jours === 0 ? "aujourd'hui"
    : c.jours === 1 ? 'hier' : 'il y a ' + c.jours + ' j');
  const lien = c.phone ? 'https://wa.me/' + c.phone.replace(/\D/g, '') : '';
  /* Une conversation qui n'a qu'un numero pour nom ne dit rien : quand le club
     est connu, c'est lui le titre, et le numero passe en dessous. */
  const anonyme = /^\+?[\d\s]+$/.test(c.nom);
  const titre = (anonyme && c.club_nom) ? c.club_nom : c.nom;
  const dessous = (anonyme && c.club_nom) ? c.nom
                : (c.club_nom ? '<b>' + esc(c.club_nom) + '</b>'
                              : '<span class="mute">club inconnu</span>');
  return `<div class="item wa" data-id="${c.id}">
    <div class="body">
      <div class="t">${esc(titre)}${c.groupe ? ' <span class="tag">groupe</span>' : ''}</div>
      <div class="s">
        <span class="tag ${WA_CLASSE[c.etat] || ''}">${esc(c.etat_nom)}</span>
        ${age ? ' · ' + esc(age) : ''} · ${dessous}
        ${c.phone && !anonyme ? ' · ' + esc(c.phone) : ''}
      </div>
    </div>
    <div class="actions">
      ${lien ? `<a class="mini" href="${lien}" target="_blank" rel="noopener">Ouvrir</a>` : ''}
      ${c.club_id ? `<button class="mini" data-a="delier">Détacher</button>`
                  : (c.suggestions || []).map(s =>
                      `<button class="mini primary" data-club="${s.club_id}"
                        title="${esc(s.ville || '')}">↳ ${esc(s.nom)}</button>`).join('')
                    + `<button class="mini" data-a="lier">Chercher…</button>
                       <button class="mini" data-a="creer">Créer le club</button>`}
      <button class="mini" data-a="ecarter" title="Ce n'est pas un club">Écarter</button>
    </div>
  </div>`;
}

function waBrancher() {
  $$('#waListe .item.wa').forEach(row => {
    const id = row.dataset.id;
    const nom = row.querySelector('.t').textContent.trim();
    /* Une suggestion se confirme d'un clic : c'est Julien qui tranche, pas
       l'application — un rattachement faux se paie en message a cote. */
    row.querySelectorAll('button[data-club]').forEach(b => b.onclick = async () => {
      try {
        await api(`/api/whatsapp/chats/${id}/lier`,
                  {method: 'POST', body: {club_id: +b.dataset.club}});
        toast('Lié à ' + b.textContent.replace('↳', '').trim());
        LOADERS.whatsapp();
      } catch (e) { toast(e.message, true); }
    });
    row.querySelectorAll('button[data-a]').forEach(b => b.onclick = async () => {
      try {
        if (b.dataset.a === 'ecarter') {
          await api(`/api/whatsapp/chats/${id}/ecarter`, {method: 'POST', body: {}});
          toast(nom + ' écarté');
        } else if (b.dataset.a === 'delier') {
          await api(`/api/whatsapp/chats/${id}/lier`, {method: 'POST', body: {club_id: null}});
          toast('Détaché');
        } else if (b.dataset.a === 'creer') {
          const r = await api(`/api/whatsapp/chats/${id}/club`, {method: 'POST', body: {}});
          toast('Club « ' + r.club + ' » créé');
        } else {
          return waChoisirClub(id, nom);
        }
        LOADERS.whatsapp();
      } catch (e) { toast(e.message, true); }
    });
  });
}

async function waChoisirClub(id, nom) {
  modal(`<h2>Lier « ${esc(nom)} »</h2>
    <p class="hint">Cherchez le club auquel cette conversation correspond.</p>
    <input id="waQ" placeholder="nom du club…" style="width:100%;margin:10px 0">
    <div id="waRes" class="list"></div>`);
  const champ = $('#waQ');
  champ.value = nom;
  champ.focus();
  const chercher = async () => {
    const q = champ.value.trim();
    if (q.length < 2) { $('#waRes').innerHTML = ''; return; }
    const d = await api('/api/clubs?search=' + encodeURIComponent(q) + '&limit=12');
    $('#waRes').innerHTML = d.items.length ? d.items.map(c =>
      `<div class="item"><div class="body"><div class="t">${esc(c.name)}</div>
        <div class="s">${esc(c.city || '')} ${esc(c.sport || '')}</div></div>
        <div class="actions"><button class="mini primary" data-club="${c.id}">Lier</button></div>
      </div>`).join('') : '<div class="empty">Aucun club de ce nom.</div>';
    $$('#waRes button[data-club]').forEach(b => b.onclick = async () => {
      try {
        await api(`/api/whatsapp/chats/${id}/lier`,
                  {method: 'POST', body: {club_id: +b.dataset.club}});
        closeModal(); toast('Conversation liée'); LOADERS.whatsapp();
      } catch (e) { toast(e.message, true); }
    });
  };
  champ.oninput = chercher;
  chercher();
}

/* ================= MISSIONS ================= */
let composerData = null;

/* L'ecran Missions a ete retire : zero mission creee en deux mois. */
LOADERS.partenaires = async function () {
  const d = await api('/api/partenaires');
  partCache = d.items;
  const badge = $('#partBadge');
  badge.hidden = !d.n;
  badge.textContent = d.n;
  renderPartenaires();
};

function initiales(nom) {
  const mots = String(nom || '?').replace(/[^\w\s'-]/g, ' ').trim().split(/[\s'-]+/);
  const utiles = mots.filter(m => m.length > 1 && !/^(le|la|les|de|du|des|club|as|us|sc)$/i.test(m));
  const pris = (utiles.length ? utiles : mots).slice(0, 2);
  return pris.map(m => m[0]).join('').toUpperCase() || '?';
}

function renderPartenaires() {
  const q = ($('#partSearch').value || '').toLowerCase();
  const vus = partCache.filter(p => !q || (p.name || '').toLowerCase().includes(q)
    || (p.city || '').toLowerCase().includes(q)
    || (p.contacts || []).some(c => ((c.first_name || '') + ' ' + (c.last_name || ''))
        .toLowerCase().includes(q)));
  if (!vus.length) {
    $('#partListe').className = '';
    $('#partListe').innerHTML = partCache.length
      ? '<div class="empty">Aucun partenaire ne correspond.</div>'
      : `<div class="empty">Aucun partenaire pour l'instant.<br>
         Collez vos contacts, ou ajoutez un club à la main.</div>`;
    return;
  }
  $('#partListe').className = 'part-grille';
  $('#partListe').innerHTML = vus.map(p => {
    const ct = (p.contacts || [])[0] || {};
    const qui = [ct.first_name, ct.last_name].filter(Boolean).join(' ');
    const [tl, tc] = TEMP[p.temperature] || ['', ''];
    const verdict = (p.distance_verdict || '').toLowerCase();
    return `<div class="part" data-part="${p.id}">
      <div class="haut">
        <div class="rond" style="--teinte:${teinte(p.name)}">${blason(p)}</div>
        <div class="ident">
          <div class="club">${esc(p.name)}</div>
          <div class="qui">${qui ? esc(qui) : 'contact à nommer'}${
            ct.role ? ' · ' + esc(ct.role) : ''}</div>
        </div>
        ${p.km != null ? `<div class="dist ${esc(verdict)}">
            <b>${p.km} km</b><span>${p.trajet_min ? p.trajet_min + ' min' : ''}</span>
          </div>` : ''}
      </div>

      ${ct.phone
        ? `<a class="tel" href="tel:${esc(ct.phone)}"><span class="pic">📞</span>${esc(ct.phone_fmt)}</a>`
        : '<div class="tel vide">Pas de numéro — cliquez Fiche pour l\'ajouter</div>'}

      ${p.prochaine_date ? `<div class="date">📅 ${esc(p.prochaine_date.name)}
         — ${frDate(p.prochaine_date.start_date)}</div>` : ''}

      <div class="etiquettes">
        <span class="tag ${tc}">${esc(tl)}</span>
        ${p.city ? '<span class="tag">' + esc(p.city) + '</span>' : ''}
        ${p.partenaire_depuis ? '<span class="tag">depuis ' + esc(p.partenaire_depuis) + '</span>' : ''}
        ${p.n_contacts > 1 ? '<span class="tag">' + p.n_contacts + ' contacts</span>' : ''}
        ${ct.email ? '<a class="tag" href="mailto:' + esc(ct.email) + '">email</a>' : ''}
      </div>

      ${p.partenaire_note ? '<div class="mot">' + esc(p.partenaire_note) + '</div>' : ''}

      <div class="bas">
        <button class="primary" data-a="relance">Relancer</button>
        <button class="mini" data-a="fiche">Fiche</button>
        <button class="mini" data-a="logo"
          title="Coller l&rsquo;adresse du site du club, ou de son logo">Logo</button>
        <button class="mini" data-a="note">Note</button>
        <button class="mini danger" data-a="retirer">×</button>
      </div></div>`;
  }).join('');

  $$('#partListe [data-part]').forEach(carte => {
    const id = +carte.dataset.part;
    const p = partCache.find(x => x.id === id);
    $$('button[data-a]', carte).forEach(b => b.onclick = async () => {
      const a = b.dataset.a;
      try {
        if (a === 'fiche') return openClub(id);
        if (a === 'note') {
          const v = prompt('Ce que vous avez fait ensemble :', p.partenaire_note || '');
          if (v === null) return;
          await api('/api/partenaires/' + id, { method: 'PATCH',
            body: { partenaire_note: v } });
          return LOADERS.partenaires();
        }
        if (a === 'retirer') {
          if (!confirm('Retirer ' + p.name + ' des partenaires ?\nLe club reste dans vos clubs.')) return;
          await api('/api/partenaires/' + id, { method: 'DELETE' });
          return LOADERS.partenaires();
        }
        if (a === 'logo') {
          /* On ne cherche pas le logo tout seul sur le web : la premiere image
             ramenee par une recherche sur « Cap d'Ail » a toutes les chances
             d'etre le blason d'un autre club. C'est Julien qui donne l'adresse. */
          const url = prompt('Adresse du site du club, ou du logo directement :',
                             p.website || '');
          if (!url) return;
          const champ = /\.(png|jpe?g|svg|webp|gif|ico)(\?|$)/i.test(url)
            ? { image: url } : { site: url };
          toast('Récupération…');
          await api('/api/clubs/' + id + '/logo', { method: 'POST', body: champ });
          toast('Logo récupéré');
          return LOADERS.partenaires();
        }
        if (a === 'relance') {
          const d = await api('/api/partenaires/' + id + '/relancer',
                              { method: 'POST', body: {} });
          if (d && d.id) montrerMessage(d.id, 'Relance ' + esc(p.name));
          else toast('Message préparé');
        }
      } catch (e) { toast(e.message, true); }
    });
  });
}
$('#partSearch').addEventListener('input', debounce(renderPartenaires));

$('#partCartes').onclick = () => { $('#partImport').hidden = !$('#partImport').hidden; };
$('#partAnnuler').onclick = () => {
  $('#partImport').hidden = true;
  $('#partCollage').value = ''; $('#partResultat').innerHTML = '';
};

$('#partLire').onclick = async () => {
  const text = $('#partCollage').value.trim();
  if (!text) return toast('Collez d\'abord l\'export', true);
  try {
    // Un export de discussion porte des vCards ; un collage a la main, non.
    const route = text.toUpperCase().includes('BEGIN:VCARD')
      ? '/api/partenaires/vcards' : '/api/partenaires/coller';
    const d = await api(route, { method: 'POST', body: { text } });
    d.items.forEach(it => { it.club = it.club_propose || ''; });
    $('#partResultat').innerHTML = `<p class="hint">${d.n} carte(s). Vérifiez le club
      de chacune : c'est lui qui sert de fiche.</p>` +
      `<div class="table"><table><thead><tr><th></th><th>Carte</th><th>Téléphone</th>
       <th>Club</th><th></th></tr></thead><tbody>` +
      d.items.map((it, i) => `<tr>
        <td class="pick"><input type="checkbox" class="vc" data-i="${i}" checked></td>
        <td>${esc(it.nom)}${it.fonction ? ' <span class="tag">' + esc(it.fonction) + '</span>' : ''}</td>
        <td>${esc((it.telephones_fmt || []).join(' · ') || '—')}</td>
        <td><input data-club="${i}" value="${esc(it.club)}" size="22"></td>
        <td>${it.existant ? '<span class="tag warn">déjà connu</span>' : '<span class="tag on">nouveau</span>'}</td>
      </tr>`).join('') + `</tbody></table></div>
      <div class="toolbar" style="margin-top:10px">
        <button class="primary" id="vcSave">Enregistrer en partenaires</button></div>`;
    $$('#partResultat input[data-club]').forEach(inp => inp.oninput = () => {
      d.items[+inp.dataset.club].club = inp.value;
    });
    $('#vcSave').onclick = async () => {
      const keep = $$('#partResultat input.vc').filter(c => c.checked)
        .map(c => d.items[+c.dataset.i]).filter(it => (it.club || '').trim());
      if (!keep.length) return toast('Renseignez au moins un club', true);
      const r = await api('/api/partenaires/vcards/valider',
        { method: 'POST', body: { items: keep } });
      toast(`${r.clubs} club(s) et ${r.contacts} contact(s) enregistrés`);
      $('#partAnnuler').click();
      LOADERS.partenaires();
    };
  } catch (e) { toast(e.message, true); }
};

$('#partNouveau').onclick = async () => {
  const name = prompt('Nom du club partenaire :');
  if (!name) return;
  const depuis = prompt('Depuis quand travaillez-vous ensemble ? (facultatif)') || '';
  const note = prompt('Ce que vous avez fait ensemble (facultatif) :') || '';
  try {
    await api('/api/partenaires', { method: 'POST', body: { name, depuis, note } });
    toast('Partenaire ajouté');
    LOADERS.partenaires();
  } catch (e) { toast(e.message, true); }
};

/* ================= PENSE-BETE ================= */
/* Une idee qui revient en tete (« studio cannes ») devient tout de suite les
   messages a envoyer, club par club, sans passer par chaque fiche. Ouvrir
   WhatsApp ou Mail vaut relecture : le brouillon est valide au passage. */
const MEMO_NOM = {studio_club: 'shooting studio', studio_whatsapp: 'shooting studio',
                  studio_rdv: 'shooting studio',
                  calendrier_mail: 'dates de la saison', relance_1: 'relance'};

function memoCarte(d) {
  const qui = [d.first_name, d.last_name].filter(Boolean).join(' ');
  const role = d.contact_role ? ' (' + d.contact_role + ')' : '';
  const fini = d.status === 'sent';
  return `<div class="memo-carte${fini ? ' fini' : ''}" id="memo${d.id}">
    <div class="tete"><b>${esc(d.club_name || '—')}</b>
      ${d.club_city ? '<span class="tag">' + esc(d.club_city) + '</span>' : ''}
      <span class="qui">${esc(qui + role)} · ${esc(d.to_addr || '')}</span>
      ${fini ? '<span class="tag on">envoyé</span>' : ''}</div>
    ${d.channel === 'email'
      ? `<input class="subj" data-f="subject" value="${esc(d.subject || '')}">` : ''}
    <textarea id="memo-mot-${d.id}" data-f="body">${esc(d.body)}</textarea>
    ${Reform.barre('memo-mot-' + d.id, [d.club_name, d.club_city].filter(Boolean).join(' · '))}
    <div class="acts">
      ${d.whatsapp ? '<button class="wa" data-m="wa">WhatsApp</button>' : ''}
      ${d.mailto ? '<button class="primary" data-m="mail">Ouvrir dans Mail</button>' : ''}
      <button data-m="copy">Copier</button>
      <button data-m="sent">✓ Envoyé</button>
      <button data-m="reject">Écarter</button>
      <span class="grow"></span>
      <button class="mini" data-m="club">Fiche</button>
    </div></div>`;
}

function memoLier(d) {
  const root = $('#memo' + d.id);
  if (!root) return;
  Reform.brancher(root);
  const val = f => { const e = $(`[data-f="${f}"]`, root); return e ? e.value : null; };
  const valider = async () => {
    await api('/api/drafts/' + d.id, { method: 'PATCH',
      body: { subject: val('subject'), body: val('body') } });
    const f = await api('/api/drafts/' + d.id);
    if (f.status === 'draft')
      await api(`/api/drafts/${d.id}/approve`, { method: 'POST', body: {} });
    return f;
  };
  $$('button[data-m]', root).forEach(b => b.onclick = async () => {
    try {
      const m = b.dataset.m;
      if (m === 'club') return openClub(d.club_id);
      if (m === 'copy') return copy((val('subject') ? val('subject') + '\n\n' : '') + val('body'));
      if (m === 'wa') {
        const f = await valider();
        window.open(f.whatsapp, '_blank');
        return toast('WhatsApp ouvert — envoyez, puis cliquez « Envoyé »');
      }
      if (m === 'mail') {
        const f = await valider();
        location.href = f.mailto;
        return;
      }
      if (m === 'sent') {
        await valider();
        await api(`/api/drafts/${d.id}/sent`, { method: 'POST',
          body: { schedule_followups: true } });
        root.classList.add('fini');
        return toast('Tracé, relances programmées');
      }
      if (m === 'reject') {
        await api(`/api/drafts/${d.id}/reject`, { method: 'POST', body: { note: 'pense-bête' } });
        root.remove();
        return toast('Écarté');
      }
    } catch (e) { toast(e.message, true); }
  });
}

/* Ce qu'on a trouve dans la base, groupe par nature. Chaque ligne mene a la
   fiche ; quand il y a un numero, WhatsApp est a un clic. */
function peindreRecherche(r, texte) {
  const boite = $('#memoCartes');
  if (!r.total) {
    boite.innerHTML = `<div class="resume">Rien trouvé pour
      <b>${esc(texte)}</b>. <span class="grow"></span>
      <button class="mini" id="memoFermer">Fermer</button></div>`;
    $('#memoFermer').onclick = () => { boite.innerHTML = ''; $('#memoTexte').value = ''; };
    return;
  }
  boite.innerHTML = `<div class="resume"><b>${r.total} résultat(s)</b>
      pour ${esc(r.mots.join(' '))}
      <span class="grow"></span>
      <button class="mini" id="memoFermer">Fermer</button></div>`
    + r.groupes.map(g => `<div class="cherche-groupe">
        <h3>${esc(g.nom)} <span class="n">${g.n}</span>${
          g.approchant ? '<em>approchant — aucun n’a tous les mots</em>' : ''}</h3>
        ${g.items.map(it => `<div class="cherche-ligne${it.extrait ? ' avec-extrait' : ''}"
            data-onglet="${esc(it.onglet)}"
            data-club="${it.club_id || (g.quoi === 'clubs' ? it.id : '')}">
          <b>${esc(it.titre || '')}</b>
          <span class="sous">${esc(it.sous || '')}</span>
          <span class="grow"></span>
          ${it.tel ? `<a class="mini" href="https://wa.me/${
            String(it.tel).replace(/\D/g, '')}" target="_blank"
            rel="noopener">WhatsApp</a>` : ''}
          ${it.tel ? `<a class="mini" href="tel:${esc(it.tel)}">Appeler</a>` : ''}
          ${it.extrait ? `<q class="extrait">${esc(it.extrait)}</q>` : ''}
        </div>`).join('')}
      </div>`).join('');

  $('#memoFermer').onclick = () => { boite.innerHTML = ''; $('#memoTexte').value = ''; };
  $$('#memoCartes .cherche-ligne').forEach(l => l.onclick = e => {
    if (e.target.closest('a')) return;          // le bouton garde son lien
    if (l.dataset.club) return openClub(+l.dataset.club);
    show(l.dataset.onglet);
  });
}

$('#memoForm').onsubmit = async e => {
  e.preventDefault();
  const text = $('#memoTexte').value.trim();
  if (!text) return;
  const boite = $('#memoCartes');
  boite.innerHTML = '<div class="resume">Recherche…</div>';
  try {
    const r = await api('/api/memo', { method: 'POST', body: { text } });
    /* Sans intention reconnue (studio, dates, relance), le serveur a cherche
       au lieu de refuser : on affiche ce qu'il a trouve. */
    if (r.mode === 'recherche') return peindreRecherche(r, text);
    const sur = r.filtres.length ? ' · ' + r.filtres.join(', ') : '';
    const plus = [];
    if (r.deja_envoye.length) plus.push(r.deja_envoye.length + ' déjà envoyé(s)');
    if (r.sans_contact.length) plus.push(r.sans_contact.length + ' sans email ni portable : ' +
      r.sans_contact.map(c => c.name).join(', '));
    boite.innerHTML = `<div class="resume"><b>${r.cartes.length} message(s)</b>
        ${esc((MEMO_NOM[r.modele] || r.modele) + sur)}
        ${plus.length ? '· ' + esc(plus.join(' · ')) : ''}
        <span class="grow"></span><button class="mini" id="memoFermer">Fermer</button></div>` +
      (r.cartes.length ? r.cartes.map(memoCarte).join('')
        : '<div class="resume">Aucun club ne correspond. Essayez une ville ou un sport.</div>');
    r.cartes.forEach(memoLier);
    $('#memoFermer').onclick = () => { boite.innerHTML = ''; $('#memoTexte').value = ''; };
  } catch (err) {
    boite.innerHTML = '';
    toast(err.message, true);
  }
};
