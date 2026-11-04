# Cahier des charges — openAntennaValidation, comparateur d'antennes HF par RSSI et SNR

Nov 3, 2026 · @Kriss

## 1. Contexte et objectifs

openAntennaValidation compare les performances de réception de deux à dix antennes en mesurant, sur une même station et pendant une même période, le niveau reçu (RSSI), le plancher de bruit et le rapport signal sur bruit (SNR) des signaux présents dans une bande HF. Les campagnes durent de 1 minute à 1 mois. Les résultats sont consultables sur une page web locale et, si l'opérateur le souhaite, publiés sur un site WordPress commun à plusieurs opérateurs.

Objectifs mesurables :

- Mesurer l'écart (RSSI, plancher de bruit, SNR) de chaque antenne à une antenne de référence, et entre toutes les paires d'antennes, avec son intervalle de confiance, par fréquence, par classe de signal et par période (jour, crépuscule, nuit).
- Comparer jusqu'à 10 antennes en même temps (mode simultané), après un test de charge du PC et une estimation de l'espace disque nécessaire.
- Chiffrer, avant chaque campagne, l'erreur propre à la méthode (campagne de qualification) et ne conclure qu'au-delà de cette marge.
- Mettre en évidence les effets de propagation : une antenne meilleure de jour en NVIS peut être moins bonne la nuit en DX.
- Ramener les mesures au pied d'antenne en tenant compte des pertes de la chaîne (coaxial, balun, préampli…), sans jamais modifier la mesure brute.
- Publier sur WordPress des agrégats légers, filtrables par opérateur, bande et types d'antennes.

Principes directeurs :

- **Comparer, pas mesurer dans l'absolu** : l'écart A−B est fiable ; le niveau en dBm n'est qu'indicatif.
- **Le SNR compte autant que le RSSI** : en HF, le bruit extérieur domine souvent ; une antenne qui capte plus de signal et autant de bruit en plus n'est pas meilleure.
- **Chiffrer plutôt qu'interdire** : une limite de la méthode est mesurée, communiquée à l'opérateur et jointe aux résultats ; l'opérateur décide.
- **Ne jamais perdre la donnée brute** : mesures brutes, gain, matériel, configuration de l'installation et version des corrections sont stockés pour pouvoir tout recalculer.
- **Qualifier plutôt que supprimer** : une mesure douteuse reçoit un code qualité ; elle n'est pas effacée.
- **Pas de démodulation dans le cœur** : le programme repère lui-même les signaux par leur forme spectrale. Le décodage FT8/FT4/WSPR est une option (section 10).
- **Logiciel libre** (section 16).

### Vocabulaire

Pour éviter la confusion entre antennes A/B et modes, les deux modes de réception sont nommés :

| Terme | Signification |
| --- | --- |
| Antennes A, B, C… | Les antennes comparées, de 2 à 10 |
| Antenne de référence | Celle à laquelle les autres sont comparées ; A par défaut, choisie par l'opérateur et modifiable à l'affichage |
| Voie | Une antenne et sa chaîne de réception jusqu'au SDR (ou jusqu'à l'entrée du SDR en mode commuté) |
| **Mode commuté** | Un seul SDR à plusieurs entrées (2 sur le RSP2 Pro, 3 sur le RSPdx) ; on bascule rapidement entre les antennes |
| **Mode simultané** | De 2 à 10 SDR, un par antenne, étalonnés, à l'écoute de la même bande au même instant |
| **Fréquences imposées** | Mode de mesure : l'opérateur donne une ou plusieurs fréquences |
| **Bande automatique** | Mode de mesure : l'opérateur donne Fmin et Fmax, le programme choisit ses signaux |
| Campagne de mesure | Session de mesure de 1 min à 1 mois, sur une bande, avec une installation décrite |
| Campagne de qualification | Campagne « A/A » : une seule antenne sur toutes les voies, pour mesurer l'erreur de la méthode |
| Segment | Partie d'une campagne à configuration constante ; tout changement ouvre un nouveau segment |

Dans la suite, « A−B » désigne l'écart d'une antenne à l'antenne de référence. Tout ce qui est dit pour deux antennes vaut pour chaque antenne comparée.

## 2. Périmètre et phases

Le développement démarre sur un SDRplay RSP2 Pro (matériel de référence). La RTL-SDR Blog V4 et l'Airspy HF+ Discovery sont ajoutées ensuite. Chaque phase doit être livrable et testée avant la suivante.

| Phase | Contenu | Critère de fin |
| --- | --- | --- |
| P0 Maquettes | Système de design (couleurs, typographie, composants, thèmes clair et sombre), maquettes interactives des écrans clés sur données simulées | Maquettes validées par l'utilisateur : résultats de campagne, liste filtrée, rapport de qualification, campagne en cours |
| P1 Socle | Squelette, configuration, base SQLite, serveur web, authentification, couche matérielle avec pilote RSP2 Pro en HF, pilote simulé | La page liste le RSP2 Pro, affiche le spectre d'une bande HF et bascule entre les antennes A et B |
| P2 Qualification | Banc matériel (splitter, atténuateur), campagne A/A, profil matériel, rapport et verdict par classe de signal | Rapport de qualification produit pour le RSP2 Pro sur au moins deux bandes |
| P3 Mode commuté, fréquences imposées | Préparation de campagne (installation, gain), mesures sur fréquences imposées, campagne longue, bande calme, agrégats | Campagne de 24 h en mode commuté, reprise après coupure, agrégats corrects |
| P4 Bande automatique | Détection et classification des signaux, choix des points de mesure et des points de bruit, réévaluation | Campagne en bande automatique sur 40 m avec signaux retenus cohérents |
| P5 Mode simultané | 2 à 10 SDR, test de charge, estimation de l'espace disque, alignement temporel, étalonnage par qualification A/A, inversion d'antennes | Campagne simultanée avec deux RSP2 Pro, puis avec au moins 3 SDR ; biais résiduel mesuré ; test de charge qui refuse une configuration trop lourde (simulée) |
| P6 Résultats locaux | Pages de résultats complètes (section 12) pour 2 à 10 antennes, vue installation et vue antenne, propagation, export CSV | Une campagne de plusieurs jours à 2 antennes, puis une à 4 antennes simulées, sont lisibles et interprétables sur la page locale |
| P7 WordPress | Plugin multi-opérateurs, API d'ingestion, back-office, liste filtrée, page de campagne | Deux stations d'opérateurs différents publient, les filtres fonctionnent |
| P8 Autres SDR | Pilotes RTL-SDR Blog V4 et Airspy HF+ Discovery (mode simultané) | Qualification A/A et campagne simultanée réussies avec chacun |
| P9 Installeurs | Windows 10/11, Debian 12+, Raspberry Pi OS 64 bits | Installation complète sur machine vierge pour les trois systèmes |
| P10 Modes numériques | Décodage FT8, FT4, WSPR ; pistes par station ; mesures guidées par le décodage ; distance, azimut, classe de propagation | Campagne 40 m avec pistes validées, carte et écart par distance |
| Bonus | Générateur de bruit, signaux connus localisés, météo spatiale, synthèse multi-campagnes | Selon priorités |

Les installeurs Debian sont tenus à jour dès P1, la phase P9 les finalise et ajoute Windows.

