#!/usr/bin/env python3
# ============================================================
#  Audi kiTT — Telemetrie OBD-II (cote MPU Linux)     python/obd.py
# ============================================================
#  Role (iteration #10) : transformer des reponses OBD-II brutes (venant
#  plus tard d'un dongle ELM327 / CAN sur le port diagnostic de la TT) en
#  grandeurs physiques (vitesse, RPM, temperature moteur, carburant...),
#  puis PILOTER l'afficheur deja pret (dashboard `D:`, texte `T:`, etat
#  ERROR) avec une LOGIQUE DE SEUILS ET D'ALERTES.
#
#  Pourquoi ce module maintenant : les briques d'affichage (jauge #7,
#  nombre #8, dashboard #9) sont terminees. Ce qui manquait a la brique
#  "Telemetrie vehicule" de la roadmap, c'est la SOURCE et la DECISION :
#    - DECODER : reponses OBD-II mode 01 -> valeur physique (formules std) ;
#    - CHOISIR : quelle grandeur montrer, sur quelle echelle, en chiffres ;
#    - ALERTER : franchissement d'un seuil (surchauffe, carburant bas,
#      surregime) -> message defilant + retour au cadran.
#
#  Tout le coeur est PUR (aucune I/O, aucun materiel) => 100 % testable
#  dans le simulateur/hote, exactement comme le reste du projet. Le seul
#  element a etat (TelemetryController) se contente d'appeler KittDisplay.
#
#  References formules : OBD-II PIDs standard, mode 01 (données courantes).
#  Voir SAE J1979 ; formules reprises telles quelles (A,B,C,D = octets data).
#
#  Iteration #11 : le TelemetryController gagne une VRAIE politique
#  d'affichage au volant :
#    - ESCALADE `S:ERROR` : une alerte qui PERSISTE (>= escalate_after
#      echantillons consecutifs sous/au-dessus du seuil) fait basculer
#      l'afficheur sur l'ecran ERROR (rouge), impossible a manquer ; un
#      simple pic transitoire ne fait que defiler le message (`T:`). Le
#      retour sous le seuil ramene automatiquement au cadran.
#    - ROTATION du FOCUS : `next_focus()` fait tourner la grandeur montree
#      au cadran (vitesse -> RPM -> temperature -> carburant), cycle
#      configurable — la brique UX "choix/bascule de la grandeur focus".
#
#  Auteur : Claude (Cowork) — iteration #10 (branchement telemetrie OBD),
#                             #11 (escalade ERROR + rotation du focus).
# ============================================================

from __future__ import annotations

# main.py fournit les helpers de conversion deja testes (gauge_value,
# clamp_number) et la facade KittDisplay. On les reutilise pour ne PAS
# dupliquer la logique d'echelle/bornage (une seule source de verite).
from main import gauge_value, clamp_number, KittDisplay


# ============================================================
#  1) Decodage des PID OBD-II (mode 01) -> grandeur physique
# ============================================================
#  Chaque PID renvoie 1 a 4 octets de data (A, B, C, D). On applique la
#  formule standard. Les fonctions sont PURES et robustes : trop peu
#  d'octets => None (trame invalide), plutot que de lever ou d'inventer.

# Identifiants PID (mode 01) les plus utiles pour un tableau de bord.
PID_ENGINE_LOAD   = 0x04   # charge moteur calculee (%)
PID_COOLANT_TEMP  = 0x05   # temperature liquide de refroidissement (°C)
PID_RPM           = 0x0C   # regime moteur (tr/min)
PID_SPEED         = 0x0D   # vitesse vehicule (km/h)
PID_INTAKE_TEMP   = 0x0F   # temperature d'admission (°C)
PID_MAF           = 0x10   # debit d'air massique (g/s)
PID_THROTTLE      = 0x11   # position papillon (%)
PID_FUEL_LEVEL    = 0x2F   # niveau de carburant (%)


