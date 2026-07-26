#!/usr/bin/env python3
# ============================================================
#  Audi kiTT — Controleur Linux (cote MPU)         python/main.py
# ============================================================
#  Role : c'est le "cerveau" haut niveau qui tourne sur le MPU Linux
#  de l'Arduino UNO Q. Il DECIDE de l'etat de l'IA embarquee et le
#  POUSSE vers le MCU (STM32), seul a piloter la matrice LED 13x8.
#
#  La matrice n'est PAS accessible depuis Linux : on communique avec
#  le sketch MCU (kiTT_display.ino) via un lien serie (Router Bridge
#  d'App Lab, expose cote Linux comme un /dev/tty...).
#
#  Protocole (defini cote MCU en iteration #2, 115200 bauds, lignes \n) :
#      S:<ETAT>          ETAT parmi BOOT IDLE LISTEN THINK SPEAK WORD ERROR
#      L:<0-255>         niveau de modulation (amplitude voix / micro)
#      B:<0-255>         luminosite globale de l'afficheur (255 = plein jour)  [#5]
#      T:<message>       fait defiler <message> une fois puis revient a IDLE   [#6]
#      G:<0-255>         affiche une jauge PERSISTANTE (barre) de cette valeur [#7]
#      N:<0-999>         affiche un nombre PERSISTANT (chiffres) de cette valeur [#8]
#      D:<0-999>,<0-255> dashboard PERSISTANT : nombre (haut) + jauge (bas)    [#9]
#      P                 ping -> le MCU repond "kiTT ok"
#  Aucune commande envoyee => le MCU reste en mode DEMO autonome.
#
#  Iterations :
#    #3 : KittLink (serie/dry-run), KittDisplay (etats), scenario demo.
#    #5 : luminosite globale (dimming jour/nuit) — set_brightness() +
#         day_night_brightness(heure) pour un afficheur non eblouissant
#         la nuit. La luminosite est un reglage TRANSVERSE (n'interrompt
#         pas l'etat courant cote MCU).
#    #6 : texte defilant arbitraire — show_text() emet `T:<message>` pour
#         afficher une notification / reponse courte / plus tard la vitesse
#         OBD. Le message est nettoye (retrait des caracteres de controle qui
#         casseraient le protocole ligne) et tronque a la capacite du MCU.
#    #7 : jauge persistante — show_gauge() emet `G:<0-255>` pour afficher en
#         permanence une valeur (vitesse / RPM / carburant via OBD), lisible
#         d'un coup d'oeil au volant. Helper pur gauge_value() pour convertir
#         une grandeur physique (ex. km/h sur une echelle max) en 0..255.
#    #8 : nombre persistant — show_number() emet `N:<0-999>` pour afficher en
#         permanence une valeur NUMERIQUE EXACTE (gros chiffres centres). La
#         jauge donne la proportion d'un coup d'oeil, le nombre donne la valeur
#         precise (vitesse "90", temperature "72"...). Helper pur clamp_number().
#    #9 : dashboard combine — show_dash() emet `D:<0-999>,<0-255>` pour afficher
#         en permanence la valeur EXACTE (haut) ET la proportion (bas) sur un
#         seul ecran. show_dash_of() derive les deux d'une meme grandeur
#         physique (nombre = valeur, barre = valeur/max) : la brique naturelle
#         pour un cadran OBD (vitesse "90" + barre a 45 %).
#
#  Usage :
#      python3 main.py                 # auto-detecte le port, sinon dry-run
#      python3 main.py --dry-run       # n'ouvre aucun port : imprime les trames
#      python3 main.py --port /dev/ttyMCU0
#      python3 main.py --once IDLE     # pousse un seul etat puis quitte
#      python3 main.py --brightness 60 # regle la luminosite globale puis quitte
#      python3 main.py --text "HELLO"  # fait defiler un message puis quitte
#      python3 main.py --gauge 180     # affiche une jauge persistante puis quitte
#      python3 main.py --gauge 90 --gauge-max 200   # 90 sur une echelle de 200
#      python3 main.py --number 90     # affiche un nombre persistant puis quitte
#      python3 main.py --dash 90 --dash-max 200     # cadran : "90" + barre 45%
#      python3 main.py --ping          # ping le MCU et affiche la reponse
#
#  Auteur : Claude (Cowork) — iteration #3, luminosite #5, texte #6, jauge #7,
#                             nombre #8, dashboard combine #9
# ============================================================

