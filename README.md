# Audi kiTT

> **kiTT** = **KITT** (l'IA de *K2000 / Knight Rider*) × **Audi TT**.
> Une IA embarquée dans une Audi TT, prototypée sur un **Arduino UNO Q**.

L'UNO Q est une carte « double cerveau » : un **MPU Linux** (Qualcomm) pour la
logique haut niveau et un **MCU STM32** temps réel qui pilote la **matrice LED
intégrée 13×8 (104 LEDs)**. La matrice n'est accessible **que depuis le MCU** ;
le MPU la commande via un lien série (Router Bridge d'App Lab).

## Architecture

```
audi-kitt/
  app.yaml                       # manifeste App Lab (MCU + MPU + bridge)
  sketch/kiTT_display/           # côté MCU (STM32)
    kiTT_display.ino             # machine à états non bloquante + série
    kiTT_anim.h                  # bibliothèque d'animations PURE (partagée avec le simulateur)
  python/                        # côté MPU Linux
    service.py                   # ORCHESTRATEUR (entrypoint) : assemble le tout
    main.py                      # façade KittDisplay + protocole série + dry-run
    obd.py                       # télémétrie OBD-II (décodage PID + seuils/alertes)
    voice.py                     # assistant vocal (wake-word + amplitude→niveau + états)
    intents.py                   # reconnaissance d'intentions + routage des commandes
    test_main.py                 # 102 tests (protocole + OBD + voix + intentions + service)
  tools/anim_sim.cpp             # simulateur host des animations (ASCII, sans carte)
```

## Protocole série (MPU → MCU, 115200 bauds, lignes `\n`)

| Trame | Effet |
|-------|-------|
| `S:<ETAT>` | état : `BOOT`/`IDLE`/`LISTEN`/`THINK`/`SPEAK`/`WORD`/`ERROR` |
| `L:<0-255>` | niveau de modulation (amplitude voix/micro) |
| `B:<0-255>` | luminosité globale (255 = plein jour, ~40 = nuit) |
| `T:<message>` | fait défiler un message une fois puis revient à `IDLE` |
| `G:<0-255>` | jauge persistante (barre) |
| `N:<0-999>` | nombre persistant (gros chiffres) |
| `D:<0-999>,<0-255>` | dashboard combiné : nombre (haut) + jauge (bas) |
| `P` | ping (le MCU répond `kiTT ok`) |

Sans commande, le MCU reste en **mode démo** autonome.

## Démarrage rapide

### Côté Linux (MPU) — sans carte, en dry-run

```bash
cd python
python3 test_main.py            # 102/102 tests
python3 service.py              # démo d'orchestration (voix + télémétrie simulées)
python3 main.py --dry-run       # imprime les trames série qui partiraient
```

Le pipeline cible se branche sur le service :

```python
from service import build_service
from main import KittDisplay, KittLink

disp = KittDisplay(KittLink())              # auto-détecte le port, sinon dry-run
svc  = build_service(disp, focus="speed", llm=mon_llm, tts=mon_tts)
svc.feed_stt("kiTT montre la vitesse")      # wake-word + commande
svc.on_metric("speed", 90)                  # télémétrie
svc.serve(flux_evenements, install_signals=True)   # boucle + arrêt propre
```

`pyserial` est optionnel : en son absence (ou sans carte), tout bascule en
dry-run et imprime les trames.

### Simulateur d'animations (host)

```bash
g++ -std=c++17 -O2 tools/anim_sim.cpp -o anim_sim -lm
./anim_sim ALL          # sanity de tous les rendus + aperçu ASCII
./anim_sim DASH 90 180  # dashboard combiné
./anim_sim TEXT "KITT"  # texte défilant
```

### Côté MCU (Arduino)

Flasher `sketch/kiTT_display/` sur l'UNO Q (dépendance : `Arduino_LED_Matrix`).
Le FQBN exact reste à confirmer sur la carte.

## État du projet

Itérations #1 → #14 : affichage vivant par état, transitions douces, dimming
jour/nuit, texte défilant, jauge / nombre / dashboard, télémétrie OBD-II
(décodage + seuils + escalade ERROR + rotation du focus), assistant vocal
(wake-word + machine à états), reconnaissance d'intentions, et orchestrateur
(assemblage + LLM + watchdog + arrêt propre). **102/102 tests Python**, sanity
simulateur **13/13**.

Restent branchés au matériel réel : dongle OBD-II (ELM327/CAN), pipeline audio
(micro + wake-engine + STT + TTS + haut-parleur), LLM conversationnel, et la
toolchain de déploiement App Lab sur la carte.
