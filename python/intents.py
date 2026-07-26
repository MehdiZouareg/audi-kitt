#!/usr/bin/env python3
# ============================================================
#  Audi kiTT — Reconnaissance d'intentions vocales   python/intents.py
# ============================================================
#  Role (iteration #13) : c'est le CHAINON MANQUANT entre la parole reconnue
#  (module voice.py, #12) et les ACTIONS du systeme (telemetrie obd.py #10/#11,
#  afficheur main.py). Jusqu'ici :
#     - voice.py savait DETECTER le wake-word et animer l'ecoute/la parole ;
#     - obd.py savait CHOISIR la grandeur focus et ALERTER ;
#     ...mais rien ne traduisait une phrase du conducteur ("kiTT, montre la
#     vitesse", "mode nuit", "mets-toi en veille") en un appel concret.
#  Ce module comble ce vide : il TRANSFORME un texte reconnu (sortie STT) en
#  une INTENTION structuree, puis la ROUTE vers le bon controleur.
#
#  C'est l'item explicite de la roadmap :
#    #4 (Voix)  : "cabler next_focus OBD / commandes voiture sur les intentions
#                 reconnues" ;
#    #6 (Assistant conversationnel) : premiere brique — les commandes simples
#                 sont traitees ici, le RESTE (UNKNOWN) est laisse a un futur LLM.
#
#  Principe de conception (identique au reste du projet) :
#    - `parse_intent(texte)` est 100 % PUR (aucune I/O) => testable isolement.
#      Reconnaissance par MOTS-CLES, insensible a la casse ET aux accents (un
#      STT embarque produit souvent du texte sans accents ou mal accentue).
#      Volontairement simple et deterministe (pas de dependance NLP lourde) :
#      c'est un garde-fou rapide qui capte les commandes frequentes ; tout ce
#      qui n'est pas reconnu tombe en UNKNOWN et sera routable vers un LLM.
#    - `IntentRouter` est le SEUL element a etat : il applique une intention aux
#      controleurs injectes (KittDisplay + optionnellement TelemetryController /
#      VoiceController) et renvoie une REPONSE PARLEE (ce que le TTS dirait).
#      Aucune trame serie ecrite a la main : il passe par les facades existantes.
#
#  Boucle cible (pipeline audio reel, plus tard) :
#      texte = stt.transcribe(audio)
#      if not vc.maybe_wake(texte):           # pas le wake-word
#          res = router.route(texte)          # commande ? -> action + reponse
#          if res.handled:
#              tts.say(res.reply)             # kiTT confirme a voix haute
#          else:
#              tts.say(llm.answer(texte))     # sinon : conversation libre (#6)
#
#  Auteur : Claude (Cowork) — iteration #13 (intentions & commandes voiture).
# ============================================================

from __future__ import annotations

# On reste decouple des modules lourds : parse_intent n'importe RIEN. Le router
# appelle les facades via duck-typing (disp.set_brightness, telemetry.set_focus,
# voice.sleep...), donc il fonctionne avec de simples mocks en test.
from main import BRIGHT_DAY, BRIGHT_NIGHT, BRIGHT_DUSK


# ============================================================
#  1) Catalogue des intentions
# ============================================================
#  Chaque commande frequente du conducteur a un nom stable. Les intentions de
#  "focus" portent en plus une grandeur (`metric`) alignee sur les cles du
#  registre obd.METRICS ("speed"/"rpm"/"coolant"/"fuel").
INTENT_FOCUS      = "focus"        # montrer une grandeur precise (metric renseigne)
INTENT_NEXT_FOCUS = "next_focus"   # grandeur suivante (rotation)
INTENT_BRIGHTER   = "brighter"     # augmenter la luminosite
INTENT_DIMMER     = "dimmer"       # baisser la luminosite
INTENT_NIGHT      = "night"        # mode nuit (luminosite basse)
INTENT_DAY        = "day"          # mode jour (luminosite pleine)
INTENT_SLEEP      = "sleep"        # se mettre en veille / se taire
INTENT_GREET      = "greet"        # salutation
INTENT_UNKNOWN    = "unknown"      # non reconnu -> a router vers un LLM (#6)


class Intent:
    """Resultat immuable de l'analyse d'une phrase.

    - `name`   : une des constantes INTENT_* ci-dessus ;
    - `metric` : pour INTENT_FOCUS, la cle de grandeur ("speed"...), sinon None ;
    - `text`   : la phrase normalisee ayant servi a decider (utile au debogage).
    Egalite/representation definies pour des tests lisibles.
    """

    __slots__ = ("name", "metric", "text")

    def __init__(self, name: str, metric: str | None = None, text: str = ""):
        self.name = name
        self.metric = metric
        self.text = text

    def __eq__(self, other):
        return (isinstance(other, Intent)
                and self.name == other.name
                and self.metric == other.metric)

    def __hash__(self):
        return hash((self.name, self.metric))

    def __repr__(self):
        m = f", metric={self.metric!r}" if self.metric else ""
        return f"Intent({self.name!r}{m})"


