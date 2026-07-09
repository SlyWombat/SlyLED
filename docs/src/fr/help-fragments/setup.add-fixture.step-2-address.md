<!-- review-status: pending -->

## Ajouter un projecteur — Étape 2 : Adresse

Choisissez l'univers DMX et l'adresse de départ où ce projecteur se
trouve sur votre ligne de données. L'assistant fait une détection de
conflits en direct pour repérer les chevauchements avant de valider.

### Champs

- **Nom** — libellé côté opérateur. Par défaut le nom du profil ;
  modifiez-le librement. L'onglet Layout, le Tableau de bord et la
  vue chronologie affichent tous ce nom.
- **Univers** — 1–4 par défaut (extensible dans Paramètres → DMX).
  Chaque univers transporte 512 canaux et sort en Art-Net (un univers
  par paquet UDP vers son réseau lié).
- **Adresse de départ** — 1–512. Le projecteur occupe cette adresse
  plus les `channelCount − 1` créneaux suivants ; une lyre 13 canaux
  à l'adresse 17 occupe donc 17–29.
- **Canaux** — affiché seulement pour **Projecteur personnalisé**
  (sans profil de bibliothèque). Les profils de bibliothèque fixent
  le nombre de canaux depuis leurs métadonnées.

### Détection de conflits en direct

Pendant que vous tapez, l'assistant récupère le patch courant de
l'univers et affiche l'un des trois états :

- **No conflicts at U1 @17-29** — vert ; vous pouvez continuer.
- **Conflict: overlaps with Front Par 2** — rouge ; choisissez une
  autre adresse de départ ou modifiez d'abord le projecteur en
  conflit.
- **Error: channels extend past 512** — rouge ; vous avez dépassé la
  fin de l'univers. Passez à l'univers suivant ou choisissez une
  adresse plus basse.

### Astuces

- La numérotation d'adresses est **à base 1** sur le fil. Certaines
  consoles DMX affichent des adresses à base 0 — fiez-vous aux
  commutateurs DIP ou à l'écran LCD du projecteur, pas à la console.
- Pour sonder rapidement un créneau libre, cliquez sur **Browse All**
  à l'étape 1 → le prochain créneau sans conflit pour le profil
  choisi est prérempli ici.

**Plus d'infos →** chapitre 4, *Configuration des appareils*.
