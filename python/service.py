#!/usr/bin/env python3
# ============================================================
#  Audi kiTT — Service d'orchestration (cote MPU Linux)  python/service.py
# ============================================================
#  Role (iteration #14) : c'est le CHEF D'ORCHESTRE. Jusqu'ici, chaque brique
#  logicielle vivait dans son coin :
#     - main.py     : la facade KittDisplay (protocole serie vers le MCU) ;
#     - obd.py      : la telemetrie (decodage PID + cadran + alertes #10/#11) ;
#     - voice.py    : la couche d'affichage de l'assistant vocal (#12) ;
#     - intents.py  : la reconnaissance de commandes + routage (#13).
#  ...mais RIEN ne les assemblait en une application qui tourne. Le pipeline
#  cible ("transcription -> wake / commande / LLM -> piloter la voix -> parler")
#  n'existait que sous forme de commentaire dans intents.py. Ce module le REND
#  CONCRET, et ajoute la couche de ROBUSTESSE (brique #7 de la roadmap) :
#  journalisation, garde-fou (watchdog) qui rattrape les exceptions sans faire
#  planter l'affichage, et arret propre (SIGINT/SIGTERM).
#
#  Ce que `KittService` apporte :
#    1) `feed_stt(texte)` — POINT D'ENTREE unique d'un flux STT continu :
#         - wake-word    -> reveille l'assistant (S:LISTEN) ;
#         - sinon, si actif, la phrase est traitee comme un TOUR DE PAROLE.
#    2) `converse(texte)` — le TOUR DE PAROLE complet :
#         understood (S:THINK) -> router.route(texte) ;
#         si commande connue -> on DIT la reponse (S:SPEAK + niveau TTS -> IDLE) ;
#         sinon (UNKNOWN)     -> on interroge le LLM injecte, puis on la DIT.
#       => c'est la boucle voix<->action<->LLM enfin cablee de bout en bout.
#    3) `on_obd_frame(pid, data)` / `on_metric(cle, valeur)` — pousse la
#       telemetrie vers le cadran (reutilise TelemetryController).
#    4) `apply_brightness_for_hour(heure)` — dimming jour/nuit, en gardant la
#       memoire de luminosite du routeur SYNCHRONISEE (pour que les paliers
#       vocaux "+/- lumineux" repartent de la bonne valeur).
#    5) `serve(events)` / `dispatch(event)` — une petite BOUCLE d'evenements
#       deterministe (un vrai pipeline branchera micro + dongle OBD dessus).
#
#  Tout reste PUR / injectable : le LLM et le TTS sont de simples callables
#  optionnels, tout fonctionne en dry-run sans materiel => 100 % testable,
#  comme le reste du projet. AUCUNE nouvelle commande protocole.
#
#  Boucle cible (pipeline audio reel, plus tard) :
#      svc = build_service(disp, focus="speed", llm=mon_llm, tts=mon_tts)
#      svc.serve(flux_evenements(micro, dongle_obd), install_signals=True)
#
#  Auteur : Claude (Cowork) — iteration #14 (orchestrateur + robustesse).
# ============================================================

from __future__ import annotations

import sys

import main as _main
import obd as _obd
import voice as _voice
import intents as _intents


# Reponses de repli quand aucun LLM n'est branche (le seuil UNKNOWN->LLM de
# #13 est en place, mais tant qu'aucun modele n'est cable on repond poliment).
DEFAULT_LLM_REPLY = "Je ne sais pas encore repondre a ca."
LLM_ERROR_REPLY = "Desole, un souci technique."

# Interjections courantes qui entourent le wake-word ("ok kiTT", "hey kiTT",
# "dis kiTT") : une fois le wake-word retire, s'il ne reste QUE ces mots, la
# phrase est un simple reveil (pas une commande a traiter tout de suite).
WAKE_FILLERS = frozenset({
    "ok", "okay", "hey", "he", "eh", "hep", "dis", "allo", "allez",
    "bon", "alors", "euh", "hmm", "coucou", "oui",
})

# Garde-fou : au bout de N erreurs CONSECUTIVES sur les handlers, on tente de
# "reveiller" l'afficheur (boot -> idle) pour sortir d'un etat coince.
DEFAULT_WATCHDOG_AFTER = 5


