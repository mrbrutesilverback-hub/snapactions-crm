# Prospection clubs

CRM local pour gérer des contacts de clubs, surveiller les tournois à venir, et
préparer des messages contextualisés — **que vous relisez avant tout envoi**.

Aucune dépendance à installer : tout tourne sur le Python livré avec macOS
(testé sur Apple Silicon, Python 3.9). Vos données restent dans `data/crm.db`,
sur votre machine, et le serveur n'écoute que sur `127.0.0.1`.

## Démarrer

```bash
python3 -m prospect serve
```

Puis ouvrez http://127.0.0.1:8765 — ou double-cliquez `lancer.command`.

Pour voir l'outil rempli avant d'y mettre vos vraies données :

```bash
python3 -m prospect demo
```

## Les sept écrans

| Écran | À quoi ça sert |
|---|---|
| **Aujourd'hui** | Ce qu'il y a à faire, les messages en attente de relecture, la veille |
| **Clubs** | Fiches, statut dans le pipeline, historique, opt-out |
| **Contacts** | Les personnes, avec liens `tel:` et `mailto:` cliquables |
| **Tournois** | Ce que la veille a trouvé, et quels clubs cibler pour chacun |
| **Messages** | La file de relecture. **Rien ne part d'ici.** |
| **Import** | Dépôt de fichier Excel/CSV, ou collage de texte brut |
| **Réglages** | Votre identité, la cadence, les flux iCal, les sources, les modèles |

## Récupérer vos contacts

Trois entrées, toutes avec validation ligne par ligne avant écriture :

1. **Fichier** — déposez un `.xlsx` ou un `.csv`. Les colonnes sont reconnues
   automatiquement (Club, Nom, Fonction, Email, Téléphone, Ville, CP…) et vous
   pouvez corriger le mappage colonne par colonne. Un `.xls` ou un `.numbers`
   doit d'abord être ré-enregistré en `.xlsx` ou `.csv`.
2. **Texte collé** — un fil de forum, des signatures, une page club. Les emails
   (y compris obfusqués `contact [at] club [dot] fr`), les téléphones, les noms
   de club, les fonctions et les codes postaux sont repérés.
3. **Calendrier** — collez un lien `webcal://` ou `https://….ics` (un calendrier
   iCloud publié, par exemple). Chaque événement est classé en **piste** (un club à
   contacter) ou **date** (un événement à couvrir) ; vous corrigez avant
   d'enregistrer. Les numéros de téléphone écrits dans les titres d'événement sont
   récupérés, et chaque piste garde une trace datée dans son historique.

   À ne pas confondre avec les *sources de veille* (Réglages) : le calendrier est
   **votre** agenda, la veille cherche des tournois **extérieurs** à démarcher.
4. **Saisie manuelle** — bouton « + Club ».

Un classeur Excel à plusieurs feuilles est géré : choisissez la feuille dans le
menu déroulant. Les en-têtes composés sont reconnus (`Club / Association`,
`Ville / Station`, `Contact (nom / role)`), et une colonne « contact » qui
contient en réalité des fonctions (« Président ») est détectée d'après son
contenu, pas d'après son titre. Une case comme `Stephanie (presidente)` donne le
prénom **et** la fonction ; `Marion (com) ; Jeremy P.` garde la seconde personne
en note. Une ligne sans nom de club crée le contact sans club plutôt que de
l'agréger à un club fictif.

Les numéros sont normalisés en E.164 (`+33612345678`), y compris quand Excel a
mangé le zéro initial en les stockant comme des nombres. Le dédoublonnage se
fait sur le nom de club normalisé, le domaine du site ou de l'email, puis sur
l'email et le téléphone du contact. Une valeur déjà renseignée n'est jamais
écrasée par un import.

## La veille tournois

Dans **Réglages → Sources**, ajoutez une source. Cinq types :

- **`ics`** — un calendrier de fédération ou de ligue. Le plus fiable, commencez par là.
- **`rss`** — un flux d'actualités.
- **`json`** — une API. Config : `{"root": "data.items"}` et au besoin
  `{"fields": {"name": "titre", "start_date": "debut"}}`.
