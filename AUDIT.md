# Audit du CRM SnapActions — 25 octobre 2026

Tout ce qui suit est mesuré sur la base réelle (`data/crm.db`), le journal du
scan quotidien et le code. Aucun chiffre n'est estimé.

---

## 1. Le tableau de bord réel

| | |
|---|---|
| Clubs en fiche | **644** |
| … dans le 06 | 139 |
| … dans le 83 | 73 |
| … ailleurs (Monaco 89, Bouches-du-Rhône 41, Rhône 19, Isère 15, Haute-Savoie 13, sans département 120…) | **432** |
| Clubs joignables (tél ou mail) | 458 |
| Clubs muets | 186 |
| **Jamais contactés** | **510** |
| Contactés | 110 |
| Clients | **7** |
| Tournois à venir | 483 |
| … dans le périmètre (≤ 120 min) | 317 |
| … avec un club rattaché **et** joignable | **238** |
| Conversations WhatsApp | 609 |
| … sans fiche club | 485 |
| Messages sortants enregistrés | 312 WhatsApp, 11 mails |
| Brouillons écrits | 625 — dont **290 jamais envoyés** |
| … brouillons de plus de 14 jours, jamais envoyés | **211** |
| Relances programmées en retard | **301** |
| Tournois « à trancher » (scan du 25/10) | **409** |

### Ce que ces chiffres disent

Le CRM **trouve** très bien et **convertit** très mal : 644 clubs trouvés,
7 clients. Le rythme d'envoi est pourtant réel — entre 19 et 66 messages par
semaine depuis fin août. Le problème n'est donc pas la paresse ni l'outil
d'envoi : c'est que **la machine produit cinq à dix fois plus de travail
qu'une personne seule peut en absorber**, et que rien ne choisit à votre place.

194 actions sont en attente aujourd'hui. Vous ne pouvez pas en faire 194.

---

## 2. Les incohérences

### 2.1 Deux tiers de la base sont hors de votre zone de travail

432 clubs sur 644 sont hors 06/83. Ce sont les clubs de ski (38 fiches) et la
récolte des sources « Gros tournois — Hauts-de-France / Bretagne / Occitanie /
Belgique… ». Ces 432 fiches gonflent tous les compteurs, toutes les files et
toutes les recherches, et vous ne leur écrirez jamais.

**C'est la première cause des 510 « jamais contactés ».**

### 2.2 Le scan quotidien crée puis détruit

Journal du 25/10 : 11 sources « Gros tournois » d'autres régions ont lu
1 046 lignes, dont **940 hors périmètre**. 53 fiches sont créées. Puis, plus
bas dans le même scan : `ménage : 60 fiche(s) hors périmètre supprimée(s)`.

Tous les matins, le même travail est fait puis défait.

### 2.3 Votre meilleur écran est celui que vous n'ouvrez pas

`journee.py` produit **une liste unique, classée par coût de l'inaction et
plafonnée à 12 lignes** — exactement ce que vous demandez. Elle marche :
194 actions en attente, 12 proposées, le reste attend demain.

Nombre d'ouvertures enregistrées :

| Écran | Ouvertures |
|---|---|
| Repérage | 153 |
| Rencontres | 54 |
| Chercher | 54 |
| Répondre | 40 |
| **Aujourd'hui** | **5** |

L'écran qui décide à votre place est enterré dans `index.html#/today` et
n'apparaît pas dans le menu principal. Les écrans que vous utilisez sont tous
des **piles sans plafond**, qui montrent tout et ne concluent rien.

### 2.4 Vingt clubs « jamais contactés » le sont déjà

20 clubs ont `last_contact_at` vide alors qu'une conversation WhatsApp existe
avec leur numéro. Le moteur de relance les croit froids. La relecture WhatsApp
dit `0 liée(s)` à chaque passage parce qu'elle ne rapproche que par numéro
exact.

