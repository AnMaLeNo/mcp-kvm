# mcp-kvm

Serveur MCP qui permet à un agent (Claude Code, par exemple) d'utiliser un ordinateur physique comme le ferait une personne assise devant : il voit l'écran par la sortie HDMI et agit par un clavier et un pointeur USB. Rien n'est installé sur la cible, donc cela ne dépend pas de son système.

```
                        HDMI ──► adaptateur HDMI vers USB ──► machine de contrôle
  cible                                                        (serveur MCP)
        ◄── USB ── carte cible ◄── 1 fil + masse ── carte pont ◄── USB ──┘
            (clavier + pointeur)                 (port série)
```

- **Écran** : un adaptateur HDMI vers USB (vu comme une webcam) branché sur la machine de contrôle.
- **Entrées** : deux cartes RP2040-Zero. La carte cible se présente à la cible comme un clavier, une souris relative (toutes deux au protocole de démarrage, celui que lisent les BIOS) et un pointeur absolu (coordonnées X/Y, boutons, molette). La carte pont se présente à la machine de contrôle comme un port série. Elles se parlent par un fil de signal et la masse.

Chaque machine voit un port USB natif de RP2040 ; les alimentations 5 V des deux machines ne sont pas reliées.

## État actuel

Vérifié sur un PC (carte mère MSI B550, Windows 11 et Ubuntu en double amorçage), piloté de bout en bout par la capture et les deux cartes :

| Situation | Clavier | Souris |
|---|---|---|
| Windows | frappe exacte (560 caractères, symboles et accents), environ 67 caractères/s | pointeur absolu : clic, double-clic, clic droit, glisser, molette, au pixel près |
| Ubuntu 24.04 (écran de connexion et session) | connexion par mot de passe avec symboles, commandes tapées dans un terminal, `sudo` compris | pointeur absolu choisi automatiquement, clics dans les menus au point demandé |
| BIOS (MSI Click BIOS 5) | navigation, entrée dans le BIOS par Suppr et menu de démarrage par F11 au démarrage, redémarrage par Ctrl+Alt+Suppr | souris relative choisie automatiquement : clic et double-clic au pixel près |
| GRUB 2.12 | menu et ligne de commande, ligne de 110 caractères exacte ; 3 à 7 caractères/s selon la résolution, rythme imposé par GRUB | GRUB n'a pas de souris |

| Autre élément | État |
|---|---|
| Capture d'écran depuis le Mac | 1920×1080, 30 images/s, retard mesuré 0,07 à 0,11 s |
| Liaison à un fil entre les deux cartes | 3000 allers-retours (473 ko) sans erreur, 2,2 ms par `ping` |
| Durée d'une action avec sa capture | environ 0,5 s sous Windows, 1,7 s pour un clic dans le BIOS |
| Flash des deux cartes sans y toucher | fonctionne (5 s pour la carte cible) |

Pas encore essayé : macOS comme cible, d'autres BIOS, d'autres adaptateurs de capture.

## Câblage

| Carte pont (sur la machine de contrôle) | Carte cible (sur la cible) |
|---|---|
| GND | GND |
| GP13 | GP2 |

Les broches se changent à la compilation (`KVM_WIRE_PIN`). Le protocole est décrit dans [docs/protocol.md](docs/protocol.md).

### Avec une seule carte

Le firmware `kvm_hid_cdc` ajoute un port série USB à la carte cible : les commandes passent alors par la cible elle-même, que le Mac joint en SSH (`KVM_LINK=ssh:utilisateur@cible`). Ce montage ne demande aucun câblage, mais il exige un système démarré et un serveur SSH sur la cible : il ne permet donc ni le BIOS ni GRUB.

Un second port USB soudé sur des broches GPIO (Pico-PIO-USB) ne remplace pas la seconde carte tel quel : il ne peut être qu'hôte. En mode périphérique, il lui faut une résistance de 1,5 kΩ entre 3V3 et D+.

