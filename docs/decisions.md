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