def _need(data, n: int):
    """Renvoie les n premiers octets (int 0..255) si presents, sinon None.
    Tolere une liste/tuple d'entiers ou de valeurs convertibles."""
    if data is None:
        return None
    try:
        vals = [int(x) & 0xFF for x in data]
    except (TypeError, ValueError):
        return None
    if len(vals) < n:
        return None
    return vals[:n]


def decode_pid(pid: int, data) -> float | None:
    """Decode une reponse OBD-II mode 01 en grandeur physique (unite SI usuelle).

    `data` = octets de donnees (apres le mode et le PID), ex. [0x1A, 0xF8].
    Renvoie None si le PID est inconnu ou la trame trop courte (robuste aux
    reponses partielles/bruitees d'un dongle reel). Pur => testable.
    """
    if pid == PID_SPEED:                       # A -> km/h
        b = _need(data, 1)
        return None if b is None else float(b[0])
    if pid == PID_RPM:                         # (256*A + B)/4 -> tr/min
        b = _need(data, 2)
        return None if b is None else (256.0 * b[0] + b[1]) / 4.0
    if pid == PID_COOLANT_TEMP:                # A - 40 -> °C
        b = _need(data, 1)
        return None if b is None else float(b[0] - 40)
    if pid == PID_INTAKE_TEMP:                 # A - 40 -> °C
        b = _need(data, 1)
        return None if b is None else float(b[0] - 40)
    if pid in (PID_THROTTLE, PID_ENGINE_LOAD, PID_FUEL_LEVEL):  # 100*A/255 -> %
        b = _need(data, 1)
        return None if b is None else (100.0 * b[0]) / 255.0
    if pid == PID_MAF:                          # (256*A + B)/100 -> g/s
        b = _need(data, 2)
        return None if b is None else (256.0 * b[0] + b[1]) / 100.0
    return None                                # PID non gere


def parse_obd_response(line: str):
    """Parse une reponse ELM327 texte type "41 0C 1A F8" -> (pid, [data...]).

    - accepte les espaces multiples / la casse ;
    - le premier octet doit etre 0x41 (reponse au mode 01), sinon => None ;
    - renvoie (pid_int, liste_octets_data) ou None si illisible.
    Pur => testable. Passerelle pratique entre un dongle ELM327 et decode_pid.
    """
    if not line:
        return None
    toks = str(line).replace(",", " ").split()
    try:
        raw = [int(t, 16) for t in toks]
    except ValueError:
        return None
    if len(raw) < 2 or raw[0] != 0x41:         # 0x41 = 0x01 + 0x40 (positive response)
        return None
    pid = raw[1]
    return pid, raw[2:]


# ============================================================
#  2) Metriques : echelle d'affichage + seuils d'alerte
# ============================================================
#  Chaque grandeur decodee doit etre traduite pour l'afficheur :
#    - `scale_max` : borne haute de la BARRE (proportion) du dashboard ;
#    - `to_number` : conversion valeur physique -> nombre affiche 0..999
#      (ex. RPM/100 pour tenir sur 3 chiffres : 6800 tr/min -> "68") ;
#    - `label` : etiquette courte pour le message d'alerte defilant ;
#    - `warn`   : seuil d'alerte ; `warn_high=True` => alerte si valeur >=
#      seuil (surchauffe, surregime, survitesse), False => si valeur <=
#      seuil (carburant bas). `warn=None` => pas d'alerte pour cette metrique.

class Metric:
    """Descripteur d'affichage/alerte d'une grandeur telemetrique. Immuable
    en pratique (on ne modifie pas les instances du registre)."""

    __slots__ = ("key", "pid", "label", "unit", "scale_max",
                 "num_divisor", "warn", "warn_high")

    def __init__(self, key, pid, label, unit, scale_max,
                 num_divisor=1.0, warn=None, warn_high=True):
        self.key = key
        self.pid = pid
        self.label = label
        self.unit = unit
        self.scale_max = float(scale_max)
        self.num_divisor = float(num_divisor)   # nombre affiche = valeur / divisor
        self.warn = warn
        self.warn_high = warn_high

    def to_number(self, value: float) -> int:
        """Valeur physique -> nombre affichable 0..999 (via le diviseur)."""
        return clamp_number(value / self.num_divisor)

    def to_gauge(self, value: float) -> int:
        """Valeur physique -> niveau de barre 0..255 (proportion / scale_max)."""
        return gauge_value(value, self.scale_max)

    def is_warning(self, value: float) -> bool:
        """True si `value` franchit le seuil d'alerte de cette metrique."""
        if self.warn is None:
            return False
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        return v >= self.warn if self.warn_high else v <= self.warn


