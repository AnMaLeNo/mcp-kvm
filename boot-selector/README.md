# Sélecteur de démarrage

Un mini-système Linux qui démarre en premier à chaque allumage du PC, demande à un broker MQTT sur quel système démarrer, l'indique au BIOS et redémarre. Il sert à choisir à distance entre plusieurs systèmes installés sur la même machine, sans clavier ni menu.

```
allumage ─► BIOS ─► sélecteur (≈ 8 s) ─► lit « bureau/pc/boot/target » sur le broker
                        │                  écrit le « prochain démarrage » du BIOS
                        └─► redémarre ─► BIOS ─► le système voulu, lancé directement
```

Le système voulu est lancé par le BIOS lui-même, pas par un chargeur intermédiaire : pour Windows, rien ne distingue ce démarrage d'un démarrage ordinaire.

## Ce qu'il fait, dans l'ordre

1. GRUB (signé) charge un noyau Ubuntu (signé) et une image mémoire construite par `build.sh`. Secure Boot reste actif, rien n'est à signer.
2. `init` monte la partition du sélecteur pour y tenir son journal, active le Wi-Fi, obtient une adresse.
3. Il lit le message retenu `<préfixe>/target` : l'identifiant du système voulu.
4. Il retrouve l'entrée du BIOS qui porte le nom associé, écrit la variable `BootNext`, publie son compte rendu sur `<préfixe>/status`, et redémarre.

Il ne monte jamais les disques des autres systèmes.

## Quand quelque chose ne va pas

| Situation | Comportement |
|---|---|
| Pas de réseau, broker injoignable, aucun souhait, souhait inconnu | démarre le système de repli (`FALLBACK`, Windows par défaut) et le dit dans son compte rendu |
| Broker injoignable | quatre tentatives (`READ_ATTEMPTS`), chacune relançant la découverte d'adresse du broker : environ 16 s mesurés avant le repli, 28 s au pire |
| Le sélecteur n'est pas allé au bout (plantage, coupure) | au démarrage suivant, son GRUB rend la main au BIOS, qui lance l'entrée suivante de son ordre ; le sélecteur réessaie la fois d'après |

Le journal du dernier passage est dans `selector-last.log`, à la racine de la partition du sélecteur.

## Contrat MQTT

Préfixe `bureau/pc/boot` par défaut.

- `<préfixe>/target`, retenu : texte brut, un identifiant (`windows`, `ubuntu`, `agent`). Écrit par l'application qui pilote le PC.
- `<préfixe>/status`, retenu : JSON écrit par le sélecteur, `{"event":"selected","target":"<id>","source":"mqtt"}`, ou `"source":"fallback"` avec un champ `reason`.

## Construire et installer

Sur un Ubuntu de la machine cible, en root. Le script reprend le noyau et le chargeur signés de ce système, ainsi que son profil Wi-Fi.

```sh
sudo BROKERS=192.168.1.101 BROKER_NEIGH=192.168.1.101=<adresse matérielle du broker> ./build.sh --install
```

| Variable | Rôle | Défaut |
|---|---|---|
| `BROKERS` | adresses du broker, essayées dans l'ordre | `192.168.1.101 192.168.1.100` |
| `BROKER_NEIGH` | paires `ip=adresse matérielle`, voir plus bas | vide |
| `FALLBACK` | système de repli | `windows` |

`--install` écrit sur la partition FAT étiquetée `SELECTOR` (512 Mo suffisent, type « EFI system »). Il reste à créer l'entrée du BIOS, une fois :

```sh
sudo efibootmgr -c -d /dev/nvme0n1 -p <numéro de la partition> -L selector -l '\EFI\ubuntu\shimx64.efi'
```

Les noms des entrées du BIOS associés aux identifiants sont dans `build.sh` (`LABEL_windows`, `LABEL_ubuntu`, `LABEL_agent`).

## Trois pièges rencontrés

**L'ordre de démarrage peut appartenir au BIOS.** Sur la carte mère essayée (MSI B550), l'ordre écrit par `efibootmgr` ou par Windows est défait au démarrage suivant : la carte tient sa propre liste. Il faut y placer le sélecteur à la main, dans *Settings › Boot › UEFI Hard Disk Drive BBS Priorities*. Les entrées y sont nommées d'après leur chargeur (trois lignes « ubuntu » ici), pas d'après leur libellé.

**La découverte d'adresses peut ne pas passer entre deux clients Wi-Fi.** Sur le réseau essayé, le PC et le broker, tous deux en Wi-Fi, ne se trouvaient pas (requêtes ARP non relayées par la box), alors que le trafic direct passait. `BROKER_NEIGH` donne au sélecteur l'adresse matérielle du broker ; il la vérifie par une requête directe, ce qui le présente aussi au broker.

**Le chemin Wi-Fi vers le broker n'est pas toujours prêt du premier coup.** Un passage sur cinq environ n'a rien lu dans les 5 s de l'unique tentative d'origine et a démarré Windows par repli, alors que le souhait était publié. Lors du test de la correction, les tentatives 1 et 2 ont échoué et la 3e a réussi.

## Mesures (un PC, novembre 2026)

- Sélecteur : souhait lu 7,8 s après le lancement du noyau, dont environ 6 s d'association Wi-Fi et de DHCP.
- De l'ordre de redémarrage au système voulu : 57 s, contre une trentaine sans sélecteur (un second passage par l'écran du BIOS).
- Les trois cibles (Windows, deux Ubuntu) ont été atteintes, chacune confirmée par le compte rendu du sélecteur.

Non essayé : un autre BIOS, une liaison filaire, un changement de mot de passe Wi-Fi (il faut alors reconstruire l'image).