def _speech_env(i: int, n: int, base: float = 0.2) -> float:
    """Enveloppe d'amplitude 0..1 pseudo-parole, PURE (aucune dependance a
    l'horloge/au hasard). Sert a animer S:SPEAK quand aucun vrai flux TTS
    d'amplitude n'est fourni : cloche douce + ondulation syllabique."""
    if n <= 1:
        return base
    x = i / (n - 1)
    bell = 1.0 - (2.0 * x - 1.0) ** 2          # max au milieu
    ripple = 0.5 + 0.5 * ((i % 4) / 3.0)
    v = base + (1.0 - base) * bell * (0.6 + 0.4 * ripple)
    return max(0.0, min(1.0, v))


# ============================================================
#  Journal (leger, testable)
# ============================================================
class ServiceLog:
    """Journal minimal : garde les enregistrements en memoire (pratique pour
    les tests et un futur watchdog) et, si `echo`, les imprime sur stderr.
    Pas de dependance a l'horloge (les timestamps casseraient la reproductibilite
    des tests) : c'est un journal d'EVENEMENTS, l'horodatage viendra du systeme."""

    def __init__(self, echo: bool = True):
        self.echo = echo
        self.records: list[tuple[str, str]] = []

    def log(self, kind: str, detail: str = "") -> None:
        self.records.append((kind, detail))
        if self.echo:
            print(f"[kitt:{kind}] {detail}", file=sys.stderr, flush=True)

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.records if k == kind)