from __future__ import annotations

import argparse
import glob
import math
import sys
import time

# --- Protocole : etats reconnus par le MCU (doit rester aligne avec kiTT_anim.h) ---
STATES = ("BOOT", "IDLE", "LISTEN", "THINK", "SPEAK", "WORD", "ERROR")

# Ports serie plausibles pour le pont MPU<->MCU selon les conventions App Lab / Linux.
# On tente dans l'ordre ; le premier ouvrable gagne. Adaptable via --port.
CANDIDATE_PORTS = (
    "/dev/ttyMCU0", "/dev/ttyMCU", "/dev/ttyRPMSG0",
    "/dev/ttyACM0", "/dev/ttyAMA0", "/dev/ttyUSB0", "/dev/serial0",
)

BAUD = 115200

# --- Luminosite globale (dimming jour/nuit) -------------------------------
# Paliers alignes sur les constantes du MCU (kiTT_anim.h : KITT_BRIGHT_*).
BRIGHT_DAY = 255      # plein jour : lisibilite maximale
BRIGHT_NIGHT = 40     # nuit : discret, sans eblouir au volant
BRIGHT_DUSK = 120     # aube / crepuscule : intermediaire

# --- Texte defilant (commande T:) -----------------------------------------
# Capacite max d'un message cote MCU : KITT_TEXT_MAX(40) - 1 pour le '\0'.
# On tronque ici pour rester coherent et ne rien perdre silencieusement au fil.
TEXT_MAX = 39

# --- Jauge persistante (commande G:) --------------------------------------
GAUGE_MIN = 0        # jauge vide
GAUGE_MAX = 255      # jauge pleine (barre complete)

# --- Nombre persistant (commande N:) --------------------------------------
# Doit rester aligne avec KITT_NUM_MAX cote MCU (3 chiffres, police 3x5).
NUMBER_MIN = 0
NUMBER_MAX = 999


def day_night_brightness(hour: float) -> int:
    """Luminosite conseillee (0..255) selon l'heure locale (0..24, fraction OK).

    Profil simple et robuste, pensé pour un afficheur de voiture :
      - plein jour (~9h -> 17h)      : BRIGHT_DAY
      - nuit profonde (~21h -> 6h)   : BRIGHT_NIGHT
      - transitions douces aube/soir : interpolation lineaire entre les deux
    Pur (aucune I/O) => testable. En prod, un capteur de luminosite ambiante
    peut remplacer/affiner cette heuristique.
    """
    h = float(hour) % 24.0

    def lerp(a: int, b: int, t: float) -> int:
        t = max(0.0, min(1.0, t))
        return int(round(a + (b - a) * t))

    if 9.0 <= h < 17.0:
        return BRIGHT_DAY
    if 6.0 <= h < 9.0:              # aube : nuit -> jour
        return lerp(BRIGHT_NIGHT, BRIGHT_DAY, (h - 6.0) / 3.0)
    if 17.0 <= h < 21.0:           # crepuscule : jour -> nuit
        return lerp(BRIGHT_DAY, BRIGHT_NIGHT, (h - 17.0) / 4.0)
    return BRIGHT_NIGHT            # 21h -> 6h : nuit profonde


def sanitize_text(message: str, max_len: int = TEXT_MAX) -> str:
    """Nettoie un message avant envoi via `T:`.

    - retire tout caractere de controle (dont '\\n'/'\\r' qui casseraient le
      protocole ligne, et DEL) ;
    - tronque a `max_len` (capacite du buffer MCU).
    Pur => testable. Renvoie la chaine prete a etre placee derriere "T:".
    """
    cleaned = "".join(ch for ch in str(message) if ch >= " " and ch != "\x7f")
    return cleaned[:max_len]


