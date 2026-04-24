## 11. Profils de projecteurs DMX

### Profils integres
| Profil | Canaux | Fonctionnalites |
|--------|--------|-----------------|
| RGB generique | 3 | Rouge, Vert, Bleu |
| RGBW generique | 5 | Rouge, Vert, Bleu, Blanc, Variateur |
| Variateur generique | 1 | Intensite uniquement |
| Lyre 16 bits | 16 | Pan, Tilt, Variateur, Couleur, Gobo, Prisme |

### Editeur de profils — pas a pas (#527)

L'editeur de profils relie un canal DMX a ce qu'il *fait* — ce canal rouge
ici, ce canal pan la — et consigne tout comportement attendu par le
micrologiciel du projecteur sur chaque canal (plages de gobos, courbes
de stroboscope, emplacements de roue de couleurs). Une fois le profil
enregistre, tout l'orchestrateur peut piloter le projecteur par appels
semantiques tels que "regler la couleur sur rouge" ou "viser la scene
(1150, 2100)" au lieu de DMX brut.

#### 1. Ou le trouver

Onglet Parametres puis sous-section **Profils**. La liste affiche tous
les profils integres et personnalises, filtrables par categorie
(par / wash / spot / lyre / laser / effet). Chaque ligne comporte :

- **Modifier** — ouvre l'editeur sur le profil selectionne (desactive
  pour les profils integres ; clonez d'abord si vous voulez diverger).
- **Cloner** — copie un profil integre ou communautaire dans votre
  bibliotheque locale sous un nouvel id ; la copie est modifiable.
- **Partager** — televerse un profil personnalise vers le serveur
  communautaire (necessite Internet, debit limite par IP).
- **Supprimer** — retire un profil personnalise (les profils integres
  ne peuvent pas etre supprimes).

Cliquez sur **Nouveau profil** pour demarrer l'editeur sur un profil
vierge. Vous pouvez aussi atteindre l'editeur depuis la carte d'un
projecteur DMX en cliquant sur le nom du profil sous le bouton
**Modifier le profil**.

#### 2. Champs de haut niveau

- **Nom** — libelle visible par l'operateur affiche sur les cartes de
  projecteur et dans le selecteur de profils.
- **Fabricant** — texte libre ; utilise pour le regroupement dans le
  navigateur communautaire et pour la deduplication.
- **Categorie** — `par`, `wash`, `spot`, `moving-head`, `laser`,
  `effect`, `other`. Pilote le generateur de spectacles predefinis.
- **Nombre de canaux** — total des emplacements DMX utilises.
  Mis a jour automatiquement en ajoutant des canaux ; aussi reglable
  explicitement.
- **Mode couleur** — `rgb`, `cmy`, `rgbw`, `rgba`, `single` (variateur
  monochrome) ou `color-wheel-only`. Pilote la maniere dont le moteur
  de spectacles resout une couleur demandee.
- **Plage pan** / **Plage tilt** — balayage mecanique maximal en
  degres. Utilise par la calibration des lyres pour normaliser
  DMX vers angle.
- **Largeur du faisceau** — degres du cone de faisceau. Utilise pour
  le rendu 3D du cone et pour la prediction de couverture de
  marqueurs.

#### 3. Canaux

Chaque canal comporte :

