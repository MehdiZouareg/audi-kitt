# Audi kiTT — Données récupérables par OBD-II (TT mk2 / 8J)

Référence pour le branchement du dongle (ELM327 USB/Bluetooth sur le port
diagnostic de la TT). La TT mk2 (2006-2014) est EOBD sur bus CAN
(ISO 15765-4, 11 bits / 500 kbit/s). Trois niveaux d'accès :

## 1. PID standard mode 01 — quasi garantis sur une TT mk2 essence

Déjà décodés par `python/obd.py` (✔) ou à ajouter (formule standard SAE J1979) :

| PID  | Donnée                          | Unité | Formule (A,B = octets) | HUD |
|------|---------------------------------|-------|------------------------|-----|
| 0x04 | Charge moteur calculée ✔        | %     | 100·A/255              | jauge charge |
| 0x05 | Temp. liquide refroidissement ✔ | °C    | A − 40                 | tuile temp. eau |
| 0x0B | Pression collecteur (MAP)       | kPa   | A                      | **turbo** (voir dérivées) |
| 0x0C | Régime moteur ✔                 | tr/min| (256·A+B)/4            | jauge RPM |
| 0x0D | Vitesse véhicule ✔              | km/h  | A                      | compteur |
| 0x0E | Avance à l'allumage             | °     | A/2 − 64               | écran diag |
| 0x0F | Temp. air admission ✔           | °C    | A − 40                 | écran diag |
| 0x10 | Débit d'air massique (MAF) ✔    | g/s   | (256·A+B)/100          | **conso** (dérivée) |
| 0x11 | Position papillon ✔             | %     | 100·A/255              | pédale/gaz |
| 0x06/0x07 | Fuel trims court/long terme| %     | 100·(A−128)/128        | santé moteur |
| 0x1F | Temps depuis démarrage          | s     | 256·A+B                | durée trajet |
| 0x21 | Distance depuis voyant MIL      | km    | 256·A+B                | écran diag |
| 0x2F | Niveau carburant ✔              | %     | 100·A/255              | tuile carburant |
| 0x33 | Pression atmosphérique          | kPa   | A                      | calcul boost |
| 0x42 | Tension module de commande      | V     | (256·A+B)/1000         | tuile batterie |
| 0x46 | Température ambiante            | °C    | A − 40                 | météo/HUD |

Remarque : la disponibilité exacte se découvre à chaud via les PID bitmap
`0x00 / 0x20 / 0x40` (chaque bit = un PID supporté par le calculateur).

## 2. Modes diagnostics standard

| Mode | Donnée |
|------|--------|
| 03   | Codes défaut confirmés (DTC `P0xxx`…) — à afficher dans l'app OBD |
| 07   | Codes défaut en attente (non confirmés) |
| 02   | Freeze frame (contexte figé au moment du défaut) |
| 04   | Effacement des codes (action utilisateur, avec confirmation !) |
| 09   | VIN (PID 0x02), identifiants de calibration |

## 3. Le dongle lui-même (commandes AT de l'ELM327)

| Commande | Donnée |
|----------|--------|
| `ATRV`   | Tension batterie réelle mesurée au port OBD — dispo **contact coupé**, plus fiable que le PID 0x42 |

## 4. Spécifique VAG (mode 22 / canaux de mesure)

Accessibles selon le calculateur et le dongle (pas toujours via ELM327 générique) :
température d'huile (souvent absente du PID std 0x5C sur mk2), pression turbo
réelle vs demandée, température de boîte DSG et rapport engagé, angle volant,
kilométrage odomètre. À explorer une fois le dongle branché — non bloquant
pour le HUD.

## 5. Grandeurs dérivées (calculées côté MPU, aucune requête en plus)

- **Boost turbo** = MAP (0x0B) − pression atmosphérique (0x33) → la tuile
  « Turbo » du HUD (aujourd'hui simulée).
- **Conso instantanée** ≈ MAF / (14,7 × densité essence) → L/h, puis L/100
  avec la vitesse. **Autonomie** = niveau réservoir (0x2F × 55 L) ÷ conso moyenne.
- **Rapport engagé estimé** = vitesse / régime (paliers).
- **Chrono 0-100** dérivé de la vitesse, **puissance estimée** dérivée du MAF.

## Cadence de lecture recommandée

Un ELM327 sert ~10-20 requêtes/s. Prioriser : RPM + vitesse à ~10 Hz ;
MAP/papillon à ~5 Hz ; temps/temp./carburant/tension à ~0,5 Hz ; DTC à la
demande. Le simulateur JS du HUD (`hud/index.html`) expose déjà exactement
ces champs (`speed`, `rpm`, `coolant_temp`, `fuel`, `volt`, `boost`) — le
branchement réel remplacera `SimTelemetry` par un poll de `/telemetry`.