### 2.5 Un modèle rejeté plus souvent qu'envoyé

`tournoi_avant` : **112 rejetés, 91 envoyés**. Plus d'un brouillon sur deux
est jeté. Soit le texte ne va pas, soit on l'écrit pour les mauvaises cibles —
dans les deux cas on paie l'écriture (modèle local, temps de relecture) pour
rien.

10 modèles sur 26 n'ont **jamais** servi : `calendrier_mail`,
`calendrier_whatsapp`, `mission_photo`, `partenaire_evenement`,
`partenaire_relance`, `relais_premier`, `relance_forum`, `studio_animaux`,
`studio_animaux_whatsapp`, `tournoi_apres`.

### 2.6 Deux définitions de « joignable sur WhatsApp »

- `numeroWa()` (recopiée dans 7 fichiers) : tout numéro de 10 chiffres ou plus.
- `Envoyer.mobile()` (`envoyer.js`) : **portables seulement** (`33 6`, `33 7`,
  Monaco hors 9).

Résultat : une carte peut afficher « Reprendre contact » sans afficher le
bouton vert d'envoi, ou l'inverse. C'était exactement le bug corrigé
aujourd'hui dans « Semaines à remplir ».

### 2.7 Un mail « envoyé » à un numéro de téléphone

Journal : `Mail envoyé à +33XXXXXXXXX`. `envoi.par_mail` refuse bien une
adresse sans `@`, mais `noter_envoi` accepte n'importe quoi et écrit
« Mail envoyé à … ». La trace est fausse.

### 2.8 Vingt-quatre onglets Chrome chaque matin

`groupes : 24 grille(s) ouverte(s) dans Chrome`. Tous les jours, sans
qu'on l'ait demandé ce matin-là.

### 2.9 Le veilleur du carnet tourne pour rien

Depuis la suppression de « Vos numéros », `carnet.veiller()` relit les
5 sources du carnet d'adresses **toutes les trois minutes** et n'alimente plus
aucun écran. C'est ce veilleur qui avait créé les 13 fausses fiches.

---

## 3. Les doublons

### 3.1 Dans le code du front

| Fonction | Copies identiques |
|---|---|
| `toast()` | 10 fichiers |
| `neud()` | 7 |
| `numeroWa()` | 7 |
| `estFixe()` | 6 |
| `ouvrirLien()` | 6 |
| `copier()`, `jour()`, `depuis()`, `ouvrirMail()` | 2 à 3 chacune |

Une correction de règle téléphonique doit aujourd'hui être faite sept fois.
Elle l'a été six fois sur sept au moins une fois dans l'histoire du projet.

### 3.2 Deux écrans pour les mêmes deux piles

« Répondre » et « Aujourd'hui » lisent **exactement les mêmes sources** :

- Répondre / « ont écrit » ← `relancer.attendent()` ← `wa.suivi()`
- Aujourd'hui / famille 1 « Répondre » ← `wa.suivi()`
- Répondre / « rappels échus » ← `tasks`
- Aujourd'hui / famille 4 « Appeler » ← `tasks`

Deux écrans, deux codes, deux façons de rayer une ligne, un seul travail.

### 3.3 Quarante-cinq routes sur deux cent quatre ne sont appelées par aucun écran

Dont des sous-systèmes entiers sans interface : `bookings` (table vide),
`missions` (24 lignes), `team` (11), `crews` (8), `alertes`, `mes-dates`,
`menage`, plus les 7 routes `carnet` devenues inutiles aujourd'hui.

### 3.4 Cinq tournois en double, huit numéros partagés

- 5 paires de tournois même nom + même date (Cross UNSS Fontmerle, Coupe
  S.A.S. le Prince Albert, Sunday Ride Classic, Tir en salle au Luc, Concours
  des pistoles à Brignoles).