def gauge_value(current: float, max_value: float) -> int:
    """Convertit une grandeur physique en niveau de jauge 0..255.

    Ex. : gauge_value(90, 200) pour afficher 90 km/h sur une echelle de 200.
    Robuste : rapport borne a [0..1] (les depassements saturent la barre),
    max_value <= 0 => 0. Pur => testable. Sert de pont naturel entre la future
    telemetrie OBD (vitesse, RPM, carburant) et la commande `G:`.
    """
    try:
        mv = float(max_value)
        cur = float(current)
    except (TypeError, ValueError):
        return 0
    if mv <= 0.0:
        return 0
    ratio = max(0.0, min(1.0, cur / mv))
    return int(round(ratio * 255))


def clamp_number(value) -> int:
    """Borne une valeur numerique a l'intervalle affichable par `N:` (0..999).

    Robuste : les non-numeriques et les valeurs hors bornes sont ramenes dans
    l'intervalle. Sert de garde-fou naturel entre une source (vitesse OBD,
    temperature...) et la commande `N:`. Pur => testable.
    """
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return NUMBER_MIN
    return max(NUMBER_MIN, min(NUMBER_MAX, v))


# ============================================================
#  Transport
# ============================================================
class KittLink:
    """Lien serie vers le MCU. Bascule en 'dry-run' (stdout) si pyserial
    est absent, si aucun port n'est ouvrable, ou si --dry-run est demande.
    Ainsi la logique de pilotage est 100 % testable sans materiel."""

    def __init__(self, port: str | None = None, baud: int = BAUD,
                 dry_run: bool = False):
        self.baud = baud
        self.dry_run = dry_run
        self.port = port
        self._ser = None
        self.sent: list[str] = []          # historique (utile pour les tests)

        if dry_run:
            self._log(f"[dry-run] aucun port ouvert (baud simule {baud})")
            return

        try:
            import serial  # pyserial ; import paresseux pour rester optionnel
        except ImportError:
            self.dry_run = True
            self._log("[dry-run] pyserial absent -> mode simulation (stdout)")
            return

        ports = [port] if port else list(CANDIDATE_PORTS)
        for p in ports:
            try:
                self._ser = serial.Serial(p, baud, timeout=0.3)
                self.port = p
                self._log(f"[serie] connecte a {p} @ {baud}")
                return
            except Exception:
                continue

        # Rien d'ouvrable : on degrade proprement plutot que de planter.
        self.dry_run = True
        self._log("[dry-run] aucun port serie ouvrable -> mode simulation")

    @staticmethod
    def _log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    def send(self, line: str) -> None:
        """Envoie une ligne de protocole (le '\\n' est ajoute si besoin)."""
        if not line.endswith("\n"):
            line += "\n"
        self.sent.append(line)
        if self.dry_run or self._ser is None:
            # En dry-run, on montre la trame telle qu'elle partirait sur le fil.
            sys.stdout.write("TX> " + line)
            sys.stdout.flush()
        else:
            self._ser.write(line.encode("ascii", errors="ignore"))

    def read_reply(self, timeout: float = 0.5) -> str:
        """Lit une ligne de reponse du MCU (ex. reponse au ping). '' si dry-run."""
        if self.dry_run or self._ser is None:
            return ""
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self._ser.read(1)
            if chunk:
                if chunk in (b"\n", b"\r"):
                    if buf:
                        break
                else:
                    buf += chunk
        return buf.decode("ascii", errors="ignore").strip()

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass


