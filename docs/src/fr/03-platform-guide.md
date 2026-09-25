<!-- review-status: pending -->
## 3. Guide des plateformes

### Bureau Windows (SPA)
L'interface principale de conception et de contrôle. SPA complète à 7 onglets avec mise en page 2D/3D, éditeur de Timeline, effets spatiaux, profils DMX et gestion du firmware.

**Lancement :** `powershell -File desktop\windows\run.ps1` ou exécutez `SlyLED.exe`
**Installation :** Exécutez `SlyLED-Setup.exe` (inclut l'icône de la barre système)

### Linux (contrôleur sans écran)
Le même orchestrateur, en service d'arrière-plan sur une machine de baie ou de banc sans écran — un Raspberry Pi 4/5, un NUC ou tout hôte Ubuntu 22.04+ / Debian Bookworm+ (x86_64 ou aarch64). On le pilote depuis un navigateur sur une autre machine ou depuis l'application Android.

**Installation :** chaque version fournit `SlyLED-<version>-linux.tar.gz` (un seul fichier pour x86_64 et aarch64). Soit `curl -fsSL https://raw.githubusercontent.com/SlyWombat/SlyLED/main/desktop/linux/install.sh | sudo bash -s -- --release latest` (téléchargement et vérification SHA-256), soit téléchargez-le, `tar xzf SlyLED-<version>-linux.tar.gz` puis `sudo bash SlyLED-<version>/desktop/linux/install.sh`. Aucun clone git n'est nécessaire. Le code est installé dans `/opt/slyled` avec son propre environnement Python, le compte de service `slyled` est créé (membre de `dialout`, pour que l'onglet Firmware puisse flasher les cartes USB) et le service `slyled` démarre sur le port 8080. Ouvrez ensuite `http://<hôte>:8080`.
**Données :** projets, réglages et journaux sont conservés dans `/var/lib/slyled/SlyLED/data` ; le firmware téléchargé dans `/var/lib/slyled/SlyLED/firmware`.
**Journaux :** `journalctl -u slyled -f`
**Mise à jour :** lancez l'installateur d'une archive plus récente (ou `--release latest`) — il affiche `upgrading vX → vY` et conserve vos données. `bash install.sh --version` indique la version installée.
**Désinstallation :** `sudo bash desktop/linux/install.sh --uninstall` (conserve les données ; ajoutez `--purge` pour les supprimer avec le compte de service).
**Pare-feu :** si ufw ou firewalld est actif, l'installateur ouvre TCP 8080 et UDP 4210, 4211, 5568 et 6454.
**Réseau :** la découverte, Art-Net et le balayage des caméras utilisent toutes les interfaces réseau physiques ; les ponts Docker/VM et les tunnels VPN sont ignorés.

### Docker (hôtes Linux)
L'orchestrateur existe aussi en image, `ghcr.io/slywombat/slyled:<version>` (x86_64 et arm64) : `docker run -d --name slyled --network host --restart unless-stopped -e TZ=America/Toronto -v slyled-data:/var/lib/slyled ghcr.io/slywombat/slyled:<version>`, ou `docker compose up -d` avec le `docker-compose.yml` joint à la version.

**Le réseau hôte est obligatoire.** La découverte, le balayage HinksPix, Art-Net et le multicast sACN doivent atteindre votre réseau local, ce qu'un réseau pont Docker ne permet pas. L'image est donc **réservée à Linux** : Docker Desktop (Windows/macOS) exécute les conteneurs dans une VM. Sous Windows, utilisez `SlyLED-Setup.exe`.
**Données :** le volume `slyled-data` contient projets, réglages et firmware.
**Pare-feu :** ouvrez TCP 8080 et UDP 4210, 4211, 5568 et 6454 sur l'hôte.
**Ne le lancez pas à côté du service `slyled`** sur le même port — le second s'arrête avec « already answering on port 8080 ».

### Application Android
Outil opérateur en direct pour exécuter des spectacles depuis votre téléphone. Se connecte au serveur de bureau par Wi-Fi. Depuis la version 1.8.1, l'onglet Contrôle est refait en **Surface de commande** — voir #888 / `docs/design/mobile_ui_redesign.md`.

**Installation :** Transférez `slyled-android.apk` sur votre téléphone et installez-le (sideload).
**Connexion :** Numérisez le code QR de l'onglet Paramètres du bureau, ou entrez l'adresse IP du serveur et le port manuellement.

![Écran de connexion Android](screenshots/android/android-connection.png)

