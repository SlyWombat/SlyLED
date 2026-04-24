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

