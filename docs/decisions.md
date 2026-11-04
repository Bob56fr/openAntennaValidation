# Décisions et écarts au cahier des charges

Chaque écart est proposé, justifié et validé avant implémentation (section 16 du cahier des charges).

## D1 — Noms des modes de réception (2026-11-03)

Les modes « a » et « b » des premiers échanges deviennent **mode commuté** (un SDR, deux entrées) et **mode simultané** (deux SDR), pour ne pas les confondre avec les antennes A et B.

## D2 — Reprise d'openAISReceiver (2026-11-03)

Le pilote SDRplay par ctypes (sans SoapySDR), l'interface `SdrDriver`, le stockage par migrations, la file d'envoi WordPress et les conventions de travail sont copiés depuis `c:\dev\openAISReceiver`, puis adaptés. Une bibliothèque commune sera extraite quand les deux projets seront stables.

## D3 — De 2 à 10 antennes (2026-11-03)

Le mode simultané accepte de 2 à 10 SDR, un par antenne. Avant chaque campagne, un test de charge du PC et une estimation de l'espace disque peuvent refuser le lancement. Sur WordPress, l'administrateur fixe une taille maximale de paquet par campagne ; la station adapte la résolution envoyée, et le nombre d'antennes n'est jamais un motif de refus. Les écarts sont exprimés par rapport à une antenne de référence, avec une matrice des paires et une correction des comparaisons multiples.

## D4 — Nom du projet (2026-11-04)

Le projet s'appelle **openAntennaValidation**. Noms techniques : paquet Python `open_antenna_validation`, commande `oav`, plugin WordPress `open-antenna-validation` (espace REST `open-antenna-validation/v1`), port web local 8660 (8650 est utilisé par openAISReceiver). Le dossier de travail `c:\dev\openDualSDR` peut être renommé sans effet sur le dépôt.

## D5 — Licence du code repris (2026-11-04)

Le code repris d'openAISReceiver, déclaré « Proprietary » dans ce projet, est republié sous GPLv3 dans openAntennaValidation, avec l'accord de son auteur.

## D6 — Un processus d'acquisition par SDR dès P1 (2026-11-04)

Le pilote du SDR vit dans un processus dédié (`acquisition.py`, contexte `spawn`), qui échange avec le service par deux files (commandes, événements). C'est l'architecture du mode simultané (section 4) ; la mettre en place dès P1 évite une refonte en P5. Les tests utilisent la même boucle dans un fil (`inline=True`). Le processus enfant surveille son parent : s'il disparaît (arrêt forcé), l'enfant s'arrête et abandonne ses files, pour ne jamais garder le SDR. Vérifié par un arrêt forcé du service sous Windows.

## D7 — Pilote SDRplay en HF : ce qui est vérifié et ce qui ne l'est pas (2026-11-04)

- Vérifié dans les en-têtes de l'API 3.15 : 9 états LNA sur les entrées A/B du RSP2 (`RSPII_NUM_LNA_STATES`), `amPortSel` à `AMPORT_2` par défaut avec l'antenne A, bascule par `Update_Rsp2_AntennaControl`, 16 appareils au plus (`SDRPLAY_MAX_DEVICES`).
- À confirmer dans la spécification PDF : la correspondance physique `AMPORT_1` (Hi-Z) / `AMPORT_2` (A/B), reprise d'openAISReceiver ; les réductions de gain LNA en HF. Faute de ces tables, l'affichage en dBm est désactivé en HF.
- Chaque bloc reçu porte son indice absolu ; les trous signalés par `firstSampleNum` comptent comme échantillons perdus. Une commande de bascule est située dans le flux (indice du prochain échantillon à recevoir) ; l'instant réel sera mesuré en P2.
- À 250 kS/s, la bande utile est limitée à 200 kHz par le filtre FI. La CB (2 MHz) demandera une fréquence d'échantillonnage plus élevée que les 2 MS/s actuels : point à traiter en P4.

## D8 — Catalogue des câbles reporté à P3 (2026-11-04)

Le plan de P1 prévoyait le catalogue des câbles avec la configuration. Il est reporté à P3 (installation), où il sera utilisé, pour ne pas figer dès maintenant des pertes non vérifiées.

## D9 — Campagne de démonstration (2026-11-04, demande de l'utilisateur)

