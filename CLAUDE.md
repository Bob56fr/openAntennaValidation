# openAntennaValidation — conventions pour Claude Code

Spécification : [docs/cahier-des-charges.md](docs/cahier-des-charges.md). Écarts validés : [docs/decisions.md](docs/decisions.md). Maquettes validées (P0) : [design/](design/).

## Règles de travail

- Développement phase par phase (section 2 du cahier des charges). Avant une phase : plan court. Après : résumé, reste à faire, procédure de test manuelle. Arrêt pour validation à chaque fin de phase.
- Un commit par étape cohérente, message explicite en français.
- Toute déviation au cahier des charges est proposée et justifiée avant implémentation, puis consignée dans `docs/decisions.md`.
- Ne jamais inventer de fonction ou de champ des bibliothèques des fabricants (API SDRplay, librtlsdr, libairspyhf) ni d'interface des décodeurs : vérifier dans la documentation ou les en-têtes (`C:\Program Files\SDRplay\API\inc`), signaler toute incertitude.
- Les tests sur matériel réel sont faits par l'utilisateur.
- Projet frère : `c:\dev\openAISReceiver` (pilote SDRplay, export WordPress, leçons de `docs/decisions.md`). S'en inspirer plutôt que réinventer.

## Conventions de code

- Python 3.11+, annotations partout, `mypy --strict` sur `src/open_antenna_validation`.
- Interface et messages utilisateur en français ; code, identifiants et commentaires en anglais.
- Tous les seuils et constantes métier dans la configuration (`config.py` + `config/default.toml`, tenus identiques par un test).
- Horodatages : entiers en millisecondes UTC (`ts_ms`). Mesures brutes jamais modifiées ; corrections recalculables.
- Seul le paquet `hardware` connaît un matériel précis ; le reste passe par `SdrDriver`.
- Un processus d'acquisition par SDR (`acquisition.py`) ; le service ne touche jamais le pilote directement (D6).
- Schéma de base : nouvelles tables par migrations numérotées `storage/migrations/NNNN_nom.sql`, jamais en modifiant une migration publiée.
- Front-end : HTML/CSS/JS sans framework ni compilation, système de design de `web/style.css` (repris des maquettes P0), ECharts et polices embarqués dans `web/vendor/` (fonctionnement hors ligne), thèmes clair et sombre par jetons CSS.
- Couleurs d'antennes : palette de 8 couleurs dans l'ordre fixe (`--s1` à `--s8`), toujours doublée d'un marqueur ou d'un style de trait.

## Commandes

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\pytest            # tests sans matériel
.\.venv\Scripts\pytest -m hardware  # tests avec le SDRplay branché
.\.venv\Scripts\ruff check src tests
.\.venv\Scripts\mypy
.\.venv\Scripts\oav --home .\data init --driver simulated
.\.venv\Scripts\oav --home .\data serve   # http://localhost:8660/
```

Les trois contrôles (`pytest`, `ruff check`, `mypy`) doivent passer avant chaque fin de phase.

Pièges Windows : PowerShell 5.1 écrit en UTF-8 avec BOM (`Set-Content`, `Out-File`) ; ne pas l'utiliser pour modifier `pyproject.toml` ni les fichiers TOML. Un processus enfant `multiprocessing` doit abandonner ses files si le parent disparaît (D6).

## Avancement

| Phase | État |
| --- | --- |
| P0 Maquettes | Validé (2026-11-04) |
| P1 Socle | Livré, en attente de test sur le RSP2 Pro |
| P2 Qualification | Banc matériel livré (D12), en attente de test sur le RSP2 Pro ; campagne A/A livrée en P3 |
| P3 Mode commuté, fréquences imposées | Livré avec le simulateur (D13), en attente de test sur le RSP2 Pro |
| P4 Bande automatique | Livré avec le simulateur (D15), en attente de test sur le RSP2 Pro |
| P5 Mode simultané | À faire |
| P6 Résultats locaux | En partie (D11) : démonstration, liste, page de résultats sur l'API |
| P7 WordPress | À faire |
| P8 RTL-SDR V4 et Airspy HF+ | À faire |
| P9 Installeurs | Linux : première version (`install/linux`) ; Windows : à faire |
| P10 Modes numériques | À faire |
