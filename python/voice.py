#!/usr/bin/env python3
# ============================================================
#  Audi kiTT — Assistant vocal (cote MPU Linux)      python/voice.py
# ============================================================
#  Role (iteration #12) : donner un "visage" a l'assistant vocal en
#  pilotant la matrice LED au fil d'une interaction parlee, EXACTEMENT
#  comme le module OBD (obd.py) pilote le cadran au fil de la telemetrie.
#
#  Le pipeline audio reel (wake-word "kiTT", STT, LLM, TTS) tournera cote
#  Linux plus tard et dependra du materiel (micro, haut-parleur) et de
#  bibliotheques lourdes. Ce module NE fait PAS ce traitement : il fournit
#  la COUCHE DE DECISION D'AFFICHAGE, purement evenementielle, qui traduit
#  le CYCLE DE VIE d'une interaction vocale en etats/niveaux de la matrice :
#
#      (silence)  --wake()------>  ECOUTE   (S:LISTEN, niveau = micro)
#      ECOUTE     --understood()-> REFLEXION(S:THINK)
#      REFLEXION  --reply_start()->PAROLE   (S:SPEAK, niveau = TTS)
#      PAROLE     --reply_end()--> VEILLE   (S:IDLE)
#
#  Tout est PUR / injectable (aucune I/O, aucun materiel, aucune dependance
#  audio) => 100 % testable dans l'hote, comme le reste du projet. Le seul
#  element a etat (VoiceController) se contente d'appeler KittDisplay ; il
#  reutilise les etats/commandes deja definis (S:/L:/T:), SANS ajouter de
#  commande protocole.
#
#  Ce que ce module apporte a la brique #4 (Voix) de la roadmap :
#    - is_wake_word()      : reconnaissance TOLERANTE du mot de reveil "kiTT"
#      (robuste aux variantes de transcription d'un STT : kit, kite, quitte...) ;
#    - level_from_amplitude(): conversion pure amplitude micro/TTS (0..1) ->
#      niveau L: 0..255, avec porte de bruit (gate) et courbe perceptuelle ;
#    - VoiceController     : machine a etats de l'interaction, qui pousse
#      LISTEN/THINK/SPEAK/IDLE + le niveau, gere le barge-in (re-reveil
#      pendant la parole), l'echo du texte reconnu (T:) et l'etat d'echec
#      (S:ERROR). Un futur pipeline audio n'aura qu'a appeler ces methodes.
#
#  Auteur : Claude (Cowork) — iteration #12 (couche d'affichage vocale).
# ============================================================

from __future__ import annotations

# On reutilise la facade et les helpers deja testes de main.py : une seule
# source de verite pour le protocole serie et la sanitation de texte.
from main import KittDisplay, sanitize_text


# ============================================================
#  1) Etats du cycle de vie d'une interaction vocale
# ============================================================
#  Ce sont les etats de l'INTERACTION (cote logique), a ne pas confondre
#  avec les etats d'AFFICHAGE du MCU (S:LISTEN...). VoiceController fait la
#  correspondance entre les deux.
V_IDLE     = "idle"        # en veille : rien en cours (matrice au repos)
V_LISTEN   = "listen"      # ecoute active (apres wake-word)
V_THINK    = "think"       # traitement (STT + LLM)
V_SPEAK    = "speak"       # restitution vocale (TTS en cours)
V_FAIL     = "fail"        # erreur (ecran ERROR) jusqu'a recover()

VOICE_STATES = (V_IDLE, V_LISTEN, V_THINK, V_SPEAK, V_FAIL)


# ============================================================
#  2) Reconnaissance du mot de reveil "kiTT" (pur, tolerant)
# ============================================================
#  Un STT embarque transcrit rarement "kitt" a la lettre. On accepte donc un
#  petit ensemble d'homophones/variantes plausibles, insensible a la casse et
#  a la ponctuation. Volontairement simple et deterministe (pas de dependance
#  a une lib de phonetique) : c'est un garde-fou, le vrai wake-word engine
#  (openWakeWord/porcupine...) restera la premiere ligne de detection.

# Variantes acceptees telles quelles (deja normalisees en minuscules).
WAKE_WORDS = frozenset({
    "kitt", "kit", "kite", "kitty", "quitte", "quit", "keat", "kt",
    "kid",           # transcription bruitee frequente
})