Le site WordPress et la page locale montrent une campagne fictive, construite comme les maquettes de P0, tant qu'aucune vraie campagne n'existe (cahier des charges, section 13). Elle est marquée partout comme exemple et n'entre dans aucune statistique.

## D10 — Consultation sans matériel (2026-11-04, demande de l'utilisateur)

Le service et la page locale fonctionnent sans SDR branché (cahier des charges, section 16). L'acquisition vérifie la présence du SDR avant de l'ouvrir ; s'il manque, l'état devient « absent » (et non « erreur »), la détection est relancée de 5 s en doublant jusqu'à 1 min, et le journal ne garde que les changements d'état. Les commandes du récepteur répondent 400 tant qu'il n'est pas en marche ; tout le reste de l'API et des pages reste servi.

## D11 — Campagnes de démonstration et page de résultats avancées avant P2 (2026-11-04, demande de l'utilisateur)

Pour tester la mise en page sans matériel, une partie de P6 est réalisée dès maintenant :

- `demo.py` écrit deux campagnes fictives (40 m, 5 jours, 2 et 6 antennes) dans le schéma définitif : antennes, chaînes et pertes, campagne, voies, deux segments (coax de B remplacé), signaux, agrégats par fenêtre de 15 min, activité. Création au démarrage du service, une seule fois (`[demo] enabled`).
- `campaigns.py` définit le **format d'échange des résultats** (`/api/campaigns/{uuid}/results`) : par signal et par voie, les médianes par fenêtre telles que mesurées au SDR ; la page ajoute les pertes de chaîne du segment pour la vue Antenne. Ce format servira aussi au plugin WordPress.
- Pages `/campaigns` et `/campaign?id=…` : portage de la maquette P0 sur l'API ; `results.css` et `results.js` sont prévus pour être repris par le plugin.
- Restent pour P6 : tableau des mesures exclues (codes qualité), export CSV, pistes FT8 (P10). Les intervalles de confiance utilisent encore l'approximation de la maquette (taille effective n/8), le bootstrap par blocs viendra avec les vraies campagnes.

## D12 — P2 : banc matériel ; la campagne A/A passe en P3 (2026-11-04)

- **Banc matériel livré** (`bench.py`, `qualification.py`, page `/qualification`) : cinq étapes guidées (plancher propre, splitter symétrique, câbles inversés, atténuateur sur B, isolation en option). Les mesures tournent dans le processus d'acquisition, qui détient le pilote.
- **Bascule lue dans le flux** : avec l'atténuateur, chaque bascule devient une marche ; l'instant réel est le passage à mi-hauteur (en dB), d'où la latence et sa gigue. Le parasite et le temps d'établissement sont lus sur la moyenne de centaines de bascules alignées sur cet instant : une bascule seule est trop bruitée pour une tolérance de ±0,2 dB. Sans marche (splitter symétrique), l'alignement se fait sur la commande et le temps mesuré est un majorant.
- **Tolérance effective** : jamais inférieure à 3 fois le bruit résiduel de la moyenne. Si elle dépasse le double de la tolérance réglée, la mesure est déclarée « bruitée » (source trop variable : voix, évanouissement) et un avertissement conseille le générateur de bruit.
- **Neutralisation** = latence p99 + établissement + marge (0,2 ms) ; temps utile = 1 − neutralisation / durée sur chaque entrée.
- **Correction des entrées** = moyenne des mesures câbles normaux et inversés, par sous-bande ; la demi-différence est le déséquilibre du splitter.
- **Verdict provisoire par classe** d'après le temps utile à la cadence nécessaire à la classe (porteuses et numériques 2 Hz, SSB 20 Hz ; seuils 70 % et 40 %). **Les marges en dB viendront de la campagne A/A**, déplacée en P3 : elle demande le moteur de mesure des campagnes et la classification des signaux ; ce sera une campagne dont l'écart attendu est nul.
- Le profil est enregistré dans `hardware_profiles` (identité, bande, gain, cadence), l'ancien profil de la même identité et de la même bande est désactivé, validité 180 jours.

## D13 — P3 : moteur de campagne en mode commuté (2026-11-04)