# ============================================================
#  2) Normalisation du texte (pure, tolerante aux accents)
# ============================================================
# Repli accent -> ASCII : un STT embarque transcrit souvent "temperature"
# sans accent, ou "regime"/"régime" indifferemment. On compare sur une base
# ASCII minuscule pour ne pas rater une commande a cause d'un accent.
_ACCENT_MAP = str.maketrans(
    "àâäáãåçéèêëíìîïñóòôöõøúùûüýÿ",
    "aaaaaaceeeeiiiinoooooouuuuyy",
)


def normalize(text: str) -> str:
    """Minuscule + repli des accents + reduction aux lettres/chiffres/espaces.

    Ex. "Régime, s'il te plaît !" -> "regime s il te plait". Pur => testable.
    Les mots restent separes par des espaces simples (facilite le matching mot
    a mot et la recherche de locutions).
    """
    low = str(text).lower().translate(_ACCENT_MAP)
    out = []
    for ch in low:
        out.append(ch if (ch.isalnum() or ch.isspace()) else " ")
    return " ".join("".join(out).split())


# ============================================================
#  3) Vocabulaire des commandes (locutions normalisees)
# ============================================================
#  Chaque intention est associee a une liste de locutions (deja normalisees) ;
#  une phrase declenche l'intention si elle CONTIENT l'une d'elles (sous-chaine
#  sur mots entoures d'espaces). On teste les intentions les plus SPECIFIQUES
#  d'abord (ex. "mode nuit" avant un eventuel "nuit" isole) via l'ordre de
#  PRIORITY plus bas.

# Grandeurs telemetriques : locution -> cle obd.METRICS.
_METRIC_PHRASES = {
    "speed":   ("vitesse", "vites", "km h", "kmh", "allure", "vite je roule",
                "je roule a combien", "a quelle vitesse"),
    "rpm":     ("regime", "rpm", "tours moteur", "tours minute", "compte tours",
                "compte tour", "tr min", "le moteur tourne"),
    "coolant": ("temperature", "temp moteur", "temperature moteur", "chaleur",
                "il fait chaud dans le moteur", "refroidissement", "le moteur chauffe"),
    "fuel":    ("carburant", "essence", "reservoir", "fuel", "gasoil", "gazole",
                "niveau d essence", "combien il reste", "autonomie"),
}

_BRIGHTER_PHRASES = (
    "plus lumineux", "plus clair", "plus de lumiere", "monte la luminosite",
    "augmente la luminosite", "eclaire", "eclaircis", "plus fort la lumiere",
)
_DIMMER_PHRASES = (
    "moins lumineux", "plus sombre", "moins de lumiere", "baisse la luminosite",
    "diminue la luminosite", "assombris", "assombri", "reduis la luminosite",
)
_NIGHT_PHRASES = ("mode nuit", "il fait nuit", "vision nocturne", "eclairage nuit")
_DAY_PHRASES   = ("mode jour", "il fait jour", "plein jour", "eclairage jour")

_SLEEP_PHRASES = (
    "en veille", "mets toi en veille", "mise en veille", "endors toi", "dors",
    "au revoir", "a plus tard", "a plus", "tais toi", "silence", "chut",
    "arrete toi", "arrete de parler", "stop", "annule", "laisse tomber",
)
_GREET_PHRASES = (
    "bonjour", "bonsoir", "salut", "coucou", "hello", "hey", "yo", "re",
)


def _contains_phrase(norm_text: str, phrase: str) -> bool:
    """True si `phrase` (deja normalisee) apparait comme suite de mots ENTIERS
    dans `norm_text`. On entoure d'espaces pour ne pas confondre "temp" avec
    "temperature" ou "vite" avec "invite"."""
    return f" {phrase} " in f" {norm_text} "


# Ordre de priorite : les intentions les plus specifiques d'abord. `night`/`day`
# avant `dimmer`/`brighter` (un "mode nuit" explicite prime), et le focus en
# dernier (locutions les plus generiques).
def _match_metric(norm_text: str) -> str | None:
    for key, phrases in _METRIC_PHRASES.items():
        for ph in phrases:
            if _contains_phrase(norm_text, normalize(ph)):
                return key
    return None