def _normalize_token(text: str) -> str:
    """Reduit un texte a des lettres/chiffres minuscules (retire ponctuation,
    accents ASCII simples et espaces). Pur => testable."""
    out = []
    for ch in str(text).lower():
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def is_wake_word(phrase: str, extra=()) -> bool:
    """True si `phrase` contient (ou est) le mot de reveil "kiTT".

    - insensible a la casse et a la ponctuation ;
    - accepte un petit jeu de variantes de transcription (WAKE_WORDS) ;
    - `extra` : variantes supplementaires propres a l'utilisateur (ex. surnom).
    On teste chaque MOT de la phrase (le wake-word peut etre precede/suivi de
    bruit : "ok kitt", "kitt tu m'entends"). Pur => testable.
    """
    if not phrase:
        return False
    vocab = set(WAKE_WORDS)
    vocab.update(_normalize_token(e) for e in extra)
    # mot par mot (sur les espaces), chacun normalise
    for raw in str(phrase).split():
        if _normalize_token(raw) in vocab:
            return True
    # secours : phrase entiere collee (ex. STT sans espaces)
    return _normalize_token(phrase) in vocab


# ============================================================
#  3) Amplitude audio -> niveau d'affichage L: (pur)
# ============================================================
def level_from_amplitude(amplitude: float, gate: float = 0.03,
                         ceil: float = 1.0, gamma: float = 0.6,
                         floor_level: int = 0) -> int:
    """Convertit une amplitude audio (0..1, RMS micro ou enveloppe TTS) en
    niveau L: 0..255 pour moduler l'animation LISTEN/SPEAK.

    - `gate`  : porte de bruit ; en-dessous => 0 (l'animation \"respire\" au
      repos plutot que de trembler sur le bruit de fond) ;
    - `ceil`  : amplitude consideree comme \"pleine echelle\" (sature a 255) ;
    - `gamma` : courbe perceptuelle (<1 met en valeur les sons faibles, la
      voix parait plus \"vivante\" ; 1.0 = lineaire) ;
    - `floor_level` : niveau minimal renvoye DES QU'ON DEPASSE la porte (utile
      pour garder un minimum de vie visible pendant la parole).
    Robuste aux entrees non numeriques / hors bornes. Pur => testable.
    """
    try:
        a = float(amplitude)
    except (TypeError, ValueError):
        return 0
    if not a > gate:                       # gere aussi NaN (NaN > gate == False)
        return 0
    span = ceil - gate
    if span <= 0.0:
        norm = 1.0
    else:
        norm = (a - gate) / span
    norm = max(0.0, min(1.0, norm))
    if gamma != 1.0 and norm > 0.0:
        norm = norm ** gamma
    level = int(round(norm * 255))
    fl = max(0, min(255, int(floor_level)))
    if level < fl:                         # plancher une fois la porte franchie
        level = fl
    return max(0, min(255, level))