Le modèle de données prévoit dès P1 les champs des phases ultérieures (segments, pistes, indicatif, locator, distance, classe de propagation), pour éviter toute refonte.

Hors périmètre de cette version :

- Bandes au-dessus de 30 MHz (l'architecture ne l'interdit pas).
- Campagne couvrant plusieurs bandes à la fois : une campagne reste dans la bande instantanée du SDR. Pour une antenne large bande, on fait une campagne par bande.
- Commutateur RF externe pour les SDR à une seule entrée : prévu par l'architecture, non développé.
- Décodage PSK31 et modes en texte libre.
- Émission.
- Enregistrement ou rediffusion audio.

## 3. Bandes de fréquences

Le plan de bandes est un fichier de configuration (IARU Région 1 par défaut), modifiable par l'opérateur. Valeurs par défaut :

| Bande | Limites par défaut | Remarque |
| --- | --- | --- |
| 80 m | 3,500 – 3,800 MHz | |
| 85 m | 3,400 – 3,500 MHz | 3,450 MHz ± 50 kHz |
| 60 m | 5,3515 – 5,3665 MHz | Variable selon les pays |
| 45 m | 6,610 – 6,710 MHz | 6,660 MHz ± 50 kHz |
| 40 m | 7,000 – 7,200 MHz | |
| 30 m | 10,100 – 10,150 MHz | |
| 20 m | 14,000 – 14,350 MHz | |
| 17 m | 18,068 – 18,168 MHz | |
| 15 m | 21,000 – 21,450 MHz | |
| 12 m | 24,890 – 24,990 MHz | |
| CB 11 m | 26,000 – 28,000 MHz | CB et bandes voisines |
| 10 m | 28,000 – 29,700 MHz | |

- L'opérateur choisit une bande, ou une plage Fmin–Fmax libre comprise entre 3 et 30 MHz.
- La plage doit tenir dans la bande instantanée utile du SDR choisi. Sinon le programme refuse et propose un découpage en plusieurs campagnes.
- Sous-bandes des modes numériques (section 10) : dans le même fichier, à vérifier avant P10.

## 4. Architecture et stack technique

Un service Python possède les SDR, conduit les campagnes, stocke les mesures, sert la page locale et pousse les agrégats vers WordPress. En mode simultané, chaque SDR est lu par un processus d'acquisition dédié, coordonné par le service.

Stack :

- **Python 3.11 ou plus récent**, code typé, `mypy --strict` sur le cœur.
- **FastAPI + Uvicorn** pour l'API HTTP et les WebSockets.
- **numpy / scipy** pour le traitement du signal.
- **Accès direct aux bibliothèques des fabricants par ctypes**, sans SoapySDR : API SDRplay 3.x, librtlsdr (branche RTL-SDR Blog), libairspyhf. C'est la décision D1 d'openAISReceiver, pour la même raison : SoapySDR est difficile à installer sous Windows.
- **SQLite** (mode WAL) pour la base locale.
- **Front-end** : HTML/CSS/JS sans framework ni étape de compilation ; **ECharts** pour les graphiques et **Leaflet** pour les cartes, embarqués dans le dépôt (pas de CDN, la page locale doit marcher hors ligne). Les fonds de carte OpenStreetMap, eux, sont chargés par le navigateur quand Internet est disponible.
- **WordPress** : plugin PHP 8.1+, tables dédiées, même front-end JS que la page locale.
- **pytest** pour les tests.

### Reprise d'openAISReceiver

Les modules suivants sont copiés puis adaptés. On extraira une bibliothèque commune quand les deux projets seront stables.

| Élément repris | Adaptation |
| --- | --- |
| Pilote SDRplay ctypes (`hardware/sdrplay`) | Tables LNA des bandes HF (0–12 MHz et 12–60 MHz), constante dBm HF, bascule A/B horodatée |
| Interface `SdrDriver`, pilote simulé, identité matérielle | Deux antennes, signaux HF simulés (porteuses, SSB, FT8, fading) |
| Stockage, migrations numérotées, journal des événements | Nouveau schéma |
| File d'envoi WordPress (base comme file, délai croissant, gzip, repli `?rest_route=`, quota) | Nouveau contrat |
| Authentification de la page, thèmes clair et sombre | Nouveau système de design (P0) |
| Conventions (`CLAUDE.md`, `decisions.md`) | Telles quelles |

### Modules du service

| Module | Rôle |
| --- | --- |
| `config` | Configuration TOML, validation, valeurs par défaut, plan de bandes, catalogue des câbles |
| `hardware` | Pilotes : détection, ouverture, fréquence, gains, choix d'antenne, lecture IQ |
| `acquisition` | Lecture continue des flux, horodatage, commutation d'antenne, détection de saturation |
| `dsp` | FFT, mesures de niveau et de bruit, détection et classification des signaux |
| `qualification` | Banc matériel, campagne A/A, profils matériels, verdicts |
| `campaign` | Machine à états des campagnes, planification des mesures, segments, bande calme |
| `installation` | Antennes, chaînes de réception, pertes en fonction de la fréquence, historique |
| `analysis` | Appariement, écarts, statistiques, classes solaires, résumé automatique |
| `digital` | (P10) Décodeurs, pistes par station, mesures guidées |
| `geo` | Locator Maidenhead, distance, azimut, élévation du soleil, angle de départ estimé |
| `storage` | Accès base, migrations |
| `export` | Agrégation et envoi vers WordPress, file d'attente |
| `web` | API REST, WebSockets, fichiers statiques, authentification |

### Arborescence attendue

```
openAntennaValidation/
  src/open_antenna_validation/   # modules ci-dessus
  web/                 # page locale (et sources du front-end partagé)
  wordpress/open-antenna-validation/
  tests/
  install/windows/
  install/linux/
  config/default.toml
  docs/
  pyproject.toml
  README.md
  CLAUDE.md
```

## 5. Matériel SDR et couche d'abstraction

Toute la logique métier passe par une interface matérielle unique ; seul un pilote connaît les spécificités d'un matériel.

### Interface `SdrDriver`

- `detect()` : liste des appareils (type, modèle, numéro de série).
- `open(serial)` / `close()`.
- `set_frequency(hz)`, `set_sample_rate(sps)`, `set_ppm(ppm)`, `set_bias_tee(bool)`.
- `gain_steps()`, `set_gain_step(index)`, `get_gain_state()` : l'AGC est **toujours** désactivée.
- `antennas()` : liste des entrées utilisables pour le mode commuté (vide si une seule).
- `set_antenna(name)` : bascule ; le pilote rend l'heure de la commande, l'heure de l'effet est mesurée dans le flux (section 6).
- `read_iq()` : blocs `complex64` avec un compteur d'échantillons, pour détecter les pertes.
- `instant_bandwidth()` : bande utile sans repliement, pour la fréquence d'échantillonnage choisie.

### Matériels visés

