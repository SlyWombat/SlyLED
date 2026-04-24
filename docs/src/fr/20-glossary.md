## 20. Glossaire

SlyLED touche à l'éclairage, au réseau, à la vision par ordinateur et au firmware embarqué — cela fait beaucoup d'acronymes et de jargon. Cette section développe chaque acronyme utilisé ailleurs dans le manuel et définit les termes du domaine qui n'ont pas de développement littéral (« précalcul », « univers », « blink-confirm », etc.).

Les entrées sont classées alphabétiquement sur la colonne **Terme**. Pour les acronymes qui se regroupent autour d'un concept commun (p. ex. `RX` / `RY` / `RZ`), le groupe apparaît sous le premier membre.

| Terme | Développement | Définition en langage clair | Où il apparaît |
|-------|---------------|------------------------------|----------------|
| **API** | Application Programming Interface | L'ensemble des points de terminaison HTTP qu'un programme expose pour que d'autres programmes les appellent. | §19 Référence API ; routes `/api/*` partout. |
| **ARM** | Advanced RISC Machine | Architecture CPU utilisée par la Giga R1, le Raspberry Pi et l'Orange Pi. « Lent sur ARM » dans le manuel désigne ces cartes. | §14 Nœuds caméra (environnements d'estimation de profondeur). |
| **Art-Net** | — | Protocole DMX-sur-Ethernet par Artistic Licence. L'orchestrateur envoie des paquets `ArtDMX` à un pont Art-Net, qui les relaie aux appareils DMX. | §2 Walkthrough étape 3b ; §12 Profils DMX. |
| **ArtDMX** | Paquet DMX Art-Net | Un paquet de données Art-Net de 512 canaux. | §2 Walkthrough étape 3b. |
| **ArtPoll** | Paquet de découverte Art-Net | La diffusion de découverte Art-Net utilisée pour trouver les ponts. | §2 Walkthrough étape 3a. |
| **baking** (précalcul) | — | Compilation d'une chronologie en un flux de scènes DMX pré-calculé afin que la lecture n'ait pas à recalculer les effets à chaque image. | §10 Précalcul et lecture. |
| **battleship search** | — | Stratégie de découverte lors de l'étalonnage qui sonde une grille grossière sur toute la plage pan/tilt avant de raffiner — plus rapide qu'un balayage dense quand la région atteignable du faisceau est petite. | Annexe B §B.3 Découverte. |
| **BFS** | Breadth-First Search | Algorithme de parcours de graphe qui explore vers l'extérieur depuis un point de départ, un anneau à la fois. Utilisé dans l'étalonnage de projecteur motorisé pour cartographier la frontière de la région visible à partir d'une première position de faisceau détectée. | Annexe B §B.3 Cartographie. |
| **blink-confirm** | — | Contrôle de rejet des reflets : après détection d'un pixel candidat de faisceau, nudger légèrement pan et tilt et vérifier que le pixel détecté bouge réellement. Un reflet reste en place ; un vrai faisceau bouge. | Annexe B §B.3 ; issue #658. |
| **CPU** | Central Processing Unit | Le processeur principal. | §14 Nœuds caméra. |
| **CRGB** | Color RGB | Structure C++ de FastLED pour un seul pixel RGB. | Modules firmware (`GigaLED.h`). |
| **CRUD** | Create, Read, Update, Delete | Raccourci pour « les quatre opérations de base de type base de données ». | §4 Configuration des appareils ; §12 Profils DMX. |
| **CSI** | Camera Serial Interface | Port natif de caméra à nappe du Raspberry Pi (non pris en charge dans SlyLED v1.x — utilisez des caméras USB). | §14 Nœuds caméra. |
| **dark reference** | — | Capture prise avec tous les faisceaux d'étalonnage éteints, soustraite des images suivantes afin que la détection de faisceau ne soit pas trompée par l'éclairage ambiant. | Annexe A §A.5 ; issue #651. |
| **DHCP** | Dynamic Host Configuration Protocol | Comment les appareils d'un réseau obtiennent une adresse IP. Le nom d'hôte de la carte apparaît dans DHCP afin que les routeurs la listent par nom. | §14 Déploiement des nœuds caméra. |
| **DMX** | Digital Multiplex | Protocole standard de l'industrie pour le contrôle d'éclairage — 512 canaux par univers, transporté sur câble à paire torsadée ou sur Ethernet (Art-Net / sACN). | §2 Walkthrough ; §12 Profils DMX ; annexe B. |
| **DOF** | Degrees of Freedom | Axes indépendants selon lesquels un système peut se déplacer. Le modèle paramétrique de projecteur motorisé de SlyLED a 6 DOF (yaw, pitch, roll, décalage pan, décalage tilt, plus échelle). | Annexe B §B.3 Ajustement du modèle. |
| **ESP32** | — | Famille de microcontrôleurs Espressif utilisée pour les nœuds exécutants LED (WiFi + double-cœur, jusqu'à 8 chaînes LED). | §4 Configuration des appareils ; §15 Firmware. |
| **extrinsic** | — | La pose d'une caméra (position + rotation) dans l'espace scène/monde. La sortie de solvePnP. À apparier avec **intrinsic**. | Annexe A §A.4. |
| **FastLED** | — | Bibliothèque Arduino pour piloter les rubans LED adressables de type WS2812B. Utilisée sur ESP32 et D1 Mini ; **non** fiable sur la Giga R1 (chemin PWM personnalisé à la place). | §15 Firmware ; particularités matérielles CLAUDE.md. |
| **fixture** (appareil) | — | Tout appareil d'éclairage adressable — une bande LED, un wash DMX, un projecteur motorisé ou une caméra (qui s'enregistre comme un « appareil » plaçable pour avoir une position dans la disposition). | §4 Configuration des appareils ; partout. |
| **FOV** | Field of View | La largeur angulaire qu'une caméra ou un objectif voit. Stocké comme `fovDeg` + `fovType` (horizontal/vertical/diagonal). Utilisé comme repli intrinsèque lorsque de vraies intrinsèques étalonnées ne sont pas disponibles. | Annexe A §A.3. |
| **FPS** | Frames Per Second | Fréquence de rafraîchissement pour la lecture en direct ou l'émulation. | §11 Émulateur d'aperçu de spectacle. |
| **FQBN** | Fully Qualified Board Name | L'identifiant arduino-cli pour une cible de carte, p. ex. `arduino:mbed_giga:giga`. | §15 Firmware. |
| **GET / POST / PUT / DELETE** | — | Méthodes HTTP. GET lit, POST crée/déclenche, PUT met à jour, DELETE supprime. | §19 Référence API. |
| **GPIO** | General-Purpose Input/Output | Broche configurable sur un microcontrôleur — utilisée pour les lignes de données LED sur l'ESP32. | §4 Configuration des appareils (ESP32 uniquement). |
| **homography** (homographie) | — | Matrice 3×3 qui associe des points d'un plan à des points d'un autre via transformation projective. SlyLED utilise une homographie pixel↔sol comme alternative rapide aux extrinsèques 3D complètes pendant l'étalonnage. | Annexe A §A.4. |
| **HSV** | Hue, Saturation, Value | Représentation colorimétrique utilisée pour la détection de faisceau par filtre colorimétrique (les bandes de teinte identifient « le faisceau vert » indépendamment de la luminosité). | Annexe A §A.8 Détection de faisceau. |
| **HTML** | HyperText Markup Language | Le balisage dont est fait le SPA. | §3 Guide des plateformes. |
| **HUD** | Heads-Up Display | Superposition affichant l'état en direct (utilisé dans la vue 3D). | §5 Disposition du plateau. |
| **ID** | Identifier | Toute clé courte qui nomme quelque chose de manière unique (ID d'appareil, ID de marqueur ArUco, etc.). | Partout. |
| **IK** | Inverse Kinematics | Étant donné un point cible, calculer les valeurs pan/tilt qui y visent le faisceau. Le modèle paramétrique de projecteur motorisé fournit l'IK une fois l'étalonnage terminé. | Annexe B §B.3 Ajustement du modèle. |
| **intrinsic** | — | Les paramètres optiques internes d'une caméra : distance focale (`fx`, `fy`), point principal (`cx`, `cy`) et distorsion de l'objectif. Indépendants de l'emplacement de la caméra — cela, c'est l'**extrinsic**. | Annexe A §A.3. |
| **IP** | Internet Protocol | Schéma d'adressage des appareils en réseau (`192.168.x.y`). | §14 Nœuds caméra. |
| **JPEG** | Joint Photographic Experts Group | Format d'image compressé utilisé pour les captures de caméra. | §14 Nœuds caméra. |
| **JSON** | JavaScript Object Notation | Le format texte utilisé pour les corps de requête/réponse API et les fichiers de données persistés. | §19 Référence API. |
| **kinematic model** (modèle cinématique) | — | Modèle mathématique qui décrit comment les moteurs d'un appareil traduisent les valeurs DMX pan/tilt en une direction de visée dans l'espace scène. SlyLED ajuste un modèle cinématique à 6 DOF par projecteur motorisé étalonné. | Annexe B §B.3. |
| **LAN** | Local Area Network | Le réseau physique/WiFi que l'orchestrateur et les nœuds exécutants partagent. | §2 Walkthrough. |
| **LED** | Light-Emitting Diode | Les LED RGB adressables (WS2812B et similaires) sont le type d'appareil principal. | §4 Configuration des appareils. |
| **LM** | Levenberg–Marquardt | Solveur de moindres carrés non linéaires utilisé pour ajuster le modèle paramétrique de projecteur motorisé aux échantillons d'étalonnage. | Annexe B §B.3 Ajustement du modèle. |
| **LSQ** | Least-Squares | La technique d'ajustement que LM raffine. Lorsque l'étalonnage bascule sur un ajustement « basé sur la médiane », c'est parce que LSQ n'a pas convergé. | Annexe A §A.6. |
| **Mbed OS** | — | Le système d'exploitation temps réel fonctionnant sur la Giga R1. Explique pourquoi `analogWrite()` et certaines bibliothèques se comportent différemment sur la Giga. | Particularités matérielles CLAUDE.md. |
| **mDNS** | Multicast DNS | DNS sans configuration sur multicast — comment `SLYC-1234.local` se résout sur le LAN sans serveur DNS. | §14 Déploiement des nœuds caméra. |
| **NTP** | Network Time Protocol | Comment les nœuds exécutants synchronisent leurs horloges pour que les heures de démarrage soient coordonnées. | Protocole (`Globals.cpp`). |
| **NVS** | Non-Volatile Storage | Magasin clé/valeur adossé à la flash de l'ESP32. SlyLED utilise l'espace de noms `"slyled"`. Équivalent à l'EEPROM sur le D1 Mini. | §4 Configuration des appareils. |
| **ONNX** | Open Neural Network Exchange | Format portable de fichier de réseau neuronal. YOLOv8n et Depth-Anything-V2 sont livrés comme fichiers ONNX afin d'être exécutés via `onnxruntime` sur ARM. | §14 Nœuds caméra. |
| **OFL** | Open Fixture Library | Base de données communautaire de profils d'appareils DMX. SlyLED peut importer des JSON OFL. | §12 Profils DMX. |
| **orchestrator** (orchestrateur) | — | Le serveur Flask de bureau (Windows/Mac) ou le parent Giga qui héberge le SPA, conçoit les spectacles et pilote les nœuds exécutants et caméras. L'un des trois niveaux. | §1 Premiers pas. |
| **OS** | Operating System | — | Matériel CLAUDE.md. |
| **OTA** | Over-the-Air | Mise à jour firmware poussée par WiFi au lieu d'USB. | §15 Firmware et mises à jour OTA. |
| **PDF** | Portable Document Format | Le format du manuel empaqueté. Généré par `tests/build_manual.py`. | Annexe C. |
| **performer** (nœud exécutant) | — | Un nœud exécutant LED ESP32, D1 Mini ou enfant Giga. L'un des trois niveaux. | §1 Premiers pas. |
| **PnP / solvePnP** | Perspective-n-Point | Algorithme OpenCV qui calcule la pose 3D d'une caméra à partir de ≥3 correspondances 2D↔3D connues. `SOLVEPNP_SQPNP` est le solveur préféré ; `SOLVEPNP_ITERATIVE` est la solution de repli. | Annexe A §A.4. |
| **PNG** | Portable Network Graphics | Format d'image sans perte utilisé pour les captures d'écran. | §2 Walkthrough. |
| **PR** | Pull Request | Flux de travail Git/GitHub — changement proposé sur une branche, relu avant fusion. | Annexe C §C.4. |
| **PWM** | Pulse-Width Modulation | Technique de variation où la LED est allumée et éteinte rapidement. Sur la Giga R1, c'est implémenté en logiciel parce que `analogWrite()` est interdit sur les broches RGB embarquées. | Particularités matérielles CLAUDE.md. |
| **QA** | Quality Assurance | Rôle de test — dans le flux de travail SlyLED, la QA exécute les suites Playwright + tests et ouvre des issues plutôt que de patcher le code source. | Annexe C. |
| **QR** | Quick Response (code) | Code-barres 2D. Pas la même chose qu'un marqueur ArUco — ArUco est conçu pour solvePnP, QR pour les charges utiles de données. | — |
| **RANSAC** | Random Sample Consensus | Algorithme robuste d'ajustement de plan — échantillonne de petits sous-ensembles aléatoires, trouve le modèle avec le plus d'inliers. SlyLED l'utilise pour détecter les plans sol et mur dans des nuages de points bruités. | Annexe A §A.7. |
| **reprojection RMS** | — | Après solvePnP, projeter les points 3D à travers la pose résolue et mesurer la distance en pixels jusqu'aux coins détectés. Rapporté comme la racine quadratique moyenne sur tous les points. <2 px est excellent, 2–5 px est utilisable, >5 px signifie que quelque chose ne va pas. | Annexe A §A.4. |
| **RGB / RGBW** | Red, Green, Blue [, White] | Modèles colorimétriques LED standard. RGBW ajoute une LED blanche dédiée pour des blancs plus purs. | §4 Configuration des appareils. |
| **RMS** | Root-Mean-Square | Agrégation en moyenne quadratique des erreurs (`sqrt(mean(x²))`). Plus sensible aux valeurs aberrantes qu'une moyenne simple — c'est pourquoi elle est utilisée comme métrique de qualité d'étalonnage. | Annexe A §A.4. |
| **Rodrigues** | — | Conversion mathématique entre un vecteur de rotation (`rvec` issu de solvePnP) et une matrice de rotation 3×3. `cv2.Rodrigues()`. | Annexe A §A.4. |
| **RSSI** | Received Signal Strength Indicator | La puissance du signal WiFi qu'un nœud exécutant entend. Rapportée en dBm ; l'orchestrateur la stocke comme une magnitude non signée, donc « 69 » signifie « −69 dBm ». | Charge utile PONG du protocole UDP. |
| **RTOS** | Real-Time Operating System | Un OS avec des garanties de timing déterministes. Mbed OS sur la Giga est un RTOS. | Matériel CLAUDE.md. |
| **runner** | — | Un séquenceur d'étapes chargé dans un nœud exécutant. Chaque étape est une action (couleur, motif, plage LED) avec une durée ; le runner boucle la liste d'étapes en synchronisation avec l'orchestrateur. | §4 Configuration des appareils ; §13 Spectacles préréglés. |
| **RX / RY / RZ** | — | Rotations autour des axes X, Y, Z du repère de la scène, en degrés. En schéma v2 : `rx` = pitch, `ry` = roll, `rz` = yaw/pan. Ne lisez jamais `rotation[1]` ou `rotation[2]` directement — passez toujours par `rotation_from_layout()`. | Annexe A §A.9. |
| **sACN** | Streaming ACN | Alternative DMX-sur-Ethernet à Art-Net, définie par la RFC 7724. SlyLED parle les deux. | §12 Profils DMX. |
| **SCP** | Secure Copy Protocol | Transfert de fichiers sur SSH. Comment le firmware caméra atteint l'Orange Pi / Raspberry Pi. | §15 Firmware → Déploiement caméra. |
| **solvePnP** | — | Voir **PnP**. | Annexe A §A.4. |
| **SPA** | Single-Page Application | L'interface d'orchestrateur de bureau est une page HTML unique qui charge des modules JavaScript au lieu de naviguer entre pages. | §3 Guide des plateformes. |
| **SQPNP** | — | Une variante spécifique de l'algorithme solvePnP (`cv2.SOLVEPNP_SQPNP`) choisie parce qu'elle tolère moins de correspondances que le solveur itératif. | Annexe A §A.4. |
| **SRAM** | Static Random-Access Memory | La RAM rapide et volatile d'un microcontrôleur. Budget serré sur le D1 Mini — le manuel met en garde contre les objets String et l'allocation sur le tas. | Règles de performance CLAUDE.md. |
| **SSH** | Secure Shell | Protocole de connexion distante chiffré. Comment l'orchestrateur atteint les shells des nœuds caméra pour le déploiement firmware. | §15 Firmware → Déploiement caméra. |
| **SVG** | Scalable Vector Graphics | Format d'image vectoriel utilisé par les exportateurs de diagrammes. | Annexe C. |
| **TCP** | Transmission Control Protocol | Réseau fiable, orienté connexion. Le trafic HTTP (pages de configuration, appels API) circule sur TCP. | Discussion du protocole UDP. |
| **tiling** | — | Détection de style Sliced-Aided Hyper-Inference (SAHI) : découper une grande image en patchs qui se chevauchent, exécuter le détecteur sur chacun, recoudre les résultats. Améliore la précision de détection des petits objets au prix du temps d'exécution. Contrôlé par l'option `tile` de `/scan`. | §14 Nœuds caméra. |
| **TTL** | Time-To-Live | Délai après lequel une ressource (p. ex. une réservation de projecteur motorisé) expire automatiquement. Les réservations de mover-control ont un TTL de 15 s. | Annexe B §B.7. |
| **UDP** | User Datagram Protocol | Réseau sans connexion, au mieux. Utilisé pour tout le trafic orchestrateur↔nœud exécutant (découverte, actions, contrôle de runner) parce qu'il est à faible latence et que le protocole filaire tolère une perte occasionnelle de paquets. | Protocole filaire ; CLAUDE.md §Protocole binaire UDP. |
| **UI** | User Interface | — | Partout. |
| **universe** (univers) | — | Un espace d'adressage DMX — 1–512 canaux. Un spectacle couvre généralement plusieurs univers ; Art-Net les adresse comme `net.subnet.universe`. | §12 Profils DMX. |
| **URL** | Uniform Resource Locator | Adresse web. | §14 Nœuds caméra. |
| **USB** | Universal Serial Bus | — | §15 Flash USB firmware. |
| **V4L2** | Video for Linux 2 | L'API de capture vidéo du noyau utilisée par les nœuds caméra (`cv2.VideoCapture` sur Orange Pi / Raspberry Pi). Les nœuds vidéo SoC ISP comme `sunxi-vin` et `bcm2835-isp` sont filtrés — seules les caméras USB ordinaires s'enregistrent. | §14 Nœuds caméra. |
| **WiFi** | — | Réseau sans fil 802.11. Les nœuds exécutants et les nœuds caméra rejoignent le LAN de l'orchestrateur par WiFi. | §2 Walkthrough. |
| **WLED** | — | Firmware open source populaire pour les contrôleurs LED à base d'ESP32/8266. SlyLED inclut un pont pour que les appareils WLED apparaissent comme des nœuds exécutants. | §4 Configuration des appareils ; `desktop/shared/wled_bridge.py`. |
| **WS2812B** | — | Puce LED RGB adressable courante (alias « NeoPixel »). Le périphérique RMT de l'ESP32 la pilote en matériel ; le D1 Mini la bit-bang en logiciel. | §4 Configuration des appareils. |
| **YOLO** | You Only Look Once | Réseau neuronal de détection d'objets en une seule passe. Les nœuds caméra SlyLED exécutent YOLOv8n via ONNX Runtime pour la détection de personne/objet sur `POST /scan`. | §14 Nœuds caméra. |
| **ZIP** | — | Format de fichier d'archive, utilisé pour le paquet de version. | §15 Registre firmware. |

> **Pas sûr de la signification d'un terme ?** Si un terme apparaît dans le manuel mais n'est pas dans ce tableau, c'est un bug dans le glossaire — ouvrez une issue ou une PR contre [#663](https://github.com/SlyWombat/SlyLED/issues/663).

---

<a id="appendix-a"></a>