# Registre des metriques supportees (indexe par cle courte).
# Seuils choisis comme des valeurs "grand public" raisonnables pour une TT
# essence ; ajustables une fois la vraie voiture branchee.
METRICS = {
    "speed":    Metric("speed",   PID_SPEED,        "VIT",  "km/h", scale_max=220,
                       num_divisor=1.0,   warn=180, warn_high=True),   # survitesse
    "rpm":      Metric("rpm",     PID_RPM,          "RPM",  "tr/min", scale_max=8000,
                       num_divisor=100.0, warn=6500, warn_high=True),  # surregime -> "65"
    "coolant":  Metric("coolant", PID_COOLANT_TEMP, "TEMP", "°C",   scale_max=130,
                       num_divisor=1.0,   warn=110, warn_high=True),   # surchauffe
    "fuel":     Metric("fuel",    PID_FUEL_LEVEL,   "FUEL", "%",    scale_max=100,
                       num_divisor=1.0,   warn=10,  warn_high=False),  # carburant bas
    "throttle": Metric("throttle",PID_THROTTLE,     "GAZ",  "%",    scale_max=100,
                       num_divisor=1.0),                               # pas d'alerte
    "load":     Metric("load",    PID_ENGINE_LOAD,  "LOAD", "%",    scale_max=100,
                       num_divisor=1.0),
    "intake":   Metric("intake",  PID_INTAKE_TEMP,  "AIR",  "°C",   scale_max=80,
                       num_divisor=1.0),
}

# Index inverse PID -> cle, pour router une reponse decodee vers sa metrique.
_PID_TO_KEY = {m.pid: m.key for m in METRICS.values()}


def metric_for_pid(pid: int):
    """Renvoie la Metric associee a un PID (ou None si non geree)."""
    key = _PID_TO_KEY.get(pid)
    return METRICS.get(key) if key else None


def dash_pair(key: str, value: float) -> tuple[int, int]:
    """(nombre, barre) a envoyer via `D:` pour la metrique `key` a `value`.

    Ex. dash_pair("rpm", 6800) => (68, ~217) : chiffre "68" (centaines de
    tr/min) + barre a 6800/8000. Leve KeyError si `key` inconnue (usage interne).
    """
    m = METRICS[key]
    return m.to_number(value), m.to_gauge(value)


def alert_text(key: str, value: float) -> str:
    """Message defilant court pour une alerte, ex. "TEMP 112" / "FUEL 8".

    Utilise le nombre AFFICHE (meme convention que le dashboard) pour rester
    coherent avec ce que lit le conducteur. Pur => testable.
    """
    m = METRICS[key]
    return f"{m.label} {m.to_number(value)}"


def severity(key: str, value: float) -> str:
    """'warn' si la valeur franchit le seuil de la metrique, sinon 'ok'."""
    m = METRICS.get(key)
    if m is None:
        return "ok"
    return "warn" if m.is_warning(value) else "ok"


# ============================================================
#  3) Controleur telemetrie : pousse le tout vers l'afficheur
# ============================================================
#  Seul element A ETAT du module. Il decide, a chaque nouvel echantillon :
#    - normal            -> cadran combine `D:` (valeur exacte + proportion) ;
#    - NOUVELLE alerte    -> message defilant d'alerte (edge-declenche, une
#      fois) puis le cadran reprend au tick suivant ;
#    - retour a la normale -> l'alerte est rearmee pour la prochaine fois.
#  L'anti-repetition des trames est deja assure par KittDisplay (show_dash /
#  show_text). On garde ici uniquement la memoire "quelles metriques sont
#  actuellement en alerte" pour ne pas re-scroller le message en boucle, et
#  (iteration #11) un compteur d'echantillons consecutifs en alerte par
#  metrique pour decider d'ESCALADER une alerte SOUTENUE vers l'ecran ERROR.