- **Moteur dans le processus d'acquisition** (`engine.py`) : chaque cycle visite chaque entrée une fois ; durée par entrée = 1 / (nombre d'entrées × cadence) ; les premiers échantillons après la bascule sont écartés (neutralisation du profil, ou 5 ms sans profil) ; une trame FFT (puissance de 2 tenant dans le reste) par passage. Puissance de chaque signal dans sa largeur ; plancher local = médiane des cases voisines (±5 kHz ou 3 largeurs) hors de tout signal, ramenée à la largeur.
- **Une valeur par signal et par entrée et par période** (10 s, au plus un tiers de la fenêtre) : moyenne de puissance (porteuses, numériques), médiane pour la voix (SSB, AM/FM). Codes qualité en champ de bits : SATURATION, PERTE_FLUX, ABSENT (SNR sous 6 dB).
- **Horloge de la campagne = compteur d'échantillons** : un simulateur accéléré déroule une campagne plus vite que le temps réel (tests).
- **Accord sans pic DC** : le SDR est accordé au plus près du centre des fréquences, décalé par pas de 1 kHz jusqu'à ce que chaque signal soit à plus de 2 kHz du centre et reste, avec son voisinage de bruit, dans la bande utile. Défaut découvert grâce au simulateur, qui place désormais ses signaux en fréquence absolue (`air_center_hz`).
- **Agrégats** à la fermeture de chaque fenêtre (10 s jusqu'à 1 h, 1 min jusqu'à 24 h, 5 min jusqu'à 7 jours, 15 min au-delà), avec la correction des entrées du profil (B + correction A − B, interpolée à la fréquence du signal) ; mesures brutes inchangées. Écarts à la référence et écarts de toutes les paires.
- **Bande calme** quand aucun signal n'est présent pendant 5 min (mesures poursuivies, plancher compris) ; **interruption** quand le SDR disparaît ou que le service s'arrête, reprise automatique.
- **Campagne A/A** : même antenne sur les deux entrées ; à la fin, marge par classe = |biais| + 2 écarts-types des médianes de fenêtre de B − A, écrite dans le profil ; les campagnes suivantes la reprennent. Sans marge connue, la page de résultats l'indique et ne s'appuie que sur l'intervalle de confiance.
- **Installation** (`installation.py`) : chaînes versionnées ; pertes des câbles interpolées en a·√f + b·f sur deux points de fiche technique (valeurs **à vérifier**, une perte mesurée peut être saisie).
- Reste pour P4 : choix automatique des signaux (bande automatique), réduction de la cadence en bande calme.

## D14 — Banc : critère d'établissement robuste (2026-11-04)

Le temps d'établissement variait d'un essai à l'autre (2,7 à 4 ms pour 1,6 ms simulées) : le niveau final était estimé par une médiane (biaisée sous la moyenne pour du bruit) et une pointe de bruit isolée suffisait à repousser la fin de l'établissement. Désormais : normalisation par la moyenne du niveau final, enveloppe lissée sur 0,3 ms, tolérance effective d'au moins 4 fois le bruit résiduel, et seuls les dépassements soutenus (au moins 0,3 ms) comptent, la transition elle-même restant prise en compte. Sur dix tirages : 1,14 à 1,37 ms pour 1,36 ms théoriques.

## D15 — P4 : bande automatique (2026-11-04)

- **Relevé** (`detect.survey`) : les entrées alternent par tranches de 0,5 s pendant `survey_s` (60 s) ; on garde la puissance moyenne de chaque case FFT par tranche. Les spectrogrammes des deux entrées sont appariés tranche par tranche (maximum) : un signal présent sur l'une ou l'autre compte.
- **Détection** : plancher local = 30e percentile du spectre médian sur 8 kHz glissants ; cases candidates = 90e percentile dans le temps à plus de `detect_db` (8 dB) au-dessus (les signaux intermittents comptent) ; cases contiguës, trous de 2 cases tolérés. **Largeur occupée mesurée à 20 dB sous le pic du signal**, pas au-dessus du plancher : les lobes secondaires d'un signal fort (SNR 60 dB) dépassent le plancher de loin et faisaient classer une porteuse en « numérique » et une SSB en « AM/FM ». Signaux rejetés sous 12 dB de SNR ou 20 % de présence (jupes d'un signal plus fort).
- **Classes** : ≤ 400 Hz et présent ≥ 90 % du temps avec une enveloppe stable (≤ 2,5 dB) = porteuse/CW ; ≤ 400 Hz sinon = numérique étroit ; 400 Hz à 1,5 kHz = numérique ; 1,5 à 4 kHz = SSB ; 4 à 12 kHz = AM/FM ; au-delà, ignoré. Rang : enveloppe constante, puis présence, puis stabilité, puis SNR. Pas de recouvrement entre signaux retenus.
- **Point de bruit** : chaque campagne automatique mesure aussi l'endroit le plus calme de la bande (classe `noise`), loin des signaux et du pic DC : plancher de chaque antenne même en bande calme (section 8), et le moteur tourne en continu, ce qui cadence la réévaluation sur l'horloge de la campagne. Il est écarté des graphiques des signaux.
- **Réévaluation** toutes les `reevaluate_s` (1 h) : un signal retrouvé (même classe, centre à moins d'une demi-largeur) garde son identifiant ; un signal disparu est clos (`valid_to_ms`) ; de nouveaux signaux complètent jusqu'à `max_signals` (8). Le SDR reste accordé au centre de la bande ; la détection écarte le pic DC.
- **Bande calme** (les deux modes) : la période de mesure passe à `calm_period_s` (60 s) ; en bande automatique, nouveau relevé toutes les `calm_probe_s` (5 min). Retour à la cadence normale dès qu'un signal revient.
- Page de résultats : « Toutes classes » moyenne les classes mesurées dans la fenêtre (le FT8 n'émet qu'un créneau sur deux) ; l'écart est calculé classe par classe puis moyenné.

## D16 — P5 : mode simultané (2026-11-04)

- **Un processus d'acquisition par SDR** (D6 conservé) : le SDR principal (`[hardware]`) et les autres (`[hardware] others`, liste de numéros de série et d'entrées, 9 au plus). Chaque processus a son moteur ; le service apparie les mesures. Le spectre en direct reste celui du SDR principal ; le tableau de bord liste tous les SDR.
- **Grille temporelle commune** : le service fixe un instant de départ (maintenant + 0,5 s, le temps que les commandes arrivent) ; chaque moteur rattrape son flux puis jette les échantillons reçus avant cet instant. Les horodatages viennent ensuite du compteur d'échantillons de chaque SDR.
- **Heure d'un échantillon** = minimum, sur toute la période, de (heure de retour d'une lecture − compteur / débit) : une lecture ne rend jamais la main avant l'arrivée de ses échantillons, seulement après (retard du processeur, rattrapage). Une première version qui prenait l'heure d'une seule lecture mesurait 16 ms d'écart médian entre deux simulateurs temps réel, 27 ms au 99e centile ; avec le départ commun et cet estimateur : 0,7 à 1 ms en médiane, 4,3 ms au 99e centile.
- **Désalignement** : écart entre SDR mesuré à chaque période ; au-delà de `align_tolerance_ms` (5 ms), les mesures de voix (SSB, AM/FM) de la période sont codées DÉSALIGNÉE (bit 8) et exclues des agrégats ; porteuses et numériques sont gardés (leur enveloppe ne change pas en quelques millisecondes). **Dérive des horloges** (1 ppm = 86 ms par jour) : au-delà de la moitié de la tolérance trois périodes de suite, le service relance tous les moteurs sur un nouvel instant commun (une période perdue).
- **Fenêtres** closes quand toutes les voies ont rendu une période postérieure ; fin de campagne quand toutes les voies ont atteint la fin prévue. Interruption de toute la campagne si un des SDR disparaît, reprise quand tous sont en marche. En bande automatique, le premier SDR relève la bande sur sa seule entrée pendant que les autres attendent.
- **Étalonnage = campagne A/A simultanée** (même antenne sur tous les SDR par un splitter) : par signal, médiane des écarts de fenêtre de chaque SDR à la référence → correction de gain par SDR et par fréquence (`channels_db`, ajoutée à la correction déjà appliquée) ; marge par classe = 2 écarts-types des résidus. Le résultat est un profil (`hardware_profiles`, `qualification_runs` de type `aa`, réception `simultaneous`) pour l'ensemble des identités **dans l'ordre des voies** et la bande ; il remplace le banc de commutation, qui ne s'applique pas. Sans étalonnage, pas de correction et campagne « non qualifiée ». Verdict « attention » pour la voix si l'écart temporel au 99e centile dépasse la tolérance.
- **Test de charge obligatoire** avant une campagne simultanée (valable 1 h, mêmes SDR) : chaque SDR mesure `max_signals` signaux de 2,7 kHz, une période par seconde, pendant `load_test_s` (30 s). Charge d'un SDR = temps processeur / durée de signal ; charge de la station = somme / nombre de cœurs. Sous `load_ok` (70 %) : OK ; sous `load_max` (85 %) ou échantillons perdus : lancement après confirmation ; au-delà : refus. Le nombre de SDR n'est pas en soi un critère. Résultat joint à la campagne.
- **Contrôle d'espace disque** avant toute campagne : besoin = max(volume estimé × 1,2, volume + 1 Gio) ; refus en dessous, avec le besoin et la place libre.
- Bande utile d'une campagne simultanée = la plus étroite des SDR ; SDR de modèles différents acceptés mais signalés (`heterogeneous`).
- **Écart au cahier des charges (section 9), signalé après coup** : le cahier prévoit d'estimer le décalage entre SDR par corrélation des enveloppes de puissance d'un signal commun. Ce qui est fait : départ sur un instant d'horloge commun et horodatage de chaque période. Raisons : la corrélation demande un signal commun à enveloppe rapide (voix, évanouissements) présent sur toutes les antennes, ce qui manque justement en bande calme ou avec des porteuses seules ; l'horodatage mesure l'écart à chaque période quel que soit le contenu de la bande, et permet de réaligner. Limite : il suppose que l'heure d'arrivée des échantillons reflète l'heure de réception (gigue USB de quelques millisecondes). **À valider sur matériel réel** ; si l'écart mesuré y paraît douteux, la corrélation d'enveloppes peut être ajoutée en contrôle, sur les signaux SSB, sans changer le reste.

