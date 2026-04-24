## 14. Nœuds caméra

Les nœuds caméra sont des ordinateurs monocartes Orange Pi ou Raspberry Pi équipés de **caméras USB**. Ils fournissent des captures d'image en direct et de la détection d'objets par IA pour la préparation de la scène.

> **Remarque :** seules les caméras USB sont prises en charge. Les caméras à nappe CSI du Pi (p. ex. Pi Camera Module, Freenove FNK0056) ne sont pas prises en charge en v1.x. Utilisez des webcams USB à la place.

### Ajouter un nœud caméra
1. Flashez un Orange Pi avec l'image OS prise en charge
2. Connectez-le au même réseau WiFi que l'orchestrateur
3. Dans l'onglet **Firmware**, configurez les identifiants SSH (par défaut : `root` / `orangepi`)
4. Cliquez sur **Scan for Boards** pour trouver l'appareil sur le réseau
5. Cliquez sur **Install** pour déployer le firmware caméra via SSH+SCP

### Page de configuration de la caméra
Chaque nœud caméra sert une interface web locale à `http://<camera-ip>:5000/config` :
- **Tableau de bord** — informations sur la carte, fiches par caméra avec capture en direct et détection
- **Paramètres** — nom de l'appareil, redémarrage, réinitialisation d'usine

### Captures
Cliquez sur **Capture Frame** sur n'importe quelle fiche caméra pour prendre une capture JPEG. Utilise OpenCV pour une capture rapide, avec fswebcam en solution de repli.

### Détection d'objets
Cliquez sur **Detect Objects** (bouton violet) pour exécuter la détection IA YOLOv8n sur l'image actuelle de la caméra :
- Des cadres englobants avec étiquettes et pourcentages de confiance sont dessinés sur une couche de canevas
- **Curseur de seuil** (0,1–0,9) — filtrer selon la confiance de détection
- **Résolution** (320/640) — plus bas est plus rapide, plus haut est plus précis
- **Case Auto** — détecter en continu toutes les 3 secondes
- Latence typique : ~500 ms de capture + ~500 ms d'inférence sur Orange Pi 4A

La détection nécessite le modèle ONNX YOLOv8n (`models/yolov8n.onnx`, 12 Mo), qui est téléversé automatiquement lors du déploiement du firmware.