# Ordre par defaut de rotation de la grandeur montree au cadran (bascule
# volant via next_focus). Les grandeurs les plus utiles a surveiller d'abord.
DEFAULT_FOCUS_CYCLE = ("speed", "rpm", "coolant", "fuel")

# Nombre d'echantillons CONSECUTIFS en alerte au-dela duquel on considere la
# condition SOUTENUE (et non un pic transitoire) => bascule ecran ERROR.
DEFAULT_ESCALATE_AFTER = 3


class TelemetryController:
    """Route des grandeurs telemetriques vers l'afficheur kiTT avec alertes.

    Politique d'affichage (iteration #11) :
      - regime normal            -> cadran `D:` de la metrique focus ;
      - franchissement de seuil  -> message d'alerte defilant `T:` (une fois) ;
      - alerte SOUTENUE          -> ecran `S:ERROR` (>= escalate_after ticks) ;
      - retour sous le seuil     -> rearmement + retour au cadran.

    Exemple d'usage (boucle OBD reelle, plus tard) :
        ctrl = TelemetryController(disp, focus="speed")
        while True:
            pid, data = parse_obd_response(dongle.readline())
            ctrl.feed_pid(pid, data)      # decode + affiche si c'est le focus
            # un appui bouton pourrait appeler ctrl.next_focus() pour changer
            # la grandeur montree au cadran (vitesse -> RPM -> temp -> carbu).
    """

    def __init__(self, disp: KittDisplay, focus: str = "speed",
                 focus_cycle=None, escalate_after: int = DEFAULT_ESCALATE_AFTER):
        self.disp = disp
        self.focus = focus                 # metrique actuellement affichee au cadran
        self.focus_cycle = tuple(focus_cycle) if focus_cycle else DEFAULT_FOCUS_CYCLE
        self.escalate_after = max(1, int(escalate_after))
        self._warned: set[str] = set()     # metriques en alerte "deja annoncee"
        self._streak: dict[str, int] = {}  # nb d'echantillons consecutifs en alerte
        self._escalated = False            # ecran ERROR actuellement affiche ?

    def set_focus(self, key: str) -> None:
        """Change la grandeur montree au cadran (ex. bascule vitesse<->RPM)."""
        if key in METRICS:
            self.focus = key

    def next_focus(self) -> str:
        """Passe a la grandeur SUIVANTE du cycle (bascule volant). Renvoie la
        nouvelle cle focus. Ignore les cles du cycle absentes du registre ; si
        le focus courant n'est pas dans le cycle, repart au debut. Sans effet
        immediat sur l'afficheur : le prochain echantillon de la grandeur
        rafraichira le cadran."""
        cycle = [k for k in self.focus_cycle if k in METRICS]
        if not cycle:
            return self.focus
        try:
            i = cycle.index(self.focus)
        except ValueError:
            i = -1                         # focus hors cycle -> cycle[0] ensuite
        self.focus = cycle[(i + 1) % len(cycle)]
        return self.focus

    def update(self, key: str, value: float) -> str:
        """Traite un echantillon (key, value). Renvoie la severite ('ok'/'warn').

        - Front montant d'un seuil : scroll UN message d'alerte `T:` (visible
          meme si ce n'est pas la metrique focus).
        - Alerte SOUTENUE (>= escalate_after echantillons consecutifs, toutes
          metriques confondues) : bascule sur l'ecran `S:ERROR` tant qu'elle
          dure (anti-repetition assuree par KittDisplay).
        - Sinon, si `key` est la metrique focus : rafraichit le cadran `D:`.
        - Le retour sous le seuil rearme l'alerte et ramene au cadran.
        """
        if key not in METRICS:
            return "ok"
        sev = severity(key, value)

        announced = False
        if sev == "warn":
            self._streak[key] = self._streak.get(key, 0) + 1
            if key not in self._warned:        # front montant : annonce unique
                self._warned.add(key)
                self.disp.show_text(alert_text(key, value))
                announced = True
        else:
            self._warned.discard(key)          # rearme pour la prochaine alerte
            self._streak[key] = 0

        # Escalade : une alerte qui PERSISTE prime sur tout et occupe l'ecran.
        critical = any(s >= self.escalate_after for s in self._streak.values())
        if critical:
            self.disp.set_state("ERROR")       # ecran rouge (anti-repet cote MCU)
            self._escalated = True
            return sev                         # l'ecran ERROR prime sur le cadran
        self._escalated = False                # plus de condition critique

        if announced:
            return sev                         # laisse le message d'alerte defiler

        if key == self.focus:                  # cadran de la grandeur suivie
            n, g = dash_pair(key, value)
            self.disp.show_dash(n, g)
        return sev

    def feed_pid(self, pid: int, data) -> str:
        """Decode une reponse OBD (pid + data) et la traite via update().
        Renvoie 'ok'/'warn', ou 'na' si le PID n'est pas gere / trame invalide."""
        m = metric_for_pid(pid)
        if m is None:
            return "na"
        value = decode_pid(pid, data)
        if value is None:
            return "na"
        return self.update(m.key, value)