- 8 numéros portés par plusieurs clubs : `+377 92 XX XX XX` sur **5 clubs**
  monégasques, `+33 7 XX XX XX XX` sur 4. Ce sont des standards, pas des
  contacts : les appeler envoie au mauvais interlocuteur.

---

## 4. Écran par écran : ce que ça vous fait gagner, ce que ça vous coûte

### Repérage (153 ouvertures — votre écran principal)
**Ce que ça fait gagner :** la veille tourne seule la nuit, les dates arrivent
le matin avec le trajet calculé, le message est déjà écrit et adapté à la
famille d'événement, et l'envoi part en un clic.
**Ce que ça coûte :** 409 tournois à trancher. L'écran montre une file
infinie et ne dit jamais « voilà les six d'aujourd'hui ».
**Plus intelligent :** trancher automatiquement ce qui est évident — hors
périmètre, date passée, sport sans enfants — et ne présenter que le reste.

### Répondre (40)
**Ce que ça fait gagner :** c'est le seul endroit où le travail est vraiment
mâché — la conversation, la réponse écrite d'après le fil, l'envoi réel.
**Ce que ça coûte :** 93 rappels échus affichés, 301 en réalité. Et c'est le
doublon d'« Aujourd'hui ».
**Plus intelligent :** fusionner les deux écrans, garder le plafond.

### Rencontres (54)
**Ce que ça fait gagner :** après une journée sur le terrain, les numéros
ramassés remontent seuls de WhatsApp, avec le fil et un brouillon.
**Ce que ça coûte :** sur 609 conversations, 485 n'ont pas de fiche et 484
ont un numéro que la base ne connaît pas. Le tri à trois boutons
(peut me booker / du métier / sans suite) est la bonne idée, mais il faut
encore le faire à la main sur chaque carte.
**Plus intelligent :** le modèle local lit déjà les fils — il peut proposer le
tri lui-même, à valider d'un clic.

### Chercher (54)
**Ce que ça fait gagner :** une question en français, une réponse appuyée sur
vos fiches. Remise en service aujourd'hui (elle était cassée depuis au moins
le 24/10, et les 4 routines quotidiennes échouaient avec elle).
**Ce que ça coûte :** rien, elle est gratuite et locale.

### Semaines à remplir (22)
**Ce que ça fait gagner :** c'est le seul écran qui part de **votre** agenda
(les trous) et non de la file de prospects. C'est le bon sens économique.
**Ce que ça coûte :** peu vu. 4 semaines creuses sur les 12 à venir.

### Aujourd'hui (5) — le gâchis
Décrit en 2.3.

### Studio (17), Flyer (15), Partenaires (16), Relais (13)
Peu utilisés, mais peu coûteux. `relais.html` et `partenaires.html` ne sont
atteignables que depuis `index.html`, lui-même peu ouvert.

---

## 5. Ce que je recommande, par rapport gain / effort

### A. Remettre « Aujourd'hui » au centre *(gros gain, petit effort)*
Le mettre en tête du menu, à la place ou au-dessus de Repérage, et supprimer
l'écran Répondre dont il reprend les deux piles. Une liste de 12, pas six
piles de 300. Le code existe déjà et fonctionne.

### B. Sortir les 432 clubs hors zone *(gros gain, petit effort)*
Soit les supprimer, soit les marquer `hors_zone` et les exclure de tous les
compteurs et de toutes les files. Dans la foulée, **désactiver les 11 sources
« Gros tournois » d'autres régions** : elles lisent 1 000 lignes par jour pour
produire des fiches qu'on efface le matin même.
Vos files passeraient de 510 à ~180 prospects — un nombre qu'on peut faire.

