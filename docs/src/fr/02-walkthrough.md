## 2. Walkthrough : premier spectacle en 30 minutes

Ce walkthrough construit un spectacle complet de projecteurs motorisés DMX depuis zéro — découverte matérielle, configuration d'appareils, disposition, enregistrement de caméras, actions, chronologie et lecture. Chaque étape a été validée de bout en bout pendant les tests QA (issue #533). Suivez dans l'ordre ; chaque étape s'appuie sur la précédente.

**Ce dont vous avez besoin :**
- Orchestrateur SlyLED lancé sur Windows ou Mac
- Au moins un projecteur motorisé DMX connecté via un pont Art-Net/sACN (p. ex. Enttec ODE Mk3)
- Au moins un nœud caméra USB sur le réseau (Orange Pi ou Raspberry Pi)
- Tous les appareils sur le même sous-réseau LAN que l'orchestrateur

---

### Étape 1 — Lancer et créer un nouveau projet

Démarrez l'orchestrateur :

```powershell
powershell -File desktop\windows\run.ps1
```

Ouvrez `http://localhost:8080` dans Chrome ou Edge. Le SPA se charge sur l'onglet Tableau de bord.

![SPA au lancement affichant l'onglet Tableau de bord](screenshots/walkthrough-533/01-launch.png)

Allez dans l'onglet **Paramètres** → **Projet** → cliquez sur **Nouveau projet**, puis nommez-le (p. ex. « Walkthrough Show »).

![Boîte de dialogue de nouveau projet](screenshots/walkthrough-533/02-new-project.png)

---

### Étape 2 — Définir les dimensions de la scène

Dans **Paramètres** → **Scène**, entrez les dimensions de votre zone de performance :
- Largeur : 6000 mm (6 m)
- Hauteur : 3000 mm (3 m)
- Profondeur : 4000 mm (4 m)

Cliquez sur **Enregistrer**. Le canevas de disposition se redimensionnera pour correspondre à ces dimensions.

---

### Étape 3a — Découvrir le matériel DMX

Allez dans l'onglet **Setup**. Dans la section **DMX Nodes**, cliquez sur **Discover Nodes**. SlyLED diffuse un paquet ArtPoll ; les ponts Art-Net du réseau répondent sous 3 secondes.

![Onglet Setup après la découverte matérielle — nœud Art-Net affiché](screenshots/walkthrough-533/03a-discover-hardware.png)

Tous les nœuds découverts apparaissent dans la liste avec leur IP, leur port et leur nombre d'univers. Si votre pont n'est pas trouvé :
- Confirmez qu'il est alimenté et sur le même sous-réseau LAN
- Vérifiez que le port UDP 6454 n'est pas bloqué par un pare-feu local
- Certains ponts exigent que l'IP source Art-Net corresponde à leur sous-réseau configuré

---

### Étape 3b — Configurer et démarrer le moteur DMX

Allez dans **Paramètres** → **DMX** :

1. **Routage des univers :** réglez l'univers 1 → votre IP de nœud Art-Net (ou laissez en diffusion `255.255.255.255` pour atteindre tous les nœuds du sous-réseau).
2. Cliquez sur **Start Engine**. L'indicateur d'état devient vert (« Running »).

![Configuration du moteur DMX — routage des univers et démarrage](screenshots/walkthrough-533/03b-dmx-engine.png)
![Routage DMX — univers 1 assigné au pont](screenshots/walkthrough-533/03-dmx-routing.png)

> **Important :** le moteur doit être en marche avant d'ajouter des appareils DMX ou de lancer un étalonnage. Si vous arrêtez et redémarrez l'orchestrateur, redémarrez le moteur ici.

---

### Étape 4 — Ajouter des appareils projecteur motorisé DMX

Allez dans l'onglet **Setup** → cliquez sur **+ DMX Fixture**. L'assistant d'appareil s'ouvre.

**Trouver le bon profil :**
1. Dans le champ **Search**, tapez le nom de votre appareil (p. ex. « Sly Moving Head Super Mini »)
2. Les résultats affichent d'abord les profils locaux, puis communautaires (depuis la bibliothèque partagée), puis OFL (Open Fixture Library)
3. Si un téléchargement de profil communautaire échoue (« imported: 0 »), il peut contenir des types de canaux non pris en charge — repliez-vous sur un profil générique local ou cherchez directement dans OFL
4. Pour un projecteur motorisé 16 canaux générique sans correspondance exacte, cherchez « moving head » dans OFL et importez la correspondance la plus proche

**Appareil 1 (côté cour, stage left) :**
- Nom : `MH1 SL`
- Univers : 1, adresse de départ : 1
- Profil : votre profil de projecteur motorisé
- Cliquez sur **Create Fixture**

![Projecteur motorisé 1 ajouté à l'onglet Setup](screenshots/walkthrough-533/04a-mh1-sly-added.png)

**Appareil 2 (côté jardin, stage right) :**
- Nom : `MH2 SR`
- Univers : 1, adresse de départ : 17
- Profil : même profil
- Cliquez sur **Create Fixture**

![Projecteur motorisé 2 ajouté à l'onglet Setup](screenshots/walkthrough-533/04b-mh2-sly-added.png)

---

### Étape 5 — Ajouter un appareil wash ou spot

Ajoutez tout appareil supplémentaire (p. ex. un spot wash 350 W) :
- Nom : `Spot C`
- Univers : 1, adresse de départ : 33
- Profil : votre profil spot/wash

![Spot 350 W ajouté à l'onglet Setup](screenshots/walkthrough-533/05-350w-spot-added.png)

---

### Étape 6 — Enregistrer les nœuds caméra

Toujours sur l'onglet **Setup**, faites défiler jusqu'à la section **Camera Nodes**. Cliquez sur **Discover Cameras**. Les nœuds caméra exécutant `slyled-cam` répondent à la même diffusion UDP que les nœuds exécutants.

Sinon, entrez l'IP de la caméra manuellement et cliquez sur **Add**.

**Caméra 1 (gauche) :**
- IP : l'IP de votre caméra (p. ex. `192.168.10.50`)
- Nom : `Cam Left`

**Caméra 2 (droite) :**
- IP : l'IP de votre seconde caméra
- Nom : `Cam Right`

![Deux caméras ajoutées à l'onglet Setup](screenshots/walkthrough-533/06-cameras-added.png)

Chaque caméra apparaît avec un statut en ligne/hors ligne. Cliquez sur **Snap** pour vérifier le flux en direct.

![Capture caméra 1 — vue côté gauche](screenshots/walkthrough-533/06-cam1_left_hires.png)
![Capture caméra 2 — vue côté droit](screenshots/walkthrough-533/06-cam2_right.png)

> **Remarque :** la découverte caméra renvoie parfois 0 nœud à la première diffusion à cause du timing UDP. Si aucune caméra n'est trouvée, attendez 3 secondes et cliquez de nouveau sur **Discover**. C'est une intermittence connue (#542) en cours de correction dans une prochaine version.

---

### Étape 7 — Positionner tous les appareils sur la disposition

Passez à l'onglet **Layout**. Tous les appareils ajoutés apparaissent dans la barre latérale gauche comme « non placés ».

**Placer et positionner chaque appareil :**

1. Cliquez sur un appareil dans la barre latérale pour le sélectionner
2. Cliquez sur le canevas pour le placer, ou faites-le glisser depuis la barre latérale
3. Double-cliquez sur l'appareil placé pour ouvrir la boîte de dialogue d'édition et entrer des coordonnées exactes

| Appareil | X (mm) | Y (mm) | Z (mm) |
|----------|--------|--------|--------|
| MH1 SL | 1500 | 3000 | 500 |
| MH2 SR | 4500 | 3000 | 500 |
| Spot C | 3000 | 3000 | 500 |
| Cam Left | 0 | 2500 | 0 |
| Cam Right | 6000 | 2500 | 0 |

Cliquez sur **Enregistrer** après avoir entré les coordonnées de chaque appareil.

![Onglet Layout — appareils placés aux positions initiales](screenshots/walkthrough-533/04c-layout-initial.png)
![Onglet Layout — tous les appareils positionnés](screenshots/walkthrough-533/04d-layout-positions.png)
![Appareils caméra positionnés sur la disposition](screenshots/walkthrough-533/06c-cameras-positioned.png)

> **Astuce :** utilisez la vue 3D (bascule dans la barre d'outils de disposition) pour vérifier visuellement que les projecteurs motorisés sont élevés sur le pont et dirigés vers le sol de la scène.

---

### Étape 8 — Ajouter un objet de scène

Allez dans l'onglet **Layout** → cliquez sur **+ Object** dans la barre d'outils.

- **Nom :** `Music Stand`
- **Type :** Prop (mobile — peut être suivi par les projecteurs motorisés)
- **Position :** X : 3000, Y : 0, Z : 2000 (centre scène, au niveau du sol, mi-profondeur)
- **Taille :** 300 × 1200 × 300 mm

Cliquez sur **Enregistrer**. L'objet apparaît comme un rectangle étiqueté sur le canevas.

![Objet pupitre sur la disposition](screenshots/walkthrough-533/08-music-object.png)

---

### Étape 9 — Lancer l'étalonnage des projecteurs motorisés

Avant que les projecteurs motorisés puissent suivre des positions avec précision, étalonnez chacun. Cette étape exige que les nœuds caméra soient positionnés dans la disposition (étape 7) et que le moteur DMX soit en marche (étape 3b).

Dans l'onglet **Layout**, double-cliquez sur `MH1 SL`. Cliquez sur **Calibrate**.

![Boutons d'étalonnage dans la boîte de dialogue d'édition d'appareil](screenshots/walkthrough-533/07-calibrate-buttons.png)
![Interface de l'assistant d'étalonnage](screenshots/walkthrough-533/07-calibrate-ui.png)

- Sélectionnez **Green** comme couleur de faisceau (bon contraste sur les sols sombres)
- Cliquez sur **Start Calibration**
- L'assistant s'exécute automatiquement à travers huit phases : warmup → découverte → blink-confirm → cartographie/convergence → construction de grille → balayage de vérification → ajustement du modèle → porte paramétrique tenue de côté → sauvegarde
- Répétez pour `MH2 SR`

L'étalonnage prend typiquement de 2 à 4 minutes par projecteur. Pour la référence phase par phase complète — ce que fait chaque phase, combien de temps elle devrait prendre, quels replis existent et quoi vérifier quand une phase cale — voir [Annexe B — Pipeline d'étalonnage de projecteur motorisé](#appendix-b--moving-head-calibration-pipeline-draft).

---

### Étape 10 — Créer des actions

Allez dans l'onglet **Actions**. Vous allez créer deux actions : une visée statique et un balayage en huit.

**Action 1 : Aim Red (spot statique)**
1. Cliquez sur **+ New Action**
2. **Nom :** `Aim Red`
3. **Type :** `DMX Scene`
4. **Couleur :** rouge (255, 0, 0)
5. **Dimmer :** 255
6. Cliquez sur **Save Action**

![Action Aim Red — visée au centre de la scène](screenshots/walkthrough-533/09-aim-red.png)

**Action 2 : Figure Eight (balayage dynamique)**
1. Cliquez sur **+ New Action**
2. **Nom :** `Figure Eight`
3. **Type :** `Track`
4. **Target Objects :** laisser vide (suivre tous les objets mobiles)
5. **Cycle Time :** 4000 ms
6. Cliquez sur **Save Action**

![Action de suivi Figure Eight](screenshots/walkthrough-533/11d-figure8-action.png)

---

### Étape 11 — Construire une chronologie

Allez dans l'onglet **Runtime** (libellé **Shows** dans certaines versions). Cliquez sur **+ New Timeline**.

Une boîte de dialogue demande le nom — entrez `Walkthrough Show`. Une seconde boîte de dialogue demande la durée — entrez `120` (secondes). Cliquez sur OK.

![Éditeur de chronologie avec pistes](screenshots/walkthrough-533/11e-timeline.png)

**Ajouter des pistes :**

Pour chaque appareil ou groupe, cliquez sur **+ Add Track** :
- Piste pour `MH1 SL` — ajoutez un clip : `Aim Red` à 0 s, durée 10 s
- Piste pour `MH1 SL` — ajoutez un clip : `Figure Eight` à 10 s, durée 110 s
- Piste pour `MH2 SR` — ajoutez un clip : `Figure Eight` à 0 s, durée 120 s
- Piste pour `All Performers` — ajoutez un clip avec une couleur de wash ambiant

> L'action Track (type 18) s'évalue en temps réel pendant la lecture et n'a pas besoin d'être précalculée image par image — elle lit les positions des objets en direct à 40 Hz.

---

### Étape 12 — Précalculer et démarrer la lecture

1. Cliquez sur **Bake** — le moteur compile la chronologie en séquences d'actions par appareil. La progression affiche le nombre d'images.
2. Cliquez sur **Start** — la lecture synchronisée NTP commence.

Observez la vue **Runtime** :
- Les cônes de faisceau s'animent en 3D au fil de la chronologie
- Le motif en huit se déplace dans l'espace de la scène
- La sortie DMX est envoyée via Art-Net aux appareils physiques

![Vue Runtime avec cônes de faisceau animés](screenshots/walkthrough-533/11f-runtime.png)

Pour tester un blackout :
- Cliquez sur **Stop**, puis déclenchez une action **Blackout** depuis le panneau Paramètres → Contrôle de groupe

![État blackout — tous les faisceaux éteints](screenshots/walkthrough-533/10-blackout.png)

---

### Étape 13 — Enregistrer le projet

Allez dans **Paramètres** → **Projet** → cliquez sur **Export**. Un fichier `.slyshow` est téléchargé contenant tous les appareils, positions de disposition, objets, enregistrements de caméras, données d'étalonnage, actions et chronologies.

Pour recharger : Paramètres → Projet → **Import** → sélectionnez le fichier `.slyshow`.

![Projet enregistré — tout l'état groupé dans le fichier .slyshow](screenshots/walkthrough-533/12-saved.png)

---

### Dépannage du walkthrough

| Problème | Solution |
|----------|----------|
| **Aucun nœud Art-Net découvert** | Confirmez que le pont est sur le même sous-réseau ; port UDP 6454 non bloqué |
| **Le moteur DMX ne démarre pas** | Vérifiez Paramètres → DMX → vérifiez que le routage des univers est configuré |
| **Le téléchargement de profil communautaire échoue** | Le profil a des types de canaux non pris en charge — utilisez un profil local ou OFL à la place |
| **La position de l'appareil se réinitialise à 0,0,0** | Assurez-vous que `saveFixture()` se termine avant de changer d'onglet ; utilisez le bouton Enregistrer de la boîte de dialogue d'édition |
| **La découverte caméra renvoie 0** | Attendez 3 s et réessayez — la première diffusion peut arriver avant que le socket soit lié (#542) |
| **L'étalonnage n'arrive pas à détecter le faisceau** | Tamisez la lumière ambiante, vérifiez que la couleur du faisceau contraste avec le sol, vérifiez que la caméra peut voir le faisceau |
| **Figure Eight ne bouge pas les projecteurs motorisés** | Vérifiez que l'action Track n'a pas de restriction `trackFixtureIds` ; confirmez que le moteur est en marche |
| **Les pistes de chronologie manquent après création** | Ajoutez les pistes manuellement après la création de la chronologie — elles ne sont pas créées automatiquement |

---