# ============================================================
#  API haut niveau : refletent l'etat de l'IA sur la matrice
# ============================================================
class KittDisplay:
    """Facade lisible au-dessus du protocole serie. Le reste du code Linux
    (voix, OBD, LLM...) appellera ces methodes plutot que d'ecrire des trames."""

    def __init__(self, link: KittLink):
        self.link = link
        self._state: str | None = None
        self._level: int | None = None
        self._bright: int | None = None
        self._text: str | None = None
        self._gauge: int | None = None
        self._number: int | None = None
        self._dash: tuple[int, int] | None = None

    # --- primitives protocole ---
    def set_state(self, state: str) -> None:
        state = state.upper()
        if state not in STATES:
            raise ValueError(f"etat inconnu: {state!r} (attendus: {', '.join(STATES)})")
        # Optimisation : ne re-emet pas un etat identique inutilement.
        if state != self._state:
            self.link.send(f"S:{state}")
            self._state = state
            self._gauge = None      # un etat remplace la jauge cote MCU
            self._number = None     # ...le nombre
            self._dash = None       # ...et le dashboard

    def set_level(self, level: int) -> None:
        level = max(0, min(255, int(level)))
        if level != self._level:
            self.link.send(f"L:{level}")
            self._level = level

    def set_brightness(self, brightness: int) -> None:
        """Luminosite globale de l'afficheur (0..255). Anti-repetition."""
        brightness = max(0, min(255, int(brightness)))
        if brightness != self._bright:
            self.link.send(f"B:{brightness}")
            self._bright = brightness

    def show_text(self, message: str) -> str:
        """Fait defiler `message` sur la matrice (commande `T:`), puis le MCU
        revient de lui-meme a IDLE. Le message est nettoye/tronque. Renvoie le
        texte reellement envoye ('' si vide apres nettoyage => aucune trame)."""
        msg = sanitize_text(message)
        if not msg:
            return ""
        self.link.send(f"T:{msg}")
        self._text = msg
        # Le MCU terminera le defilement en IDLE : on invalide l'etat cache
        # pour que le prochain set_state re-emette la trame meme si c'est IDLE.
        self._state = None
        self._gauge = None
        self._number = None
        self._dash = None
        return msg

    def show_gauge(self, value: int) -> int:
        """Affiche une jauge PERSISTANTE (barre) de `value` (0..255) via `G:`.

        La jauge reste affichee jusqu'a une autre commande. Anti-repetition :
        une valeur identique n'est pas re-emise (utile quand une source OBD
        rafraichit souvent la meme valeur). Renvoie la valeur bornee envoyee.
        """
        value = max(GAUGE_MIN, min(GAUGE_MAX, int(value)))
        if value != self._gauge:
            self.link.send(f"G:{value}")
            self._gauge = value
            # La jauge remplace l'etat/le nombre/le dashboard cote MCU : invalider.
            self._state = None
            self._number = None
            self._dash = None
        return value

    def show_gauge_of(self, current: float, max_value: float) -> int:
        """Comme show_gauge mais a partir d'une grandeur physique et de son max
        (ex. show_gauge_of(90, 200) pour 90 km/h). Renvoie la valeur envoyee."""
        return self.show_gauge(gauge_value(current, max_value))

    def show_number(self, value: int) -> int:
        """Affiche un nombre PERSISTANT (gros chiffres) de `value` (0..999) via `N:`.

        Complement de la jauge : la jauge donne une proportion glancable, le
        nombre donne la valeur EXACTE (vitesse, temperature...). Le nombre reste
        affiche jusqu'a une autre commande. Anti-repetition : une valeur identique
        n'est pas re-emise (une source OBD peut rafraichir souvent la meme valeur).
        Renvoie la valeur bornee (0..999) reellement envoyee.
        """
        value = clamp_number(value)
        if value != self._number:
            self.link.send(f"N:{value}")
            self._number = value
            # Le nombre remplace l'etat/la jauge/le dashboard cote MCU : invalider.
            self._state = None
            self._gauge = None
            self._dash = None
        return value

    def show_dash(self, number: int, gauge: int) -> tuple[int, int]:
        """Affiche un DASHBOARD PERSISTANT (nombre + jauge) via `D:<num>,<gauge>`.

        Reunit les briques #7 et #8 sur un seul ecran : `number` (0..999) en gros
        chiffres en haut, `gauge` (0..255) en barre en bas. On lit d'un coup la
        valeur EXACTE et la PROPORTION. Reste affiche jusqu'a une autre commande.
        Anti-repetition sur le COUPLE (nombre, jauge) : rien n'est re-emis si les
        deux sont inchanges (utile pour une source OBD frequente). Renvoie le
        couple borne reellement envoye.
        """
        number = clamp_number(number)
        gauge = max(GAUGE_MIN, min(GAUGE_MAX, int(gauge)))
        pair = (number, gauge)
        if pair != self._dash:
            self.link.send(f"D:{number},{gauge}")
            self._dash = pair
            # Le dashboard remplace l'etat/la jauge/le nombre cote MCU : invalider.
            self._state = None
            self._gauge = None
            self._number = None
        return pair

    def show_dash_of(self, current: float, max_value: float,
                     number: float | None = None) -> tuple[int, int]:
        """Comme show_dash mais derive nombre ET jauge d'une grandeur physique.

        La barre = proportion current/max_value, et le chiffre = `current`
        (ou `number` s'il est fourni, pour afficher une autre grandeur que la
        proportion, ex. barre = carburant %, chiffre = km restants). C'est le
        pont naturel entre la telemetrie OBD et le cadran combine.
        Ex. show_dash_of(90, 200) => chiffre "90", barre a 45 %.
        """
        g = gauge_value(current, max_value)
        n = clamp_number(current if number is None else number)
        return self.show_dash(n, g)

    def auto_brightness(self, hour: float) -> int:
        """Regle la luminosite selon l'heure (day/night). Renvoie la valeur."""
        b = day_night_brightness(hour)
        self.set_brightness(b)
        return b

    def ping(self) -> str:
        self.link.send("P")
        return self.link.read_reply()

    # --- raccourcis semantiques ---
    def boot(self):            self.set_state("BOOT")
    def idle(self):            self.set_state("IDLE")
    def think(self):           self.set_state("THINK")
    def word(self):            self.set_state("WORD")
    def error(self):           self.set_state("ERROR")

    def listen(self, level: int = 128):
        self.set_state("LISTEN")
        self.set_level(level)

    def speak(self, level: int = 200):
        self.set_state("SPEAK")
        self.set_level(level)


