## Annexe C — Maintenance de la documentation

> Cette annexe décrit le contrat entre les annexes d'étalonnage ci-dessus et le code source qui les implémente. Elle existe pour l'issue [#662](https://github.com/SlyWombat/SlyLED/issues/662) et reste volontairement concise — les détails complets se trouvent dans `docs/DOCS_MAINTENANCE.md`.

### C.1 Fichiers faisant autorité

Toute pull request qui modifie le comportement d'étalonnage dans l'un de ces fichiers doit inclure une révision de l'annexe A ou B dans la même PR :

**Étalonnage des projecteurs motorisés :** `desktop/shared/mover_calibrator.py`, `mover_control.py`, `parametric_mover.py`, `desktop/shared/spa/js/calibration.js`, `desktop/shared/parent_server.py` (routes `/api/calibration/mover/*`).

**Étalonnage des caméras :** `firmware/orangepi/camera_server.py`, `beam_detector.py`, `depth_estimator.py`, `desktop/shared/space_mapper.py`, `surface_analyzer.py`, `camera_math.py`, `desktop/shared/parent_server.py` (routes `/api/aruco/markers*`, `/api/cameras/<fid>/stage-map`, `/api/cameras/<fid>/aruco/*`, `/api/cameras/<fid>/intrinsic*`, `/api/cameras/<fid>/beam-detect`, `/api/space/scan`).

### C.2 Liste de contrôle du réviseur (forme abrégée)

Sur une PR qui touche à l'étalonnage, vérifiez que :

- Les noms de phases dans `mover_calibrator.py` correspondent au tableau de l'annexe B §B.2
- Les constantes de temporisation du tableau §B.7 correspondent toujours au code
- Les chemins d'endpoints ainsi que les formes de requête et de réponse de l'annexe A correspondent aux signatures des routes Flask
- Le schéma de rotation v2 (§A.9) correspond toujours à `camera_math.py::rotation_from_layout`
- Les chaînes d'état écrites dans le dictionnaire de statut d'étalonnage correspondent au diagramme de la machine à états

La liste de contrôle complète, y compris la vérification du rendu des diagrammes Mermaid sous `docs/diagrams/` et les critères de retrait de la bannière BROUILLON, se trouve dans `docs/DOCS_MAINTENANCE.md`.

### C.3 Régénérer le manuel

- Source canonique : `docs/USER_MANUAL.md` (ce fichier).
- `docs/SlyLED_User_Manual.docx` et `.pdf` sont **construits séparément** par `tests/build_manual.py`, qui reconstruit le document à partir de zéro plutôt que d'analyser ce markdown. La chaîne docx/PDF n'inclut pas encore ces annexes — travail de suivi.
- Les sources des diagrammes se trouvent dans `docs/diagrams/*.mmd`. Les blocs Mermaid sont intégrés directement dans le markdown afin que GitHub les affiche nativement ; des outils externes comme Kroki peuvent générer des SVG ou PNG à partir des fichiers autonomes pour l'inclusion dans le PDF.

### C.4 Contrôle d'application

Aucun contrôle de dérive automatique n'est en place pour l'instant. Options proposées, par ordre croissant de coût :

1. Case à cocher du modèle de PR (`.github/pull_request_template.md`)
2. Grep via GitHub Actions : échoue les PR qui touchent à la liste source sans toucher à `docs/USER_MANUAL.md`, avec une étiquette de dérogation
3. Agent de dérive planifié (hebdomadaire)

Ces mesures nécessitent des modifications dans `.github/` et sont suivies comme éléments connexes sous #662.

### C.5 Retrait de la bannière BROUILLON

Les bannières BROUILLON sur les annexes A et B devront être retirées une fois que les éléments en cours listés dans `docs/DOCS_MAINTENANCE.md §"When to bump the DRAFT banner"` seront tous confirmés comme fusionnés. Au moment de la rédaction de cette annexe (2026-04-23), les éléments suivants sont connus pour être partiels ou pas encore en code : #653 budgets de temps, #654 porte paramétrique mise de côté, #655 suréchantillonnage médian complet, #658 confirmation par clignotement hors chemin battleship, #659 filtre de cible par polygone de vue du sol, #661 densité battleship adaptative.
