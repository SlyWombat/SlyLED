<!-- review-status: pending -->

## Projecteur Contrôleur gyro

Un **projecteur Contrôleur gyro** lie un palet gyro physique
(l'appareil ESP32-S3 à écran LCD rond) à une lyre assignée. Quand le
palet est actif, son orientation pilote la visée de la lyre en temps
réel — pointez le palet vers le sol, le faisceau suit.

### Champs clés

- **Nom** — libellé côté opérateur ; apparaît sur le Tableau de bord
  et la page État pour distinguer plusieurs palets.
- **Lyre assignée** — le projecteur motorisé que ce palet contrôle.
  Un palet sans assignation est enregistré mais inerte.
- **Bascule Actif** — le premier contrôle du dialogue Configurer.
  Vert émeraude = orientation diffusée vers la lyre ; gris ardoise =
  au repos. La désactiver libère la revendication proprement (la lyre
  passe au noir, pas à une pose figée).
- **Assistant d'axes de visée** — définit la rotation
  corps-vers-scène du palet (`forward_local`, `up_local`). Passez-le
  une fois par orientation de montage physique ; le résultat persiste
  sur l'enregistrement du palet.

### Pièges courants

- La lyre doit avoir une **position d'origine (Home) définie** avant
  que le gyro puisse la piloter. L'assistant de création propose Home
  à la création d'une lyre ; si vous l'avez sauté, la carte
  d'étalonnage affichera « Home not set » et la revendication gyro
  sera refusée.
- Le lissage a été retiré en v1.7.122 (#877). L'orientation du palet
  est transmise telle quelle — toute position pointée par le gyro est
  un vecteur valide, et la lyre gère son propre mouvement mécanique
  via le canal DMX `pan-tilt-speed` (quand le profil en a un).
- Si deux opérateurs appuient sur Start simultanément, un seul gagne
  la revendication ; le perdant voit « NO RESPONSE » sur le LCD du
  palet. C'est voulu — l'orchestrateur arbitre pour que la lyre ne
  bascule jamais entre deux sources de visée en plein spectacle.

**Plus d'infos →** l'annexe *Télécommande* du manuel complet.