## Installation (machine de contrôle, macOS)

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e .
tools/build-capture.sh      # module de capture natif, demande Xcode
tools/build.sh              # firmwares, demande le pico-sdk et arm-none-eabi-gcc
```

Chemins par défaut du pico-sdk 2.1.1 et de la toolchain : `~/pico/pico-sdk` et `~/pico/toolchain/bin` (variables `PICO_SDK_PATH` et `PICO_TOOLCHAIN_BIN`).

Sans le module de capture natif, le serveur se rabat sur ffmpeg, limité sur macOS au mode non compressé de l'adaptateur : 5 images/s et 0,5 à 0,6 s de retard.

## Flasher les cartes sans y toucher

```sh
.venv/bin/python tools/flash_bridge.py     # carte pont, branchée sur le Mac
echo 'KVM_SSH=utilisateur@cible' > .kvm.env
tools/flash.sh                             # carte cible (ajouter « cdc » pour le montage à une carte)
```

La carte cible reçoit son firmware par son port USB, donc par la cible : `tools/flash.sh` lui demande de passer dans son chargeur, puis copie le fichier en SSH sur le disque `RPI-RP2` qui apparaît sur la cible. Il faut pour cela, sur la cible Windows, un serveur OpenSSH et un dossier `%USERPROFILE%\mcp-kvm-dev` contenant un environnement Python `venv` avec `pyserial`.

La toute première fois, chaque carte doit être branchée en maintenant son bouton BOOT. Les protections qui évitent d'y retoucher ensuite sont décrites dans [docs/protocol.md](docs/protocol.md).

## Utilisation

Réglages, par variables d'environnement :

| Variable | Rôle | Défaut |
|---|---|---|
| `KVM_LINK` | liaison vers la carte : `serial`, `serial:<port>` ou `ssh:<utilisateur@cible>` | port série local s'il y en a un, sinon SSH vers `KVM_SSH` |
| `KVM_SSH` | `utilisateur@cible`, pour la liaison SSH | aucun |
| `KVM_CAPTURE_DEVICE` | nom de l'adaptateur de capture | `USB3. 0 capture` |
| `KVM_CAPTURE_SIZE` | taille d'image demandée à l'adaptateur | `1920x1080` |
| `KVM_CAPTURE_BACKEND` | `native`, `ffmpeg` ou `auto` | `auto` |
| `KVM_SCREENSHOT_WIDTH` | largeur des captures renvoyées à l'agent | `1280` |
| `KVM_LAYOUT` | disposition clavier réglée sur la cible | `us` |
| `KVM_TARGET_OS` | système de la cible (`windows` active les codes Alt pour les accents) | `windows` |
| `KVM_POINTER` | souris utilisée : `auto`, `absolute` ou `relative` | `auto` |

Dans Claude Code :

```sh
claude mcp add kvm -- /chemin/vers/mcp-kvm/.venv/bin/mcp-kvm
```

Outils exposés : `screenshot`, `zoom`, `click`, `move`, `drag`, `scroll`, `type_text`, `key`, `status`, `recalibrate_pointer`. Les coordonnées sont celles de la capture renvoyée (origine en haut à gauche). Chaque action renvoie une nouvelle capture, prise une fois l'écran stabilisé.

Pour essayer à la main : `.venv/bin/python -m mcp_kvm.cli` (voir l'aide en tête de [src/mcp_kvm/cli.py](src/mcp_kvm/cli.py)).

### Une seule façon de cliquer, deux souris

L'agent demande toujours un point : `click(x, y)`. Le serveur choisit la souris que la cible accepte, en regardant l'écran :

1. Il place le pointeur absolu sur le point, le décale de 10 pixels et le remet. Ce qui a changé puis est revenu à l'image, c'est le curseur : s'il est là où on l'attend, le pointeur absolu fonctionne (cas de Windows).
2. Sinon il essaie la souris relative : il pousse le curseur dans le coin haut gauche, avance de distances connues et repère le curseur de la même façon. Il en tire le nombre de pixels par unité (sur le BIOS essayé : 1,25 en X et 0,94 en Y, soit 1024×768 étiré sur la capture).
3. En mode relatif, chaque déplacement repart du coin, puis le serveur repère le curseur et corrige l'écart restant. S'il n'arrive pas à moins de 2 pixels du point, l'outil refuse de cliquer.

Le choix est refait de lui-même quand la cible redémarre ou passe du BIOS à un système (la carte cible compte les configurations USB). L'outil `recalibrate_pointer` refait le test sur demande et rend compte du résultat. Chaque action de pointeur indique la souris utilisée, par exemple « Mouse used: absolute pointer » ou « Mouse used: relative mouse, 1.25 x 0.94 screenshot pixels per count ». Aucun modèle de reconnaissance n'est nécessaire, puisque c'est le serveur qui fait bouger le curseur.

### Taille du texte à l'écran

`KVM_SCREENSHOT_WIDTH` règle le nombre de pixels envoyés à l'agent, pas la taille du texte : une capture plus petite contient le même écran, en moins net. L'outil `zoom` renvoie une région à la résolution complète de la capture.

Pour que tout soit écrit plus gros, c'est la résolution de sortie de la cible qu'il faut baisser (ou sa mise à l'échelle qu'il faut augmenter), dans ses réglages d'affichage. L'adaptateur de capture ne peut pas l'imposer : c'est la cible qui choisit parmi les modes qu'il annonce.

## Limites connues

- Avant le démarrage du système, une carte graphique choisit seule sur quelle sortie elle affiche : aucun réglage ne permet de désigner l'adaptateur. Sur le PC essayé, avec l'écran en DisplayPort et l'adaptateur en HDMI, le BIOS n'apparaissait que sur l'écran ; avec les deux en HDMI sur la carte, il apparaît sur la capture. À vérifier sur chaque machine, en échangeant les câbles si besoin.
- Un répartiteur HDMI (une entrée, deux sorties) fait voir un seul écran à la cible, mais celui essayé a cessé de transmettre l'image après un redémarrage de la cible, sur ses deux sorties, jusqu'à être retiré. Ne pas compter dessus pour une machine redémarrée sans personne à côté.
- Sous Windows, une nouvelle combinaison d'écrans démarre en mode « étendu » : la capture montre alors un second bureau vide (image noire) et le pointeur absolu ne correspond plus à la capture. Passer en mode « dupliquer » (`Win+P`, ou `DisplaySwitch.exe /clone` dans la session). Le réglage est conservé aux redémarrages suivants.
- GRUB prend les frappes lentement et redessine son écran avec retard : une ligne tapée n'est apparue que quelques secondes après la fin de la saisie, un changement de sélection jusqu'à une dizaine de secondes après, et le compte à rebours affiché était en retard sur le vrai. Reprendre une capture avant de valider, et arrêter le compte à rebours (une touche) sans attendre de le voir.
- Dans le BIOS essayé, un déplacement de souris envoyé juste derrière le retour au coin est perdu ; le serveur attend 150 ms (perte constatée sans pause, plus aucune à partir de 50 ms). Un autre BIOS peut demander une autre valeur (`HOME_PAUSE_S` dans [src/mcp_kvm/pointer.py](src/mcp_kvm/pointer.py)).
- La molette n'existe pas en mode souris relative : utiliser les flèches et Page précédente/suivante.

- Le clavier envoie des positions de touches : `KVM_LAYOUT` doit correspondre à la disposition réglée sur la cible. Seule `us` est fournie.
- Identifiants USB de test (`1209:0001` à `1209:0003`) : à remplacer avant toute diffusion.