# ============================================================
#  4) Controleur d'interaction vocale : pilote l'afficheur
# ============================================================
class VoiceController:
    """Machine a etats d'une interaction vocale, qui pousse l'afficheur kiTT.

    C'est le pendant \"voix\" du TelemetryController (OBD) : un pipeline audio
    reel appellera ces methodes au fil des evenements, sans jamais ecrire de
    trame serie a la main.

    Transitions (renvoient un bool : True si acceptee, sinon ignoree) :
        wake()          (silence|parole) -> ECOUTE   [barge-in permis]
        hear(amp)       en ECOUTE : module le niveau micro (S:LISTEN + L:)
        understood(txt) ECOUTE -> REFLEXION (S:THINK ; echo optionnel du texte)
        reply_start()   REFLEXION -> PAROLE (S:SPEAK)
        say(amp)        en PAROLE : module le niveau TTS (L:)
        reply_end()     PAROLE -> VEILLE (S:IDLE)
        sleep()         force le retour en VEILLE (S:IDLE) depuis n'importe ou
        fail(msg)       -> ECHEC (S:ERROR, + message defilant optionnel)
        recover()       ECHEC -> VEILLE (S:IDLE)

    Les transitions invalides sont IGNOREES (renvoient False) plutot que de
    lever : un flux audio bruite ne doit pas faire planter l'affichage.
    """

    def __init__(self, disp: KittDisplay, wake_words=(),
                 listen_level: int = 96, speak_floor: int = 40,
                 echo_command: bool = False):
        self.disp = disp
        self.extra_wake = tuple(wake_words)      # variantes utilisateur du wake-word
        self.listen_level = max(0, min(255, int(listen_level)))
        self.speak_floor = max(0, min(255, int(speak_floor)))
        self.echo_command = bool(echo_command)   # scroller le texte reconnu ?
        self.state = V_IDLE
        self.last_command: str = ""              # derniere phrase \"understood\"
        self.turns = 0                           # nb d'interactions completees

    # --- introspection ---
    def is_active(self) -> bool:
        """True si une interaction est en cours (ni veille ni echec)."""
        return self.state in (V_LISTEN, V_THINK, V_SPEAK)

    # --- detection du wake-word (pratique : combine reco + wake) ---
    def maybe_wake(self, phrase: str) -> bool:
        """Reveille l'assistant si `phrase` contient le wake-word. Renvoie True
        si le reveil a eu lieu. Passerelle pratique pour un flux STT continu."""
        if is_wake_word(phrase, extra=self.extra_wake):
            return self.wake()
        return False

    # --- transitions du cycle de vie ---
    def wake(self) -> bool:
        """Entre en ECOUTE (apres detection du wake-word). Autorise depuis la
        VEILLE, mais aussi pendant la PAROLE (barge-in : l'utilisateur coupe
        kiTT pour reformuler). Ignore si deja en ECOUTE/REFLEXION ou en ECHEC.
        """
        if self.state in (V_IDLE, V_SPEAK):
            self.state = V_LISTEN
            self.disp.listen(self.listen_level)   # S:LISTEN + niveau de depart
            return True
        return False

    def hear(self, amplitude: float) -> int | None:
        """Module l'animation d'ecoute avec l'amplitude micro (0..1). N'a d'effet
        qu'en ECOUTE. Renvoie le niveau L: emis, ou None si hors ecoute."""
        if self.state != V_LISTEN:
            return None
        level = level_from_amplitude(amplitude)
        self.disp.set_level(level)
        return level

    def understood(self, text: str = "") -> bool:
        """Fin de l'ecoute : passe en REFLEXION (S:THINK). `text` = phrase
        reconnue (memorisee ; scrollee via T: si echo_command). Autorise
        uniquement depuis l'ECOUTE. Renvoie True si accepte."""
        if self.state != V_LISTEN:
            return False
        self.last_command = sanitize_text(text) if text else ""
        if self.echo_command and self.last_command:
            # Echo bref du texte reconnu (le MCU revient a IDLE apres le scroll,
            # mais on enchaine tout de suite sur THINK ci-dessous).
            self.disp.show_text(self.last_command)
        self.state = V_THINK
        self.disp.think()                         # S:THINK
        return True

    def reply_start(self) -> bool:
        """Debut de la restitution vocale : passe en PAROLE (S:SPEAK). Autorise
        depuis REFLEXION (cas normal) ou ECOUTE (reponse immediate sans calcul).
        """
        if self.state in (V_THINK, V_LISTEN):
            self.state = V_SPEAK
            self.disp.speak(self.speak_floor if self.speak_floor else 200)
            return True
        return False

    def say(self, amplitude: float) -> int | None:
        """Module l'animation de parole avec l'amplitude TTS (0..1). N'a d'effet
        qu'en PAROLE. Un plancher (speak_floor) garde un minimum de vie meme sur
        les silences courts entre mots. Renvoie le niveau L: emis, ou None."""
        if self.state != V_SPEAK:
            return None
        level = level_from_amplitude(amplitude, floor_level=self.speak_floor)
        self.disp.set_level(level)
        return level

    def reply_end(self) -> bool:
        """Fin de la restitution : retour en VEILLE (S:IDLE). Autorise depuis la
        PAROLE. Compte une interaction complete. Renvoie True si accepte."""
        if self.state == V_SPEAK:
            self.state = V_IDLE
            self.turns += 1
            self.disp.idle()                      # S:IDLE
            return True
        return False

    def sleep(self) -> bool:
        """Force le retour en VEILLE (S:IDLE) depuis n'importe quel etat actif
        (ex. timeout d'inactivite, annulation). No-op si deja en veille."""
        if self.state == V_IDLE:
            return False
        self.state = V_IDLE
        self.disp.idle()
        return True

    def fail(self, message: str = "") -> bool:
        """Signale une erreur (STT/LLM/TTS indisponible...) : ecran ERROR, plus
        un message defilant optionnel. Reste en ECHEC jusqu'a recover()/wake().
        """
        self.state = V_FAIL
        self.disp.error()                         # S:ERROR (ecran rouge)
        if message:
            self.disp.show_text(message)          # detail defilant (revient IDLE)
        return True

    def recover(self) -> bool:
        """Sort de l'ECHEC vers la VEILLE (S:IDLE). No-op hors ECHEC."""
        if self.state != V_FAIL:
            return False
        self.state = V_IDLE
        self.disp.idle()
        return True