- **`csv`** — un fichier tabulaire en ligne.
- **`html`** — une page web, via un motif regex à groupes nommés :
  `{"pattern": "<h3>(?P<name>[^<]+)</h3>.*?(?P<start_date>\\d{2}/\\d{2}/\\d{4})", "base_url": "https://…"}`

« Lancer la veille » interroge toutes les sources, dédoublonne, puis prépare les
brouillons pour les tournois qui tombent dans votre fenêtre de contact.

**Le rapprochement tournoi → club** retient les clubs du même département et des
départements limitrophes, actifs, non opt-out, pas contactés depuis 14 jours, et
pas déjà sollicités sur ce tournoi. La fenêtre par défaut est J-75 à J-10 : trop
tôt on est oublié, trop tard le club est déjà dans le rush.

En ligne de commande, pour une exécution planifiée :

```bash
python3 -m prospect veille      # sources + préparation des brouillons
python3 -m prospect relances    # brouillons pour les relances échues
python3 -m prospect stats       # résumé en console
```

## Le garde-fou d'envoi

C'est le point non négociable de l'outil : **aucune fonction d'envoi n'existe
dans le code.** Un message suit `brouillon → validé → envoyé`, et :

- « Marqué envoyé » est **refusé (HTTP 409)** sur un message non validé ;
- « Validé » ne fait qu'autoriser — ça n'envoie rien ;
- l'envoi réel se fait par vous, dans votre messagerie (bouton « Ouvrir dans
  Mail » ou « Copier ») ;
- « Marqué envoyé » ne fait ensuite que tracer l'échange, faire avancer le
  statut du club, et programmer les relances.

Un club passé en « Ne plus contacter » voit ses relances annulées, ses
brouillons écartés et ses contacts marqués — immédiatement.

## Missions : appeler les photographes

Onglet **Missions**. À gauche, les événements de l'agenda pour lesquels personne
n'a encore été appelé, avec le **groupe WhatsApp déduit de la discipline** :
un match de basket va vers le groupe Basket, une régate vers Voiles.

Cliquez **Composer** : le message est prêt, avec le titre, la date en clair
(« samedi 10 octobre », « du 10 au 11 octobre »), le lieu et l'organisateur.
Vous ajustez, puis **Copier + ouvrir WhatsApp** : le message part dans le
presse-papiers et WhatsApp passe au premier plan. Vous ouvrez le groupe, Cmd+V,
Entrée. Puis **Postée dans le groupe** pour le tracer.

La copie passe par trois voies successives — l'API du navigateur, la méthode
historique, puis `pbcopy` côté serveur. La première échoue quand la fenêtre n'a
pas le focus ; la dernière ne peut pas échouer, le serveur tournant sur votre
Mac.

**Aucune API ne permet de poster dans un groupe WhatsApp** — ni la Cloud API de
Meta, qui ne fait que du 1-à-1 client, ni les liens `wa.me`, qui ne visent que
des individus. L'outil s'arrête donc au presse-papiers, et c'est volontaire :
les bibliothèques non officielles qui savent le faire font bannir le compte.

Le bloc **Prévenir en direct** liste vos photographes ; ceux dont vous avez
renseigné le numéro ont un bouton WhatsApp qui ouvre une discussion individuelle
avec le message pré-rempli — utile quand le groupe ne répond pas.

Les groupes se règlent dans la table `crews`, les photographes dans l'onglet
Missions (bouton **+ Ajouter**).

## Ce qui a déjà été échangé

WhatsApp ne donne accès à **aucune API pour relire votre historique** : ni la
Cloud API de Meta (qui ne voit que ce qui transite par elle), ni rien d'autre.
L'export est le seul moyen légitime.

### Le plus rapide : l'index local

