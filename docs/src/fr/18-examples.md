## 18. Exemples

### Exemple A : suivi caméra — les projecteurs motorisés suivent une personne (#376)

Faites que des projecteurs motorisés DMX suivent automatiquement les personnes détectées par une caméra.

**Prérequis :**
- Au moins un nœud caméra en ligne (onglet Firmware → déployer + vérifier)
- Au moins un appareil projecteur motorisé DMX placé dans l'onglet Layout
- Profil de projecteur motorisé configuré avec plage pan/tilt
- Moteur Art-Net/sACN en marche (Settings → DMX → Start)
- Étalonnage de projecteur motorisé terminé (voir Exemple C) pour une visée précise

**Étapes :**

1. **Vérifier le matériel** — ouvrez l'onglet Setup. Confirmez que vos projecteurs motorisés affichent un statut vert et que les nœuds caméra sont en ligne. Si les caméras sont hors ligne, vérifiez le WiFi et déployez le firmware depuis l'onglet Firmware.

2. **Démarrer le suivi caméra** — cliquez sur le bouton **Track** dans l'onglet Setup (à côté de Snap), ou allez dans l'onglet Layout, cliquez sur un appareil caméra et cliquez sur le bouton **Track** dans la modale d'édition. Le nœud caméra commence à exécuter la détection YOLO selon les classes et paramètres configurés dans les réglages de suivi de la caméra (voir [Configuration du suivi](#tracking-configuration)). Les objets détectés apparaissent comme marqueurs étiquetés dans la vue 3D.

