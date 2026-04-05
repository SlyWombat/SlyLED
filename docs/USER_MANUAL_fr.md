# Manuel d'utilisation SlyLED — Systeme d'eclairage volumetrique 3D (v8.5)

## Table des matieres
1. [Premiers pas](#1-premiers-pas)
2. [Guide des plateformes](#2-plateformes)
3. [Configuration des projecteurs](#3-configuration-des-projecteurs)
4. [Mise en page de la scene](#4-mise-en-page)
5. [Objets de scene](#5-objets-de-scene)
6. [Effets spatiaux](#6-effets-spatiaux)
7. [Action Track](#7-action-track)
8. [Construction d'une Timeline](#8-timeline)
9. [Compilation et lecture](#9-compilation)
10. [Emulateur de previsualisation](#10-previsualisation)
11. [Profils de projecteurs DMX](#11-profils-dmx)
12. [Spectacles predefinis](#12-spectacles-predefinis)
13. [Firmware et mises a jour OTA](#13-firmware)
14. [Limites du systeme](#14-limites)
15. [Depannage](#15-depannage)
16. [Reference rapide API](#16-api)

---

## 1. Premiers pas

SlyLED est un systeme de controle d'eclairage LED et DMX a trois niveaux :
- **Orchestrateur** (application de bureau Windows/Mac ou application Android) — concevoir des spectacles et controler la lecture
- **Performers** (ESP32/D1 Mini) — executer les effets LED sur le materiel
- **Pont DMX** (Giga R1 WiFi) — transmettre Art-Net/sACN vers les projecteurs DMX

### Demarrage rapide
1. Lancez l'application de bureau : `powershell -File desktop\windows\run.ps1` (Windows) ou `bash desktop/mac/run.sh` (Mac)
2. Ouvrez le navigateur a l'adresse `http://localhost:8080`
3. Allez dans l'onglet **Configuration**, cliquez sur **Decouvrir** pour trouver les Performers sur votre reseau
4. Allez dans l'onglet **Mise en page** pour positionner les projecteurs sur la scene
5. Allez dans l'onglet **Execution**, chargez un **Spectacle predefini**, cliquez sur **Compiler et demarrer**

---

## 2. Guide des plateformes

### Bureau Windows (SPA)
L'interface principale de conception et de controle. SPA complete a 7 onglets avec mise en page 2D/3D, editeur de Timeline, effets spatiaux, profils DMX et gestion du firmware.

**Lancement :** `powershell -File desktop\windows\run.ps1` ou executez `SlyLED.exe`
**Installation :** Executez `SlyLED-Setup.exe` (inclut l'icone de la barre systeme)

### Application Android
Compagnon mobile pour la surveillance et le controle de lecture. Disponible sur le meme reseau WiFi que le serveur de bureau.

**Installation :** Transferez `SlyLED.apk` sur votre telephone et installez-le.
**Connexion :** Entrez l'adresse IP du serveur et le port (affiches dans l'onglet Parametres du bureau).

**Fonctionnalites Android :**
- **Tableau de bord** — statut des Performers, indicateurs en ligne/hors ligne
- **Configuration** — visualiser les projecteurs, decouvrir les Performers
- **Mise en page** — canevas 2D avec zoom par pincement, repositionnement par glissement, placement par toucher, cones de faisceau DMX, visualisation des objets, boutons d'acces rapide a la mise en page, affichage de la patrouille pour les objets mobiles
- **Actions** — parcourir et creer des effets LED
- **Execution** — emulateur de spectacle avec points LED et cones de faisceau DMX, compilation/synchronisation/lecture de Timeline, spectacles predefinis
- **Parametres** — nom du serveur, luminosite, reinitialisation d'usine

### Configuration du firmware (ESP32/D1 Mini)
Chaque Performer propose une page de configuration a 3 onglets a l'adresse `http://<adresse-ip>/config` :
- **Tableau de bord** — nom d'hote, version du firmware, statut de l'action active
- **Parametres** — nom de l'appareil, description, nombre de chaines
- **Configuration** — nombre de LED par chaine, longueur, direction, broche GPIO (ESP32)

---

## 3. Configuration des projecteurs

### Que sont les projecteurs ?
Un projecteur est l'entite principale sur la scene. Il encapsule le materiel physique et ajoute des attributs au niveau de la scene :
- **Projecteurs LED** — lies a un Performer enfant, avec des chaines LED
- **Projecteurs DMX** — lies a un univers/adresse DMX, avec un profil et un point de visee

### Ajout de projecteurs LED
1. Allez dans l'onglet **Configuration**, cliquez sur **Decouvrir** pour trouver les Performers
2. Cliquez sur **Ajouter un projecteur** puis selectionnez le type "LED"
3. Liez a un Performer et configurez les chaines (nombre de LED, longueur, direction)

### Ajout de projecteurs DMX (assistant)
Cliquez sur **+ Projecteur DMX** dans l'onglet Configuration pour lancer l'assistant en 3 etapes :
1. **Choisir le projecteur** : Recherchez dans l'Open Fixture Library (700+ projecteurs) ou creez un projecteur personnalise
2. **Definir l'adresse** : Univers, adresse de depart et nom — avec detection de conflits en temps reel
3. **Confirmer** : Verifiez tous les parametres, cliquez sur "Creer le projecteur"

### Moniteur DMX
Parametres puis DMX puis **Moniteur DMX** ouvre une grille en temps reel de 512 canaux par univers. Cliquez sur n'importe quelle cellule pour definir une valeur. Code couleur par intensite.

### Controle de groupe de projecteurs
Parametres puis DMX puis **Controle de groupe** ouvre un panneau de controle pour les groupes de projecteurs. Curseur de variateur principal, curseurs R/V/B, et boutons de preselection rapide des couleurs (Chaud, Froid, Rouge, Eteint).

### Test des canaux DMX
Dans l'onglet Configuration, cliquez sur **Details** sur n'importe quel projecteur DMX pour ouvrir le panneau de test des canaux :
- **Curseurs** pour chaque canal avec sortie DMX en direct
- **Boutons rapides** : Tout allume, Noir, Blanc, Rouge, Vert, Bleu
- **Etiquettes de capacite** indiquant ce que fait chaque plage de valeurs (p. ex. "Stroboscope lent vers rapide")
- Les modifications prennent effet immediatement sur le projecteur physique via Art-Net/sACN

### Types de projecteurs
| Type | Description |
|------|-------------|
| **Lineaire** | Bande LED. Pixels le long d'un chemin. |
| **Point** | Source lumineuse DMX avec cone de faisceau. |
| **Groupe** | Collection de projecteurs cibles comme un seul. |

---

## 4. Mise en page de la scene

### Canevas 2D
L'onglet Mise en page affiche une vue frontale 2D de la scene. Les dimensions de la scene (largeur x hauteur) sont definies dans les Parametres.

La barre d'outils de mise en page fournit : Enregistrer, bascule de mode 2D/3D (affiche le mode actuel en texte), Recentrer, Vue du dessus, Vue de face, Disposition automatique DMX, Afficher/masquer les chaines LED. Les bascules actives sont surlignees en vert.

Utilisez le parametre URL `?tab=` pour un lien direct vers n'importe quel onglet (p. ex. `?tab=layout`).

| Action | Bureau | Android |
|--------|--------|---------|
| **Placer un projecteur** | Glisser depuis la barre laterale | Toucher le projecteur puis toucher le canevas |
| **Deplacer un projecteur** | Glisser sur le canevas | Glisser sur le canevas |
| **Supprimer un projecteur** | Double-clic puis Supprimer | Toucher puis Modifier puis Supprimer |
| **Zoom** | Molette de la souris | Geste de pincement |
| **Panoramique** | — | Glissement a deux doigts |
| **Modifier les coordonnees** | Double-clic | Toucher le projecteur place |
| **Modifier un objet** | Double-clic | Toucher l'objet dans la liste |

**Elements affiches :**
- Lignes de grille a espacement de 1 metre
- Etiquettes des dimensions de la scene
- **Projecteurs LED** : noeuds verts avec lignes de chaines colorees (fleches de direction)
- **Projecteurs DMX** : noeuds violets avec triangles de cone de faisceau vers le point de visee
- **Objets** : rectangles semi-transparents avec etiquettes de nom (rognes aux limites de la scene)
- **Points de visee** : cercles rouges aux points de visee DMX

### Fenetre 3D (bureau uniquement)
Basculez en mode 3D pour une scene Three.js interactive :
- Camera orbitale avec glissement de la souris
- Cones de faisceau en geometrie 3D
- Spheres de visee deplacables pour les projecteurs DMX
- Plans/boites d'objets avec transparence

### Systeme de coordonnees
- **aimPoint[0]** = X (position horizontale, mm)
- **aimPoint[1]** = Y (hauteur depuis le sol, mm) — utilise pour l'axe vertical du canevas 2D
- **aimPoint[2]** = Z (profondeur, mm) — utilise uniquement dans la fenetre 3D
- **canvasW** = largeur de la scene x 1000 (mm)
- **canvasH** = hauteur de la scene x 1000 (mm)

---

## 5. Objets de scene

Les objets representent des elements physiques sur la scene — murs, sols, ponts d'eclairage, ecrans et accessoires/artistes.

### Types d'objets
| Type | Mobilite par defaut | Description |
|------|---------------------|-------------|
| **Mur** | Statique | Mur de fond, verrouille aux dimensions de la scene (largeur x hauteur) |
| **Sol** | Statique | Sol de scene, verrouille aux dimensions de la scene (largeur x (profondeur + 1 m)) |
| **Pont** | Statique | Pont d'eclairage |
| **Ecran** | Statique | Surface de projection |
| **Accessoire** | Mobile | Artiste, element de decor ou element mobile |
| **Personnalise** | Mobile | Objet defini par l'utilisateur |

### Objets verrouilles a la scene
Les objets mur et sol peuvent etre verrouilles aux dimensions de la scene. Lorsque vous modifiez la taille de la scene dans les Parametres, les objets verrouilles se redimensionnent automatiquement.

### Mobilite
- **Statique** : Position fixe. Ne peut pas etre suivi par les lyres.
- **Mobile** : La position peut changer pendant la lecture. Peut etre suivi par les lyres DMX via l'action Track.

### Mouvement de patrouille
Les objets mobiles peuvent patrouiller (osciller) d'avant en arriere pendant la lecture :
- **Axe** : Gauche-droite (X), avant-arriere (Z), ou diagonal (X+Z)
- **Preselections de vitesse** : Lent (cycle de 20 s), Moyen (10 s), Rapide (5 s), ou Personnalise
- **Plage** : Pourcentage de debut/fin de la dimension de la scene (par defaut 10 %--90 %)
- **Lissage** : Sinusoidal ou Lineaire

La patrouille est evaluee a 40 Hz dans la boucle de lecture DMX, avant que les actions Track ne lisent les positions des objets.

### Objets temporels
Les systemes externes peuvent creer des objets ephemeres via `POST /api/objects/temporal` :
- Toujours en memoire (jamais enregistres sur disque)
- Necessitent `ttl` > 0 (duree de vie en secondes)
- Expirent automatiquement a la fin du TTL
- Les mises a jour de position actualisent le TTL
- Affiches dans le visualiseur d'execution avec contour en pointilles et badge de compte a rebours
- Utiles pour l'integration de suivi par camera

---

## 6. Effets spatiaux

### Effets spatiaux vs actions classiques
- **Actions classiques** (Solide, Chenillard, Arc-en-ciel, etc.) : S'executent localement sur chaque Performer. Motif base sur l'index des pixels. Lorsqu'elles sont assignees a des projecteurs DMX, les actions classiques sont automatiquement converties en segments de scene DMX avec les valeurs par defaut appropriees pour le variateur, le pan/tilt.
- **Actions DMX** : Controlent directement les fonctionnalites specifiques au DMX :
  - **Scene DMX** — Definir des valeurs exactes pour le variateur, pan, tilt, stroboscope, gobo, roue de couleurs, prisme
  - **Mouvement Pan/Tilt** — Animer le pan/tilt d'une position de depart a une position d'arrivee dans le temps
  - **Selection de gobo** — Selectionner une position de la roue de gobos
  - **Roue de couleurs** — Selectionner une position de la roue de couleurs
  - **Track** (Type 18) — Faire suivre les objets mobiles par les lyres en temps reel (voir [Action Track](#7-action-track))
- **Effets spatiaux** : Operent dans l'espace 3D. Une sphere de lumiere balayant la scene illumine differents projecteurs a differents moments.

SlyLED prend en charge 19 types d'actions au total : 14 actions classiques LED plus 5 actions DMX/spatiales (Scene DMX, Mouvement Pan/Tilt, Selection de gobo, Roue de couleurs, Track).

### Creation d'un effet spatial
Naviguez vers l'onglet **Actions** puis **+ Nouvel effet spatial**.

| Champ | Description |
|-------|-------------|
| **Forme** | Sphere, Plan ou Boite |
| **Couleur** | Couleur RGB appliquee aux pixels a l'interieur du champ |
| **Taille** | Rayon (sphere), epaisseur (plan) ou dimensions (boite) |
| **Debut/Fin du mouvement** | Positions 3D en millimetres |
| **Duree** | Temps de deplacement du debut a la fin |
| **Lissage** | Lineaire, acceleration, deceleration, acceleration-deceleration |
| **Melange** | Remplacer, Ajouter, Multiplier, Ecran |

---

## 7. Action Track

### Action Track (Type 18)
Fait suivre les objets mobiles par les lyres DMX en temps reel pendant la lecture.

**Fonctionnement :**
1. Creez des objets mobiles (accessoires/artistes) dans l'onglet Mise en page
2. Creez une action Track dans l'onglet Actions
3. Selectionnez les objets cibles et configurez l'assignation
4. Pendant la lecture, la boucle a 40 Hz calcule le pan/tilt pour chaque lyre

**Algorithme d'assignation :**
- Nombre egal de lyres et d'objets : correspondance 1:1
- Plus de lyres que d'objets : repartition uniforme entre les objets
- Plus d'objets que de lyres : cycle a travers les objets (2 s par cible par defaut)

**Champs :**
| Champ | Description |
|-------|-------------|
| trackObjectIds | ID des objets cibles (vide = tous les objets mobiles) |
| trackCycleMs | Temps de cycle lors du cyclage (par defaut 2000 ms) |
| trackOffset | Decalage global [x,y,z] en mm |
| trackFixtureIds | ID de projecteurs specifiques (vide = toutes les lyres) |
| trackFixtureOffsets | Surcharges [x,y,z] par projecteur |
| trackAutoSpread | Repartir plusieurs lyres sur la largeur de l'objet |

---

## 8. Construction d'une Timeline

1. Allez dans l'onglet **Execution** puis **+ Nouvelle Timeline**
2. Definissez le nom et la duree
3. **+ Ajouter un Track** pour chaque projecteur (ou "Tous les Performers")
4. **+ Ajouter un clip** pour assigner des effets avec une heure de debut et une duree
5. Les clips peuvent se chevaucher — ils se melangent selon le mode de melange de leur effet

---

## 9. Compilation et lecture

### Compiler
Compile une Timeline en instructions d'action minimales par Performer :
1. Cliquez sur **Compiler** — la progression affiche le nombre d'images et de segments
2. Cliquez sur **Synchroniser** pour envoyer les instructions aux Performers via UDP
3. Cliquez sur **Demarrer** pour une lecture synchronisee par NTP

### Sortie
- **Segments d'action** : Sequences des 19 types d'actions (14 classiques + 5 DMX/spatiales)
- **Fichiers LSQ** : Donnees RGB brutes par pixel a 40 Hz (telechargeables en ZIP)
- **Donnees de previsualisation** : 1 couleur par chaine par seconde pour l'emulateur

---

## 10. Emulateur de previsualisation

Le bureau et Android incluent une previsualisation en temps reel du spectacle :

### Previsualisation du tableau de bord
Lorsqu'un spectacle est en cours, l'onglet Tableau de bord affiche un canevas de previsualisation en direct de la scene a cote du tableau de statut des Performers et de la barre de progression de la lecture.

### SPA de bureau
Le canevas de l'emulateur apparait dans l'onglet Execution sous la Timeline. Affiche :
- **Projecteurs LED** : Points colores le long des chemins de chaines avec effets de halo
- **Projecteurs DMX** : Triangles de cone de faisceau avec couleurs pilotees par la previsualisation
- **Points de visee** : Cercles rouges aux points de visee
- **Etiquettes des projecteurs** : Noms sous chaque noeud
- **Compteur de temps** : MM:SS ecoule / total

### Application Android
La carte `ShowEmulatorCanvas` affiche :
- Les memes points de chaines LED et cones de faisceau DMX que sur le bureau
- Les objets affiches comme rectangles d'arriere-plan
- Les couleurs de previsualisation mises a jour chaque seconde pendant la lecture

### Visualisation des champs spatiaux
Pendant la lecture d'un spectacle, l'emulateur d'execution affiche les effets spatiaux actifs se deplacant a travers la scene :
- **Sphere** : cercle colore translucide se deplacant le long du chemin de mouvement
- **Plan** : bande translucide horizontale ou verticale balayant la scene
- **Boite** : rectangle translucide a la position actuelle de l'effet
- Noms des effets affiches comme etiquettes a leur position actuelle
- Mise a jour a chaque image, synchronisee avec le temps de lecture ecoule

### Installations DMX uniquement
L'emulateur affiche correctement les installations DMX uniquement (sans Performers LED). Les cones de faisceau violets statiques sont toujours visibles, avec des couleurs en direct lorsqu'un spectacle est en cours.

---

## 11. Profils de projecteurs DMX

### Profils integres
| Profil | Canaux | Fonctionnalites |
|--------|--------|-----------------|
| RGB generique | 3 | Rouge, Vert, Bleu |
| RGBW generique | 5 | Rouge, Vert, Bleu, Blanc, Variateur |
| Variateur generique | 1 | Intensite uniquement |
| Lyre 16 bits | 16 | Pan, Tilt, Variateur, Couleur, Gobo, Prisme |

### Editeur de profils
Onglet Parametres puis **Profils** puis **Nouveau profil** ou **Modifier** :
- Definir les canaux avec nom, type (rouge/vert/bleu/variateur/pan/tilt/etc.), valeur par defaut
- Definir la largeur du faisceau, la plage pan/tilt pour les lyres
- Importer depuis le format JSON Open Fixture Library (OFL)

### Parcourir l'Open Fixture Library
Cliquez sur **Rechercher OFL** dans Parametres puis Profils pour acceder a plus de 700 projecteurs de l'[Open Fixture Library](https://open-fixture-library.org) :

**Recherche** : Tapez un nom de projecteur, un fabricant ou un mot-cle puis les resultats s'affichent avec des boutons d'importation.

**Parcourir par fabricant** : Cliquez sur **Fabricants** pour voir toutes les marques avec le nombre de projecteurs. Cliquez sur un fabricant pour voir tous ses projecteurs. Cliquez sur **Tout importer** pour importer tous les projecteurs de ce fabricant d'un coup.

**Importation en masse** : Depuis les resultats de recherche, cliquez sur **Tout importer** pour importer tous les projecteurs correspondants. Depuis la page d'un fabricant, cliquez sur **Tout importer** pour l'ensemble du catalogue de la marque.

Les projecteurs multi-modes creent automatiquement un profil SlyLED par mode.

### Bibliotheque communautaire de projecteurs
Partagez et decouvrez des profils avec d'autres utilisateurs SlyLED :

1. **Parcourir** : Cliquez sur **Communaute** dans Parametres > Profils pour rechercher, voir les recents ou les populaires
2. **Telecharger** : Cliquez sur Telecharger — importe immediatement dans votre bibliotheque locale
3. **Partager** : Cliquez sur **Partager** sur n'importe quel profil personnalise pour le telecharger vers la communaute
4. **Deduplication** : Le serveur detecte les doublons par empreinte de canaux (memes canaux = meme projecteur)
5. **Recherche unifiee** : Lors de l'ajout d'un projecteur DMX, les requetes de recherche interrogent simultanement Local + Communaute + OFL

Serveur communautaire : https://electricrv.ca/api/profiles/

### Importation/Exportation
- **Communaute** : Partager/telecharger des profils avec d'autres utilisateurs
- **Rechercher OFL** : Parcourir, rechercher et importer en masse depuis l'Open Fixture Library
- **Coller OFL** : Coller du JSON OFL brut pour les projecteurs hors ligne
- **Importer un lot** : Charger un pack de profils precedemment exporte
- **Exporter** : Telecharger tous les profils personnalises en JSON
- **Les profils integres** ne peuvent pas etre modifies ou supprimes

---

## 12. Spectacles predefinis

14 spectacles preconstruits disponibles depuis l'onglet Execution puis **Charger un spectacle** puis **Predefinis** :

| Predefini | Description |
|-----------|-------------|
| Rainbow Up | Plan arc-en-ciel montant du sol au plafond |
| Rainbow Across | Sphere arc-en-ciel balayant de gauche a droite |
| Slow Fire | Effet de feu chaud sur tous les projecteurs |
| Disco | Etincelles pastel scintillantes |
| Ocean Wave | Vague bleue avec teinte sarcelle |
| Sunset Glow | Respiration chaude avec balayage dore |
| Police Lights | Stroboscope rouge avec flash bleu balayant |
| Starfield | Etincelles blanches sur fond sombre |
| Aurora Borealis | Rideau vert avec miroitement violet |
| Spotlight Sweep | Orbe chaud — les lyres le suivent |
| Concert Wash | Projecteur magenta + spot ambre suiveur |
| Figure Eight | Orbes croises — les lyres tracent des chemins en X |
| Thunderstorm | Eclairs — les lyres poursuivent les impacts |
| Dance Floor | Spots orbitaux rapides — suivi rapide |

---

## 13. Firmware et mises a jour OTA

### Flash USB
1. Allez dans l'onglet **Firmware**
2. Selectionnez le port COM et le binaire du firmware
3. Cliquez sur **Flasher** — la progression affiche le pourcentage

### OTA (mise a jour sans fil)
1. Definissez les identifiants WiFi dans l'onglet Firmware
2. Cliquez sur **Verifier les mises a jour** — affiche la comparaison de version par appareil
3. Cliquez sur **Mettre a jour** sur tout Performer obsolete
4. L'appareil redemarre automatiquement apres le flash

### Registre du firmware
`firmware/registry.json` liste les binaires disponibles avec le type de carte et la version. Le systeme OTA compare la version du registre avec le firmware signale par chaque Performer.

---

## 14. Limites du systeme

| Ressource | Teste | Maximum recommande |
|-----------|-------|--------------------|
| Projecteurs DMX | 120 | 500+ |
| Performers LED | 12 | 50 |
| Total des projecteurs | 132 | 500+ |
| Univers | 4 | 32 768 (Art-Net) |
| LED par chaine | 65535 | Adressage uint16 |
| Chaines par enfant | 8 | Constante du protocole |
| Clips de Timeline | 50 | 200+ |
| Spectacles predefinis | 14 | Integres (extensibles) |
| Reponse API (132 projecteurs) | < 1 ms | Sous la milliseconde |
| Memoire (132 projecteurs) | 46 Mo | Mise a l'echelle lineaire |
| Reseau (132 projecteurs) | 221 Ko | Par cycle de test |

Voir `docs/STRESS_TEST.md` pour les donnees de benchmark completes.

---

## 15. Depannage

| Probleme | Solution |
|----------|----------|
| **Vue d'execution vide** | Verifiez que les projecteurs sont positionnes dans la Mise en page. Les installations DMX uniquement s'affichent desormais (correctif v8.1). |
| **Cone de faisceau dans la mauvaise direction** | aimPoint[1] est la hauteur (Y), pas la profondeur (Z). Verifiez les valeurs du point de visee. |
| **Crash JSON sur Android** | Mettez a jour vers la v8.1 — aimPoint est passe de Int a Double. Reinitialisation d'usine : necessite desormais un en-tete de confirmation. |
| **Erreur de sauvegarde de spectacle** | Mettez a jour vers la v8.1 — le point de terminaison `/api/show/export` etait manquant. |
| **Echec de la verification du firmware** | Mettez a jour vers la v8.1 — bugs corriges pour le BOM UTF-8 et l'iteration de dictionnaire dans registry.json. |
| **Fenetre 3D ne s'affiche pas** | Utilisez Chrome/Firefox/Edge avec le support WebGL. |
| **Performers non synchronises** | Verifiez que tous les appareils sont sur le meme reseau WiFi. Actualisez dans l'onglet Configuration. |
| **Taille du canevas incorrecte** | Les dimensions de la scene (Parametres) determinent la taille du canevas : canvasW = scene.w x 1000. |

---

## 16. Reference rapide API

### Scene et mise en page
| Methode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/layout` | Mise en page avec projecteurs et positions |
| GET/POST | `/api/stage` | Dimensions de la scene (l, h, p en metres) |
| GET/POST | `/api/objects` | Objets de scene (murs, sols, ponts, accessoires) |
| POST | `/api/objects/temporal` | Creer des objets temporels (bases sur le TTL) |

### Projecteurs
| Methode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/fixtures` | Lister / creer |
| GET/PUT/DELETE | `/api/fixtures/:id` | CRUD |
| PUT | `/api/fixtures/:id/aim` | Definir le point de visee |

### Spectacles et Timelines
| Methode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/timelines` | Lister / creer |
| POST | `/api/timelines/:id/bake` | Demarrer la compilation |
| POST | `/api/timelines/:id/start` | Demarrer la lecture |
| GET | `/api/show/presets` | Lister les spectacles predefinis |
| GET/POST | `/api/show/export`, `/api/show/import` | Sauvegarder/charger un fichier de spectacle |

### DMX
| Methode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET | `/api/dmx-profiles` | Lister les profils |
| GET | `/api/dmx/patch` | Carte d'adresses des univers |
| POST | `/api/dmx/start`, `/api/dmx/stop` | Controle du moteur |