## D17 — P6 : statistiques, qualité des données, export (2026-11-04)

- **Bootstrap par blocs mobiles** (`web/stats.js`, partagé avec le futur plugin WordPress, testé avec Node depuis pytest) : intervalle de confiance à 95 % de la médiane des écarts de fenêtre, rééchantillonnés par blocs de fenêtres consécutives. Longueur de bloc = premier décalage où l'autocorrélation passe sous 0,2, au moins n^(1/3), au plus n/4 ; 500 tirages reproductibles (mêmes données, même intervalle). Pour des fenêtres corrélées (évanouissements lents), l'intervalle est 3 à 4 fois plus large qu'un calcul naïf ; pour des fenêtres indépendantes, 30 à 40 % plus large (prudent). Remplace l'approximation « taille effective n/8 » de D11.
- **Écart affirmé** seulement s'il dépasse la marge de qualification **et** que l'intervalle de confiance exclut zéro (avant : |écart| > max(marge, demi-largeur)).
- **Correction de Holm** sur les paires (au-delà de 2 antennes), avec la p-valeur bilatérale du bootstrap ; chaque paire n'est testée qu'une fois. Avec 500 tirages et 45 paires, seule une paire dont aucun tirage ne croise zéro peut passer en tête : prudent, voulu.
- Le calcul reste dans le navigateur : la page recalcule tout quand on change de référence, de grandeur, de classe ou de vue ; les résultats ne sont pas figés côté station.
- **Qualité des données** (`quality` du format d'échange) : par antenne, nombre de mesures et mises à l'écart par motif (saturation, perte de flux, désalignée), « signal absent » à part (gardé pour le plancher) ; liste des interruptions avec leur motif. Chiffres plausibles marqués « démonstration » pour les campagnes fictives, qui n'ont pas de mesures brutes.
- **Export CSV** (`/api/campaigns/{uuid}/export?kind=aggregates|measurements&dialect=std|fr`) : agrégats par fenêtre ou mesures brutes, en flux (des millions de lignes sur un mois), UTF-8 avec BOM, horodatage UTC ISO et en millisecondes. Deux dialectes : standard (virgule, point décimal) et tableur français (point-virgule, virgule décimale). Mesures brutes non corrigées, correction du profil dans sa colonne.
- Restent : pistes FT8 rejetées par motif (P10), réaffichage des classes exclues.

## D18 — P7 : plugin WordPress et publication (2026-11-04)

- **Contrat** : `docs/wordpress/ingest.schema.json` et `limits.schema.json`, validés dans les tests par le serveur factice `tests/fake_wordpress.py`. Deux routes protégées par le jeton de la station (`limits`, `ingest`), corps gzip déclaré `application/gzip`, jeton aussi dans `X-OAV-Token`, repli `?rest_route=` (constats D14 d'openAISReceiver repris tels quels).
- **Ce qui part de la station** : les métadonnées de la campagne telles que la page locale les affiche (campagne, voies avec leur type d'antenne, segments et pertes, signaux, activité, qualité des données) et les médianes de RSSI et de plancher par fenêtre, signal et voie. Pas de mesure brute, pas d'IQ ; locator réduit à 4 caractères (`locator_chars`). Le fuseau horaire de la station est désormais enregistré avec chaque campagne, pour que les heures locales du site soient celles de la station.
- **Visibilité** décidée sur la station, campagne par campagne : privée (rien n'est envoyé ; défaut), publique, non listée (accessible par le lien seulement). L'assistant propose de publier quand le site est configuré. Une campagne publiée n'est pas retirée du site si elle redevient privée : le masquage et la suppression se font sur le site.
- **File d'attente = la base** (table `publication`, migration 0002) : seules les fenêtres publiées closes partent pendant la campagne ; `sent_until_ms` n'avance qu'après acceptation ; nouvel essai à délai croissant jusqu'à `retry_max_s`. Métadonnées renvoyées seulement quand elles changent (empreinte).
- **Taille du paquet** = longueur du JSON des métadonnées + nombre de fenêtres × 40 octets, même calcul des deux côtés. À la première publication, la station choisit la fenêtre publiée (multiple de la fenêtre locale) qui fait tenir toute la campagne prévue dans 80 % de la limite du site ; valeur publiée = médiane des médianes locales. **Écart au cahier des charges** : le second niveau prévu (écarts entre paires résumés par classe solaire) n'existe pas, car la page ne lit pas d'écarts de paires : elle les recalcule à partir des médianes par voie ; seule la fenêtre s'élargit, jusqu'à 288 fois la fenêtre locale. Le nombre d'antennes n'est jamais un motif de refus. Si le site refuse quand même (413), la campagne passe « trop grosse » et n'est plus envoyée ; la repasser en publique sur la station la renvoie, avec une fenêtre recalculée si rien n'était parti.
- **Plugin** (`wordpress/open-antenna-validation`) : tables dédiées par `dbDelta` (stations, campagnes, fenêtres ; clé de fenêtre = campagne, signal, voie, début, d'où l'idempotence), quota sur les tables du plugin (507), limite de paquet vérifiée dans une transaction (413 et annulation). Rôles « Opérateur » et « Opérateur en attente » : l'administrateur valide ; un opérateur gère ses stations (un jeton par ordinateur, affiché une fois, seule son empreinte est gardée, lié à la première station qui l'utilise) et ses campagnes (titre affiché, commentaire, masquage, suppression, CSV). Une campagne supprimée reste en pierre tombale : la station qui la renverrait est acquittée sans que rien ne soit gardé.
- **Une seule page de résultats** : le plugin reprend `results.js`, `stats.js`, la vue de `campaign.html` (entre les repères `oav:view`) et le système de design, cantonné à `.oav-root` par `wordpress/build.py` pour ne pas toucher au thème du site. `results.js` lit `window.OAV_CONFIG` (adresse des résultats, pas d'export). Codes courts : `[oav_campagnes]` (liste filtrée ; `?campagne=` affiche une campagne sur la même page), `[oav_campagne uuid="…"]`, `[oav_stations]`.
- **Filtres** de la liste côté navigateur : opérateur, bande, paire de types d'antennes non ordonnée, réception, SDR, période, qualifiées seulement.
- **Démonstrations** : produites par `demo.py` au format d'échange lors de la construction du plugin ; affichées tant qu'aucune vraie campagne n'est publique (réglable : toujours, jamais).
- **Vérifié** sur un vrai WordPress 6.6 (PHP 8.2, MariaDB 11.4, Docker sur la Debian, port 8082) : deux opérateurs, deux stations, publication, idempotence, jeton d'une autre station refusé, campagne non listée, page de campagne identique à la page locale (`pytest -m wordpress`). Non vérifié : un hébergement OVH réel, les permaliens « simples » sur le vrai plugin (seulement sur le serveur factice).
- Non fait (bonus) : synthèse multi-campagnes, catalogue partagé des signaux connus.