- **Offset** — numero de canal 0-indexe au sein de la plage d'adresses
  du projecteur (pas de l'univers). Un projecteur 16 canaux a les
  offsets 0..15.
- **Nom** — libelle visible par l'operateur. Correspond a la
  documentation du projecteur.
- **Type** — le role *semantique*. Types courants :
  `pan`, `pan-fine`, `tilt`, `tilt-fine`, `dimmer`, `red`, `green`,
  `blue`, `white`, `amber`, `uv`, `color-wheel`, `gobo`, `prism`,
  `focus`, `zoom`, `frost`, `strobe`, `macro`, `reset`.
  Le type est ce que le code en aval lit quand il veut controler "le
  variateur" — vous pouvez renommer le canal mais le type est le
  contrat.
- **Bits** — 8 (un emplacement DMX) ou 16 (deux emplacements :
  grossier a cet offset + fin a offset+1). Utilisez 16 bits pour pan
  et tilt si le projecteur le supporte ; le reste est generalement
  8 bits.
- **Par defaut** — valeur que le moteur ecrit lorsqu'aucun effet ne
  remplace le canal. Laissez vide pour "mettre a 0 au repos".
  Utilisez une valeur non nulle pour les canaux que le projecteur
  doit avoir actifs pour fonctionner (par exemple une macro
  d'allumage de lampe, un emplacement shutter-ouvert).

#### 4. Capacites

Chaque canal peut porter une liste de capacites qui decrivent ce que
signifient les plages de valeurs DMX pour le projecteur :

- **WheelSlot** — position de roue de couleurs ou de gobos. Plage
  `[min, max]`, libelle (`"Rouge"`, `"Ouvert"`, `"Motif 3"`) et — pour
  les roues de couleurs — une **hex `color`** optionnelle comme
  `#FF0000`. Le resolveur RGB vers emplacement de l'orchestrateur
  (utilise par la compilation de spectacle et la calibration des
  lyres) choisit l'emplacement le plus proche par distance
  euclidienne en espace RGB ; chaque emplacement etiquete couleur a
  donc besoin que sa hex soit renseignee. Sans la hex, le pipeline
  RGB retombe silencieusement sur l'emplacement 0 (blanc/ouvert),
  ce qui est le piege du #624.
- **WheelRotation** — plage de roue rotative pour les effets de cycle
  (`"Cycle CW rapide-lent"`, `"Cycle CCW lent-rapide"`).
- **WheelShake** — plages de tremblement sur les roues de gobos.
- **ShutterStrobe** — plage avec un `shutterEffect` de `"Open"`,
  `"Closed"` ou `"Strobe"`. L'aide "ouvrir le shutter pendant la
  calibration" de l'orchestrateur parcourt ces capacites pour
  trouver la bonne valeur DMX.
- **Prism**, **PrismRotation**, **Effect**, **NoFunction** — meme
  modele : `range`, libelle, champs specifiques au type optionnels.

Chaque ligne de capacite vous laisse choisir le type dans une liste
deroulante, definir `min`/`max`, ajouter un libelle et (pour
`WheelSlot` sur les roues de couleurs) une pastille hex.

#### 5. Enregistrer et partager

- **Enregistrer** persiste le profil vers
  `desktop/shared/data/dmx_profiles/` (gitignore par installation)
  et met a jour la liste du SPA.
- **Partager avec la communaute** televerse le JSON du profil vers le
  serveur electricrv.ca. Le serveur deduplique par empreinte de
  canaux ; soumettre un profil deja televerse produit une reponse
  "ce projecteur est deja couvert" avec un lien vers l'entree
  existante.
- **Exporter** telecharge tous les profils personnalises en un seul
  bundle JSON. Utilisez ceci pour transferer une bibliotheque de
  profils entre installations sans passer par le serveur
  communautaire.

#### 6. Quand creer le sien vs importer depuis OFL

- **Importer depuis OFL** d'abord — plus de 700 projecteurs s'y
  trouvent deja, et importer est a un clic. Les benevoles de l'Open
  Fixture Library ont passe des annees a curer les listes de
  capacites.
- **Cloner et modifier** si le projecteur est proche d'un profil OFL
  mais qu'un canal ou deux different (mise a jour de micrologiciel,
  variante de mode).
- **Creer de zero** uniquement quand le projecteur n'est
  vraiment ni dans OFL ni dans la communaute. Quand vous avez
  termine, partagez-le pour que personne d'autre n'ait a le refaire.

### Reference rapide heritee

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

