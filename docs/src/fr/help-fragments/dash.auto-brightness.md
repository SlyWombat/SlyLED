<!-- review-status: pending -->

## Auto-luminosité

La carte **Auto-luminosité** du Tableau de bord affiche l'entrée audio
locale en direct qui pilote le facteur de luminosité globale. Quand le
téléphone (ou une source de rebouclage WASAPI) diffuse l'amplitude, la
sortie de chaque projecteur est multipliée par la valeur d'enveloppe
en direct avant que l'étape de précalcul n'écrive le DMX — les
lumières « respirent » donc avec la musique sans aucune programmation
par action.

### Ce que la carte affiche

- **cur** — valeur d'enveloppe instantanée (0–255). Mise à jour au
  rythme d'échantillonnage de la source enregistrée.
- **range** — les bornes min/max configurées. Les valeurs sous le
  plancher sont écrêtées à 0 ; celles au-dessus du plafond à 1. Les
  deux se règlent par source dans **Paramètres → Général →
  Auto-luminosité**.
- **globalBrightness** — le facteur actuellement appliqué à tous les
  projecteurs (0–255). C'est ce que le moteur de précalcul multiplie
  dans chaque valeur de canal.
- **last** — secondes écoulées depuis le dernier paquet de données.
  Au-delà de quelques secondes, la source a généralement décroché —
  basculez le mode contrôleur du téléphone à off puis on pour
  reconnecter.

### Vérifier que le téléphone diffuse

1. Ouvrez l'onglet Paramètres de l'application Android.
2. Choisissez un périphérique d'entrée (rebouclage WASAPI sur l'hôte
   orchestrateur, ou le microphone du téléphone).
3. La carte du Tableau de bord doit passer au vert avec
   `last < 1.0s` en une seconde ou deux.
4. Déplacez le curseur **Sensibilité** ; `cur` doit suivre en temps
   réel.

### Pièges

- Le rebouclage WASAPI ne fonctionne que sur les hôtes orchestrateurs
  Windows. Sur Mac, utilisez le microphone du téléphone (#879).
- Une entrée sans `range` signifie que l'orchestrateur n'a jamais
  reçu de paquet d'enregistrement — généralement un APK périmé qui ne
  parle pas le protocole v1.7.126 (#879).
- Auto-luminosité ≠ gradateur maître. La carte met **tout** à
  l'échelle, y compris le maître. Pour graduer indépendamment,
  utilisez **Paramètres → DMX → Master**.

**Plus d'infos →** annexe *Télécommande*.