**Barre de navigation inférieure (3 onglets) :** Scène / Contrôle / État. Les Paramètres se trouvent dans l'engrenage ⚙ en haut à droite, pas dans la barre inférieure.

**Gestes de la barre supérieure :**
- **Appui long sur le logo SlyLED** → blackout instantané (master = 0). Double-haptique soutenu. Le seul geste « bouton rouge » ; les autres actions de sécurité vivent comme boutons par page.
- **Pastille de connexion** — point vert = Connecté ; pulsation orange lente = Reconnexion (dégradée) ; pulsation rouge rapide = Hors ligne. Tapez pour réessayer.
- **Engrenage ⚙ Paramètres** — nom du serveur, dimensions de la scène, calibration de la Luminosité automatique, export/import de la configuration, déconnexion.

**Onglet Scène** — viewport en direct affichant tous les projecteurs avec cônes de faisceau, marqueurs d'objets suivis, plancher quadrillé. Pincement pour zoomer + glissement pour panoramiquer.

![Vue Scène Android](screenshots/android/android-stage-idle.png)

**Onglet Contrôle (refait pour la v1.8.1) :** ancre Now Playing persistante au-dessus d'un pager à 4 pages.

- **Master** *(page par défaut)* — curseur de luminosité global avec pas de ±5 % + halo lors du glissement. Bascule Luminosité automatique (déplacée depuis Paramètres) + sélecteur de source (Micro / Lecture / USB) + indicateur d'enveloppe en direct.

  ![Contrôle · Master](screenshots/android/android-control-master.png)

- **Grab** — vignettes de têtes mobiles montrant la couleur courante + flèche de direction pan/tilt. Rangée de favoris en haut (étoilez pour ajouter). Tapez une vignette → Mode contrôleur (pan/tilt piloté par le gyroscope à 20 Hz). Bouton « Tous au repos » en haut à droite pour ramener toutes les têtes.

  ![Contrôle · Grab](screenshots/android/android-control-grab.png)

- **Fixtures** — projecteurs DMX non mobiles (machines à bulles, machines à fumée, washes, pars, stroboscopes) avec raccourcis pilotés par profil : 🫧 bulles, 💨 fumée faible/moyenne/forte, 🌀 ventilateur lent/moyen/rapide, 💡 nuanciers de couleurs, 🟣 UV, ⚡ stroboscope momentané, 🧼 maintien-pour-nettoyer. « Plus de contrôles → » ouvre une feuille par canal avec des curseurs de capacité. Bouton « Arrêter tous les effets » en haut à droite tue stroboscopes + bulles/fumée en parallèle.

  ![Contrôle · Fixtures](screenshots/android/android-control-fixtures.png)

- **Shows** — sections étoilés → récents → tous, classés par dernier lancement. Lancement en un toucher. Appui long pour étoiler.

  ![Contrôle · Shows](screenshots/android/android-control-shows.png)

**L'ancre Now Playing** se trouve au-dessus du pager — nom, pastille de boucle, temps écoulé / total, barre de progression, ARRÊT et Suivant.

**Onglet État** — surveillance des appareils (Performers en ligne/hors ligne, RSSI, firmware), nœuds caméra avec bouton Suivre pour démarrer/arrêter le suivi de personne, et état du moteur Art-Net/DMX.

![État Android](screenshots/android/android-status.png)

**Feuille Paramètres** (⚙ en haut à droite) — nom du système, unités, dimensions de la scène (L × H × P), mode sombre, journalisation, plus le bloc de configuration de la Luminosité automatique (activation, mode du modèle, curseurs sensibilité/plancher/plafond/attaque/relâchement).

![Paramètres Android](screenshots/android/android-settings.png)

**Mode contrôleur (Grab → tapez une tête mobile) :** Tenez le téléphone et pointez où vous voulez le faisceau — pan/tilt suit l'orientation du téléphone à 20 Hz. Tapez Recentrer pour calibrer, X pour quitter. Démarrage / arrêt par appui protégés par nonce+ACK (#825). À la première utilisation sur un nouveau téléphone, l'assistant d'axe (#869) mesure les axes du repère du téléphone ; environ 10 secondes.

### Configuration du firmware (ESP32/D1 Mini)
Chaque Performer propose une page de configuration à 3 onglets à l'adresse `http://<adresse-ip>/config` :
- **Tableau de bord** — nom d'hôte, version du firmware, statut de l'action active
- **Paramètres** — nom de l'appareil, description, nombre de chaînes
- **Configuration** — nombre de LED par chaîne, longueur, direction, broche GPIO (ESP32)

---

