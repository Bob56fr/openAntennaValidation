# Plan de test sur matériel réel

Ce document rassemble les essais à faire sur le vrai matériel, des phases P1 à P10. Tout a été développé et vérifié avec le récepteur simulé, mais rien n'a encore tourné sur un vrai SDR ni sur l'air.

**Comment l'utiliser.** Faites les essais dans l'ordre : chaque partie s'appuie sur la précédente. Pour chaque essai, notez le résultat (**OK**, **KO** ou **non fait**) et ce que vous avez observé, surtout quand c'est différent de l'attendu. Un KO bien décrit vaut mieux qu'un OK douteux. Renvoyez ensuite le document rempli, avec les fichiers demandés à la fin.

Les commandes sont données pour Windows (développement) ; sous Linux, remplacez `.\.venv\Scripts\oav` par `oav` (service installé : `sudo -u oav env OAV_HOME=/var/lib/open-antenna-validation /opt/open-antenna-validation/venv/bin/oav`).

## 0. Préparation

| | |
| --- | --- |
| Matériel indispensable | RSP2 Pro, deux antennes HF, un splitter (diviseur) et deux câbles identiques |
| Utile | Atténuateur fixe de 10 dB, charge 50 Ω, générateur de bruit ou balise stable |
| Pour P8 | Deux RTL-SDR Blog V4, ou deux Airspy HF+ |
| Pour P10 | WSJT-X installé (Windows : site de WSJT-X ; Debian : installé par l'installeur), locator de la station renseigné |
| Horloge | **À régler avant tout essai FT8/WSPR** : au premier contrôle, l'horloge du PC de développement avait 0,85 s d'avance. Sous Windows : Paramètres › Heure › « Synchroniser maintenant ». |

**T0.1 — Contrôles du logiciel.** `.\.venv\Scripts\pytest`, `ruff check src tests`, `mypy`.
Attendu : tout passe (environ 150 tests ; ceux qui demandent WSJT-X sont sautés s'il manque).
Résultat :

**T0.2 — Version.** `.\.venv\Scripts\oav --version` et `git log -1`.
Résultat (à recopier) :

## 1. Socle et RSP2 Pro (P1)

**T1.1 — Détection.** RSP2 Pro branché, API SDRplay installée : `oav detect`.
Attendu : le RSP2 Pro avec son numéro de série et ses entrées A, B (et Hi-Z).
Résultat :

**T1.2 — Mesure rapide.** `oav probe --frequency 7.1e6` (antennes sur A et B).
Attendu : niveau, plancher et écrêtage plausibles sur chaque entrée ; pas d'écrêtage permanent.
Résultat :

**T1.3 — Page locale.** `oav serve`, puis http://localhost:8660/ : spectre en direct, changement d'entrée A/B, de fréquence et de gain.
Attendu : le spectre suit ; chaque bascule et chaque changement de gain sont pris en compte sans erreur.
Résultat :

**T1.4 — Sans SDR.** Débranchez le RSP2 pendant que la page est ouverte, attendez 1 min, rebranchez.
Attendu : « SDR non branché » (pas d'erreur), campagnes toujours consultables ; reprise seule au rebranchement.
Résultat :

**T1.5 — Entrée Hi-Z.** Correspondance physique AMPORT (D7) : antenne sur Hi-Z, entrée Hi-Z choisie.
Attendu : le signal arrive bien par l'entrée choisie.
Résultat :

## 2. Qualification (P2)

**T2.1 — Banc complet.** Page Qualification, sur 40 m : plancher propre (charges 50 Ω), splitter, câbles inversés, atténuateur 10 dB sur B, isolation (facultatif).
Attendu : latence et gigue de bascule, établissement à ±0,2 dB, écart mesuré proche de 10 dB avec l'atténuateur, déséquilibre du splitter faible ; profil enregistré puis accepté.
Résultat (latence, établissement, neutralisation, temps utile) :

**T2.2 — Mesure bruitée.** Recommencez l'étape atténuateur sur un signal qui varie (voix SSB).
Attendu : avertissement « mesure bruitée », conseil du générateur de bruit.
Résultat :

## 3. Mode commuté, fréquences imposées (P3)

**T3.1 — Campagne A/A commutée.** Même antenne sur A et B par le splitter, 1 h, 3 à 5 fréquences (balise, radiodiffusion, FT8).
Attendu : écart proche de 0 dB ; marges par classe écrites dans le profil.
Résultat (écart, marges) :

**T3.2 — Campagne de 24 h.** Deux antennes différentes sur 40 m, 24 h.
Attendu : fin à l'heure prévue ; résultats lisibles ; bande calme détectée la nuit si c'est le cas.
Résultat :

**T3.3 — Reprise.** Pendant une campagne : débranchez le SDR 2 min, puis arrêtez et relancez le service.
Attendu : campagne INTERROMPUE puis reprise seule ; interruptions visibles dans « Qualité des données ».
Résultat :

## 4. Bande automatique (P4)

**T4.1 — Relevé.** Campagne en bande automatique sur une portion de 40 m (≤ 150 kHz), 2 h.
Attendu : relevé de 60 s, signaux choisis plausibles (porteuses d'abord), réévaluation toutes les heures.
Résultat (signaux retenus) :

**T4.2 — 10 m la nuit.** Bande automatique sur 28,0 à 28,2 MHz pendant une nuit.
Attendu : passage en « bande calme », mesures ralenties, sans arrêt.
Résultat :

## 5. Mode simultané (P5)

Avec deux SDR (deux RSP, ou les SDR de la partie 8).

**T5.1 — Test de charge.** Page Nouvelle campagne › réception simultanée › « Vérifier la charge ».
Attendu : verdict OK sur un PC récent ; noter la charge par SDR.
Résultat :

**T5.2 — Étalonnage A/A simultané.** Même antenne sur les deux SDR par le splitter, 1 h.
Attendu : correction de gain par SDR, marges ; écart entre SDR (alignement) de quelques ms.
Résultat (correction, alignement p50/p99) :

**T5.3 — Campagne simultanée.** Deux antennes, 24 h.
Attendu : pas de période DÉSALIGNÉE en continu ; resynchronisation si les horloges dérivent.
Résultat :

## 6. Résultats (P6)

**T6.1 — Page de résultats.** Sur la campagne T3.2 : vues Installation et Antenne, RSSI, bruit, SNR, profil sur 24 h, jour par jour.
Attendu : phrase de synthèse cohérente avec ce que vous savez de vos antennes ; écarts significatifs seulement au-delà de la marge.
Résultat :

**T6.2 — Export CSV.** Agrégats et mesures brutes, formats français et standard.
Attendu : ouverture correcte dans le tableur (accents, virgules).
Résultat :

## 7. Site WordPress (P7)

**T7.1 — Installation sur le site.** Plugin 0.3.0 (`python wordpress/build.py --zip`) sur le site, page `[oav_campagnes]`, opérateur et jeton.
Attendu : « Tester la liaison » OK depuis la station.
Résultat :

**T7.2 — Publication.** Une campagne publique, une non listée.
Attendu : la publique dans la liste du site, la non listée accessible par son lien seulement ; page de campagne identique à la page locale ; locator réduit à 4 caractères.
Résultat :

**T7.3 — Coupure réseau.** Coupez Internet 1 h pendant une campagne publiée.
Attendu : envoi repris seul ensuite, sans trou.
Résultat :

## 8. RTL-SDR Blog V4 et Airspy HF+ (P8)

**T8.1 — Préparation.** Windows : pilote WinUSB par Zadig pour chaque clé RTL-SDR ; numéro de série unique : `rtl_eeprom -s 00000101` (puis 00000102), débrancher et rebrancher.
Attendu : `oav detect` (avec `--driver rtlsdr` ou `airspyhf` à l'init) liste chaque SDR avec son numéro.
Résultat :

**T8.2 — Airspy sans Zadig.** Sous Windows 10/11, l'Airspy HF+ fonctionne-t-il sans Zadig ?
Résultat :

**T8.3 — Lecture HF.** `pytest -m hardware -k "rtlsdr or airspyhf"`, puis `oav probe --frequency 7.1e6`.
Attendu : niveau et plancher plausibles.
Résultat :

**T8.4 — Bande utile.** Banc de qualification (T2.1) sur un RTL-SDR et sur un Airspy : la bande utile annoncée (80 % de l'échantillonnage) est-elle juste ?
Résultat :

**T8.5 — 10 m sur V4.** `sample_rate = 2400000`, bande automatique 28,5 à 29,5 MHz.
Attendu : accord juste sous 28,8 MHz ; toute la bande mesurée.
Résultat :

**T8.6 — Simultané mixte.** Une campagne A/A puis une vraie campagne avec un RTL-SDR et un Airspy (ou le RSP2).
Attendu : campagne marquée hétérogène ; correction de gain de chaque SDR.
Résultat :

## 9. Installeurs (P9)

**T9.1 — Debian vierge.** `sudo ./install/linux/install.sh --sdr sdrplay` (fichier `.run` de SDRplay dans le dossier personnel), puis une seconde fois sans option.
Attendu : page sur le port 8660, icône, service actif ; redémarrage de la machine : la station repart seule ; `wsjtx` installé.
Résultat :

**T9.2 — Raspberry Pi OS 64 bits.** Même essai sur un Pi 4 ou 5, avec RTL-SDR (`--sdr rtlsdr`).
Attendu : idem ; charge acceptable (test de charge).
Résultat :

**T9.3 — Windows.** `install\windows\install.cmd -Sdr sdrplay`, puis l'icône du bureau.
Attendu : page ouverte ; la fenêtre relance la station si vous tuez Python dans le Gestionnaire des tâches ; Ctrl+C l'arrête ; `-AutoStart` la lance à l'ouverture de session ; WSJT-X détecté.
Résultat :

**T9.4 — Import.** Copiez `config.toml` et `oav.sqlite3` d'une station vers l'autre système (`--import` / `-Import`).
Attendu : campagnes retrouvées.
Résultat :

**T9.5 — Désinstallation.** `uninstall.sh --purge` / menu Démarrer.
Attendu : plus de service, d'icône, de règle de pare-feu ; les données restent sans `--purge`.
Résultat :

## 10. Modes numériques (P10)

**T10.1 — Horloge.** Tableau de bord : pas d'avertissement d'horloge ; le décrocher volontairement (avancer l'heure de 2 s) le fait apparaître dans l'heure (ou au redémarrage du service).
Résultat :

**T10.2 — Campagne FT8 commutée, 40 m.** RSP2 Pro, deux antennes, « Modes numériques » › FT8, 40 m, 24 h.
Attendu : bascule toutes les 30 s ; tableau de bord : créneaux décodés, pistes validées, dernières stations ; stations décodées comparables à ce que WSJT-X affiche au même moment sur la même antenne.
Résultat (nombre de pistes validées et rejetées par motif) :

**T10.3 — Résultats numériques.** Sur T10.2 : carte, écart selon la distance, l'angle et l'azimut, Venn, tableau.
Attendu : écart cohérent avec la partie spectrale de la même campagne ; avec une antenne directive (azimut saisi dans le catalogue), un lobe dans sa direction sur le diagramme polaire.
Résultat :

**T10.4 — WSPR.** Campagne WSPR de 24 h (30 m ou 40 m).
Attendu : décodages toutes les 2 min ; pistes validées ; ordre des antennes équilibré.
Résultat :

**T10.5 — FT8 simultané.** Deux SDR, deux antennes, FT8 20 m, quelques heures.
Attendu : une station décodée sur une seule antenne est mesurée aussi sur l'autre (colonne « Décodée par » du tableau et diagramme de Venn) ; écart par station plus serré qu'en commuté.
Résultat :

**T10.6 — Charge du décodage.** Pendant T10.5, sur le PC puis sur un Pi : charge du processeur.
Attendu : le décodage d'un créneau finit avant le suivant ; sinon, essayer `decode_depth = 2`.
Résultat :

**T10.7 — Exports et site.** Exports « Décodages » et « Pistes par station » ; publication de T10.2 sur le site.
Attendu : la section « Stations décodées » s'affiche aussi sur le site ; distances données depuis le carré de 4 caractères.
Résultat :

**T10.8 — Windows, FT4 et WSPR.** Sous Windows, une campagne FT4 courte.
Attendu : avertissement « pas d'encodeur FT4 » à la création ; mesures marquées `seen` dans l'export des décodages.
Résultat :

## Points ouverts à mesurer

- Bande utile réelle du RSP2 Pro, de la V4 et de l'Airspy HF+ à la fréquence d'échantillonnage utilisée (T2.1, T8.4).
- Plages de fréquence des fiches fabricants : V4 dès 500 kHz, Airspy HF+ de 500 Hz à 31 MHz.
- Pertes de câble calculées depuis les fiches techniques (page Installation) : à comparer à une perte mesurée si possible.
- Gigue USB entre SDR en mode simultané (T5.2) ; perte USB d'une RTL-SDR, non comptée par sa bibliothèque.
- Charge du décodage WSJT-X avec plusieurs SDR sur un Raspberry Pi (T10.6).

## À renvoyer

- Ce document rempli.
- Les journaux : `data\logs\` sous Windows ou le répertoire de la station ; `journalctl -u oav -n 1000` pour le service Linux.
- Les exports CSV des campagnes qui posent question, et des captures d'écran des pages surprenantes.
- Pour un KO d'installation : `%ProgramData%\oav-install.log` ou `/var/log/oav-install.log`.
