<!-- review-status: pending -->

## Profils communautaires

Le sous-panneau **Communauté** permet de chercher dans le catalogue de
profils de projecteurs contribué par les utilisateurs, de prévisualiser
la disposition des canaux d'un profil, et de l'importer dans votre
bibliothèque locale en un clic.

### Recherche

- Tapez au moins deux caractères ; la recherche porte sur les champs
  nom et fabricant, sans sensibilité à la casse.
- Les résultats affichent **Fabricant · Nom · nombre de canaux ·
  courte description** pour chaque occurrence, plus un badge
  **Community** pour les distinguer des occurrences Local et OFL dans
  le panneau de recherche unifié.
- Le catalogue est récupéré à la demande — la première recherche
  après le lancement de l'orchestrateur peut prendre quelques
  secondes le temps que le cache se réchauffe.

### Partage

Cliquez sur **Share My Profiles** pour téléverser vos profils
personnalisés locaux vers le catalogue communautaire. L'étape de
téléversement retire les métadonnées identifiantes (hôte de
l'orchestrateur, chemin de fichier, nom d'utilisateur) et assigne un
identifiant court (slug) comme `my-custom-mover-3ch`. Ce slug est le
seul identifiant dont les autres opérateurs ont besoin pour importer
votre profil.

### Déduplication

Quand vous importez un profil qui existe déjà localement (même `id`),
l'importateur :

1. Compare les définitions de canaux octet par octet.
2. Si elles sont identiques, ne fait rien et rapporte « déjà
   importé ».
3. Si elles diffèrent, demande : **Garder le local**, **Écraser avec
   la version communautaire**, ou **Enregistrer côte à côte**
   (ajoute `-community` au slug).

Côte à côte est le choix sûr par défaut — vos modifications locales
ne sont pas écrasées.

### Pièges

- Les profils communautaires sont contribués par la communauté et non
  testés sur votre rig. Pour les lyres, lancez l'assistant
  d'étalonnage après l'import ; pour les projecteurs hybrides
  RGB + roue, vérifiez que la carte des positions de roue correspond
  à la séquence de gélatines de votre matériel.
- Un téléchargement échoué (erreur réseau ou limite de débit) ne
  laisse aucun état local — réessayer est sans danger.

**Plus d'infos →** chapitre 12, *Profils d'appareils DMX*.