3. **Créer une action Track** — allez dans l'onglet Actions. Cliquez sur **+ New Action**.
   - **Nom :** `Person Follow`
   - **Type :** `Track` (dernière option du menu déroulant)
   - **Couleur :** choisissez la couleur du faisceau (p. ex. rouge pour un spot)
   - Laissez **Target Objects** vide — cela signifie « suivre TOUTES les personnes détectées »
   - **Cycle Time :** 2000 ms (à quelle vitesse les projecteurs changent s'il y a cyclage)
   - Cochez **Fixed assignment** si vous voulez un 1:1 strict (projecteur 1 = personne 1, extras ignorés)
   - Cliquez sur **Save Action**

4. **Créer une chronologie** — allez dans l'onglet Shows. Cliquez sur **+ New Timeline**, nommez-la « Person Tracking », définissez la durée à 600 s, activez **Loop**. La chronologie peut être vide — les actions Track s'évaluent globalement pendant toute lecture.

5. **Démarrer la lecture** — cliquez sur **Bake**, puis **Start**. La boucle de lecture DMX à 40 Hz commence. L'action Track lit tous les objets temporels mobiles (personnes détectées), calcule pan/tilt pour chaque projecteur, définit dimmer et couleur, et envoie des paquets Art-Net au pont.

6. **Tester** — marchez devant la caméra. Dans les 2 secondes, un marqueur personne rose apparaît dans la vue 3D. Les projecteurs motorisés devraient s'allumer dans la couleur choisie et viser vers vous.

**Comportement d'assignation :**

| Personnes en vue | Avec 2 projecteurs motorisés |
|------------------|------------------------------|
| 1 personne | Les deux projecteurs visent la même personne |
| 2 personnes | Un projecteur par personne (1:1) |
| 3+ personnes (cyclage) | Les projecteurs cyclent entre les personnes toutes les 2 s |
| 3+ personnes (fixe) | Les 2 premières sont suivies, la 3e est ignorée |

**Dépannage :**

| Problème | Solution |
|----------|----------|
| Aucun marqueur personne en 3D | Vérifiez le statut du nœud caméra — le suivi est-il en marche ? Essayez un Scan manuel pour vérifier que la détection fonctionne. |
| Personne détectée mais les projecteurs ne bougent pas | Vérifiez que le moteur Art-Net est en marche. Vérifiez l'étalonnage du projecteur motorisé. Vérifiez que la lecture de la chronologie est active. |
| Les projecteurs s'allument mais visent la mauvaise position | Exécutez l'étalonnage de projecteur motorisé (Exemple C). Sans étalonnage, le système utilise des estimations géométriques qui peuvent être imprécises. |
| Les projecteurs répondent avec délai | Normal — la détection tourne à 2 fps avec ~1 s de latence de capture. Les objets temporels ont un TTL de 5 s. |

---

### Exemple B : suivi par projecteur motorisé avec effets spatiaux (#379)

Faites que des projecteurs motorisés suivent un objet virtuel balayant la scène — aucune caméra requise. Cet exemple parcourt le flux de travail complet, de la configuration de la scène à l'aperçu 3D en direct avec cônes de faisceau animés.

**Prérequis :**
- Orchestrateur SlyLED en marche (Windows ou Mac)
- Aucun matériel physique requis — cet exemple s'exécute entièrement dans l'émulateur

**Partie 1 — Configuration de la scène et des appareils**

1. **Définir les dimensions de la scène** — ouvrez l'onglet Settings. Sous **Stage**, entrez les dimensions de votre zone de performance :
   - Largeur : 6000 mm (6 m)
   - Hauteur : 3000 mm (3 m)
   - Profondeur : 4000 mm (4 m)
   - Cliquez sur **Save**. La vue 3D se redimensionnera pour correspondre à ces dimensions.

2. **Créer un profil DMX** — allez dans Settings → **Profiles** → cliquez sur **New Profile**. Cela définit la disposition des canaux de votre projecteur motorisé :
   - **Nom :** `Narrow Spot`
   - **Beam Width :** 8 (degrés — faisceau étroit pour un suivi visible)
   - **Pan Range :** 540, **Tilt Range :** 270
   - **Channels :** ajoutez 6 canaux dans cet ordre :
     - Canal 0 : Pan (16 bits) — pan coarse
     - Canal 1 : Pan Fine — pan fine (auto-lié)
     - Canal 2 : Tilt (16 bits) — tilt coarse
     - Canal 3 : Tilt Fine — tilt fine (auto-lié)
     - Canal 4 : Dimmer
     - Canal 5 : Red, Canal 6 : Green, Canal 7 : Blue
   - Cliquez sur **Save Profile**

![Éditeur de profil avec configuration narrow spot](screenshots/example-b-profile.png)

3. **Ajouter deux projecteurs motorisés** — allez dans l'onglet Setup. Cliquez deux fois sur **+ Add Fixture** pour créer deux projecteurs motorisés DMX :
   - **Appareil 1 :** nom : `Mover SL` (Stage Left), Universe : 1, Start Address : 1, Profile : `Narrow Spot`
   - **Appareil 2 :** nom : `Mover SR` (Stage Right), Universe : 1, Start Address : 14, Profile : `Narrow Spot`

   Les deux appareils apparaissent dans le tableau Setup avec des badges « DMX » violets et le nom du profil.

**Partie 2 — Disposition 3D et effet spatial**

4. **Positionner les projecteurs sur le pont** — passez à l'onglet Layout. Dans la barre latérale, vous verrez les deux projecteurs listés comme « non placés ». Faites glisser chacun dans la vue 3D :
   - **Mover SL :** position X : 1500, Y : 0, Z : 2800 (côté cour, sur le pont). Réglez la rotation à tilt : −30, pan : −15.
   - **Mover SR :** position X : 4500, Y : 0, Z : 2800 (côté jardin, sur le pont). Réglez la rotation à tilt : −30, pan : 15.

   Passez en vue 3D pour confirmer que les deux projecteurs sont élevés sur le pont et dirigés vers le sol de la scène. Les cônes de faisceau devraient être visibles comme des triangles translucides.

![Onglet Layout — vue 3D avec deux projecteurs positionnés sur le pont](screenshots/example-b-layout-3d.png)

5. **Créer un effet spatial** — allez dans l'onglet Actions. Cliquez sur **+ New Action** :
   - **Nom :** `Sweep Green`
   - **Type :** Spatial Effect
   - **Shape :** Sphere
   - **Radius :** 800 mm
   - **Color :** vert (0, 255, 0)
   - **Motion Start :** X : 1000, Y : 2000, Z : 0 (côté cour, mi-profondeur, niveau du sol)
   - **Motion End :** X : 5000, Y : 2000, Z : 0 (côté jardin, même profondeur et hauteur)
   - **Duration :** 8 secondes
   - **Easing :** Linear
   - Cliquez sur **Save Action**

   Cela crée une sphère verte de lumière qui balaie du côté cour au côté jardin en 8 secondes. Appliquée aux projecteurs motorisés, ils suivront la position centrale de la sphère.

![Onglet Actions avec l'effet spatial Sweep Green configuré](screenshots/example-b-action.png)

**Partie 3 — Chronologie, précalcul et lecture**

6. **Créer une chronologie** — allez dans l'onglet Shows. Cliquez sur **+ New Timeline** :
   - **Nom :** `Mover Tracking Demo`
   - **Duration :** 20 secondes
   - **Loop :** activé
   - Ajoutez une piste ciblant **All Performers**
   - Ajoutez un clip référençant l'effet `Sweep Green`, commençant à 0 s avec 8 s de durée

![Onglet Shows avec chronologie contenant le clip d'effet spatial](screenshots/example-b-timeline.png)

7. **Précalculer la chronologie** — cliquez sur le bouton **Bake**. Le moteur de précalcul calcule les angles pan/tilt par appareil pour chaque tranche de temps :
   - Pour chaque image de 25 ms, il calcule la position de la sphère le long du chemin de mouvement
   - Pour chaque projecteur, il calcule les angles pan/tilt nécessaires pour viser cette position
   - Le dimmer est réglé à 255 et les canaux de couleur sont réglés à vert
   - Attendez la confirmation « Bake complete » (typiquement <1 seconde)

8. **Démarrer la lecture et vérifier** — passez à l'onglet Runtime. Cliquez sur **Start** :
   - La vue 3D montre les deux cônes de faisceau animés en temps réel
   - À T=0 s, les deux faisceaux visent la position de départ (côté cour)
   - Au fur et à mesure que l'effet balaie, les faisceaux suivent la sphère verte à travers la scène
   - À T=8 s, les deux faisceaux ont suivi la sphère jusqu'au côté jardin
   - La chronologie boucle, et le balayage redémarre

![Runtime — cônes de faisceau en position de départ (T=0 s)](screenshots/example-b-tracking-t0.png)
![Runtime — faisceaux suivant à mi-balayage (T=5 s)](screenshots/example-b-tracking-t5.png)
![Runtime — faisceaux en position finale (T=10 s)](screenshots/example-b-tracking-t10.png)

**À quoi faire attention :**
- Les deux cônes de faisceau doivent être verts (correspondant à la couleur de l'effet)
- Les cônes doivent se déplacer en douceur de gauche à droite
- L'intensité du faisceau (opacité) doit être > 0 pendant le balayage, indiquant une sortie active
- Si les cônes de faisceau n'apparaissent pas, assurez-vous que les appareils sont positionnés dans l'onglet Layout et que la chronologie est précalculée

**Variantes :**
- Changez la forme de l'effet spatial en **Plane** pour un mur de lumière qui balaie
- Ajoutez un second effet sur une piste séparée avec un timing différent pour des motifs croisés
- Essayez le spectacle préréglé **Figure Eight** (Runtime → Load Show) pour un motif croisé prêt à l'emploi

---

### Exemple C : étalonnage manuel de projecteur motorisé (#381)

Étalonnez un projecteur motorisé afin que le système sache exactement où atterrit son faisceau pour toute position pan/tilt. Ce processus en deux parties découvre d'abord la plage visible du faisceau (grille pan/tilt) puis construit une carte de lumière qui associe chaque position pan/tilt à des coordonnées de scène réelles.

**Prérequis :**
- Au moins un nœud caméra en ligne et positionné dans l'onglet Layout
- Étalonnage caméra terminé — la caméra doit avoir une carte de scène valide (voir Exemple D)
- Appareil projecteur motorisé ajouté dans Setup et positionné dans l'onglet Layout
- Moteur Art-Net/sACN en marche (Settings → DMX → Start)
- Éclairage ambiant tamisé — le faisceau doit être clairement visible à la caméra sur le sol
- Le faisceau doit être visé sur le sol dans le champ de vision de la caméra, pas directement sur la caméra

**Partie 1 — Découverte pan/tilt et étalonnage de grille**

1. **Ouvrir le panneau d'étalonnage** — allez dans l'onglet Layout. Double-cliquez sur l'appareil projecteur motorisé que vous voulez étalonner. Dans la boîte de dialogue d'édition, cliquez sur le bouton **Calibrate**. L'assistant d'étalonnage s'ouvre en affichant le nom de l'appareil, le statut d'étalonnage actuel (le cas échéant) et les modes d'étalonnage disponibles.

![Panneau d'étalonnage avant démarrage — affiche le nom de l'appareil et les options d'étalonnage](screenshots/example-c-calibrate-panel.png)

2. **Choisir la couleur du faisceau** — sélectionnez une couleur qui contraste bien avec la surface de votre sol :
   - **Vert** fonctionne mieux sur sols sombres (bois, moquette foncée)
   - **Magenta** fonctionne mieux sur sols clairs (blanc, béton)
   - **Rouge** ou **Bleu** sont des alternatives si les choix par défaut se confondent avec votre environnement
   - La couleur importe parce que la caméra utilise un filtrage colorimétrique pour isoler le faisceau de la lumière ambiante

3. **Lancer la découverte** — cliquez sur **Start Calibration**. Le système exécute une séquence de découverte automatique :
   - **Phase 1 — Balayage de grille grossière :** l'appareil balaie ~40 positions pan/tilt (8 colonnes × 5 rangées) sur toute sa plage. La caméra guette l'apparition du faisceau sur le sol après chaque mouvement.
   - **Phase 2 — Raffinement fin :** une fois le faisceau trouvé, le système spiraler vers l'extérieur depuis cette position pour affiner le centre exact de la région visible.
   - La découverte se termine typiquement en 30–60 secondes. L'indicateur de progression affiche « Discovering... » avec la position de balayage courante.

![Découverte en cours — balayage de grille grossière avec la caméra guettant le faisceau](screenshots/example-c-discovery.png)

4. **Cartographie BFS** — après la découverte, le système cartographie automatiquement toute la région visible :
   - Depuis la position de faisceau découverte, il avance dans 4 directions (haut/bas/gauche/droite dans l'espace pan/tilt)
   - À chaque position, la caméra capture une image et détecte le centroïde du faisceau
   - Le système enregistre la position pixel du faisceau et la convertit en millimètres scène à l'aide de l'homographie de la caméra
   - La cartographie s'arrête aux frontières où le faisceau quitte le champ de vision de la caméra ou tombe hors de la scène
   - Collecte jusqu'à 60 positions d'échantillon, typiquement en 2–3 minutes
   - Le système utilise des temps de settle adaptatifs (0,8–2,5 s) par mouvement et une double capture de vérification pour s'assurer que le faisceau s'est arrêté avant d'enregistrer

5. **Construction et revue de grille** — les échantillons collectés sont compilés en une grille d'interpolation bilinéaire :
   - Le résumé d'étalonnage affiche :
     - **Sample count :** nombre de positions détectées avec succès (visez 30+)
     - **Pan range :** plage normalisée (p. ex. 0,15–0,85 signifie que le faisceau est visible sur 70 % de la plage pan)
     - **Tilt range :** plage normalisée
     - **Grid density :** finesse d'échantillonnage de la grille
   - La grille permet une recherche directe rapide : étant donné une valeur (pan, tilt), calculer le (X, Y) scène où atterrit le faisceau

![Étalonnage de grille terminé — résumé affichant le nombre d'échantillons, la plage pan/tilt et la densité de grille](screenshots/example-c-grid-result.png)

**Partie 2 — Étalonnage de carte de lumière (recherche coordonnées scène vers pan/tilt)**

6. **Construire la carte de lumière** — cliquez sur **Build Light Map**. Cela étend l'étalonnage en balayant une grille systématique 20×15 à travers la région visible découverte :
   - Pour chaque position de grille, l'appareil se déplace à la valeur pan/tilt
   - La caméra détecte le faisceau et enregistre les X/Y/Z scène exacts où il atterrit
   - Cela construit une table de correspondance complète (pan, tilt) → (stageX, stageY, stageZ)
   - La progression affiche « Building light map... N/300 » avec des mises à jour en temps réel
   - Temps de complétion typique : 5–10 minutes pour une grille complète 20×15

![Construction de carte de lumière en cours — balayage systématique avec cartographie des coordonnées scène](screenshots/example-c-light-map.png)

7. **Vérifier la recherche inverse** — une fois la carte de lumière construite, utilisez le bouton **Aim** pour tester la correspondance inverse :
   - Entrez une position cible scène (p. ex. centre scène : X=3000, Y=2000, Z=0)
   - Cliquez sur **Aim** — le système utilise une interpolation pondérée par l'inverse des distances des 4 échantillons les plus proches de la carte de lumière pour calculer les valeurs pan/tilt exactes
   - L'appareil se déplace à la position calculée
   - Vérifiez visuellement que le faisceau atterrit sur (ou très près de) le point cible sur la scène
   - Essayez 3–4 cibles différentes à travers la scène pour confirmer la précision
   - Un bon étalonnage devrait placer le faisceau dans les 100–200 mm de la cible à des distances de scène typiques

![Vérification de visée — faisceau visé à la position cible scène à l'aide de la carte de lumière étalonnée](screenshots/example-c-aim-verify.png)

8. **Enregistrer l'étalonnage** — les données d'étalonnage sont automatiquement enregistrées avec l'appareil. La carte de lumière et les données de grille persistent entre les sessions et sont incluses dans les exports de fichier projet (.slyshow).
   - Les actions Track utilisent la carte de lumière pour viser les personnes détectées
   - Les actions Pan/Tilt Move l'utilisent pour des balayages interpolés fluides
   - La vue 3D l'utilise pour afficher des directions de cône de faisceau précises

**Étalonnage manuel (alternative — aucune caméra requise) :**

Si l'étalonnage automatisé n'est pas disponible (pas de caméra, ou la caméra ne peut pas voir le faisceau), utilisez l'assistant d'étalonnage manuel :

1. Onglet Layout → double-cliquez sur le projecteur motorisé → cliquez sur **Manual Calibrate**
2. **Définir les positions des marqueurs** — ajoutez 4–6 marqueurs physiques à des positions scène connues. Entrez les coordonnées X, Y, Z de chaque marqueur (en mm). Répartissez les marqueurs à travers la scène : avant-gauche, avant-droite, arrière-centre au minimum.
3. **Jog vers chaque marqueur** — pour chaque marqueur, utilisez les curseurs pan/tilt pour viser manuellement le faisceau jusqu'à ce qu'il atterrisse exactement sur le marqueur physique. Cliquez sur **Record** pour enregistrer l'échantillon (pan, tilt) → (stageX, stageY, stageZ).
4. **Ajoutez au moins 4 échantillons** répartis à travers la scène pour un bon ajustement affine. Plus d'échantillons (6+) améliorent la précision, en particulier aux bords de la scène.
5. Cliquez sur **Compute** — le système ajuste une transformation affine 3D à partir de vos échantillons :
   - `pan = a1*stageX + b1*stageY + c1*stageZ + d1`
   - `tilt = a2*stageX + b2*stageY + c2*stageZ + d2`
   - La transformation affine extrapole au-delà des points étalonnés pour une couverture de toute la scène

**Quand ré-étalonner :**
- Appareil physiquement déplacé vers une nouvelle position ou un nouvel angle
- Changement de lieu (différentes dimensions de scène ou surface de sol)
- Après une mise à jour firmware qui change la plage pan/tilt ou le comportement moteur
- Si la précision de visée se dégrade avec le temps (dérive moteur)
- Après changement de l'orientation de montage de l'appareil (droit vs. inversé)

---

### Exemple D : étalonnage caméra avec marqueurs ArUco (#380)

Étalonnez une caméra afin que les coordonnées pixel puissent être associées à des positions scène réelles. C'est un prérequis pour la détection de faisceau, le suivi de personne et l'étalonnage de projecteur motorisé — sans cela, le système ne peut pas convertir ce que la caméra voit en millimètres scène réels.

**Prérequis :**
- Nœud caméra en ligne et joignable sur le réseau (déployez le firmware depuis l'onglet Firmware si nécessaire)
- Appareil caméra enregistré dans le système (onglet Setup → Discover, ou Settings → Cameras → ajouter manuellement)
- Appareil caméra placé dans l'onglet Layout à sa position physique
- Une imprimante pour imprimer la feuille de marqueurs ArUco (papier A4/Letter standard)
- Un mètre ruban pour enregistrer les positions des marqueurs sur la scène
- La caméra doit avoir une vue dégagée du sol de la scène où les marqueurs seront placés

**Partie 1 — Préparer et placer les marqueurs ArUco**

1. **Imprimer les marqueurs ArUco** — allez dans Settings → Cameras. Cliquez sur le bouton **Print ArUco Markers**. Une modale s'ouvre avec 6 marqueurs ArUco 4×4 imprimables (ID 0–5), chacun de 150 mm × 150 mm :
   - Cliquez sur **Download** ou utilisez la boîte de dialogue d'impression du navigateur pour imprimer la feuille de marqueurs
   - Imprimez à 100 % d'échelle (pas de mise à l'échelle/ajustement à la page) — la taille physique doit correspondre aux 150 mm attendus pour un étalonnage précis
   - Les marqueurs peuvent être imprimés sur papier blanc ordinaire, mais le carton est plus durable

![Boîte de dialogue d'impression de marqueurs ArUco — 6 marqueurs prêts à imprimer](screenshots/example-d-print-markers.png)

2. **Placer les marqueurs sur le sol de la scène** — positionnez les marqueurs imprimés à des emplacements connus sur la scène :
   - **Minimum :** 3 marqueurs (suffisant pour une homographie de base)
   - **Recommandé :** 4–6 marqueurs pour une meilleure précision
   - **Stratégie de placement :**
     - Répartissez les marqueurs sur tout le champ de vision de la caméra
     - Placez au moins un marqueur près de chaque coin de la zone visible
     - Placez les marqueurs à plat sur le sol — les marqueurs inclinés réduisent la précision
     - Mesurez la position de chaque marqueur depuis l'origine de la scène (coin arrière-droit au niveau du sol) :
       - X = distance depuis la droite de la scène (mm)
       - Y = distance depuis le mur du fond (mm)
       - Z = 0 (niveau du sol)
   - Enregistrez l'ID du marqueur et ses coordonnées (X, Y) — vous les entrerez à l'étape 5

**Partie 2 — Enregistrer et positionner la caméra**

3. **Enregistrer la caméra** — si le nœud caméra n'est pas déjà enregistré :
   - Allez dans l'onglet Setup et cliquez sur **Discover** — les nœuds caméra répondent à la diffusion UDP
   - Ou allez dans Settings → Cameras → entrez l'adresse IP de la caméra manuellement
   - Chaque capteur de caméra USB sur le nœud apparaît comme un appareil séparé
   - Vérifiez que la caméra est en ligne : son statut devrait afficher « Online » avec un indicateur vert

![Panneau de configuration caméra dans Settings — liste des caméras avec IP, statut et badges d'étalonnage](screenshots/example-d-camera-config.png)

4. **Positionner la caméra en 3D** — passez à l'onglet Layout :
   - Trouvez l'appareil caméra dans la barre latérale (listé comme « non placé » s'il est nouveau)
   - Faites-le glisser dans la vue 3D à la position physique réelle de la caméra
   - Réglez la rotation pour correspondre à la direction de visée réelle de la caméra :
     - Une caméra montée sur un mur à 2 m de hauteur, visée à 30 degrés vers le bas aurait rotation Z=2000, tilt=−30
   - En vue 3D, la caméra apparaît comme un tronc de pyramide (pyramide) montrant son champ de vision
   - Vérifiez que le tronc couvre la zone où vous avez placé les marqueurs ArUco

**Partie 3 — Lancer l'étalonnage et vérifier**

5. **Lancer l'étalonnage ArUco** — dans l'onglet Layout, cliquez sur l'appareil caméra pour le sélectionner. Cliquez sur le bouton **Calibrate** :
   - L'assistant s'ouvre et récupère une capture en direct depuis la caméra
   - Le système détecte automatiquement tous les marqueurs ArUco visibles et les met en évidence avec des superpositions vertes
   - **Pour chaque marqueur détecté :**
     - L'ID du marqueur est affiché sur la superposition
     - Entrez les coordonnées scène réelles du marqueur (X, Y en mm) que vous avez mesurées à l'étape 2
     - Cliquez sur **Record** pour enregistrer la correspondance pixel-vers-scène de ce marqueur
   - Après enregistrement de tous les marqueurs, cliquez sur **Compute** — le système construit une matrice d'homographie qui associe toute coordonnée pixel aux coordonnées sol de la scène

![Capture caméra avec marqueurs ArUco détectés — superpositions vertes affichant les ID de marqueurs](screenshots/example-d-detection.png)

6. **Revoir les résultats d'étalonnage** — le résumé d'étalonnage affiche :
   - **Reprojection error :** à quel point l'homographie calculée correspond aux points enregistrés. Plus bas est mieux :
     - <10 mm : excellent — adapté au suivi de précision
     - 10–20 mm : bon — adéquat pour la plupart des cas d'usage
     - 20–50 mm : correct — envisagez d'ajouter plus de marqueurs ou de re-mesurer les positions
     - >50 mm : médiocre — revérifiez les mesures de marqueurs et réessayez
   - **Reference points :** nombre de marqueurs utilisés (devrait correspondre à ce que vous avez enregistré)
   - **Coverage area :** la zone scène couverte par l'étalonnage (plus grand est mieux)

![Étalonnage terminé — erreur de reprojection, points de référence et résumé de couverture](screenshots/example-d-result.png)

7. **Enregistrer et appliquer** — cliquez sur **Save** pour persister l'étalonnage :
   - Le badge de l'appareil caméra se met à jour pour afficher une coche verte « Cal »
   - Toutes les fonctionnalités qui dépendent de la conversion pixel-vers-scène utilisent maintenant cet étalonnage :
     - **Suivi de personne :** les cadres englobants détectés sont convertis en positions scène
     - **Détection de faisceau :** les centroïdes de faisceau deviennent des coordonnées scène pour l'étalonnage de projecteur motorisé
     - **Étalonnage de projecteur motorisé :** tout l'assistant d'étalonnage de projecteur motorisé (Exemple C) en dépend
   - Les données d'étalonnage sont incluses dans les exports de fichier projet (.slyshow) pour la portabilité

**Astuces pour un étalonnage précis :**
- **La taille des marqueurs compte :** utilisez les marqueurs de 150 mm à la taille d'impression standard. Les marqueurs plus petits sont plus difficiles à détecter à distance.
- **Le placement à plat est critique :** même une légère inclinaison (marqueur sur une surface froissée) peut décaler le centre détecté de 10–20 mm.
- **Couvrez les bords :** l'homographie est la plus précise à l'intérieur de l'enveloppe convexe de vos marqueurs de référence. Placez les marqueurs aux extrêmes de la vue de la caméra, pas seulement au centre.
- **Conditions d'éclairage :** la détection ArUco fonctionne dans la plupart des éclairages, mais évitez l'éblouissement direct sur les marqueurs imprimés (papier brillant sous des lumières vives).
- **Ré-étalonnez quand :**
  - La caméra est physiquement déplacée (même légèrement)
  - L'objectif de la caméra est changé ou le zoom est ajusté
  - Les dimensions de la scène changent (les marqueurs seraient à des positions différentes)
  - La précision du suivi ou de la détection de faisceau se dégrade

---

### Exemple E : spot suit la personne — préréglage de suivi en direct (#382)

Utilisez le préréglage intégré **Spotlight: Follow Person** pour faire que des projecteurs motorisés suivent automatiquement les personnes détectées par une caméra en temps réel.

**Prérequis :**
- Au moins un nœud caméra en ligne avec détection de personne fonctionnelle (vérifiez d'abord avec un Scan manuel)
- Au moins un appareil projecteur motorisé DMX placé dans l'onglet Layout
- Étalonnage caméra terminé (voir Exemple D) pour un positionnement scène précis
- Étalonnage de projecteur motorisé terminé (voir Exemple C) pour une visée pan/tilt précise
- Moteur Art-Net/sACN en marche

**Étapes :**

1. **Charger le préréglage** — allez dans l'onglet Runtime. Cliquez sur **Load Show** (ou le menu déroulant des préréglages). Sélectionnez **Spotlight: Follow Person** dans la liste des préréglages.
   - Si aucun nœud caméra n'est enregistré, un avertissement apparaît : « No camera node registered — person detection will not work »
   - Si aucun projecteur motorisé n'est configuré, un avertissement apparaît concernant les projecteurs motorisés manquants
   - Le préréglage se charge même avec des avertissements — vous pourrez ajouter le matériel manquant plus tard

2. **Ce qu'il crée** — le préréglage configure automatiquement :
   - Une **action Track** (type 18) sur chaque projecteur motorisé disponible, ciblant `objectType: "person"`
   - Une couleur de spot chaude (255, 240, 200) à dimmer plein pour le faisceau
   - Un wash ambiant bleu tamisé (10, 5, 30) sur tous les appareils LED pour un cadrage atmosphérique
   - Une chronologie bouclée de 10 minutes qui maintient la boucle de lecture DMX en marche

3. **Démarrer le suivi caméra** — cliquez sur le bouton **Track** dans l'onglet Setup ou dans la modale d'édition d'appareil caméra. Le nœud caméra commence à exécuter la détection selon les classes et paramètres de suivi configurés (voir [Configuration du suivi](#tracking-configuration)). Les objets détectés apparaissent comme marqueurs étiquetés dans la vue 3D.

4. **Démarrer la lecture** — cliquez sur **Bake**, puis **Start**. La boucle de lecture DMX à 40 Hz commence. L'action Track lit tous les objets temporels personne et calcule pan/tilt pour chaque projecteur en temps réel.

5. **Marcher sur scène** — dans les 2 secondes suivant votre entrée dans la vue de la caméra, un marqueur personne rose apparaît. Les projecteurs motorisés s'allument avec la couleur chaude de spot et visent vers vous. En vous déplaçant, les faisceaux suivent.

**Comportement avec plusieurs personnes :**
- 1 personne, 2 projecteurs : les deux projecteurs visent la même personne
- 2 personnes, 2 projecteurs : un projecteur par personne (auto-répartition)
- 3+ personnes, 2 projecteurs : les projecteurs cyclent entre les personnes toutes les 2 secondes

**Quand personne n'est détecté :**
- Les projecteurs s'estompent à 0 (blackout) et conservent leur dernière position
- Dès qu'une personne est détectée à nouveau, les projecteurs re-visent et s'allument immédiatement

**Astuces :**
- Utilisez un profil de faisceau étroit (8–15 degrés) pour un effet de spot dramatique
- Assurez-vous que la salle est assez sombre pour que la caméra distingue le faisceau de la lumière ambiante
- Si le suivi semble saccadé, augmentez le FPS de capture de la caméra ou réduisez le seuil de confiance
- L'action Track fonctionne aux côtés d'autres effets de chronologie — vous pouvez ajouter des washs de couleur spatiaux sur des pistes à priorité inférieure

---