# ============================================================
#  Scenario de demonstration (cote Linux)
# ============================================================
def _envelope(t: float, period: float = 0.9) -> int:
    """Amplitude 0..255 pseudo-parole : porteuse + petites syllabes."""
    carrier = 0.5 * (1.0 + math.sin(t * 2.0 * math.pi / period))
    syllables = 0.5 * (1.0 + math.sin(t * 11.0))
    v = 0.55 * carrier + 0.45 * carrier * syllables
    return int(max(0.0, min(1.0, v)) * 255)


def run_demo(disp: KittDisplay, loops: int = 1, fps: float = 20.0,
             sleeper=time.sleep, hour: float | None = None) -> None:
    """Rejoue un cycle d'interaction realiste et pousse les etats au MCU.
    'sleeper' est injectable pour rendre le scenario testable (no-op en test).
    'hour' (optionnel) regle d'abord la luminosite jour/nuit avant le cycle."""
    if hour is not None:
        disp.auto_brightness(hour)
    dt = 1.0 / fps
    for _ in range(loops):
        # Reveil
        disp.boot()
        sleeper(1.2)

        # Veille (scanner K2000)
        disp.idle()
        sleeper(2.0)

        # Ecoute : le niveau suit une "montee de voix" de l'utilisateur
        disp.set_state("LISTEN")
        for i in range(int(1.6 * fps)):
            disp.set_level(_envelope(i * dt, period=1.3))
            sleeper(dt)

        # Reflexion
        disp.think()
        sleeper(1.6)

        # Parole : amplitude modulee facon TTS
        disp.set_state("SPEAK")
        for i in range(int(2.4 * fps)):
            disp.set_level(_envelope(i * dt, period=0.7))
            sleeper(dt)

        # Signature : defilement "kiTT" (logo dedie, etat WORD)
        disp.word()
        sleeper(2.4)

        # Message defilant arbitraire (commande T:) : demontre le texte libre
        disp.show_text("KITT 2000")
        sleeper(2.6)

        # Jauge persistante (commande G:) : simule une montee en vitesse OBD,
        # affichee en barre "glancable" plutot qu'en texte defilant.
        for kmh in (0, 30, 60, 90, 120, 150):
            disp.show_gauge_of(kmh, 200)     # echelle 0..200 km/h
            sleeper(0.5)
        sleeper(1.2)

        # Nombre persistant (commande N:) : meme grandeur, mais valeur EXACTE.
        # Complement naturel de la jauge (proportion) : ici la vitesse chiffree.
        for kmh in (150, 120, 90):
            disp.show_number(kmh)
            sleeper(0.6)
        sleeper(1.2)

        # Dashboard combine (commande D:) : le cadran final reunit les deux —
        # valeur EXACTE (chiffres) ET proportion (barre) sur le meme ecran, ce
        # que verra typiquement le conducteur pour la vitesse OBD.
        for kmh in (90, 110, 130):
            disp.show_dash_of(kmh, 200)      # chiffre = km/h, barre = km/h / 200
            sleeper(0.6)
        sleeper(1.2)

        # Retour veille
        disp.idle()
        sleeper(1.0)