def parse_intent(text: str) -> Intent:
    """Analyse une phrase reconnue -> Intent. Pur, deterministe, tolerant aux
    accents/casse/ponctuation. Tout ce qui n'est pas une commande connue
    renvoie INTENT_UNKNOWN (a router vers un LLM pour la conversation libre)."""
    n = normalize(text)
    if not n:
        return Intent(INTENT_UNKNOWN, text="")

    # 1) Reglages d'affichage explicites (les plus specifiques).
    if any(_contains_phrase(n, normalize(p)) for p in _NIGHT_PHRASES):
        return Intent(INTENT_NIGHT, text=n)
    if any(_contains_phrase(n, normalize(p)) for p in _DAY_PHRASES):
        return Intent(INTENT_DAY, text=n)
    if any(_contains_phrase(n, normalize(p)) for p in _BRIGHTER_PHRASES):
        return Intent(INTENT_BRIGHTER, text=n)
    if any(_contains_phrase(n, normalize(p)) for p in _DIMMER_PHRASES):
        return Intent(INTENT_DIMMER, text=n)

    # 2) Mise en veille / silence.
    if any(_contains_phrase(n, normalize(p)) for p in _SLEEP_PHRASES):
        return Intent(INTENT_SLEEP, text=n)

    # 3) Grandeur suivante (rotation du focus).
    if (_contains_phrase(n, "suivant") or _contains_phrase(n, "suivante")
            or _contains_phrase(n, "autre chose") or _contains_phrase(n, "change")
            or _contains_phrase(n, "grandeur suivante")):
        return Intent(INTENT_NEXT_FOCUS, text=n)

    # 4) Grandeur telemetrique precise ("montre la vitesse"...).
    metric = _match_metric(n)
    if metric is not None:
        return Intent(INTENT_FOCUS, metric=metric, text=n)

    # 5) Salutation (apres les commandes : "salut kiTT" ne doit pas manger un
    #    "montre la vitesse" dans la meme phrase).
    if any(_contains_phrase(n, normalize(p)) for p in _GREET_PHRASES):
        return Intent(INTENT_GREET, text=n)

    return Intent(INTENT_UNKNOWN, text=n)


# ============================================================
#  4) Reponses parlees (ce que le TTS restituerait)
# ============================================================
# Etiquettes lisibles pour la confirmation vocale d'un focus.
_SPOKEN_METRIC = {
    "speed":   "Vitesse",
    "rpm":     "Regime moteur",
    "coolant": "Temperature moteur",
    "fuel":    "Niveau de carburant",
    "throttle":"Acceleration",
    "load":    "Charge moteur",
    "intake":  "Temperature d admission",
}


def spoken_metric(key: str) -> str:
    """Nom parle d'une grandeur (fallback = la cle brute)."""
    return _SPOKEN_METRIC.get(key, key)


# ============================================================
#  5) Resultat de routage
# ============================================================
class RouteResult:
    """Ce que renvoie IntentRouter.route().

    - `intent`  : l'Intent reconnu ;
    - `reply`   : la phrase que kiTT dirait a voix haute ("" si rien a dire) ;
    - `handled` : True si une commande a ete traitee ici. Si False (UNKNOWN), le
      pipeline doit passer la main a un LLM (conversation libre, #6).
    """

    __slots__ = ("intent", "reply", "handled")

    def __init__(self, intent: Intent, reply: str = "", handled: bool = False):
        self.intent = intent
        self.reply = reply
        self.handled = handled

    def __repr__(self):
        return (f"RouteResult({self.intent!r}, reply={self.reply!r}, "
                f"handled={self.handled})")