# ============================================================
#  Service : le chef d'orchestre
# ============================================================
class KittService:
    """Assemble et coordonne les briques kiTT en une application vivante.

    Dependances injectees :
      - `disp`      : KittDisplay (obligatoire) — la sortie vers le MCU ;
      - `telemetry` : TelemetryController (optionnel) — cadran OBD ;
      - `voice`     : VoiceController (optionnel) — machine a etats vocale ;
      - `router`    : IntentRouter (optionnel) — commandes du conducteur ;
      - `llm`       : callable(texte)->str (optionnel) — conversation libre ;
      - `tts`       : callable(texte)->iterable d'amplitudes 0..1 (optionnel) —
                      pour animer S:SPEAK au rythme reel de la synthese vocale ;
      - `logger`    : ServiceLog (optionnel, sinon cree par defaut).

    Toutes les briques sont appelees par duck-typing => de simples mocks
    suffisent en test. Le service NE parle PAS au materiel directement : il
    passe par les facades deja testees.
    """

    def __init__(self, disp, telemetry=None, voice=None, router=None,
                 llm=None, tts=None, logger=None,
                 watchdog_after: int = DEFAULT_WATCHDOG_AFTER):
        self.disp = disp
        self.telemetry = telemetry
        self.voice = voice
        self.router = router
        self.llm = llm
        self.tts = tts
        self.log = logger if logger is not None else ServiceLog()
        self.watchdog_after = max(1, int(watchdog_after))

        self.errors = 0                 # nb total d'erreurs rattrapees
        self._consec_errors = 0         # erreurs consecutives (pour le watchdog)
        self._running = False           # boucle serve() active ?
        self.turns = 0                  # nb de tours de parole traites

    # ------------------------------------------------------------------
    #  Cycle de vie
    # ------------------------------------------------------------------
    def boot(self) -> None:
        """Sequence de demarrage : ecran BOOT puis retour en veille (IDLE)."""
        self.disp.boot()
        self.disp.idle()
        self.log.log("boot", "pret")

    def shutdown(self) -> None:
        """Arret propre : remet l'afficheur en veille et journalise."""
        try:
            if self.voice is not None:
                self.voice.sleep()
            else:
                self.disp.idle()
        except Exception as e:                 # ne jamais planter a l'arret
            self.log.log("error", f"shutdown: {e!r}")
        self.log.log("shutdown", f"turns={self.turns} errors={self.errors}")

    def request_stop(self) -> None:
        """Demande l'arret de la boucle serve() (appelable depuis un signal)."""
        self._running = False

    # ------------------------------------------------------------------
    #  Entree STT : wake-word OU tour de parole
    # ------------------------------------------------------------------
    def feed_stt(self, text: str):
        """Alimente le service avec une phrase brute d'un flux STT continu.

        - contient le wake-word "kiTT" -> reveille (entre en ECOUTE) PUIS traite
          le reste de la phrase comme une commande ("kiTT, mode nuit" fait les
          DEUX : reveil + reglage, au lieu de perdre la commande) ;
        - pas de wake-word mais assistant ACTIF -> tour de parole ;
        - sinon (en veille, phrase non adressee) -> ignoree.
        Renvoie la reponse parlee du tour, ou None si simple reveil/ignore.
        """
        woke, remainder = self._split_wake(text)
        if woke:
            if self.voice is not None:
                self.voice.wake()              # veille/parole -> ECOUTE
            self.log.log("wake", str(text))
            # Commande collee au wake-word dans la meme phrase ? on l'enchaine.
            # Un reste qui n'est QUE des interjections ("ok kiTT") = simple reveil.
            if self._is_meaningful(remainder):
                return self.converse(remainder)
            return None
        if self.voice is None or self.voice.is_active():
            return self.converse(text)
        self.log.log("ignore", str(text))      # pas adressee a kiTT
        return None

    def _split_wake(self, text: str):
        """(woke, remainder) : detecte le wake-word dans `text`, le RETIRE et
        renvoie le reste de la phrase. Permet de traiter "kiTT montre la vitesse"
        comme un reveil suivi d'une commande. Sans couche voix => jamais de wake."""
        if self.voice is None:
            return False, text
        vocab = set(_voice.WAKE_WORDS)
        vocab.update(_voice._normalize_token(e) for e in self.voice.extra_wake)
        kept, woke = [], False
        for w in str(text).split():
            if _voice._normalize_token(w) in vocab:
                woke = True                    # mot = wake-word -> on l'absorbe
            else:
                kept.append(w)
        return woke, " ".join(kept)

    @staticmethod
    def _is_meaningful(remainder: str) -> bool:
        """True s'il reste au moins un mot qui n'est pas une simple interjection
        (apres retrait du wake-word). "ok" -> False ; "mode nuit" -> True."""
        words = _intents.normalize(remainder).split()
        return any(w not in WAKE_FILLERS for w in words)

    # ------------------------------------------------------------------
    #  Tour de parole complet : STT -> intention/LLM -> reponse parlee
    # ------------------------------------------------------------------
    def converse(self, text: str) -> str:
        """Traite UN tour de parole et renvoie la reponse dite (str, '' si kiTT
        se tait). Assemble la voix (affichage), le routeur (commandes) et le
        LLM (conversation libre) selon le pipeline cible du projet."""
        self.log.log("stt", str(text))

        # 1) On s'assure que l'affichage reflete la REFLEXION. Si la voix n'est
        #    pas deja active (cas d'un appel direct sans wake prealable), on la
        #    reveille (en sortant d'abord d'un eventuel ecran d'echec).
        if self.voice is not None and not self.voice.is_active():
            self.voice.recover()               # no-op hors ECHEC
            self.voice.wake()                  # -> ECOUTE
        if self.voice is not None:
            self.voice.understood(text)        # ECOUTE -> REFLEXION (S:THINK)

        # 2) Decision : commande connue (routeur) sinon conversation libre (LLM).
        res = self.router.route(text) if self.router is not None else None
        handled = bool(res is not None and res.handled)
        if handled:
            reply = res.reply
            self.log.log("intent", f"{res.intent.name} -> {reply!r}")
        else:
            reply = self._ask_llm(text)
            self.log.log("llm", f"{text!r} -> {reply!r}")

        # 3) Restitution : on DIT la reponse (animation S:SPEAK), ou l'on repart
        #    proprement en veille s'il n'y a rien a dire (ex. mise en veille).
        if self.voice is not None:
            if reply:
                self.voice.reply_start()       # -> PAROLE (S:SPEAK)
                self._speak(reply)
                self.voice.reply_end()         # -> VEILLE (S:IDLE)
            else:
                self.voice.sleep()             # rien a dire -> retour veille
        elif reply:
            # Pas de couche voix : on affiche au moins la reponse en defilement.
            self.disp.show_text(reply)

        self.turns += 1
        return reply

    def _ask_llm(self, text: str) -> str:
        """Interroge le LLM injecte pour une phrase non reconnue (#6). Repli
        poli si aucun LLM n'est branche ; capture d'exception (le LLM peut etre
        un service reseau faillible) => l'assistant reste vivant."""
        if self.llm is None:
            return DEFAULT_LLM_REPLY
        try:
            answer = self.llm(text)
        except Exception as e:
            self.log.log("error", f"llm: {e!r}")
            return LLM_ERROR_REPLY
        return answer or DEFAULT_LLM_REPLY

    def _speak(self, reply: str) -> None:
        """Anime l'etat PAROLE au fil de la reponse. Si un backend TTS fournit
        des amplitudes reelles, on les suit ; sinon on synthetise une enveloppe
        dont la longueur suit celle du texte (kiTT "vit" pendant qu'il parle)."""
        if self.tts is not None:
            for amp in self.tts(reply):
                self.voice.say(amp)
            return
        n = max(4, min(24, len(reply)))
        for i in range(n):
            self.voice.say(_speech_env(i, n))

    # ------------------------------------------------------------------
    #  Telemetrie
    # ------------------------------------------------------------------
    def on_obd_frame(self, pid: int, data) -> str:
        """Pousse une reponse OBD brute (pid + octets) vers le cadran."""
        if self.telemetry is None:
            return "na"
        sev = self.telemetry.feed_pid(pid, data)
        self.log.log("obd", f"pid={pid:#04x} -> {sev}")
        return sev

    def on_metric(self, key: str, value: float) -> str:
        """Pousse une grandeur deja decodee (key, value) vers le cadran."""
        if self.telemetry is None:
            return "na"
        sev = self.telemetry.update(key, value)
        self.log.log("metric", f"{key}={value} -> {sev}")
        return sev

    def next_focus(self) -> str | None:
        """Fait tourner la grandeur montree au cadran (bouton/commande volant)."""
        if self.telemetry is None:
            return None
        key = self.telemetry.next_focus()
        self.log.log("focus", key)
        return key

    # ------------------------------------------------------------------
    #  Luminosite jour/nuit (en gardant le routeur synchronise)
    # ------------------------------------------------------------------
    def apply_brightness_for_hour(self, hour: float) -> int:
        """Regle la luminosite selon l'heure et SYNCHRONISE la memoire du
        routeur (sinon un "+ lumineux" vocal repartirait d'une valeur perimee)."""
        b = self.disp.auto_brightness(hour)
        if self.router is not None:
            self.router.brightness = b
        self.log.log("brightness", f"hour={hour} -> {b}")
        return b

    # ------------------------------------------------------------------
    #  Boucle d'evenements + garde-fou
    # ------------------------------------------------------------------
    def dispatch(self, event) -> None:
        """Traite un evenement (kind, payload) de facon ROBUSTE (via _guard).

        Types reconnus :
          ("stt",  "phrase")            -> feed_stt
          ("obd",  (pid, [data...]))    -> on_obd_frame
          ("metric", ("speed", 90))     -> on_metric
          ("hour", 20.0)                -> apply_brightness_for_hour
          ("next_focus", None)          -> next_focus
          ("stop", None)                -> request_stop
        Un type inconnu est journalise sans rien casser.
        """
        try:
            kind, payload = event
        except (TypeError, ValueError):
            self.log.log("error", f"event mal forme: {event!r}")
            return

        if kind == "stt":
            self._guard("stt", self.feed_stt, payload)
        elif kind == "obd":
            self._guard("obd", lambda: self.on_obd_frame(payload[0], payload[1]))
        elif kind == "metric":
            self._guard("metric", lambda: self.on_metric(payload[0], payload[1]))
        elif kind == "hour":
            self._guard("hour", self.apply_brightness_for_hour, payload)
        elif kind == "next_focus":
            self._guard("next_focus", self.next_focus)
        elif kind == "stop":
            self.request_stop()
        else:
            self.log.log("unknown_event", str(kind))

    def _guard(self, label: str, fn, *args):
        """Execute `fn(*args)` en RATTRAPANT toute exception : une trame OBD
        corrompue ou un LLM qui plante ne doit jamais figer l'afficheur. En cas
        d'erreur : on journalise, on montre un ecran d'erreur (visible au
        volant), et le watchdog rattrape un eventuel etat coince."""
        try:
            out = fn(*args)
        except Exception as e:
            self.errors += 1
            self._consec_errors += 1
            self.log.log("error", f"{label}: {e!r}")
            try:                               # signaler visuellement l'erreur
                if self.voice is not None:
                    self.voice.fail("ERR")
                else:
                    self.disp.error()
            except Exception:
                pass
            if self._consec_errors >= self.watchdog_after:
                self._watchdog_reset()
            return None
        self._consec_errors = 0                # succes -> rearme le compteur
        return out

    def _watchdog_reset(self) -> None:
        """Tente de "reveiller" un afficheur coince apres des erreurs repetees."""
        self.log.log("watchdog", f"reset apres {self._consec_errors} erreurs")
        self._consec_errors = 0
        try:
            if self.voice is not None:
                self.voice.recover()
            self.disp.boot()
            self.disp.idle()
        except Exception as e:
            self.log.log("error", f"watchdog: {e!r}")

    def serve(self, events, install_signals: bool = False) -> int:
        """Boucle principale : boot, consomme `events` (iterable de tuples), puis
        arret propre. Renvoie le nombre d'evenements traites. `events` peut etre
        un generateur qui bloque sur le materiel reel ; en test, une liste finie.
        `install_signals` installe SIGINT/SIGTERM -> arret propre (runs reels)."""
        if install_signals:
            self._install_signals()
        self._running = True
        self.boot()
        processed = 0
        try:
            for ev in events:
                if not self._running:
                    break
                self.dispatch(ev)
                processed += 1
        except KeyboardInterrupt:
            self.log.log("signal", "KeyboardInterrupt")
        finally:
            self.shutdown()
        return processed

    def _install_signals(self) -> None:
        import signal
        for s in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
            if s is None:
                continue
            try:
                signal.signal(s, lambda *_: self.request_stop())
            except Exception:
                pass