# ============================================================
#  CLI
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Controleur Linux kiTT : pilote la matrice LED du MCU via serie.")
    p.add_argument("--port", help="port serie du MCU (sinon auto-detection)")
    p.add_argument("--baud", type=int, default=BAUD, help="debit serie (defaut 115200)")
    p.add_argument("--dry-run", action="store_true",
                   help="n'ouvre aucun port : imprime les trames sur stdout")
    p.add_argument("--once", metavar="ETAT",
                   help="pousse un seul etat (%s) puis quitte" % "/".join(STATES))
    p.add_argument("--level", type=int, default=200,
                   help="niveau a envoyer avec --once (0..255)")
    p.add_argument("--brightness", type=int, metavar="0-255",
                   help="regle la luminosite globale puis quitte (0..255)")
    p.add_argument("--text", metavar="MESSAGE",
                   help="fait defiler MESSAGE sur la matrice puis quitte")
    p.add_argument("--gauge", type=float, metavar="VAL",
                   help="affiche une jauge persistante puis quitte "
                        "(0..255, ou 0..--gauge-max si fourni)")
    p.add_argument("--gauge-max", type=float, metavar="MAX",
                   help="echelle de --gauge (ex. 200 pour des km/h)")
    p.add_argument("--number", type=float, metavar="VAL",
                   help="affiche un nombre persistant (0..999) puis quitte")
    p.add_argument("--dash", type=float, metavar="VAL",
                   help="affiche un dashboard (nombre + jauge) puis quitte : "
                        "chiffre = VAL, barre = VAL/--dash-max (defaut 0..255)")
    p.add_argument("--dash-max", type=float, metavar="MAX",
                   help="echelle de la barre de --dash (ex. 200 pour des km/h)")
    p.add_argument("--ping", action="store_true", help="ping le MCU puis quitte")
    p.add_argument("--loops", type=int, default=1,
                   help="nombre de cycles du scenario demo (defaut 1)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    link = KittLink(port=args.port, baud=args.baud, dry_run=args.dry_run)
    disp = KittDisplay(link)
    try:
        if args.ping:
            reply = disp.ping()
            print(f"ping -> {reply!r}" if reply else "ping -> (pas de reponse / dry-run)")
            return 0
        if args.brightness is not None:
            disp.set_brightness(args.brightness)
            print(f"luminosite poussee : {max(0, min(255, args.brightness))}")
            return 0
        if args.text is not None:
            sent = disp.show_text(args.text)
            print(f"texte pousse : {sent!r}" if sent else "texte vide apres nettoyage : rien envoye")
            return 0
        if args.gauge is not None:
            if args.gauge_max is not None:
                v = disp.show_gauge_of(args.gauge, args.gauge_max)
                print(f"jauge poussee : {v} ({args.gauge:g}/{args.gauge_max:g})")
            else:
                v = disp.show_gauge(int(round(args.gauge)))
                print(f"jauge poussee : {v}")
            return 0
        if args.number is not None:
            v = disp.show_number(args.number)
            print(f"nombre pousse : {v}")
            return 0
        if args.dash is not None:
            if args.dash_max is not None:
                n, g = disp.show_dash_of(args.dash, args.dash_max)
                print(f"dashboard pousse : nombre={n} barre={g} ({args.dash:g}/{args.dash_max:g})")
            else:
                # Sans echelle : la barre reprend directement VAL borne 0..255.
                n, g = disp.show_dash(args.dash, int(round(args.dash)))
                print(f"dashboard pousse : nombre={n} barre={g}")
            return 0
        if args.once:
            state = args.once.upper()
            if state in ("LISTEN", "SPEAK"):
                disp.set_state(state)
                disp.set_level(args.level)
            else:
                disp.set_state(state)
            print(f"etat pousse : {state}")
            return 0
        # Par defaut : scenario de demonstration
        run_demo(disp, loops=args.loops)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