# ============================================================
#  6) Routeur : applique une intention aux controleurs
# ============================================================
class IntentRouter:
    """Traduit une intention en action concrete sur le systeme kiTT.

    Dependances injectees (toutes optionnelles sauf l'afficheur) :
      - `disp`      : KittDisplay (obligatoire) — pour la luminosite ;
      - `telemetry` : TelemetryController (obd.py) — pour set_focus/next_focus ;
      - `voice`     : VoiceController (voice.py) — pour la mise en veille propre.
    Toutes appelees par duck-typing => de simples mocks suffisent en test.

    Le routeur ne connait PAS le pipeline audio : il renvoie juste la reponse
    a dire (RouteResult.reply). C'est l'appelant qui la passe au TTS.
    """

    # Pas de luminosite absolue : on regle par paliers pour "plus/moins clair".
    def __init__(self, disp, telemetry=None, voice=None,
                 brightness: int | None = None, step: int = 50):
        self.disp = disp
        self.telemetry = telemetry
        self.voice = voice
        # Etat de luminosite courant (le protocole B: n'a pas de lecture : on
        # memorise ici pour pouvoir incrementer/decrementer par paliers).
        self.brightness = BRIGHT_DAY if brightness is None else _clip8(brightness)
        self.step = max(1, int(step))

    # --- helpers internes ---
    def _apply_brightness(self, value: int) -> int:
        self.brightness = _clip8(value)
        self.disp.set_brightness(self.brightness)
        return self.brightness

    def _set_focus(self, key: str) -> bool:
        """Change le focus si la telemetrie est branchee ET connait la cle."""
        if self.telemetry is None:
            return False
        before = getattr(self.telemetry, "focus", None)
        self.telemetry.set_focus(key)
        return getattr(self.telemetry, "focus", None) == key or before == key

    # --- point d'entree principal ---
    def route(self, text: str) -> RouteResult:
        """Analyse `text` puis applique l'intention. Renvoie un RouteResult."""
        return self.handle(parse_intent(text))

    def handle(self, intent: Intent) -> RouteResult:
        """Applique un Intent deja analyse (utile pour tester le routage seul)."""
        name = intent.name

        if name == INTENT_FOCUS:
            ok = self._set_focus(intent.metric)
            label = spoken_metric(intent.metric)
            if ok:
                return RouteResult(intent, f"{label}.", handled=True)
            # Telemetrie absente : on ne peut pas afficher la grandeur, mais on
            # accuse reception (le pipeline saura que la commande est comprise).
            return RouteResult(intent, f"{label} indisponible.", handled=True)

        if name == INTENT_NEXT_FOCUS:
            if self.telemetry is not None:
                key = self.telemetry.next_focus()
                return RouteResult(intent, f"{spoken_metric(key)}.", handled=True)
            return RouteResult(intent, "Telemetrie indisponible.", handled=True)

        if name == INTENT_BRIGHTER:
            self._apply_brightness(self.brightness + self.step)
            return RouteResult(intent, "Plus lumineux.", handled=True)

        if name == INTENT_DIMMER:
            self._apply_brightness(self.brightness - self.step)
            return RouteResult(intent, "Plus sombre.", handled=True)

        if name == INTENT_NIGHT:
            self._apply_brightness(BRIGHT_NIGHT)
            return RouteResult(intent, "Mode nuit.", handled=True)

        if name == INTENT_DAY:
            self._apply_brightness(BRIGHT_DAY)
            return RouteResult(intent, "Mode jour.", handled=True)

        if name == INTENT_SLEEP:
            # Mise en veille propre : on passe par VoiceController s'il est la
            # (il gere la machine a etats), sinon on force l'afficheur en IDLE.
            if self.voice is not None:
                self.voice.sleep()
            else:
                self.disp.idle()
            return RouteResult(intent, "", handled=True)   # kiTT se tait

        if name == INTENT_GREET:
            return RouteResult(intent, "Bonjour.", handled=True)

        # INTENT_UNKNOWN : non traite ici -> le pipeline route vers le LLM (#6).
        return RouteResult(intent, "", handled=False)


def _clip8(value) -> int:
    try:
        return max(0, min(255, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


# ============================================================
#  7) Demo (dry-run) : une petite conversation de commandes
# ============================================================
def run_intent_demo(disp, telemetry=None, voice=None) -> list[RouteResult]:
    """Rejoue une sequence de commandes du conducteur et applique chacune.
    Renvoie la liste des RouteResult (pratique pour la demo/les tests). En
    dry-run, on 'voit' partir les trames B:/S: correspondantes."""
    router = IntentRouter(disp, telemetry=telemetry, voice=voice)
    phrases = [
        "bonjour kiTT",
        "montre-moi la vitesse",
        "et le regime moteur ?",
        "grandeur suivante",
        "mode nuit",
        "un peu plus lumineux",
        "quelle est la temperature du moteur",
        "raconte-moi une blague",     # UNKNOWN -> laisse au LLM
        "mets-toi en veille",
    ]
    results = []
    for p in phrases:
        results.append(router.route(p))
    return results


def main(argv=None) -> int:
    """CLI minimal : joue la demo d'intentions en dry-run (aucun materiel)."""
    import main as kitt
    import obd
    link = kitt.KittLink(dry_run=True)
    disp = kitt.KittDisplay(link)
    telemetry = obd.TelemetryController(disp, focus="speed")
    for res in run_intent_demo(disp, telemetry=telemetry):
        tag = "->" if res.handled else "..(LLM).."
        print(f"[{res.intent.name:10}] {tag} {res.reply!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