| Matériel | Mode commuté | Mode simultané | Bande utile indicative | Remarques |
| --- | --- | --- | --- | --- |
| **SDRplay RSP2 Pro** (référence) | ✅ entrées A et B | ✅ deux appareils | plusieurs MHz | Port Hi-Z non utilisé (chemin d'entrée différent) |
| RTL-SDR Blog V4 | ❌ | ✅ deux clés | ~2,4 MHz | 8 bits : dynamique limitée en HF ; numéro de série unique obligatoire |
| Airspy HF+ Discovery | ❌ | ✅ deux appareils | ~660 kHz (à vérifier) | Excellent en HF ; 10 m et CB en plusieurs campagnes |

Les autres modèles SDRplay déjà gérés par le pilote d'openAISReceiver (RSP1A, RSP1B, RSPdx, RSPduo en tuner unique) restent possibles mais ne sont pas testés. Le RSPdx a trois entrées et peut faire le mode commuté sur trois antennes.

### Nombre de SDR (mode simultané)

- De 2 à 10 SDR, un par antenne. La limite de 10 est réglable ; au-delà, l'interface et les graphiques ne sont pas garantis.
- Le nombre de SDR n'est limité que par la machine : le test de charge (section 8) décide.
- Des **modèles identiques** sont recommandés. Des modèles mélangés sont acceptés, mais la campagne est marquée « hétérogène » : dynamique, filtres d'entrée et plancher propre diffèrent, et seule la qualification A/A en chiffre l'effet.
- Chaque SDR doit être identifiable par son numéro de série (obligatoire pour les RTL-SDR, à programmer avec `rtl_eeprom`).
- Contraintes USB documentées : débit partagé par contrôleur, hubs alimentés, alimentation des clés. La fréquence d'échantillonnage est la plus basse qui couvre la plage, avec décimation matérielle, pour limiter le débit.
- Combiner les deux modes (plusieurs SDR qui commutent chacun leurs entrées) n'est pas prévu.

### Réglages imposés pendant une campagne

- Gain fixe, AGC désactivée, sur toute la campagne. Un changement de gain ouvre un nouveau segment.
- En mode commuté, les deux antennes partagent le même gain : il est choisi pour l'antenne la plus forte.
- Fréquence centrale décalée pour écarter le pic DC des signaux mesurés.
- Taux d'écrêtage surveillé en continu ; les mesures prises pendant un écrêtage reçoivent le code SATURATION.

### Choix du gain (préparation de campagne)

Mesure courte sur les deux antennes, puis gain proposé :

- signal le plus fort au moins X dB sous la pleine échelle (réglable) ;
- plancher de bruit d'antenne au moins Y dB au-dessus du plancher propre du SDR (mesuré à la qualification) quand c'est possible, pour que le bruit extérieur domine.

L'opérateur valide ou modifie.

### Identité du matériel

Chaque mesure est rattachée à un identifiant matériel : type, modèle, numéro de série, entrée, mode de gain. Les profils de qualification et les étalonnages sont liés à cette identité.

### Points à vérifier dans la documentation (sans rien supposer)

- API SDRplay : nombre d'appareils ouvrables par un même processus ; instant d'application d'une bascule d'antenne dans le flux.
- RTL-SDR V4 : mode HF (convertisseur intégré), plage de gain et comportement sous 30 MHz.
- Airspy HF+ : fréquences d'échantillonnage et bande utile réelle.

## 6. Qualification

La qualification mesure l'erreur propre à la méthode avant une campagne. Elle sert aux deux modes : en mode commuté, elle caractérise la commutation ; en mode simultané, elle constitue l'étalonnage. Elle se fait avec **une seule antenne (ou une source) branchée sur toutes les voies par un splitter** (2 à 10 sorties).

Un splitter à N sorties perd au moins 10·log10(N) dB (environ 9 dB pour 8 voies, plus ses pertes propres) : le SNR baisse d'autant. Avec beaucoup de voies, le générateur de bruit est recommandé, ou une antenne qui reçoit des signaux forts. En mode simultané, chaque SDR est étalonné par rapport au SDR de la voie de référence.

### Niveau 1 : banc matériel (quelques minutes, mode commuté)

Source : générateur de bruit large bande (option recommandée) ou antenne.

1. **Splitter symétrique** :
   - déséquilibre des voies A/B en fonction de la fréquence ;
   - le même test, câbles inversés, retire le déséquilibre du splitter ;
   - parasite à la commutation (amplitude, durée) ;
   - continuité du flux (échantillons perdus, redémarrage).
2. **Atténuateur connu (par exemple 10 dB) sur une branche** : la bascule devient une marche visible dans le flux. On mesure :
   - la latence entre la commande et l'effet, et sa gigue ;
   - le temps d'établissement à ±0,2 dB (réglable) ;
   - la justesse : l'écart mesuré doit égaler l'atténuation.
3. **Isolation** (option) : signal sur une entrée, charge 50 Ω sur l'autre ; fuite de l'entrée inactive.
4. **Plancher propre du SDR** : entrées sur charge 50 Ω, au gain de la campagne.
5. (P10) **Tolérance du décodeur** : taux de décodage FT8 avec et sans commutation rapide, sur le même signal.

À chaque bascule, les échantillons sont **neutralisés** pendant latence maximale + temps d'établissement. La **fraction utile** se déduit de ce temps et de la cadence de commutation.

### Niveau 2 : campagne A/A en réception réelle (1 h à 24 h)

Même bande, même gain, mêmes réglages que la future campagne, une seule antenne sur les deux voies. L'écart vrai est nul : tout écart mesuré est l'erreur de la méthode.

Résultats :

- biais résiduel, par fréquence (il devient une correction) ;
- dispersion par classe de signal (porteuse/CW, numérique étroit, SSB, AM/FM) et par durée de fenêtre ;
- **plus petite différence détectable** par classe et par fenêtre ;
- en mode simultané : table d'étalonnage en fonction de la fréquence, dérive dans le temps, décalage temporel entre les deux flux.

### Profil matériel et verdict

Le profil enregistré contient : identité, bande, gain, cadence de commutation, neutralisation, fraction utile, isolation, plancher propre, table de correction, marges par classe de signal.

Verdict par classe de signal, jamais global :

| Verdict | Condition (seuils réglables) | Effet |
| --- | --- | --- |
| ✅ Exploitable | Marge inférieure au seuil, fraction utile suffisante | Classe incluse dans les résultats |
| ⚠️ Dégradé | Marge plus large ou fraction utile réduite | Incluse, avec la marge affichée |
| ❌ Inexploitable | Neutralisation trop longue pour la classe, ou marge excessive | Exclue par défaut ; réaffichable |

Exemple de rapport :

> Mode commuté — RSP2 Pro — 40 m
> ✅ Porteuses / CW / numériques : marge ±0,3 dB
> ⚠️ SSB : marge ±1,8 dB, 62 % du temps utile
> ❌ Décodage FT8 avec commutation rapide : 41 % de décodages perdus → commutation par créneau proposée

### Règles

- L'opérateur lit le rapport et **accepte explicitement** les limites avant de lancer la campagne. Il peut aussi changer ses réglages, ou lancer la campagne sans qualification : elle est alors marquée « non qualifiée ».
- La campagne est liée à son rapport, visible localement et sur WordPress.
- Le programme propose une nouvelle qualification si l'identité du matériel, la bande, le gain ou la cadence changent, ou après un délai réglable (6 mois par défaut).
- Les impossibilités connues sont affichées sans test : pas de mode commuté pour un SDR à une entrée, plage plus large que la bande utile.

## 7. Installation de l'opérateur

La description de l'installation est obligatoire pour lancer une campagne : sans elle, les résultats ne sont pas interprétables.

### Antennes (catalogue de l'opérateur, réutilisé d'une campagne à l'autre)

- **Type**, pris dans une liste fermée et extensible par l'administrateur WordPress : dipôle, V inversé, verticale ¼ λ, verticale avec radians, EFHW, long fil, delta loop, quad, boucle magnétique, Yagi, Moxon, boucle active, mini-whip, Beverage, autre.
- Modèle ou description libre, marque, bandes prévues.
- Polarisation (horizontale, verticale, mixte).
- **Hauteur** au-dessus du sol (m). C'est une métadonnée : la hauteur change le diagramme de rayonnement, elle ne se corrige pas.
- **Azimut** de l'axe principal (degrés vrais) pour une antenne directive, et orientation pour un dipôle.
- Photo (option).

### Chaîne de réception (une par antenne, de l'antenne au SDR)

Liste ordonnée de maillons, chacun avec sa perte ou son gain en dB :

- **Coaxial** : type pris dans un catalogue (RG58, RG213, H155, LMR400, Aircell 7…) et longueur. La perte est interpolée à la fréquence de mesure à partir des valeurs du catalogue. L'opérateur peut aussi saisir directement une perte mesurée par bande.
- Balun ou unun, self d'arrêt, parafoudre, filtre, relais : perte saisie par bande, valeur par défaut proposée.
- Préampli : gain saisi par bande.

### Corrections et vues

- **Vue « installation »** : mesures telles que reçues par le SDR.
- **Vue « antenne »** : RSSI corrigé des pertes et gains de la chaîne, ramené au pied d'antenne.
- **Le SNR n'est jamais corrigé.** Un indicateur « bruit extérieur dominant » compare le plancher mesuré au plancher propre du SDR : quand le bruit extérieur domine, une perte de coaxial baisse autant le signal que le bruit.
- Les corrections sont calculées, jamais écrites dans les mesures brutes ; une chaîne corrigée après coup se réapplique à toute la campagne.

### Station

- Indicatif de l'opérateur, locator (6 caractères en local), position précise optionnelle.
- Sur WordPress, locator tronqué à 4 ou 6 caractères au choix de l'opérateur.

### Changements en cours de campagne

Tout changement (rotation d'une Yagi, câble, gain) est saisi avec son heure et ouvre un **segment**. Les graphiques séparent les segments, et les statistiques sont calculées par segment.

### Couplage entre antennes

Des antennes proches s'influencent mutuellement : diagramme, impédance, bruit capté. Les résultats décrivent donc chaque antenne **dans cette installation**, pas l'antenne seule. L'opérateur saisit la distance approximative entre les antennes. Le résumé de la campagne rappelle cette limite, surtout au-delà de deux antennes, ou quand deux antennes sont à moins d'une demi-longueur d'onde l'une de l'autre.

## 8. Campagnes de mesure

### Paramètres choisis par l'opérateur

- Mode de réception : commuté ou simultané.
- Mode de mesure : fréquences imposées ou bande automatique.
- Bande ou plage Fmin–Fmax, dans la bande utile du SDR.
- Durée : de 1 minute à 1 mois. Fin programmée ou arrêt manuel.
- Cadence : automatique, ou imposée par l'opérateur.
- Antennes (2 à 10, prises dans le catalogue), antenne de référence, installation, profil de qualification.
- Visibilité sur WordPress : publique, non listée, privée, ou pas d'envoi.

### Test de charge et espace disque (avant lancement)

Avant de lancer la campagne, le programme fait tourner **toute la chaîne** pendant quelques minutes (réglable, 3 min par défaut), avec les réglages réels : tous les SDR, même fréquence d'échantillonnage, mêmes traitements, décodage compris.

Mesures :

- charge processeur, par cœur et au total ;
- marge temps réel : temps de traitement d'un bloc rapporté à sa durée ;
- échantillons perdus par SDR (compteurs d'échantillons), erreurs USB ;
- mémoire ;
- débit d'écriture sur le disque ;
- alignement temporel entre voies (mode simultané).

Verdict (seuils réglables) :

| Verdict | Condition par défaut | Effet |
| --- | --- | --- |
| ✅ OK | Aucune perte, charge < 70 %, marge temps réel suffisante | Lancement possible |
| ⚠️ Limite | Charge entre 70 et 85 %, ou pertes rares | Lancement possible après acceptation ; propositions de réglages moins lourds |
| ❌ Refus | Pertes répétées, charge > 85 %, ou marge temps réel insuffisante | Lancement refusé ; propositions de réglages moins lourds |

Réglages moins lourds proposés : plage plus étroite (fréquence d'échantillonnage réduite), moins de signaux suivis, décodage désactivé, moins de SDR.

**Espace disque** :

- Le programme estime le volume de la campagne (mesures brutes, agrégats, décodages, journaux) à partir du nombre de voies, de la cadence, du nombre de signaux et de la durée. Il l'affiche avant le lancement.
- Le lancement est **refusé** si l'espace libre est inférieur à l'estimation plus une marge (20 % et au moins 1 Go, réglables).
- Pendant la campagne, l'espace libre est surveillé. Sous un seuil d'alerte, l'opérateur est prévenu. Sous un seuil critique, la campagne passe en INTERROMPUE (motif « disque plein ») sans perte des données déjà écrites, et reprend quand de la place est libérée.

Le **volume prévisionnel sur WordPress** est affiché aussi (section 13).

### Fréquences imposées

- Une ou plusieurs fréquences, toutes dans la bande instantanée : elles sont mesurées **en même temps** dans la même FFT, sans réaccord.
- La « vitesse de scan » devient la cadence de mesure et, en mode commuté, la cadence de commutation.
- Largeur de mesure : déterminée automatiquement par la classe du signal détecté à cette fréquence (section 9), ou imposée.

### Bande automatique

1. Relevé de découverte de la plage.
2. Détection et classification des signaux (section 9), choix de N signaux (N réglable) et de points de bruit voisins.
3. Réévaluation périodique (toutes les heures par défaut) : un signal disparu est remplacé, un nouveau signal stable est ajouté.

### Stratégies de commutation (mode commuté)

| Stratégie | Cadence | Usage |
| --- | --- | --- |
| Rapide | 10 à 50 Hz (réglable, bornée par le profil) | Mesures spectrales, SSB, signaux variables |
| Par créneau | Bascule entre deux créneaux, jamais pendant (section 10) | Décodage FT8/FT4/WSPR intact |

La séquence d'antennes est **équilibrée** (autant de A que de B). Par créneau, elle est **pseudo-aléatoire**, pour qu'un émetteur à rythme régulier ne tombe pas toujours sur la même antenne.

### Bande calme

Machine à états de l'activité :

- **ACTIVE** : mesures complètes.
- **CALME** : aucun signal pertinent au-dessus du plancher + X dB pendant N minutes. On sonde la bande toutes les N minutes (réglable), et on garde **le plancher de bruit A et B à faible cadence**. La comparaison des bruits reste utile : directivité et polarisation changent le QRM capté.
- Retour à ACTIVE dès qu'un signal pertinent revient.

Les périodes calmes sont enregistrées et grisées sur les graphiques.

### États d'une campagne

| État | Signification |
| --- | --- |
| PRÉPARATION | Saisie, choix du gain, contrôle du profil de qualification |
| EN COURS | Mesures (sous-état ACTIVE ou CALME) |
| INTERROMPUE | Coupure de courant, SDR débranché, erreur : reprise automatique dès que possible, trou enregistré |
| TERMINÉE | Durée atteinte ou arrêt manuel |
| ABANDONNÉE | Arrêt par l'opérateur avant la fin, données conservées |

Une campagne reprend après un redémarrage du service ou de la machine, tant que sa date de fin n'est pas passée.

## 9. Mesures et traitement du signal

### Conventions

- 0 dBFS = sinusoïde complexe d'amplitude 1 (comme openAISReceiver, D7).
- Niveau d'un signal : puissance intégrée sur sa largeur de mesure, corrigée de la bande équivalente de bruit de la fenêtre.
- Plancher : médiane des cases FFT libres voisines, ramenée à la même largeur.
- SNR : rapport (signal + bruit) / bruit dans la même largeur, et SNR ramené à 2 500 Hz pour comparaison avec les décodeurs.
- dBm : affichage seulement, d'après le gain et une constante estimée par modèle de SDR (incertitude affichée).

### Détection et classification des signaux

Sans démodulation, d'après la forme spectrale, la stabilité de la fréquence et la persistance :

| Classe | Signature | Largeur de mesure | Mesure retenue |
| --- | --- | --- | --- |
| Porteuse / CW | Raie étroite | ~50–200 Hz | Niveau par fenêtre |
| Numérique étroit (FT8, FT4, WSPR…) | ~6–100 Hz, enveloppe constante, durée de quelques secondes à 2 min | Largeur occupée | Niveau pendant la transmission |
| SSB | ~2,4–2,8 kHz, asymétrique, intermittente | ~2,7 kHz | Percentiles appariés (p50, p90), pas la moyenne |
| AM / FM | 6–10 kHz | Largeur occupée | Porteuse si elle existe, sinon percentiles |
| Bruit | Cases libres voisines | Celle du signal voisin | Plancher, base du SNR |

- Largeur de mesure : largeur occupée à −X dB, bornée par la classe.
- Rejet des signaux qui se chevauchent ou dérivent hors de leur fenêtre.
- En bande automatique, ordre de priorité : enveloppe constante, puis stabilité, puis SNR. Les classes jugées inexploitables par la qualification sont écartées.

### Appariement des mesures A et B

- Mode commuté, commutation rapide : paires adjacentes A puis B, échantillons neutralisés exclus.
- Mode commuté, par créneau : méthode du pivot (B comparé à la moyenne des A qui l'encadrent), qui annule une dérive lente (D11 d'openAISReceiver).
- Mode simultané : même instant, après alignement des flux. Le décalage temporel est estimé par **corrélation des enveloppes de puissance** d'un signal commun, et doit rester inférieur à X ms (réglable) ; au-delà, les mesures SSB sont exclues.

### Codes qualité

| Code | Condition | Effet |
| --- | --- | --- |
| OK | Aucune anomalie | Entre dans les statistiques |
| SATURATION | Écrêtage pendant la mesure | Exclue |
| NEUTRALISÉE | Pendant le temps de neutralisation après bascule | Non mesurée |
| CHEVAUCHEMENT | Autre signal dans la largeur de mesure | Exclue |
| PERTE_FLUX | Échantillons perdus dans la fenêtre | Exclue |
| DÉSALIGNÉE | Mode simultané, alignement temporel hors tolérance | Exclue pour la SSB |
| CLASSE_EXCLUE | Classe inexploitable selon la qualification | Exclue par défaut, réaffichable |

### Agrégats

Fenêtre de base fixe, choisie selon la durée de la campagne (valeurs réglables) :

| Durée de campagne | Fenêtre |
| --- | --- |
| ≤ 1 h | 10 s |
| ≤ 24 h | 1 min |
| ≤ 7 jours | 5 min |
| > 7 jours | 15 min |

Par signal (ou point de bruit), par antenne et par fenêtre : nombre de mesures, médiane, p10, p90, min, max, écart-type, pour le RSSI, le plancher et le SNR, ainsi que les mêmes statistiques pour l'écart apparié de chaque antenne à la référence. Les écarts entre toutes les autres paires (jusqu'à 45 pour 10 antennes) sont calculés en local à partir des mesures appariées : les statistiques d'une différence ne se déduisent pas de celles de chaque antenne. Le regroupement en pas plus larges se fait à l'affichage. Les percentiles conservent la trace des fortes variations.

### Statistiques

- Écart A−B : médiane et moyenne, avec intervalle de confiance à 95 % calculé par **bootstrap par blocs** (les mesures successives sont corrélées ; un calcul naïf sous-estimerait l'incertitude).
- Pourcentage du temps où A est meilleure que B.
- Une conclusion n'est formulée que si l'écart dépasse la marge de qualification **et** que l'intervalle de confiance exclut zéro.
- Au-delà de deux antennes :
  - matrice des écarts entre toutes les paires ;
  - classement des antennes, par période et par classe solaire ;
  - correction des comparaisons multiples (méthode de Holm) avant toute conclusion. Avec 45 paires, quelques écarts paraîtraient significatifs par hasard.

## 10. Modes numériques (P10, option)

Sur les seules sous-bandes numériques amateur, le décodage FT8, FT4 et WSPR identifie les émetteurs, ce qui donne leur distance et leur azimut. Il ne remplace pas la détection spectrale ; il est sans objet sur la CB, le 45 m et le 85 m.

### Décodeurs

- **ft8_lib** (licence MIT) pour FT8 et FT4, **wsprd** de WSJT-X (licence GPLv3) pour WSPR. Interfaces exactes à vérifier avant P10 ; aucun décodeur n'est réécrit.
- Chaque antenne (ou chaque voie) est démodulée en BLU supérieure dans un flux audio à 12 kHz autour de la fréquence de la sous-bande, puis décodée à la fin de chaque créneau (15 s en FT8, 7,5 s en FT4, 2 min en WSPR).

### Données extraites

Indicatif, locator (messages CQ et réponses avec locator en FT8 ; toutes les trames WSPR), puissance d'émission (WSPR), SNR du décodeur, décalage audio, créneau.

- Le locator d'une station est conservé pendant la campagne et appliqué à ses messages suivants.
- À défaut de locator, le préfixe de l'indicatif donne le pays (fichier `cty.dat`), donc une distance approximative marquée comme telle.

### Pistes par station

Machine à états par indicatif :

| État | Condition |
| --- | --- |
| CANDIDATE | Premier décodage |
| SUIVIE | Décodages cohérents : même indicatif, même parité de créneau, décalage audio dans la tolérance (réglable) |
| VALIDÉE | Fin de QSO (RR73, 73) ou silence de N créneaux, avec au moins K mesures : les mesures provisoires sont conservées |
| REJETÉE | Anomalie : les mesures provisoires sont écartées, le motif est enregistré |

- Motifs de rejet :
  - collision (deux indicatifs à la même fréquence et dans la même parité) ;
  - saut de fréquence, changement de parité ;
  - indicatif haché non résolu ;
  - signal qui chevauche à moins de 50 Hz.
- Un locator différent pour un même indicatif (station portable) ne fait pas rejeter la piste : il est signalé.
- Une piste peut couvrir plusieurs QSO successifs de la même station.
- Le rapport de campagne compte les pistes validées et rejetées par motif.

### Commutation par créneau (mode commuté)

- On bascule **entre deux créneaux, jamais pendant** : chaque transmission est reçue entièrement sur une seule antenne, le décodage reste intact.
- En FT8, la bascule a lieu **tous les deux créneaux (30 s)**, pour qu'une station qui émet un créneau sur deux soit reçue alternativement sur A et sur B. Une bascule toutes les 15 s ne mesurerait les stations paires que sur une seule antenne.
- En WSPR, le choix de l'antenne par créneau de 2 min suit une séquence pseudo-aléatoire équilibrée.
- Écart estimé par la méthode du pivot.

### Mesures guidées par le décodage

Le message décodé est réencodé pour connaître le ton de chaque symbole. On mesure alors :

- la puissance sur le ton émis, symbole par symbole ;
- le bruit sur les tons voisins non émis, au même instant.

Ce RSSI reste précis près du seuil de décodage et peu sensible au QRM. En mode simultané, un message décodé sur une seule antenne est mesuré aussi sur l'autre voie, aux mêmes tons et aux mêmes instants : l'écart reste mesurable quand l'une des antennes ne décode pas.

### Classes de propagation

À partir de la distance (seuils réglables, valeurs par défaut indicatives) :

| Classe | Distance par défaut |
| --- | --- |
| Onde de sol | < 30 à 80 km selon la bande (ambiguë avec le NVIS) |
| NVIS | 0 à 400 km |
| Zone de silence | 400 à 1 000 km |
| DX en un bond | 1 000 à 3 800 km |
| DX en plusieurs bonds | > 3 800 km |

**Angle de départ estimé**, pour un seul bond, avec une hauteur de couche F2 réglable (300 km par défaut) :

- θ = d / (2R) ;
- élévation = arctan[(cos θ − R/(R+h)) / sin θ].

Avec R = 6 371 km, il n'est pas calculé au-delà de la portée d'un bond. Il est présenté comme une estimation.

### Confidentialité

Indicatifs et locators sont émis publiquement (et déjà publiés par PSK Reporter et WSPRnet) : ils sont affichés tels quels. Ce choix sera consigné dans `decisions.md`.

## 11. Propagation et contexte

- **Élévation du soleil** à la station, calculée depuis son locator (algorithme de la NOAA, sans service extérieur). Trois classes, seuils réglables :
  - jour : élévation > +10° ;
  - crépuscule / ligne grise : entre −6° et +10° ;
  - nuit : < −6°.
- **Heure solaire locale**, pour superposer les jours d'une longue campagne.
- (Bonus) **Signaux connus localisés** : un catalogue, local ou partagé sur WordPress, associe une fréquence à un émetteur et à sa position (balise, radiodiffusion, station habituelle du 45 m). Ces signaux reçoivent une distance, donc une classe de propagation, même sans décodage.
- (Bonus) **Météo spatiale** : indices Kp et flux solaire, récupérés une fois par jour par le plugin WordPress (source publique de la NOAA SWPC, à confirmer) et par la station si elle a Internet.

## 12. Interface web et présentation des résultats

Un même front-end JS sert la page locale et les pages WordPress. Il lit une API JSON au format identique. Les maquettes de P0 fixent le visuel avant tout développement.

### Exigences de design

- Système de design propre au projet : couleurs, typographie, espacements, composants (cartes de résumé, badges de verdict, filtres, tableaux), thèmes clair et sombre.
- Sur WordPress, CSS cloisonné : le thème du site ne doit pas abîmer les pages du plugin, ni l'inverse.
- Ordinateur et mobile.
- Interface traduisible : français d'abord, anglais prévu.

### Vues de la page locale

| Vue | Contenu |
| --- | --- |
| Tableau de bord | Campagne en cours, état (ACTIVE, CALME…), progression, SDR, gain, alertes, dernières mesures A/B |
| Spectre | Spectre en direct des deux voies, signaux retenus et leur classe, points de bruit |
| Installation | Catalogue d'antennes, chaînes de réception, station |
| Qualification | Lancement du banc et de la campagne A/A, rapport, historique des profils |
| Nouvelle campagne | Assistant : modes, bande, durée, antennes, gain, profil, acceptation des limites |
| Campagnes | Liste, filtres, accès aux résultats, export CSV, renvoi vers WordPress |
| Réglages | Matériel, WordPress (URL, jeton, test), seuils, plan de bandes, mot de passe |
| Journal | Événements et erreurs, filtrables |

### Page de résultats d'une campagne

**Résumé** :

- dates, durée réelle, bande, modes, matériel, gain ;
- antennes A et B : type, hauteur, azimut, polarisation, chaîne de réception ;
- profil de qualification et marges, ou mention « non qualifiée » ;
- écart A−B médian avec intervalle de confiance, pour le RSSI, le bruit et le SNR ;
- pourcentage du temps où A est meilleure ;
- part du temps en bande calme ;
- résumé rédigé automatiquement, uniquement sur les écarts significatifs. Par exemple : « Sur 40 m, A est meilleure de jour de +3,2 dB ±0,4 ; B est meilleure de nuit de +2,1 dB ±0,6. Bascule vers 18 h 40 locale. »

**Graphiques** :

1. Séries temporelles A et B (RSSI, plancher, SNR) avec fond jour / crépuscule / nuit, périodes calmes grisées et limites de segments.
2. Écart A−B dans le temps, avec une bande grise ±marge de qualification.
3. **Profil sur 24 h** : écart A−B en fonction de l'heure solaire locale, tous les jours superposés (médiane et bande p10–p90).
4. Boîtes à moustaches de l'écart par classe solaire.
5. Carte de chaleur jour × heure de l'écart.
6. Écart moyen par fréquence ou par signal, avec barres d'erreur.
7. Histogramme ou fonction de répartition de l'écart.
8. Nuage de points A contre B.
9. Activité et plancher de bruit par heure.
10. (P10) Carte des stations décodées colorées par écart.
11. (P10) Écart en fonction de la distance, zones de propagation en fond.
12. (P10) Écart en fonction de l'angle de départ estimé.
13. (P10) Diagramme polaire de l'écart selon l'azimut, avec l'orientation des antennes directives.
14. (P10) Stations décodées par chaque antenne seule et par chaque combinaison : diagramme de Venn à 2 ou 3 antennes, diagramme UpSet au-delà ; tableau par station.
15. (Plus de 2 antennes) Matrice des écarts entre paires, avec intervalle de confiance et significativité.
16. (Plus de 2 antennes) Classement des antennes heure par heure (meilleure antenne par heure solaire), et classement global par classe solaire.

### Affichage de 3 à 10 antennes

Les graphiques doivent rester lisibles jusqu'à 10 antennes :

- **Une couleur fixe par antenne** dans tous les graphiques et toutes les pages, prise dans une palette catégorielle de **8 couleurs** validée pour les daltoniens en thème clair et en thème sombre. Le marqueur (cercle, carré, triangle, losange…) double toujours la couleur. Les antennes 9 et 10 reprennent les couleurs 1 et 2 avec un trait pointillé et un marqueur différent ; une palette plus large ne resterait pas distinguable.
- **Légende interactive** : afficher ou masquer une antenne ; survol d'une antenne qui atténue les autres.
- **Choix de l'antenne de référence** à l'affichage : tous les écarts sont recalculés par rapport à elle.
- **Mode « focus »** : une antenne choisie contre la référence, les autres en gris clair.
- **Petits multiples** : une vignette par antenne, à échelles identiques, quand la superposition devient illisible.
- Boîtes à moustaches et profils sur 24 h groupés par antenne ; diagramme polaire avec une courbe par antenne.
- Les maquettes de P0 incluent un cas à 2 antennes et un cas à 6 antennes.

**Commandes** :

- vue installation ou vue antenne ; RSSI, bruit ou SNR ;
- filtres par classe solaire, classe de signal, classe de propagation et segment ;
- zoom temporel ; réaffichage des classes exclues.

**Qualité des données** : taux de mesures exclues par code, interruptions, pistes rejetées par motif.

### Communication et sécurité

- API REST pour la configuration et les commandes, WebSockets pour le direct.
- Mot de passe haché (argon2), session par cookie, écoute sur le réseau local, Internet déconseillé sans VPN (comme openAISReceiver).

## 13. Plugin WordPress multi-opérateurs

Le plugin reçoit les agrégats de plusieurs stations et les présente au public. WordPress n'accède jamais aux stations. Il doit fonctionner sur un hébergement mutualisé OVH.

### Opérateurs et stations

- Rôle WordPress « Opérateur » : l'administrateur valide chaque nouvel opérateur.
- Un opérateur gère :
  - ses stations, chacune avec son jeton ;
  - son catalogue d'antennes, synchronisé depuis ses stations ;
  - ses campagnes : visibilité, masquage, titre, commentaire.
- L'administrateur peut masquer ou supprimer toute campagne, gérer les opérateurs et les types d'antennes, et fixer le quota de stockage.

### API d'ingestion

- `POST /wp-json/open-antenna-validation/v1/ingest`, avec repli automatique sur `?rest_route=`. Authentification par jeton propre à la station, envoyé aussi dans un en-tête dédié.
- Lots compressés en gzip, envoyés en `Content-Type: application/gzip` (constat D14 d'openAISReceiver), taille limitée par requête.
- Contenu :
  - campagne : métadonnées, installation, segments, profil de qualification résumé ;
  - agrégats par fenêtre ;
  - résumés par piste validée (P10) ;
  - état de la station (heartbeat).
- Aucune mesure brute, aucun IQ, aucune position précise.
- Idempotence : chaque agrégat porte un identifiant unique (station, campagne, signal, fenêtre).
- File d'attente locale : nouvel essai à délai croissant, sans perte après une coupure réseau de plusieurs jours.
- Contrat décrit par des schémas JSON livrés dans le dépôt, validés par un serveur factice dans les tests.

### Stockage et protection du site

- Tables dédiées créées par `dbDelta`, jamais de métadonnées d'articles.
- Quota sur les tables du plugin (200 Mo par défaut), refus 507 au-delà ; la station suspend ses envois sans perdre de données.
- **Taille maximale du paquet de données d'une campagne**, réglée par l'administrateur (par exemple 5 Mo) :
  - la station lit cette limite sur le site avant le lancement (endpoint dédié) et affiche le volume prévisionnel ;
  - si la limite risque d'être dépassée, la station **adapte ce qu'elle envoie** : fenêtre d'agrégation plus large pour WordPress (la base locale garde sa fenêtre), puis écarts entre paires résumés par classe solaire au lieu d'être envoyés par fenêtre. L'opérateur voit la résolution qui sera publiée ;
  - le plugin refuse un lot qui ferait dépasser la limite (code 413) ; la station s'arrête d'envoyer cette campagne et le signale ;
  - **le nombre d'antennes n'est jamais un motif de refus**.
- Pas de calcul lourd côté WordPress : statistiques et résumés sont calculés par la station.

### Pages publiques

- **Liste des campagnes** (shortcode ou bloc). Filtres :
  - opérateur ;
  - bande ;
  - **paire de types d'antennes, non ordonnée** ;
  - mode de réception, modèle de SDR, période, campagnes qualifiées seulement.
- **Page de campagne** : même présentation que la page locale (section 12).
- **État des stations** : dernier envoi, campagne en cours, état.
- (Bonus) **Synthèse multi-campagnes** : écart médian entre deux types d'antennes sur une bande, toutes campagnes confondues.

### Campagne de démonstration

Tant qu'aucune vraie campagne n'est publiée, le site doit montrer à quoi ressemblent les résultats :

- Le plugin embarque une ou plusieurs **campagnes fictives**, construites comme les maquettes de P0 : 40 m sur 5 jours, avec bascule NVIS de jour et DX de nuit, à 2 et à 6 antennes. Elles passent par le même format de données et les mêmes pages qu'une vraie campagne.
- Elles sont **marquées partout comme exemples**, dans la liste, sur la page de campagne et dans les filtres : bandeau « Campagne de démonstration, données simulées », opérateur fictif, aucune ressemblance avec un indicatif réel.
- Elles sont affichées automatiquement quand aucune vraie campagne n'est visible, et **disparaissent dès la première vraie campagne publiée**. L'administrateur peut aussi les afficher en permanence (page d'aide) ou les désactiver.
- Elles n'entrent jamais dans les statistiques, les compteurs ni la synthèse multi-campagnes.
- La page locale de la station propose la même démonstration tant qu'elle n'a aucune campagne enregistrée.

### Back-office

- Liste des campagnes : masquer, supprimer, renommer, annoter, changer la visibilité.
- Opérateurs, stations, jetons ; types d'antennes ; catalogue partagé des signaux connus (bonus) ; quota et stockage.
- Export CSV.

## 14. Modèle de données

Schéma SQLite versionné par migrations numérotées. Tables principales :

| Table | Contenu principal |
| --- | --- |
| `hardware` | Identité : type, modèle, numéro de série, entrée, mode de gain |
| `antennas` | Catalogue : type, modèle, polarisation, hauteur, azimut, photo |
| `chains` / `chain_elements` | Chaîne de réception par antenne : maillons ordonnés, câble et longueur, pertes ou gains par bande |
| `cable_catalog` | Types de câble et pertes de référence en fonction de la fréquence |
| `qualification_runs` | Banc ou A/A : matériel, bande, gain, cadence, résultats, verdicts, acceptation |
| `hardware_profiles` | Profil retenu : neutralisation, fraction utile, isolation, plancher propre, marges, table de correction |
| `campaigns` | Paramètres, états, opérateur, visibilité, profil lié, dates, nombre de voies, voie de référence, résultats du test de charge, estimation du volume |
| `campaign_channels` | Voies de la campagne : index (0 à 9), antenne, chaîne, SDR (identité) ou entrée du SDR, couleur d'affichage |
| `segments` | Campagne, début, fin, configuration (antennes, chaînes, gain, azimuts, distances entre antennes) |
| `signals` | Signaux retenus : fréquence, classe, largeur, période de validité, point de bruit associé |
| `measurements` | Horodatage, campagne, segment, signal, voie, RSSI brut, plancher, SNR, code qualité, piste (option) |
| `aggregates` | Fenêtre, signal, voie, statistiques de la voie et de son écart à la référence, envoyé à WordPress (oui/non) |
| `pair_aggregates` | Fenêtre, signal, paire de voies, statistiques de l'écart apparié |
| `activity` | Périodes ACTIVE, CALME, INTERROMPUE |
| `tracks` | (P10) Indicatif, locator, distance, azimut, classe de propagation, état, motif de rejet |
| `decodes` | (P10) Créneau, voie, indicatif, message, SNR décodeur, décalage audio |
| `known_signals` | (Bonus) Fréquence, émetteur, position |
| `events` | Journal : bascules, changements de configuration, alertes, erreurs |

Règles :

- La mesure brute n'est jamais modifiée ; corrections (chaîne, étalonnage, qualification) recalculables.
- Horodatages : entiers en millisecondes UTC (`ts_ms`).
- Rétention des mesures brutes réglable (par défaut : durée de la campagne + 90 jours) ; agrégats et rapports conservés.
- Volume visé, par voie : une campagne d'un mois doit tenir en moins de 1 Go en local. Le volume local croît avec le nombre de voies, ce qui est accepté, car l'espace est vérifié avant le lancement. Sur WordPress, il est borné par la taille maximale réglée par l'administrateur.

## 15. Installation

Un seul code source, trois procédures : Windows 10/11 64 bits, Debian 12 ou plus récent (amd64), Raspberry Pi OS 64 bits (Pi 4 ou Pi 5). Scripts idempotents, journalisés, avec désinstallation.

- Bibliothèques des fabricants : l'API SDRplay est propriétaire et non redistribuable ; l'utilisateur l'installe, l'installeur vérifie sa présence et son service (comme openAISReceiver). librtlsdr (branche RTL-SDR Blog) et libairspyhf sont installées ou compilées par l'installeur.
- Linux : service systemd sous un utilisateur dédié, règles udev, blacklist du module DVB pour la RTL-SDR.
- Windows : service (NSSM ou équivalent), pilote WinUSB par Zadig pour la RTL-SDR, raccourci vers la page locale.
- Plugin WordPress : archive zip installable depuis l'administration.

## 16. Exigences, licence et consignes pour Claude Code

### Exigences non fonctionnelles

- Fonctionnement continu pendant un mois, sans fuite mémoire ; redémarrage automatique sur erreur ; reprise de la campagne.
- **Consultation sans matériel** : le service et la page locale démarrent et fonctionnent sans aucun SDR branché. Campagnes, résultats, qualifications, installation et réglages restent consultables ; seules les commandes du récepteur sont désactivées. L'absence de SDR est un état normal (« SDR non branché »), pas une erreur : la détection est relancée à intervalle croissant (5 s à 1 min) et seul le changement d'état est journalisé. Une campagne en cours passe en INTERROMPUE et reprend dès que le SDR revient.
- Aucune perte de données en cas de coupure de courant (SQLite WAL, écritures transactionnelles).
- Horloge synchronisée (NTP) requise : la station vérifie son décalage et le signale (indispensable pour les créneaux FT8/WSPR).
- Charge CPU compatible avec un Raspberry Pi 4 en mode commuté, et avec un Pi 5 en mode simultané à 2 SDR avec décodage. Au-delà, c'est le test de charge qui décide ; le traitement de chaque voie doit pouvoir tourner sur un cœur distinct (un processus par SDR).
- Journaux rotatifs, niveau réglable.
- Interface et messages en français (traduisibles) ; code, identifiants et commentaires en anglais.
- Tous les seuils et constantes métier dans la configuration, aucun en dur.

### Licence

- Station : GPLv3. Plugin WordPress : GPLv2 ou ultérieure.
- Dépendances compatibles uniquement (ft8_lib MIT, wsprd GPLv3, ECharts Apache 2.0, Leaflet BSD) ; l'API SDRplay n'est jamais redistribuée.

### Pilote simulé et tests

- Pilote `simulated` : deux voies avec écart réglable, porteuses, SSB synthétique, signaux FT8 et WSPR, fading, bruit, écrêtage provoqué, latence et parasite de commutation réglables.
- Rejeu de fichiers IQ enregistrés.
- Tests unitaires :
  - plan de bandes, locator et distances, élévation du soleil, angle de départ ;
  - pertes de câble ;
  - classification des signaux ;
  - appariement et pivot, bootstrap par blocs ;
  - verdicts de qualification ;
  - machine à états des pistes.
- Tests d'intégration :
  - qualification complète et campagne commutée sur données simulées ;
  - reprise après interruption ;
  - export vers le serveur WordPress factice.
- Contrôles avant chaque fin de phase : `pytest`, `ruff check`, `mypy`.

### Consignes de travail

- Créer en P1 un `CLAUDE.md` reprenant les conventions, les commandes et l'avancement des phases.
- Avant chaque phase : plan court ; après : résumé, reste à faire, procédure de test manuelle courte. Arrêt pour validation à chaque fin de phase.
- Un commit Git par étape cohérente, messages explicites.
- Ne jamais inventer de fonction ou de champ des bibliothèques des fabricants, ni d'interface des décodeurs : vérifier dans la documentation ou les en-têtes, signaler toute incertitude.
- Toute déviation à ce cahier des charges est proposée et justifiée avant implémentation, puis consignée dans `docs/decisions.md`.
- Les tests sur matériel réel sont faits par l'utilisateur.

## 17. Points ouverts

- [ ] API SDRplay : un ou plusieurs appareils par processus, et nombre maximal d'appareils gérés par son service (mode simultané à N RSP2 Pro).
- [ ] Débit USB réel par SDR à la fréquence d'échantillonnage minimale, et nombre de SDR par contrôleur USB.
- [x] Palette : 8 couleurs validées (clair et sombre) ; antennes 9 et 10 par pointillé et marqueur (P0, 2026-11-04).
- [ ] Polices (IBM Plex Sans, Sans Condensed, Mono) embarquées dans le dépôt pour le fonctionnement hors ligne (licence OFL).
- [ ] Latence et temps d'établissement réels de la bascule A/B du RSP2 Pro (résultat attendu de P2).
- [ ] Bande utile réelle de l'Airspy HF+ Discovery et comportement HF de la RTL-SDR V4.
- [ ] Seuils par défaut des verdicts de qualification (marges, fraction utile minimale par classe).
- [ ] Constante dBm HF par modèle de SDR.
- [ ] Valeurs de référence du catalogue des câbles (fiches des fabricants).
- [ ] Sous-bandes numériques (FT8, FT4, WSPR) du plan de bandes, à vérifier avant P10.
- [ ] Interfaces de ft8_lib (décodage et réencodage) et de wsprd.
- [ ] Source des indices de météo spatiale.
- [ ] Hébergement du dépôt public (GitHub ou autre) et nom définitif du plugin.
- [ ] Site WordPress cible et limites réelles de l'hébergement OVH (taille des requêtes, ModSecurity).