### C. Classer les prospects par ce qu'ils peuvent rapporter *(gros gain, effort moyen)*
C'est la trouvaille de la recherche. L'État publie, gratuitement et sans clé,
le nombre de **licenciés de moins de 20 ans par commune et par fédération**
([data.sports.gouv.fr, jeu `lics_cc`](https://data.sports.gouv.fr/explore/dataset/clubs_cc/api/)).
Pour le 06 et le 83 :

| Fédération | Jeunes licenciés (06+83) | Vos fiches |
|---|---|---|
| Football | 9 906 | **248** |
| UNSS (sport scolaire) | 8 534 | — |
| **Tennis** | **7 626** | **6** |
| Voile | 3 744 | 10 |
| **Gymnastique** | **3 531** | 19 |
| Judo | 2 665 | 9 |
| **Natation** | **2 627** | **7** |
| **Basketball** | **2 579** | **7** |
| Handball | 1 627 | 6 |
| Équitation | 1 423 | 8 |
| **Ski** | **601** | **38** |

Votre base est faite aux deux tiers de football et de ski. Le ski, c'est
601 jeunes licenciés dans vos deux départements — et 38 fiches. Le tennis,
c'est 7 626 jeunes — et 6 fiches. Un club de tennis a un tournoi jeunes par
mois, une remise des prix et des parents qui achètent.

Concrètement : un champ `potentiel` sur chaque club, calculé une fois par mois
à partir de ce jeu de données, et un tri par potentiel dans Repérage et
Chercher. Plus un écran « les communes où il vous manque des clubs ».

### D. Trancher automatiquement l'évident *(gain moyen, petit effort)*
Sur 409 tournois à trancher, tout ce qui est hors périmètre, passé, ou sans
jeunes devrait être écarté sans vous déranger. Seule la zone grise remonte.

### E. Faire proposer le tri des rencontres par le modèle local *(gain moyen, effort moyen)*
Il lit déjà les fils pour écrire les réponses. Qu'il propose
« peut me booker / du métier / sans suite » et que vous confirmiez.

### F. Un seul fichier d'outils communs pour le front *(petit gain, petit effort)*
`outils.js` avec `toast`, `neud`, `numeroWa`, `estFixe`, `ouvrirLien`,
`copier`, `jour`. Et **une seule** définition de « joignable sur WhatsApp »,
celle d'`envoyer.js`, la stricte.

### G. Ménage *(petit gain, petit effort)*
Arrêter le veilleur du carnet. Supprimer les 45 routes orphelines ou leur
donner un écran. Supprimer les 10 modèles jamais utilisés. Dédoublonner les
5 tournois. Marquer les 8 numéros de standard comme « standard, pas un
contact ». Valider l'adresse dans `noter_envoi`.

### H. Réécrire `tournoi_avant` *(gain moyen, petit effort)*
112 rejets pour 91 envois. Regarder dix brouillons rejetés côte à côte dirait
pourquoi en dix minutes.

---

## 6. Ce que j'ai regardé dehors et qui ne vaut pas le coup

- **HelloAsso** : l'API v5 a bien un annuaire des formulaires publics
  (`POST /v5/directory/forms`), mais il exige le privilège `FormOpenDirectory`,
  accordé aux partenaires — pas en libre-service. Le site public se parcourt,
  mais sans filtre ville propre : mauvais rapport effort/récolte.
- **WhatsApp** : aucune API gratuite n'existe. Ce que fait le CRM aujourd'hui
  (lire la base locale, ouvrir la conversation, appuyer sur Entrée) est le
  meilleur chemin disponible.
- **Une source de plus** : vous avez déjà 483 dates à venir dont 238
  immédiatement exploitables. Ajouter une source ne sert à rien tant que
  celles-là ne sont pas traitées.

---

## 7. Si je ne devais garder que trois choses

1. **« Aujourd'hui » en tête du menu, et Répondre supprimé.** Une liste de 12.
2. **Les 432 clubs hors zone et les 11 sources lointaines dehors.** La file
   redevient faisable.
3. **Le tri par jeunes licenciés.** Le tennis, la gym, la natation et le
   basket valent, à eux quatre, 16 000 jeunes licenciés près de chez vous, et
   vous avez 39 fiches pour les quatre.
