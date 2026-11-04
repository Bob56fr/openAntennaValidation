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