### Déploiement caméra
Le processus de déploiement (depuis l'onglet **Firmware**) téléverse tous les fichiers caméra via SCP :
- `camera_server.py`, `detector.py`, `requirements.txt`, `slyled-cam.service`
- `models/yolov8n.onnx` (modèle de détection)
- Installe les paquets système (`python3-opencv`, `python3-numpy`, `v4l-utils`)
- Installe les paquets Python (`flask`, `zeroconf`, `onnxruntime`)
- Configure le service systemd `slyled-cam` pour le démarrage automatique au boot
- Affiche une comparaison de versions et prend en charge la réinstallation forcée

### Prise en charge multi-caméras
Chaque nœud peut héberger plusieurs caméras USB. Le firmware détecte automatiquement les caméras connectées et filtre les nœuds vidéo SoC internes. Chaque caméra obtient sa propre fiche dans la page de configuration avec des contrôles indépendants de capture et de détection.

### Analyse de l'environnement
Le bouton **Scan Environment** dans la barre d'outils Disposition capture un nuage de points 3D de l'espace physique :
1. Chaque caméra positionnée capture une image et exécute une estimation de profondeur
2. Les pixels sont rétroprojetés en 3D à l'aide du FOV de la caméra et de la profondeur
3. Les nuages de points de toutes les caméras sont fusionnés en coordonnées de plateau
4. **L'analyse de surface** identifie sol, murs et obstacles (piliers, mobilier)
5. Les surfaces détectées peuvent être automatiquement créées comme objets de plateau nommés

Le nuage de points peut être visualisé sous forme de points colorés dans la vue 3D (basculer avec le bouton nuage de points). Cela donne une carte visuelle de l'environnement physique que les lumières vont éclairer.

### Appareils par caméra
Chaque capteur de caméra USB sur un nœud caméra s'enregistre comme un **appareil distinct** dans la disposition. Un nœud avec 2 caméras crée 2 appareils, chacun avec :
- Sa propre position sur la scène (plaçable indépendamment)
- Son propre FOV et sa propre résolution
- Son propre vecteur de direction au repos (flèche cyan)

### Configuration du suivi

Chaque appareil caméra dispose de réglages de suivi par caméra accessibles depuis la boîte de dialogue **Edit** de l'onglet Setup. Ceux-ci contrôlent ce que la caméra détecte et comment elle se comporte pendant le suivi en direct.

![Édition caméra avec configuration du suivi](screenshots/spa-setup-edit-camera.png)

**Detect Classes** — sélection multiple des types d'objets à suivre. Le modèle YOLOv8n prend en charge 80 classes COCO ; 16 classes pertinentes pour la scène sont disponibles :

| Catégorie | Classes |
|-----------|---------|
| Personnes | Person |
| Animaux | Cat, Dog, Horse |
| Accessoires | Chair, Backpack, Suitcase, Sports Ball, Bottle, Cup, Umbrella, Teddy Bear |
| Véhicules | Bicycle, Skateboard, Car, Truck |

Par défaut, seule **Person** est sélectionnée. Ajouter d'autres classes n'a aucun impact sur les performances — YOLO évalue toujours toutes les classes en un seul passage et filtre ensuite.

**Paramètres :**

| Paramètre | Défaut | Plage | Description |
|-----------|--------|-------|-------------|
| FPS | 2 | 0,5–10 | Images de détection par seconde. Plus haut = plus réactif mais plus de CPU sur le nœud caméra. |
| Seuil | 0,4 | 0,1–0,95 | Confiance minimale pour accepter une détection. Plus bas = plus sensible mais plus de faux positifs. |
| TTL (s) | 5 | 1–60 | Secondes avant qu'une piste perdue n'expire et que son marqueur de plateau soit retiré. |
| Re-ID (mm) | 500 | 50–5000 | Distance maximale pour apparier une nouvelle détection à un objet déjà suivi. |

**Démarrer le suivi :** cliquez sur le bouton **Track** dans l'onglet Setup (à côté de Snap) ou dans la boîte de dialogue d'édition d'appareil de l'onglet Layout. Le nœud caméra commence une détection continue selon vos classes et paramètres configurés. Les objets détectés apparaissent comme marqueurs étiquetés dans la vue 3D.

### Étalonnage des projecteurs motorisés

L'assistant d'étalonnage de projecteur motorisé construit une grille d'interpolation qui associe chaque position du plateau aux angles pan/tilt exacts requis pour un projecteur motorisé DMX. Un nœud caméra positionné est requis.

**Prérequis :**
- Au moins un nœud caméra positionné dans l'onglet Layout
- Moteur Art-Net en cours d'exécution (`POST /api/dmx/start`)
- Appareil projecteur motorisé placé sur la disposition avec son profil configuré

**Démarrer l'étalonnage :**
1. Allez dans l'onglet **Layout** et double-cliquez sur un appareil projecteur motorisé DMX
2. Cliquez sur le bouton **Calibrate** dans la boîte de dialogue d'édition d'appareil
3. Choisissez une couleur de faisceau — les options sont vert, magenta, rouge, bleu (choisissez-en une qui contraste avec votre plateau)
4. Cliquez sur **Start Calibration** — l'assistant prend le relais automatiquement

**Ce qui se passe automatiquement :**
1. **Découverte** — le projecteur balaie une grille pan/tilt grossière ; la caméra détecte où le faisceau atterrit sur le sol du plateau
2. **Cartographier la région visible** — la plage pan/tilt qui maintient le faisceau dans le champ de vision de la caméra est identifiée
3. **Construire la grille d'interpolation** — le projecteur échantillonne systématiquement des points à travers la région visible ; à chaque point, la caméra enregistre les coordonnées exactes du plateau

**Progression :** un panneau de progression en temps réel affiche la phase en cours, le pourcentage complet et une vignette en direct depuis la caméra.

**Résultat :** la grille d'interpolation est enregistrée avec l'appareil et utilisée automatiquement par l'action Track et toutes les actions Pan/Tilt Move pour convertir les coordonnées de l'espace scène en valeurs pan/tilt matérielles.

> **Astuce :** lancez l'étalonnage dans un éclairage ambiant tamisé afin que le faisceau soit clairement visible par la caméra. Utilisez l'option **Beam Color** qui donne le plus haut contraste sur la surface de votre sol.

### Test d'orientation d'appareil

Avant de lancer l'étalonnage complet, utilisez le test d'orientation pour confirmer que pan et tilt sont câblés dans les directions attendues. Une orientation incorrecte fait converger l'étalonnage sur de mauvaises positions.

**Lancer le test :**
1. Double-cliquez sur un projecteur motorisé DMX dans l'onglet Layout pour ouvrir la boîte de dialogue d'édition d'appareil
2. Cliquez sur **Orientation Test** (sous la carte des canaux)
3. L'appareil se déplace à travers quatre positions de sonde : pan gauche, pan droite, tilt haut, tilt bas
4. Observez le faisceau physique et comparez-le avec les flèches à l'écran indiquant la direction attendue

**Interpréter les résultats :**
| Observation | Action |
|-------------|--------|
| Le faisceau suit les flèches | L'orientation est correcte — passez à l'étalonnage |
| Pan se déplace dans la direction opposée | Activez **Invert Pan** dans les réglages de l'appareil |
| Tilt se déplace dans la direction opposée | Activez **Invert Tilt** dans les réglages de l'appareil |
| Les axes pan et tilt sont échangés | Activez **Swap Pan/Tilt** dans les réglages de l'appareil |

**Enregistrer :** après avoir ajusté les indicateurs d'orientation, cliquez sur **Save** dans la boîte de dialogue d'édition d'appareil. Les indicateurs sont stockés avec l'appareil et appliqués automatiquement lors de tous les étalonnages et lectures ultérieurs.

---

