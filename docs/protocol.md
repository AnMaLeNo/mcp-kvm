# Protocole de commande

La carte cible reçoit des lignes de texte ASCII terminées par `\n`. Chaque ligne reçoit exactement une ligne de réponse : `ok`, `ok <données>` ou `err <raison>`.

Les commandes arrivent par deux canaux, traités de la même façon :

- la liaison série à un fil avec la carte pont (toujours active, GP2 par défaut) ;
- un port série USB sur la cible elle-même, dans le firmware `kvm_hid_cdc` seulement.

La carte pont relaie telles quelles les lignes reçues sur son port série USB, sauf celles qui commencent par `!`, qu'elle traite elle-même (voir plus bas).

Les rapports clavier et pointeur sont mis en file et envoyés dans l'ordre, un par intervalle USB : un appui suivi d'un relâchement n'est jamais fusionné. La réponse `ok` signifie « mis en file » ; `sync` attend l'envoi effectif.

| Commande | Effet |
|---|---|
| `ping` | `ok mcp-kvm <version>` |
| `info` | État : `ver`, `cdc` (1 si le port série USB est présent), `mounted`, `suspended`, `enum` (nombre de fois où la cible a configuré le périphérique : il change à chaque redémarrage ou changement de système), `boot` (deux chiffres : clavier puis souris, 1 si la cible a demandé le protocole de démarrage), `leds` (hex : bit 0 Verr Num, bit 1 Verr Maj, bit 2 Arrêt défil), `kq`/`pq`/`mq` (rapports clavier, pointeur, souris en attente), `kms`, `up` (ms depuis le démarrage), `hang`, `soft`, `failsafe` |
| `k [mods [touche ...]]` | Fixe tout l'état du clavier. Octets hexadécimaux : modificateurs, puis jusqu'à 6 codes HID. `k` seul relâche tout. |
| `t mmkk [mmkk ...]` | Tape une suite de frappes. Chaque frappe : modificateurs puis code HID, 4 chiffres hexadécimaux. Les modificateurs sont enfoncés avant la touche et gardés entre frappes qui les partagent. 160 frappes au plus par ligne. |
| `p boutons x y` | État du pointeur : boutons en hexadécimal (1 gauche, 2 droit, 4 milieu), x et y de 0 à 32767 en décimal (coin haut gauche = 0 0). |
| `m boutons dx dy` | Souris relative (protocole de démarrage, pour les BIOS) : boutons en hexadécimal, déplacements de -127 à 127 en décimal. Les boutons restent dans l'état donné jusqu'à la commande suivante. |
| `w dy [dx]` | Molette : crans de -127 à 127. `dy` positif défile vers le haut, `dx` positif vers la droite. |
| `r` | Relâche toutes les touches et tous les boutons (pointeur et souris). |
| `sync` | Répond `ok` quand tout ce qui était en file est parti. Répond `err timeout` si la cible n'a pris aucun rapport pendant 5 s : la file est alors vidée et toutes les touches et tous les boutons sont relâchés. La durée totale n'est pas limitée, car un chargeur de démarrage peut ne prendre qu'une frappe toutes les 130 ms. |
| `cfg kms <n>` | Délai minimal entre deux rapports clavier, en ms (1 à 1000, 6 par défaut). |
| `echo <texte>` | Renvoie `ok <texte>` (160 caractères au plus) : sert à éprouver la liaison. |
| `gpio <n>` | Niveau de la broche n, lue en entrée avec tirage vers le haut : sert à vérifier un câblage. |
| `wiretest` | Auto-test de la liaison à un fil : envoie 32 octets et les relit sur la ligne. `ok 32/32 level=1` quand tout va bien. Accepté seulement par le port série USB, puisqu'il écrit sur le fil. |
| `bootsel` | Redémarre dans le chargeur USB (disque `RPI-RP2`). |
| `reboot` | Redémarre le firmware. |

Modificateurs : `01` Ctrl gauche, `02` Maj gauche, `04` Alt gauche, `08` Win gauche, `10` Ctrl droit, `20` Maj droite, `40` AltGr, `80` Win droit.

## Commandes de la carte pont

| Commande | Effet |
|---|---|
| `!ping` | `ok mcp-kvm-bridge <version>` |
| `!info` | État du pont : `ver`, `wire` (niveau de la ligne, 1 au repos), `pin`, `baud`, `up`, `hang`, `soft`, `failsafe` |
| `!selftest` | Auto-test de la liaison, comme `wiretest`. |
| `!low <ms>` | Maintient la ligne à 0 pendant 1 à 10000 ms. |
| `!bootsel` | Redémarre le pont dans le chargeur USB. |
| `!reboot` | Redémarre le pont. |

## Liaison à un fil

Un seul fil de signal plus la masse, 115200 bauds, 8N1, à l'alternat : le pont envoie une ligne, la carte cible répond. La ligne est au repos à 1 par les tirages internes des deux cartes ; un émetteur ne fait que la tirer à 0 (drain ouvert), donc deux cartes qui parleraient en même temps ne peuvent pas se mettre en court-circuit.

## Reprise sans toucher aux cartes

Valable pour les deux cartes :

- Ouvrir un port série USB à 1200 bauds redémarre la carte dans le chargeur, sans passer par l'analyseur de commandes.
- Un blocage déclenche le chien de garde (3 s). Trois blocages de suite : la carte passe d'elle-même dans le chargeur.
- Après un flash ou un `reboot`, si aucune commande valide n'arrive en 60 s, la carte repasse dans le chargeur. Les scripts de flash envoient `info` juste après, ce qui désarme ce retour. Conséquence utile : une carte cible flashée avec un firmware qui n'entend pas la liaison revient d'elle-même dans le chargeur.
