<!-- review-status: pending -->

## 13. Spectacles préréglés

Les spectacles préréglés sont une façon de mettre n'importe quel rig
dans une ambiance soignée en un clic — utile pour les balances,
l'ambiance d'avant-spectacle ou une réinitialisation rapide entre
deux cues. Ouvrez-les depuis **Exécution → Charger un spectacle →
Préréglages**.

Le générateur de spectacle inspecte le rig au moment de
l'installation (bandeaux LED, projecteurs motorisés, nœuds caméra)
et adapte chaque thème à ce qui est réellement présent sur le
plateau. Les thèmes incluant une chorégraphie de projecteurs
motorisés auto-classifient tout appareil DMX dont
`panRange > 0` et `tiltRange > 0` comme candidat, puis émettent soit
des balayages coordonnés (mouvement en coordonnées plateau via
`ptStartPos` / `ptEndPos`), soit du suivi de cible en direct via une
action de suivi.

### Thèmes disponibles

| Préréglage | Description | Les projecteurs motorisés font |
| --- | --- | --- |
| **Rainbow Up** | Plan arc-en-ciel montant du sol au plafond | Balayage en coordonnées plateau le long de l'axe Z (#837) |
| **Rainbow Across** | Sphère arc-en-ciel balayant de cour à jardin | Balayage en coordonnées plateau le long de X |
| **Slow Fire** | Effet de feu chaud sur chaque appareil | Faisceau statique, gradateur scintillant |
| **Disco** | Étincelles pastel scintillantes | Course en coordonnées plateau au milieu du plateau |
| **Ocean Wave** | Vague bleue avec teinte sarcelle | Balayage lent avant-arrière |
| **Sunset Glow** | Respiration chaude avec balayage doré | Visée statique, gradateur respirant |
| **Police Lights** | Stroboscope rouge avec flash bleu balayant | Stroboscope + balayage côté lent |
| **Starfield** | Étincelles blanches sur fond sombre | Faisceau statique, gradateur scintillant |
| **Aurora Borealis** | Rideau vert avec miroitement violet | Balayage lent avant-arrière |
| **Aurora Curtain** *(nouveau en v1.7.83)* | Rideau voyageur coordonné — chaque projecteur motorisé monte sur la même cible ruban à un décalage de phase, le rideau voyage donc visiblement le rig au lieu que toutes les têtes bougent à l'unisson. Inclut une couche d'étincelles et des fondus d'entrée / sortie pour que l'effet s'ouvre et se referme proprement. | Suit une cible ruban, ping-pong sur l'axe choisi |
| **Spotlight Follow Person** | Orbe chaud que les personnes détectées par caméra héritent | Action de suivi — les projecteurs poursuivent la personne détectée |
| **Concert Wash** | Inondation magenta + balayage ambre | Balayage en coordonnées plateau, sans suivi |
| **Figure Eight** | Orbes croisés — les projecteurs tracent des chemins en X | Action de suivi — les projecteurs poursuivent des accessoires de patrouille croisés |
| **Thunderstorm** | Éclairs frappant le rig de haut en bas | Éclairs en coordonnées plateau (Z descendant, #837) |
| **Dance Floor** | Spots orbitaux rapides | Course en coordonnées plateau, sans suivi |

### Action de suivi vs balayage

La colonne description vous dit si les projecteurs poursuivent une
cible (« Action de suivi ») ou suivent un trajet de balayage
précalculé (« balayage en coordonnées plateau »). Avant v1.7.83,
plusieurs thèmes promettaient du suivi dans leur description mais
n'émettaient qu'un balayage — corrigé dans #837 et le tableau est
désormais honnête : seuls **Spotlight Follow Person**, **Figure
Eight** et **Aurora Curtain** créent une action de suivi à
l'installation.

### Personnaliser un préréglage installé

L'installation d'un préréglage l'écrit dans l'état régulier
Actions / Chronologie / Objets ; chaque partie est donc éditable
sur place :

- **Recolorier un balayage** — ouvrez l'action sous **Actions**,
  changez RVB. Le précalcul se régénère à la prochaine lecture de
  la chronologie ; pas besoin de réinstaller le préréglage.
- **Limiter une action de suivi à des projecteurs spécifiques** —
  ouvrez la section Avancé de l'action de suivi (chapitre 8),
  réglez `trackFixtureIds` aux projecteurs voulus, laissez vide
  pour la portée tous-projecteurs-motorisés.
- **Ajuster le cycle du suiveur** — Cycle Time (ms) dans la section
  Avancé contrôle la fréquence à laquelle un projecteur saute sur
  une nouvelle cible quand il y en a plus que de projecteurs.
- **Remplacer la cible de patrouille** — pour Aurora Curtain ou
  Figure Eight, l'objet de patrouille sous-jacent est dans
  l'onglet **Disposition / Objets**. Changez son axe, son
  speedPreset ou son motif ; la frame de lecture suivante prend en
  compte le nouveau mouvement.

### Comportement hors lecture

Les spectacles préréglés respectent le même contrat de
comportement au repos que toute autre chronologie (chapitre 17 →
« Comportement au repos ») : le rig se gare en home et la lampe se
ferme à la fin du spectacle, au relâchement de la revendication ou
quand l'opérateur arrête la lecture. L'objet de patrouille de
l'Aurora Curtain s'arrête automatiquement en fin de chronologie
puisque le préréglage l'installe avec
`patrolMode: on-demand` (chapitre 6).

---

## 13. Calibration des projecteurs motorises

**But.** La calibration apprend a SlyLED la realite mecanique de chaque projecteur motorise DMX : comment son support est oriente dans l'espace, ou se trouvent ses axes pan/tilt, et dans quel sens un DMX croissant entraine chaque axe. Une fois calibre, toute visee en coordonnees de scene (depuis une timeline, un puck gyro ou un telephone Android) atteint la bonne combinaison pan/tilt — sans essais-erreurs par projecteur.

### Avant de calibrer
- **Positionnez le projecteur** dans la fenetre 3D (X, Y, Z en mm) et activez `mountedInverted` s'il est suspendu.
- **Definissez le vecteur de visee de repos** en faisant glisser la sphere rouge dans la vue 3D. C'est la position par defaut a l'allumage.
- **Enregistrez une camera** qui voit le faisceau sur le sol. Le flux v2 par cible necessite une calibration par marqueurs ArUco (onglet Camera -> Calibrer).
- **Lancez le moteur Art-Net** — la calibration ecrit directement dans l'univers DMX.
- **Baissez la lumiere de salle.** La detection de faisceau se fait par contraste.

### Lancement de la calibration
Sur la ligne du projecteur DMX, cliquez sur **Calibrer**. L'assistant affiche trois blocs :

**Calibration existante** (si le projecteur est deja calibre) :
- Badge de qualite : **GOOD** (<1,5 deg RMS), **FAIR** (<3 deg RMS), **POOR** (>=3 deg RMS).
- Resume : RMS, maximum, nombre d'echantillons, nombre de conditionnement.
- Resultat du balayage de verification (le cas echeant) : RMS et erreur maximale en pixels sur des points non utilises pour l'ajustement.
- **Recalibrer (rapide, demarrage a chaud)** relance une calibration v2 qui demarre du modele existant — typiquement moins de 10 s contre ~120 s pour une premiere calibration.
- **Voir residus** ouvre le tableau par echantillon (voir "Reprise d'echantillon" plus bas).

**Options** :
- **Couleur de faisceau** — la couleur DMX emise pendant la decouverte et l'echantillonnage. Choisissez ce qui contraste le mieux avec le sol (le vert est generalement sur).
- **Methode** — *Legacy BFS (echantillonnage etendu)* est le flux v1 eprouve. *v2 par cibles* exige une calibration camera ArUco et converge point par point en utilisant le modele parametrique.
- **Chauffage** — balayage optionnel de 30 s a dimmer 0 avant l'echantillonnage. Recommande pour des projecteurs froids : les moteurs, courroies et modules LED derivent thermiquement dans la premiere minute, et calibrer a froid produit un modele qui derive pendant le spectacle.

En cliquant **Start Calibration**, l'assistant passe en vue d'execution :

- **Barre de progression** avec nom de phase (*Chauffage -> Decouverte -> Echantillonnage -> Ajustement -> Verification*).
- **Tableau par cible** (mode v2 uniquement) — chaque cible affiche un point d'etat : en attente (gris) -> convergence (orange) -> converge (vert) ou echoue (rouge). Avec nombre d'iterations et erreur finale en pixels.
- Compter 60 a 120 s pour une premiere calibration, moins de 10 s pour une recalibration a chaud.

### Que signifie un bon ajustement
A la fin vous obtenez le **resume de qualite** :
- **Erreur angulaire RMS** — sous 1 deg c'est excellent, sous 3 deg utilisable, au-dessus de 3 deg quelque chose va mal (mauvais echantillon, mauvaise orientation de support, jeu mecanique).
- **Erreur maximale sur un echantillon** — signale les valeurs aberrantes. Si le max est bien plus grand que le RMS, un seul mauvais echantillon fausse l'ajustement.
- **Nombre de conditionnement** — sous ~20 : sain ; au-dessus de 100 : echantillons geometriquement faibles (colineaires ou groupes). Ecartez les cibles.

Plus la **verification** sur 2-3 points tenus de cote (non vus par le solveur). Bordure verte : generalise bien ; orange/rouge : surajustement possible.

### Reprise d'un mauvais echantillon
Le tableau des residus liste chaque echantillon avec son erreur, colore. Cliquez sur la **X** a cote d'un echantillon pour l'exclure — le serveur reajuste a partir des echantillons restants et rafraichit les metriques. **Afficher les residus en 3D** visualise chaque echantillon comme une courte ligne coloree entre la cible voulue et le point predit au sol.

### Position de repos (#493)
Le vecteur de visee au repos (la sphere rouge en 3D) est maintenant la **position a l'allumage**. Au demarrage du moteur Art-Net, chaque projecteur amorce pan/tilt via `model.inverse(repos)` — les projecteurs s'allument pointes sur quelque chose d'utile, pas ecrases au minimum mecanique.

### Verrou de calibration (#511)
Pendant une calibration, le projecteur est verrouille : timelines, pucks gyro, telephones Android et le panneau de test DMX sont tous bloques. Un retour HTTP 423 *Locked* est renvoye. Le verrou se libere automatiquement en fin, annulation ou erreur, et ne peut jamais fuiter entre redemarrages du serveur.

### Depannage
- **"Beam not found"** — reorientez le projecteur vers la zone visible de la camera au sol et relancez.
- **"No camera homography"** (mode v2) — lancez d'abord la calibration ArUco de la camera.
- **"Only N of M targets converged"** — la camera ne voit pas certains angles. Deplacez-la ou utilisez le mode legacy BFS qui echantillonne toute la zone visible.
- **Badge POOR** — verifiez `mountedInverted`, serrez la mecanique (un etrier lache ajoute une erreur aleatoire), relancez avec chauffage active.

---