# ============================================================
#  4) Demo (dry-run) : un mini "trajet" qui exerce tout le module
# ============================================================
def run_obd_demo(disp: KittDisplay, sleeper=None) -> None:
    """Rejoue un court trajet simule qui exerce TOUTE la politique d'affichage :
    montee en vitesse, bascule de focus (vitesse -> RPM), surchauffe SOUTENUE
    (alerte defilante puis ecran ERROR, puis retour au cadran), et carburant
    bas (alerte). En dry-run, on 'voit' les trames D:/T:/S: partir. `sleeper`
    injectable (no-op en test)."""
    import time as _time
    if sleeper is None:
        sleeper = _time.sleep

    ctrl = TelemetryController(disp, focus="speed")

    # 1) Acceleration : la vitesse (focus) monte, cadran D: rafraichi.
    for kmh in (0, 40, 80, 120, 160):
        ctrl.update("speed", kmh)
        sleeper(0.4)

    # 2) Bascule de focus (UX volant) : rotation vers le RPM via next_focus().
    ctrl.next_focus()                    # speed -> rpm
    for rpm in (2000, 3500, 5000):
        ctrl.update("rpm", rpm)
        sleeper(0.4)

    # 3) Surchauffe qui S'INSTALLE : franchit le seuil (alerte defilante), puis
    #    persiste => escalade sur l'ecran ERROR (impossible a manquer au volant).
    ctrl.set_focus("coolant")
    for temp in (90, 100, 108, 112, 114, 116, 118):   # >= escalate_after -> ERROR
        ctrl.update("coolant", temp)
        sleeper(0.4)
    # Retour a la normale : sort de l'ecran ERROR, le cadran reprend.
    for temp in (100, 85):
        ctrl.update("coolant", temp)
        sleeper(0.4)

    # 4) Carburant bas : alerte "FUEL x" (seuil bas, pic bref), puis cadran.
    ctrl.set_focus("fuel")
    for pct in (25, 15, 9, 6):
        ctrl.update("fuel", pct)
        sleeper(0.4)

    # 5) Retour au calme : vitesse de croisiere, plus d'alerte.
    ctrl.set_focus("speed")
    ctrl.update("speed", 90)


def main(argv=None) -> int:
    """CLI minimal : joue la demo OBD en dry-run (aucune carte requise)."""
    import main as kitt
    link = kitt.KittLink(dry_run=True)
    disp = kitt.KittDisplay(link)
    run_obd_demo(disp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
