<!-- review-status: pending -->

## 17. Dépannage

Le tableau ci-dessous couvre les symptômes que les opérateurs
remontent le plus souvent. Chaque ligne pointe vers la version de
l'orchestrateur où le comportement sous-jacent a été modifié pour la
dernière fois ; un opérateur sur une version plus ancienne peut
ainsi décider de mettre à jour ou de contourner.

| Problème | Ce que vous voyez | Correctif ou version |
| --- | --- | --- |
| **Vue d'exécution vide** | L'onglet Exécution 3D affiche le plateau mais aucun appareil. | Vérifiez que les appareils sont positionnés dans l'onglet **Disposition**. Les installations DMX seules s'affichent correctement depuis v1.7.30. |
| **Cône de faisceau dans la mauvaise direction** | Le cône de la visualisation 3D vise le mauvais mur. | La direction du faisceau provient de la `rotation = [rx, ry, rz]` de l'appareil dans l'espace plateau. Z est vers le haut ; rx > 0 vise vers le bas. Voir le chapitre 4 pour la convention complète. |
| **Le cône de la visualisation 3D ne correspond pas au projecteur physique** | Le cône dans la visualisation pointe à gauche du plateau alors que le projecteur motorisé vise à droite. | Corrigé en v1.7.52 (#806/#809) : le vecteur de visée canonique est la source de vérité, et l'IK physique en dérive. Si la divergence persiste sur v1.7.52+, sauvegardez à nouveau les positions Home et Secondaire de l'appareil dans l'assistant Set Home. |
| **Saut de pan à la fin de l'étalonnage** | Au relâchement de l'étalonnage, le projecteur saute vers une pose différente de celle que le palet remontait. | Corrigé en v1.7.52 (#805). Avant le correctif, le repli vers l'IK historique capturait le mauvais vecteur de visée au relâchement. Les opérateurs sur v1.7.52+ qui voient encore un saut doivent le signaler avec la version du firmware du palet gyro (≥ v1.2.4 requis). |
| **L'appui sur Démarrer clignote vers « start » sur le palet** | L'opérateur appuie sur Démarrer après une coupure WiFi, l'UI du palet clignote l'accusé de revendication pendant une frame, puis revient à IDLE pendant que l'orchestrateur conserve une revendication orpheline. | Corrigé en v1.7.83 (#812 / #813 / #825). L'appui sur Démarrer utilise désormais un nonce 16 bits + CLAIM_ACK, avec des battements de cœur HB_REP pour réconcilier les états divergents. Si vous voyez le symptôme sur v1.7.83+, vérifiez que le firmware du palet est ≥ v1.2.7 (le registre vous le signale). |
| **L'auto-luminosité n'a aucun effet sur les lumières** | L'UI Auto-luminosité Android montre le maître qui glisse avec la musique, mais les têtes DMX et les bandeaux LED ne s'estompent pas. | Corrigé en v1.7.83 (#843). Le POST rapide diffuse désormais `CMD_SET_BRIGHTNESS` aux nœuds LED et applique une mise à l'échelle gamma au gradateur DMX / RVB au moment du rendu. Les opérateurs sur d'anciennes versions peuvent se rabattre sur le curseur manuel Paramètres → Luminosité globale en attendant la mise à jour. |
| **La playlist en boucle s'éteint entre chaque itération** | Une playlist mono- ou multi-élément en mode **Loop All** flashe tout à zéro pendant une frame à chaque bouclage. | Corrigé en v1.7.83 (#840). Les boucles mono-élément passent par le chemin de lecture modulo, et les boucles multi-éléments passent `is_final=False` pour supprimer le balayage de blackout de fin naturelle jusqu'à ce que la playlist s'arrête réellement. |
| **L'action de suivi éteint des projecteurs motorisés sur des chronologies sans rapport** | Une chronologie qui ne référence pas une action de suivi voit quand même ses projecteurs s'éteindre dès que l'action existe dans la bibliothèque. | Corrigé en v1.7.83 (#835). Les actions de suivi n'évaluent plus que sur les chronologies qui les référencent ; les actions orphelines restent dormantes. |
| **Un préréglage promet « les têtes suivent X » mais elles ne le font pas** | Une description de thème promet du suivi, mais le rig se contente de balayer. | Corrigé en v1.7.83 (#837). Les descriptions des thèmes correspondent désormais à l'implémentation réelle : seuls Figure Eight et Spotlight Follow Person émettent une action de suivi ; les autres balayent. |
| **Le champ Roue de couleurs apparaît dans l'éditeur d'action DMX Scene** | Les volets DMX Scene / PT-Move / Gobo Select de l'éditeur d'action présentaient un champ « Roue de couleurs » qui ne faisait pas ce que l'opérateur attendait sur les appareils hybrides RVB+roue. | Corrigé en v1.7.83 (#841 / #842). L'emplacement de roue est désormais réservé au type 17 ; le moteur de précalcul / rendu dérive l'emplacement à partir du RVB via `rgb_to_wheel_slot` pour tous les autres types d'action. Une migration unique retire les champs `colorWheel: 0` périmés au premier démarrage de v1.7.83+. |
| **Visualisation 3D ne s'affiche pas** | Canevas noir là où le plateau devrait être. | Utilisez Chrome / Firefox / Edge avec le support WebGL. Vérifiez `chrome://gpu` pour l'accélération matérielle. |
| **Exécutants non synchronisés** | Un nœud enfant apparaît hors-ligne dans Configuration alors qu'il est sous tension. | Vérifiez que l'orchestrateur et le nœud enfant sont sur le même sous-réseau WiFi. Le bouton **Rafraîchir** de l'onglet Configuration relance le scan via mDNS + diffusion UDP. |
| **Canevas de mauvaise taille** | Le canevas Disposition est beaucoup plus petit ou plus grand que la pièce. | Les dimensions du plateau (Paramètres → Plateau) déterminent la taille du canevas : `canvasW = stage.w × 1000`. Ajustez la largeur / hauteur du plateau en mètres plutôt que les pixels du canevas. |
| **Flash OTA refusé pour incohérence de SHA** | L'onglet Firmware refuse la mise à jour avec `sha256 mismatch`. | Corrigé en v1.7.61 (#814). L'orchestrateur retombe désormais sur la release de firmware publiée du `releaseTag` enregistré quand le binaire sur disque diffère du registre. Si vous voyez encore l'erreur, cliquez sur **Rafraîchir** dans l'onglet Firmware pour récupérer `registry.json` depuis le serveur de releases. |
| **Verrou de stale-reason gyro qui ne s'efface pas** | « Connexion perdue » reste affiché sur la ligne de statut d'un palet alors qu'il a repris l'envoi. | Corrigé en v1.7.62 (#821) puis à nouveau en v1.7.63 (#823). L'appui sur Démarrer efface la stale_reason distante ; le cache s'auto-détruit sur un échec de lecture transitoire. |

Si vous rencontrez quelque chose qui n'est pas dans ce tableau, le
journal de l'orchestrateur (Paramètres → Journalisation → activer
le journal fichier) capture chaque envoi UDP et chaque décision de
rendu DMX taggués par numéro d'issue — signalez-le via votre canal de support avec
la section pertinente jointe et une description de ce que faisait
le rig au moment du symptôme.

---