Double-cliquez **`lire-whatsapp.command`** dans ce dossier. Il lit la base de
WhatsApp Desktop et en sort un `whatsapp-contacts.csv` : un numéro et deux dates
par conversation. **Il ne lit jamais le texte des messages** — la colonne qui les
contient (`ZTEXT`) n'est nommée nulle part dans `lire_whatsapp.py`, vérifiez-le.
La base est copiée avant lecture, WhatsApp n'est pas modifié.

Déposez ensuite le CSV dans l'onglet Import : chaque numéro est croisé avec vos
contacts, la vraie date du dernier échange remplace « Jamais contacté », et un
club qui vous a répondu passe en « a répondu ».

Le script a besoin de l'**Accès complet au disque** pour le Terminal (Réglages
Système → Confidentialité et sécurité → Accès complet au disque → ajoutez
Terminal, puis quittez-le entièrement et relancez le script). Si WhatsApp a
chiffré sa base, le script vous le dira au lieu d'échouer en silence.

### Une discussion en particulier

Dans WhatsApp : ouvrez la discussion → **Exporter la discussion** → **Sans les
médias**, puis déposez le `.txt` dans l'onglet Import. Les formats iOS et Android,
français et anglais, sont reconnus. Les messages rejoignent l'historique du club,
la date du dernier contact se met à jour, et le club passe en « a répondu » s'il
vous a répondu. Réimporter le même export ne crée pas de doublon.

Pour aller plus vite sur un lot de clubs déjà démarchés, cochez-les dans l'écran
Clubs et utilisez **« Marquer comme contactés »** : une date, un canal, et le
pipeline reflète la réalité.

## Récupérer une date annoncée dans un message

Quand un club répond « notre prochain tournoi sera le weekend du 10/11 octobre »,
ouvrez sa fiche, collez sa réponse dans **Message reçu → dates**, cliquez
**Repérer les dates**. Les dates trouvées partent dans votre calendrier et
apparaissent dans le flux iCal.

L'écriture réelle des clubs est prise en compte : « weekend du 10/11 octobre »
(deux jours), « vendredi 4decembre » (collé, sans espace), « du 12 au 14 juin »,
« le 1er février », « 03/10/2026 ». L'année est presque toujours absente : la
prochaine occurrence est retenue, et l'étiquette « année déduite » vous le dit.

Un indice de confiance accompagne chaque date. Il monte à 95 % quand le jour de
la semaine annoncé colle vraiment à la date (« vendredi 4 décembre » — le 4
décembre 2026 est bien un vendredi), et reste bas quand ça ne colle pas : c'est
souvent le club qui s'est trompé, à vous de vérifier.

Rien n'est ajouté sans que vous cochiez.

## Thème

Le bouton en haut à gauche fait défiler **Système → Clair → Sombre**. Par défaut
l'app suit le réglage de macOS ; votre choix est mémorisé dans le navigateur.

Le logo SnapActions bascule avec le thème : version noire sur fond clair,
version blanche sur fond sombre, dans la barre du haut, sur la page Réglages et
dans l'onglet du navigateur. Les fichiers sont dans `prospect/static/img/`.

La variante blanche a été fabriquée à partir de `SNAPACTION.png` : le trait noir
est inversé en blanc, tandis que le bleu et le rouge du drapeau sont conservés —
y compris la bande blanche centrale, qui reste blanche au lieu de virer au noir.
Pour la refaire après un changement de logo :

```bash
python3 /tmp/mklogo.py chemin/vers/logo-noir.png prospect/static/img/logo-sombre.png
```

## Envoyer : WhatsApp et mail

L'outil n'envoie rien. Une fois un message validé, il vous ouvre le bon canal
avec le texte déjà écrit — vous relisez une dernière fois et vous appuyez sur
Envoyer vous-même.

- **« Ouvrir dans WhatsApp »** apparaît quand le contact a un mobile (06/07).
  Le lien `wa.me` ouvre WhatsApp Desktop ou Web avec le message pré-rempli.
  Aucune API, aucun compte business, aucun risque pour votre numéro.
- **« Ouvrir dans Mail »** fait la même chose avec votre messagerie.
- **« Copier »** pour tout autre canal.

### Ce qu'il ne faut pas faire