# ============================================================
#  5) Demo (dry-run) : une interaction vocale complete simulee
# ============================================================
def _amp_envelope(i: int, n: int, base: float = 0.15) -> float:
    """Enveloppe d'amplitude 0..1 pseudo-parole (montee, syllabes, descente),
    PURE et sans dependance (pas de math.random). Sert la demo/les tests."""
    if n <= 1:
        return base
    x = i / (n - 1)                    # 0..1 sur la duree
    # cloche douce (monte puis descend) + petite ondulation \"syllabique\"
    bell = 1.0 - (2.0 * x - 1.0) ** 2  # parabole max au milieu
    ripple = 0.5 + 0.5 * ((i % 4) / 3.0)
    v = base + (1.0 - base) * bell * (0.6 + 0.4 * ripple)
    return max(0.0, min(1.0, v))


def run_voice_demo(disp: KittDisplay, sleeper=None) -> None:
    """Rejoue une interaction vocale complete (dry-run) qui exerce TOUTE la
    machine a etats : wake -> ecoute (niveau micro) -> reflexion -> parole
    (niveau TTS) -> veille, plus un barge-in et un cas d'echec. En dry-run on
    'voit' partir les trames S:LISTEN/L:/S:THINK/S:SPEAK/S:IDLE/S:ERROR.
    `sleeper` injectable (no-op en test)."""
    import time as _time
    if sleeper is None:
        sleeper = _time.sleep

    vc = VoiceController(disp, echo_command=True)

    # 1) Detection du wake-word dans un flux STT.
    vc.maybe_wake("ok kiTT")               # -> ECOUTE
    sleeper(0.3)

    # 2) Ecoute : l'utilisateur parle, le niveau micro module l'animation.
    n = 16
    for i in range(n):
        vc.hear(_amp_envelope(i, n))
        sleeper(0.05)

    # 3) Fin de phrase reconnue -> reflexion (echo du texte + S:THINK).
    vc.understood("QUELLE HEURE EST-IL")   # -> REFLEXION
    sleeper(0.6)

    # 4) Reponse vocale : le niveau TTS module l'animation de parole.
    vc.reply_start()                       # -> PAROLE
    n2 = 20
    for i in range(n2):
        vc.say(_amp_envelope(i, n2))
        sleeper(0.05)
    vc.reply_end()                         # -> VEILLE
    sleeper(0.4)

    # 5) Barge-in : l'utilisateur relance kiTT au milieu d'une reponse.
    vc.wake()                              # ECOUTE
    vc.reply_start()                       # reponse immediate (LISTEN -> SPEAK)
    vc.say(0.8)
    vc.wake()                              # barge-in : recoupe en ECOUTE
    vc.understood("NON, LA METEO")
    vc.reply_start()
    vc.say(0.5)
    vc.reply_end()                         # -> VEILLE

    # 6) Cas d'echec : service indisponible -> ecran ERROR puis recuperation.
    vc.fail("STT KO")                      # -> ECHEC (S:ERROR + message)
    sleeper(0.5)
    vc.recover()                           # -> VEILLE


def main(argv=None) -> int:
    """CLI minimal : joue la demo vocale en dry-run (aucun materiel requis)."""
    import main as kitt
    link = kitt.KittLink(dry_run=True)
    disp = kitt.KittDisplay(link)
    run_voice_demo(disp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