# ============================================================
#  Fabrique : cablage turnkey des briques
# ============================================================
def build_service(disp, focus: str = "speed", llm=None, tts=None,
                  echo_command: bool = False, logger=None,
                  focus_cycle=None) -> KittService:
    """Assemble un KittService complet et coherent a partir d'un KittDisplay.

    C'est l'objet "application" du projet : telemetrie + voix + routeur cables
    entre eux (le routeur recoit la MEME instance de voix/telemetrie que le
    service, pour que "en veille" ou "grandeur suivante" agissent au bon endroit).
    """
    telemetry = _obd.TelemetryController(disp, focus=focus, focus_cycle=focus_cycle)
    vc = _voice.VoiceController(disp, echo_command=echo_command)
    router = _intents.IntentRouter(disp, telemetry=telemetry, voice=vc)
    return KittService(disp, telemetry=telemetry, voice=vc, router=router,
                       llm=llm, tts=tts, logger=logger)


# ============================================================
#  Demo (dry-run) : une "session" mixte voix + telemetrie
# ============================================================
def run_service_demo(disp=None, llm=None, logger=None) -> KittService:
    """Rejoue une session realiste qui MELE voix et telemetrie via la boucle
    d'evenements : reveil, commandes vocales (focus, mode nuit, question libre
    -> LLM, mise en veille) ENTRELACEES avec des trames OBD. Renvoie le service
    (on inspecte disp.link.sent / svc.log.records). En dry-run, on 'voit' partir
    toutes les trames S:/L:/T:/B:/D:. Aucun materiel requis."""
    if disp is None:
        link = _main.KittLink(dry_run=True)
        disp = _main.KittDisplay(link)
    if llm is None:
        llm = lambda t: "Il est bientot midi."     # LLM de demonstration
    svc = build_service(disp, focus="speed", llm=llm, echo_command=True,
                        logger=logger)

    events = [
        ("hour", 20.0),                            # crepuscule -> dimming
        ("metric", ("speed", 50)),                 # cadran vitesse
        ("stt", "ok kiTT"),                        # reveil
        ("stt", "montre le regime moteur"),        # commande focus -> rpm
        ("obd", (_obd.PID_RPM, [0x1A, 0xF8])),     # trame OBD reelle -> cadran
        ("stt", "kiTT quelle heure est-il"),       # reveil + question -> LLM
        ("stt", "kiTT mode nuit"),                 # reveil + reglage luminosite
        ("metric", ("coolant", 118)),              # surchauffe -> alerte
        ("stt", "kiTT mets-toi en veille"),        # reveil + mise en veille
        ("stop", None),
    ]
    svc.serve(events)
    return svc


def main(argv=None) -> int:
    """CLI minimal : joue la demo d'orchestration en dry-run (aucun materiel)."""
    svc = run_service_demo()
    print(f"\n# session terminee : {svc.turns} tours, {svc.errors} erreurs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
