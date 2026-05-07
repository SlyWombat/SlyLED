<!-- review-status: pending -->

## 6. Objets de plateau

Les objets représentent les éléments physiques d'un plateau — murs,
sols, ponts d'éclairage, écrans et accessoires ou artistes — et les
« cibles » abstraites que les actions de suivi poursuivent. Tout ce
qu'un projecteur motorisé doit viser vit dans l'onglet Objets.

> L'onglet s'appelait auparavant « Surfaces » ; le renommage en
> **Objets** a été livré avec la v1.7.30. Les anciens fichiers de
> projet s'importent sans problème.

### Types d'objets

| Type | Mobilité par défaut | Description |
| --- | --- | --- |
| **Mur** | Statique | Mur de fond, verrouillé aux dimensions du plateau (largeur × hauteur) |
| **Sol** | Statique | Sol du plateau, verrouillé aux dimensions du plateau (largeur × (profondeur + 1 m)) |
| **Pont** | Statique | Pont d'éclairage |
| **Écran** | Statique | Surface de projection |
| **Accessoire** | Mobile | Artiste, élément de décor ou élément mobile |
| **Personnalisé** | Mobile | Objet défini par l'utilisateur |
| **Cible ruban** | Mobile | Ancre en coordonnées plateau qui voyage, utilisée par le préréglage Aurora Curtain (#839) — un rig coordonné passe par le même point le long de l'axe choisi |

### Objets verrouillés au plateau

Les objets mur et sol peuvent être verrouillés aux dimensions du
plateau. Lorsque vous modifiez la taille du plateau dans
Paramètres → Plateau, les objets verrouillés se redimensionnent
automatiquement.

### Mobilité

- **Statique** — position fixe. Ne peut pas être suivi par les
  projecteurs motorisés.
- **Mobile** — la position peut changer pendant l'exécution.
  Peut être suivi par les projecteurs motorisés DMX via l'action
  de suivi (chapitre 8).

### Patrouille

Les objets mobiles peuvent patrouiller pendant l'exécution. Chaque
patrouille porte un **motif**, un axe ou une forme, un temps de
cycle, et une courbe de lissage facultative. L'évaluateur de
patrouille tourne à 40 Hz dans la boucle de lecture DMX,
immédiatement avant que les actions de suivi ne lisent les
positions des objets ; la pose d'une cible patrouillée a donc
toujours une frame d'avance quand un projecteur motorisé la lit.

#### Motifs

| Motif | Géométrie | Usage |
| --- | --- | --- |
| **Ping-pong** | Va d'un coin de la boîte englobante au coin opposé, puis fait demi-tour | Le trajet prévu d'un artiste de côté de scène |
| **Cercle** | Boucle à rayon constant autour du centre de la boîte | Une plate-forme tournante ou un effet rotatif |
| **Figure-8** | Lemniscate (deux lobes) à l'intérieur de la boîte | Un trajet complexe visitant deux foyers — utile pour les croisements de scène |
| **Carré** | Quatre segments rectilignes le long du bord de la boîte | Un effet « périmètre de patrouille » utilisé dans les thèmes industriels / sécurité |
| **Ruban** *(nouveau en v1.7.83)* | Une ancre voyageuse unique sur l'axe choisi (gauche-droite, avant-arrière, haut-bas, croisé, figure-8) ; plusieurs projecteurs motorisés montent sur le ruban à des décalages de phase, le balayage voyage donc visiblement le rig au lieu que toutes les têtes bougent à l'unisson | L'effet de rideau coordonné du préréglage Aurora Curtain |

#### Vitesse

- **Lent** — cycle de 20 s.
- **Moyen** — cycle de 10 s.
- **Rapide** — cycle de 5 s.
- **Personnalisé** — réglez `cycleS` directement. Le `speedPreset`
  par défaut est `medium` ; les objets de patrouille ruban sont
  livrés avec `speedPreset: "custom"` pour que le `cycleS: 12` du
  préréglage l'emporte sur la valeur medium par défaut.

#### Plage

Pourcentage de la boîte englobante (par défaut 10 %–90 %). La
patrouille reste dans la boîte ; les cibles près d'un mur n'entraînent
pas un projecteur motorisé dans un blocage cardanique de l'IK.

#### Lissage

- **Sinus** — accélération / décélération douce (par défaut).
- **Linéaire** — vitesse constante.

#### `patrolMode`

Les objets de patrouille peuvent être réglés sur :

- `always` *(par défaut)* — la patrouille tourne tant que
  l'orchestrateur est en route. Utile pour « l'accessoire est vivant
  même avant le début du spectacle ».
- `on-demand` — la patrouille est suspendue jusqu'à ce qu'une
  chronologie active référence cet objet via une action de suivi.
  Dès que le clip référençant démarre, la patrouille reprend là où
  elle aurait été si elle avait tourné en continu (l'action de suivi
  qui s'attend à une cible mobile en voit donc une immédiatement,
  pas un accessoire immobile). Les opérateurs sont parfois surpris
  de voir un accessoire de patrouille `on-demand` immobile sur la
  vue plateau — c'est le comportement voulu.

### Objets temporels

Les systèmes externes peuvent créer des objets éphémères via
`POST /api/objects/temporal` :

- Toujours en mémoire ; jamais enregistrés sur disque.
- Nécessitent `ttl > 0` (durée de vie en secondes).
- Expirent automatiquement quand le TTL est écoulé.
- Les mises à jour de position rafraîchissent le TTL.
- Affichés dans le visualiseur d'exécution avec un contour en
  pointillés et un badge de compte à rebours.
- Le suiveur caméra pousse les personnes détectées par cette
  route — chaque détection devient un objet temporel que l'action
  de suivi du préréglage Spotlight Follow Person poursuit.

L'échelle des objets temporels suit l'ordre du moteur de rendu
`[largeur, hauteur (Z), profondeur (Y)]` — les sites d'appel qui
veulent une détection « en forme de personne » doivent envoyer
`[0,6, 1,8, 0,6]` (60 cm de large, 1,8 m de haut, 60 cm de
profondeur), pas `[0,6, 0,6, 1,8]`.

---