Automatiser l'envoi WhatsApp via une bibliothèque non officielle
(whatsapp-web.js, Baileys) est contraire aux CGU de Meta : envoyer des dizaines
de messages à froid depuis un numéro personnel fait bannir le compte, et
l'historique des conversations part avec.

La voie officielle (WhatsApp Cloud API) demande une vérification d'entreprise,
un numéro dédié et des modèles pré-approuvés par Meta. Meta refuse
habituellement les modèles de prospection à froid : sans consentement du club,
c'est précisément ce qu'ils filtrent. La seule fenêtre sûre est la réponse dans
les 24 h à un club qui vous a écrit en premier.

Pour le mail en volume, la bonne marche suivante est l'API Gmail en mode
**brouillon** : l'agent écrit dans vos Brouillons, vous ouvrez Gmail et envoyez.
La relecture reste en place. Cela demande un projet Google Cloud, des
identifiants OAuth et un Python ≥ 3.10.

## Relances et calendrier

À l'envoi d'un premier message, la cadence J+4 / J+10 / J+21 se programme toute
seule (modifiable dans Réglages, week-ends évités).

Pour les voir dans le Calendrier du Mac : **Réglages → Calendrier**, copiez le
lien, puis dans Calendrier **Fichier → Nouvel abonnement calendrier**.

**Abonnez-vous au flux « Tournois », pas au flux « Tout ».** Le flux complet
contient aussi un rappel par relance programmée : avec 58 clubs en cours, cela
fait 174 événements qui noieraient votre agenda. Les tournois seuls en font 16.
Si vous voulez quand même vos relances dans le Calendrier, prenez le flux
« Relances » comme **second abonnement** : vous pourrez le décocher d'un clic
dans la barre latérale de Calendrier.

Le serveur doit tourner pour que Calendrier puisse rafraîchir le flux. Les
relances (avec alerte 15 min avant) et les tournois (journée entière) s'y mettent
à jour tout seuls. Trois flux séparés sont disponibles si vous préférez.

Export ponctuel : `python3 -m prospect ics -o tournois.ics tournois`

## Modèles de message

Six modèles fournis, modifiables dans Réglages avec aperçu sur des données
réelles. Champs disponibles : `{prenom}`, `{club}`, `{ville}`, `{tournoi}`,
`{tournoi_date}`, `{tournoi_j}`, `{pitch}`, `{signature}`…

Un champ inconnu reste visible tel quel dans l'aperçu au lieu de faire planter
le rendu — c'est voulu, ça se voit à la relecture.

## Prospection et RGPD

Vous démarchez des associations, donc du B2B : l'intérêt légitime s'applique,
mais l'outil est construit pour que vous restiez propre.

- Le bouton **« Ne plus contacter »** coupe tout, tout de suite. Utilisez-le dès
  la première demande, sans discuter.
- La **source de chaque contact** est conservée (forum, fichier, URL du fil) :
  c'est ce qui vous permet de justifier d'où vient une donnée.
- Le modèle SMS n'est à utiliser que si la personne vous a donné son numéro
  elle-même. Jamais en premier contact.
- Trois relances puis on arrête : c'est la cadence par défaut, et c'est un
  bon usage.

## Structure

```
prospect/
  util.py      téléphones, emails, départements, dates
  db.py        schéma SQLite, réglages, modèles
  repo.py      clubs, contacts, historique, séquences de relance
  extract.py   texte brut ou tableau → fiches
  xlsx.py      lecteur .xlsx (zipfile + XML, sans dépendance)
  ics.py       génération et lecture iCalendar
  sources.py   veille tournois (ics/rss/json/html/csv)
  drafts.py    modèles, rendu, rapprochement, cycle de validation
  web.py       serveur HTTP, API JSON, flux .ics
  static/      interface (HTML/CSS/JS, sans build)
data/crm.db    vos données
```

## Sauvegarde

Tout est dans `data/crm.db`. Copiez ce fichier, c'est votre sauvegarde. Les
exports CSV (clubs, contacts, tournois) sont dans chaque écran.
