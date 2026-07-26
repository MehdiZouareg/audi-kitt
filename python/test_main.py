#!/usr/bin/env python3
# ============================================================
#  Audi kiTT — Tests du controleur Linux           test_main.py
# ============================================================
#  Verifie que main.py emet EXACTEMENT le protocole serie attendu par
#  le MCU (S:/L:/B:/T:/G:/N:/D:/P), que obd.py decode/alerte correctement,
#  que voice.py pilote la machine a etats vocale, ET (iteration #13) que
#  intents.py reconnait les commandes du conducteur et les route vers les
#  bons controleurs. Executable directement :  python3 test_main.py
# ============================================================

import main
import obd
import voice
import intents
import service


def link_capture():
    lk = main.KittLink(dry_run=True)
    lk.send = lambda line, _s=lk: _s.sent.append(
        line if line.endswith("\n") else line + "\n")
    return lk


def test_states_alignes():
    assert main.STATES == ("BOOT", "IDLE", "LISTEN", "THINK", "SPEAK", "WORD", "ERROR")


def test_trames_de_base():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.boot()
    disp.idle()
    disp.speak(200)
    assert lk.sent == ["S:BOOT\n", "S:IDLE\n", "S:SPEAK\n", "L:200\n"]


def test_pas_de_repetition_inutile():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.idle()
    disp.idle()
    disp.set_level(100)
    disp.set_level(100)
    assert lk.sent == ["S:IDLE\n", "L:100\n"]


def test_clamp_niveau():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.set_level(999)
    disp.set_level(-5)
    assert lk.sent == ["L:255\n", "L:0\n"]


def test_etat_invalide_leve():
    disp = main.KittDisplay(link_capture())
    try:
        disp.set_state("PIZZA")
    except ValueError:
        pass
    else:
        raise AssertionError("un etat invalide devrait lever ValueError")


def test_luminosite_trame_et_clamp():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.set_brightness(60)
    disp.set_brightness(60)
    disp.set_brightness(999)
    disp.set_brightness(-10)
    assert lk.sent == ["B:60\n", "B:255\n", "B:0\n"]


def test_day_night_brightness_profil():
    assert main.day_night_brightness(13.0) == main.BRIGHT_DAY
    assert main.day_night_brightness(3.0) == main.BRIGHT_NIGHT
    assert main.day_night_brightness(23.5) == main.BRIGHT_NIGHT
    assert main.BRIGHT_NIGHT <= main.day_night_brightness(7.5) <= main.BRIGHT_DAY
    assert main.day_night_brightness(6.5) < main.day_night_brightness(8.5)
    assert main.day_night_brightness(18.0) > main.day_night_brightness(20.0)
    for i in range(0, 24 * 4):
        b = main.day_night_brightness(i / 4.0)
        assert 0 <= b <= 255


def test_auto_brightness_emet_valeur_heure():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    b = disp.auto_brightness(3.0)
    assert b == main.BRIGHT_NIGHT
    assert lk.sent == [f"B:{main.BRIGHT_NIGHT}\n"]


def test_show_text_trame():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    sent = disp.show_text("VITESSE 90")
    assert sent == "VITESSE 90"
    assert lk.sent == ["T:VITESSE 90\n"]


def test_show_text_nettoie_les_sauts_de_ligne():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.show_text("A\nB\r\nC\tD")
    assert lk.sent == ["T:ABCD\n"]


def test_show_text_troncature():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    long_msg = "X" * 100
    sent = disp.show_text(long_msg)
    assert len(sent) == main.TEXT_MAX
    assert lk.sent == [f"T:{'X' * main.TEXT_MAX}\n"]


def test_show_text_vide_ne_transmet_rien():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    sent = disp.show_text("\n\r\t")
    assert sent == ""
    assert lk.sent == []


def test_show_text_invalide_letat_cache():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.idle()
    disp.show_text("HELLO")
    disp.idle()
    assert lk.sent == ["S:IDLE\n", "T:HELLO\n", "S:IDLE\n"]


def test_sanitize_text_pur():
    assert main.sanitize_text("kiTT 2000!") == "kiTT 2000!"
    assert main.sanitize_text("a\x00b\x1fc") == "abc"
    assert len(main.sanitize_text("z" * 500)) == main.TEXT_MAX


def test_show_gauge_trame_et_clamp():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    v = disp.show_gauge(180)
    assert v == 180
    disp.show_gauge(180)
    disp.show_gauge(999)
    disp.show_gauge(-10)
    assert lk.sent == ["G:180\n", "G:255\n", "G:0\n"]


def test_show_gauge_invalide_letat_cache():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.idle()
    disp.show_gauge(120)
    disp.idle()
    assert lk.sent == ["S:IDLE\n", "G:120\n", "S:IDLE\n"]


def test_set_state_invalide_le_cache_jauge():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.show_gauge(200)
    disp.idle()
    disp.show_gauge(200)
    assert lk.sent == ["G:200\n", "S:IDLE\n", "G:200\n"]


def test_gauge_value_pur():
    assert main.gauge_value(0, 200) == 0
    assert main.gauge_value(200, 200) == 255
    assert main.gauge_value(100, 200) == 128
    assert main.gauge_value(300, 200) == 255
    assert main.gauge_value(-10, 200) == 0
    assert main.gauge_value(50, 0) == 0
    prev = -1
    for kmh in range(0, 201, 10):
        v = main.gauge_value(kmh, 200)
        assert 0 <= v <= 255
        assert v >= prev
        prev = v


def test_show_gauge_of_trame():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    v = disp.show_gauge_of(90, 200)
    assert v == main.gauge_value(90, 200)
    assert lk.sent == [f"G:{v}\n"]


def test_show_number_trame_et_clamp():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    v = disp.show_number(90)
    assert v == 90
    disp.show_number(90)
    disp.show_number(5000)
    disp.show_number(-10)
    assert lk.sent == ["N:90\n", "N:999\n", "N:0\n"]


def test_show_number_invalide_letat_cache():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.idle()
    disp.show_number(72)
    disp.idle()
    assert lk.sent == ["S:IDLE\n", "N:72\n", "S:IDLE\n"]


def test_number_et_jauge_s_invalidents_mutuellement():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.show_gauge(120)
    disp.show_number(120)
    disp.show_gauge(120)
    assert lk.sent == ["G:120\n", "N:120\n", "G:120\n"]


def test_clamp_number_pur():
    assert main.clamp_number(0) == 0
    assert main.clamp_number(999) == 999
    assert main.clamp_number(1000) == 999
    assert main.clamp_number(-5) == 0
    assert main.clamp_number(89.6) == 90
    assert main.clamp_number("abc") == 0
    assert main.clamp_number(None) == 0


def test_show_dash_trame_et_clamp():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    pair = disp.show_dash(90, 115)
    assert pair == (90, 115)
    disp.show_dash(90, 115)
    disp.show_dash(5000, 999)
    disp.show_dash(-10, -3)
    assert lk.sent == ["D:90,115\n", "D:999,255\n", "D:0,0\n"]


def test_show_dash_repetition_partielle():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.show_dash(90, 115)
    disp.show_dash(91, 115)
    disp.show_dash(91, 120)
    assert lk.sent == ["D:90,115\n", "D:91,115\n", "D:91,120\n"]


def test_show_dash_of_derive_nombre_et_jauge():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    n, g = disp.show_dash_of(90, 200)
    assert n == main.clamp_number(90)
    assert g == main.gauge_value(90, 200)
    assert lk.sent == [f"D:{n},{g}\n"]
    lk2 = link_capture()
    disp2 = main.KittDisplay(lk2)
    n2, g2 = disp2.show_dash_of(50, 100, number=320)
    assert n2 == 320
    assert g2 == main.gauge_value(50, 100)
    assert lk2.sent == [f"D:320,{g2}\n"]


def test_show_dash_invalide_letat_cache():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.idle()
    disp.show_dash(72, 90)
    disp.idle()
    assert lk.sent == ["S:IDLE\n", "D:72,90\n", "S:IDLE\n"]


def test_dash_jauge_nombre_s_invalidents_mutuellement():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.show_dash(90, 115)
    disp.show_gauge(115)
    disp.show_dash(90, 115)
    disp.show_number(90)
    disp.show_dash(90, 115)
    assert lk.sent == ["D:90,115\n", "G:115\n", "D:90,115\n", "N:90\n", "D:90,115\n"]


def test_scenario_demo_emet_tous_les_etats():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    main.run_demo(disp, loops=1, sleeper=lambda *_: None)
    envoyes = "".join(lk.sent)
    for st in ("BOOT", "IDLE", "LISTEN", "THINK", "SPEAK", "WORD"):
        assert f"S:{st}\n" in envoyes, f"etat {st} manquant dans le scenario"
    assert any(t.startswith("L:") for t in lk.sent)
    assert any(t.startswith("T:") for t in lk.sent)
    assert any(t.startswith("G:") for t in lk.sent)
    assert any(t.startswith("N:") for t in lk.sent)
    assert any(t.startswith("D:") for t in lk.sent)


def test_scenario_demo_avec_heure_emet_luminosite():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    main.run_demo(disp, loops=1, sleeper=lambda *_: None, hour=22.0)
    assert lk.sent[0] == f"B:{main.BRIGHT_NIGHT}\n"


def test_enveloppe_dans_bornes():
    for i in range(0, 200):
        v = main._envelope(i * 0.05)
        assert 0 <= v <= 255


# ============================================================
#  Telemetrie OBD-II (obd.py)                          — iteration #10
# ============================================================

def test_obd_decode_vitesse():
    assert obd.decode_pid(obd.PID_SPEED, [0x00]) == 0.0
    assert obd.decode_pid(obd.PID_SPEED, [90]) == 90.0
    assert obd.decode_pid(obd.PID_SPEED, [0xFF]) == 255.0


def test_obd_decode_rpm():
    assert obd.decode_pid(obd.PID_RPM, [0x00, 0x00]) == 0.0
    assert obd.decode_pid(obd.PID_RPM, [0x1A, 0xF8]) == (256 * 0x1A + 0xF8) / 4.0
    assert obd.decode_pid(obd.PID_RPM, [0x0C, 0x80]) == 800.0


def test_obd_decode_temperature():
    assert obd.decode_pid(obd.PID_COOLANT_TEMP, [0x00]) == -40.0
    assert obd.decode_pid(obd.PID_COOLANT_TEMP, [40]) == 0.0
    assert obd.decode_pid(obd.PID_COOLANT_TEMP, [150]) == 110.0


def test_obd_decode_pourcentages():
    assert obd.decode_pid(obd.PID_THROTTLE, [0xFF]) == 100.0
    assert obd.decode_pid(obd.PID_FUEL_LEVEL, [0x00]) == 0.0
    assert abs(obd.decode_pid(obd.PID_ENGINE_LOAD, [128]) - (100 * 128 / 255)) < 1e-9


def test_obd_decode_trame_invalide():
    assert obd.decode_pid(obd.PID_RPM, [0x1A]) is None
    assert obd.decode_pid(obd.PID_SPEED, []) is None
    assert obd.decode_pid(0x99, [0x01]) is None
    assert obd.decode_pid(obd.PID_SPEED, ["zz"]) is None


def test_obd_parse_response():
    assert obd.parse_obd_response("41 0D 5A") == (0x0D, [0x5A])
    assert obd.parse_obd_response("41 0c 1A f8") == (0x0C, [0x1A, 0xF8])
    assert obd.parse_obd_response("7F 01 12") is None
    assert obd.parse_obd_response("") is None
    assert obd.parse_obd_response("NODATA") is None


def test_obd_dash_pair_echelle():
    n, g = obd.dash_pair("rpm", 6800)
    assert n == 68
    assert g == main.gauge_value(6800, 8000)
    n2, g2 = obd.dash_pair("speed", 90)
    assert n2 == 90
    assert g2 == main.gauge_value(90, 220)


def test_obd_severity_seuils():
    assert obd.severity("coolant", 100) == "ok"
    assert obd.severity("coolant", 110) == "warn"
    assert obd.severity("coolant", 120) == "warn"
    assert obd.severity("rpm", 6000) == "ok"
    assert obd.severity("rpm", 7000) == "warn"
    assert obd.severity("fuel", 20) == "ok"
    assert obd.severity("fuel", 8) == "warn"
    assert obd.severity("speed", 130) == "ok"
    assert obd.severity("speed", 190) == "warn"
    assert obd.severity("throttle", 100) == "ok"
    assert obd.severity("pizza", 999) == "ok"


def test_obd_alert_text():
    assert obd.alert_text("coolant", 112) == "TEMP 112"
    assert obd.alert_text("fuel", 8) == "FUEL 8"
    assert obd.alert_text("rpm", 6800) == "RPM 68"


def test_obd_controller_cadran_normal():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    ctrl = obd.TelemetryController(disp, focus="speed")
    ctrl.update("speed", 90)
    n, g = obd.dash_pair("speed", 90)
    assert lk.sent == [f"D:{n},{g}\n"]


def test_obd_controller_alerte_front_montant():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    ctrl = obd.TelemetryController(disp, focus="coolant")
    assert ctrl.update("coolant", 100) == "ok"
    assert ctrl.update("coolant", 112) == "warn"
    assert ctrl.update("coolant", 114) == "warn"
    textes = [t for t in lk.sent if t.startswith("T:")]
    assert textes == ["T:TEMP 112\n"]
    assert any(t.startswith("D:") for t in lk.sent)


def test_obd_controller_rearme_apres_retour_normal():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    ctrl = obd.TelemetryController(disp, focus="coolant")
    ctrl.update("coolant", 112)
    ctrl.update("coolant", 90)
    ctrl.update("coolant", 115)
    textes = [t for t in lk.sent if t.startswith("T:")]
    assert textes == ["T:TEMP 112\n", "T:TEMP 115\n"]


def test_obd_controller_feed_pid_bout_en_bout():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    ctrl = obd.TelemetryController(disp, focus="speed")
    pid, data = obd.parse_obd_response("41 0D 5A")
    sev = ctrl.feed_pid(pid, data)
    assert sev == "ok"
    n, g = obd.dash_pair("speed", 90)
    assert lk.sent == [f"D:{n},{g}\n"]
    before = len(lk.sent)
    assert ctrl.feed_pid(0x99, [0x01]) == "na"
    assert len(lk.sent) == before


def test_obd_demo_emet_cadran_et_alertes():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    obd.run_obd_demo(disp, sleeper=lambda *_: None)
    assert any(t.startswith("D:") for t in lk.sent)
    textes = [t for t in lk.sent if t.startswith("T:")]
    assert any("TEMP" in t for t in textes)
    assert any("FUEL" in t for t in textes)
    assert "S:ERROR\n" in lk.sent


# ============================================================
#  Politique d'affichage au volant                     — iteration #11
# ============================================================

def test_obd_escalade_alerte_persistante():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    ctrl = obd.TelemetryController(disp, focus="coolant", escalate_after=3)
    assert ctrl.update("coolant", 100) == "ok"
    ctrl.update("coolant", 112)
    ctrl.update("coolant", 114)
    assert "S:ERROR\n" not in lk.sent
    ctrl.update("coolant", 116)
    assert "S:ERROR\n" in lk.sent
    assert [t for t in lk.sent if t.startswith("T:")] == ["T:TEMP 112\n"]
    n_err = lk.sent.count("S:ERROR\n")
    ctrl.update("coolant", 118)
    assert lk.sent.count("S:ERROR\n") == n_err


def test_obd_escalade_retour_normal_reprend_le_cadran():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    ctrl = obd.TelemetryController(disp, focus="coolant", escalate_after=3)
    for temp in (112, 114, 116, 118):
        ctrl.update("coolant", temp)
    assert "S:ERROR\n" in lk.sent
    ctrl.update("coolant", 85)
    n, g = obd.dash_pair("coolant", 85)
    assert lk.sent[-1] == f"D:{n},{g}\n"


def test_obd_pas_d_escalade_sur_pic_transitoire():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    ctrl = obd.TelemetryController(disp, focus="coolant", escalate_after=3)
    ctrl.update("coolant", 112)
    ctrl.update("coolant", 90)
    ctrl.update("coolant", 112)
    ctrl.update("coolant", 90)
    assert "S:ERROR\n" not in lk.sent
    assert [t for t in lk.sent if t.startswith("T:")] == ["T:TEMP 112\n", "T:TEMP 112\n"]


def test_obd_next_focus_cycle():
    disp = main.KittDisplay(link_capture())
    ctrl = obd.TelemetryController(disp, focus="speed")
    assert ctrl.next_focus() == "rpm"
    assert ctrl.next_focus() == "coolant"
    assert ctrl.next_focus() == "fuel"
    assert ctrl.next_focus() == "speed"
    ctrl.focus = "throttle"
    assert ctrl.next_focus() == obd.DEFAULT_FOCUS_CYCLE[0]


def test_obd_next_focus_cycle_personnalise():
    disp = main.KittDisplay(link_capture())
    ctrl = obd.TelemetryController(disp, focus="rpm",
                                   focus_cycle=("rpm", "pizza", "fuel"))
    assert ctrl.next_focus() == "fuel"
    assert ctrl.next_focus() == "rpm"


def test_obd_next_focus_pilote_le_cadran():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    ctrl = obd.TelemetryController(disp, focus="speed")
    ctrl.update("speed", 90)
    ctrl.next_focus()
    ctrl.update("rpm", 3000)
    ctrl.update("speed", 100)
    n, g = obd.dash_pair("rpm", 3000)
    assert lk.sent[-1] == f"D:{n},{g}\n"


# ============================================================
#  Assistant vocal (voice.py)                          — iteration #12
# ============================================================

def test_voice_wake_word_variantes():
    assert voice.is_wake_word("kiTT") is True
    assert voice.is_wake_word("KITT") is True
    assert voice.is_wake_word("ok kitt,") is True
    assert voice.is_wake_word("kitt tu m'entends") is True
    assert voice.is_wake_word("kite") is True
    assert voice.is_wake_word("quitte") is True
    assert voice.is_wake_word("bonjour") is False
    assert voice.is_wake_word("") is False
    assert voice.is_wake_word("kitchen") is False


def test_voice_wake_word_extra_vocab():
    assert voice.is_wake_word("jarvis", extra=("jarvis",)) is True
    assert voice.is_wake_word("jarvis") is False


def test_voice_level_from_amplitude_bornes():
    assert voice.level_from_amplitude(0.0) == 0
    assert voice.level_from_amplitude(0.02) == 0
    assert voice.level_from_amplitude(1.0) == 255
    assert voice.level_from_amplitude(2.0) == 255
    assert voice.level_from_amplitude("boom") == 0
    assert voice.level_from_amplitude(-1.0) == 0
    prev = -1
    for i in range(4, 101):
        v = voice.level_from_amplitude(i / 100.0)
        assert 0 <= v <= 255
        assert v >= prev
        prev = v


def test_voice_level_floor_apres_la_porte():
    assert voice.level_from_amplitude(0.0, floor_level=40) == 0
    v = voice.level_from_amplitude(0.04, floor_level=40)
    assert v == 40
    assert voice.level_from_amplitude(1.0, floor_level=40) == 255


def test_voice_wake_emet_listen():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    assert vc.wake() is True
    assert vc.state == voice.V_LISTEN
    assert lk.sent == ["S:LISTEN\n", f"L:{vc.listen_level}\n"]


def test_voice_hear_module_le_niveau():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    assert vc.hear(0.5) is None
    vc.wake()
    lvl = vc.hear(0.5)
    assert lvl == voice.level_from_amplitude(0.5)
    assert lk.sent[-1] == f"L:{lvl}\n"


def test_voice_cycle_complet_trames():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    vc.wake()
    hlvl = vc.hear(0.5)
    vc.understood("QUELLE HEURE")
    vc.reply_start()
    slvl = vc.say(0.9)
    vc.reply_end()
    assert vc.state == voice.V_IDLE
    assert vc.turns == 1
    assert vc.last_command == "QUELLE HEURE"
    assert lk.sent == [
        "S:LISTEN\n", f"L:{vc.listen_level}\n",
        f"L:{hlvl}\n",
        "S:THINK\n",
        "S:SPEAK\n", f"L:{vc.speak_floor}\n",
        f"L:{slvl}\n",
        "S:IDLE\n",
    ]


def test_voice_transitions_invalides_ignorees():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    assert vc.understood("x") is False
    assert vc.reply_start() is False
    assert vc.reply_end() is False
    assert vc.say(0.5) is None
    assert vc.recover() is False
    assert lk.sent == []


def test_voice_understood_echo_et_sanitation():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp, echo_command=True)
    vc.wake()
    vc.understood("METEO\nDEMAIN")
    assert vc.last_command == "METEODEMAIN"
    textes = [t for t in lk.sent if t.startswith("T:")]
    assert textes == ["T:METEODEMAIN\n"]
    assert "S:THINK\n" in lk.sent
    assert lk.sent.index("T:METEODEMAIN\n") < lk.sent.index("S:THINK\n")


def test_voice_say_plancher():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    vc.wake(); vc.understood(); vc.reply_start()
    assert vc.say(0.04) == vc.speak_floor
    assert vc.say(0.0) == 0


def test_voice_barge_in_pendant_parole():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    vc.wake(); vc.understood(); vc.reply_start()
    assert vc.state == voice.V_SPEAK
    assert vc.wake() is True
    assert vc.state == voice.V_LISTEN
    assert vc.wake() is False
    vc.understood()
    assert vc.wake() is False


def test_voice_reply_start_depuis_ecoute():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    vc.wake()
    assert vc.reply_start() is True
    assert vc.state == voice.V_SPEAK


def test_voice_sleep_force_veille():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    vc.wake()
    assert vc.sleep() is True
    assert vc.state == voice.V_IDLE
    assert lk.sent[-1] == "S:IDLE\n"
    assert vc.sleep() is False


def test_voice_fail_et_recover():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    vc.wake()
    assert vc.fail("STT KO") is True
    assert vc.state == voice.V_FAIL
    assert "S:ERROR\n" in lk.sent
    assert "T:STT KO\n" in lk.sent
    assert vc.recover() is True
    assert vc.state == voice.V_IDLE
    assert lk.sent[-1] == "S:IDLE\n"


def test_voice_maybe_wake_passerelle():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    assert vc.maybe_wake("meteo de demain") is False
    assert vc.state == voice.V_IDLE
    assert vc.maybe_wake("dis kiTT") is True
    assert vc.state == voice.V_LISTEN


def test_voice_is_active():
    disp = main.KittDisplay(link_capture())
    vc = voice.VoiceController(disp)
    assert vc.is_active() is False
    vc.wake();        assert vc.is_active() is True
    vc.understood();  assert vc.is_active() is True
    vc.reply_start(); assert vc.is_active() is True
    vc.reply_end();   assert vc.is_active() is False


def test_voice_amp_envelope_bornes():
    for n in (1, 5, 16, 40):
        for i in range(n):
            a = voice._amp_envelope(i, n)
            assert 0.0 <= a <= 1.0


def test_voice_demo_emet_tous_les_etats():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    voice.run_voice_demo(disp, sleeper=lambda *_: None)
    envoyes = "".join(lk.sent)
    for st in ("LISTEN", "THINK", "SPEAK", "IDLE", "ERROR"):
        assert f"S:{st}\n" in envoyes, f"etat {st} manquant dans la demo vocale"
    assert any(t.startswith("L:") for t in lk.sent)
    assert any(t.startswith("T:") for t in lk.sent)


# ============================================================
#  Reconnaissance d'intentions & routage (intents.py)  — iteration #13
# ============================================================

def test_intents_normalize_accents():
    # Repli des accents + ponctuation -> base ASCII minuscule, mots separes.
    assert intents.normalize("Régime, s'il te plaît !") == "regime s il te plait"
    assert intents.normalize("TEMPÉRATURE") == "temperature"
    assert intents.normalize("  double   espace ") == "double espace"
    assert intents.normalize("") == ""


def test_intents_parse_focus_grandeurs():
    # Chaque grandeur telemetrique frequente est reconnue et mappee sur sa cle.
    assert intents.parse_intent("montre-moi la vitesse") == \
        intents.Intent(intents.INTENT_FOCUS, metric="speed")
    assert intents.parse_intent("affiche le regime moteur") == \
        intents.Intent(intents.INTENT_FOCUS, metric="rpm")
    assert intents.parse_intent("quelle est la temperature du moteur") == \
        intents.Intent(intents.INTENT_FOCUS, metric="coolant")
    assert intents.parse_intent("il reste combien de carburant") == \
        intents.Intent(intents.INTENT_FOCUS, metric="fuel")
    # tolerance accents/casse : "température" accentue passe aussi
    assert intents.parse_intent("la TEMPÉRATURE").metric == "coolant"


def test_intents_parse_reglages_affichage():
    assert intents.parse_intent("mode nuit").name == intents.INTENT_NIGHT
    assert intents.parse_intent("passe en mode jour").name == intents.INTENT_DAY
    assert intents.parse_intent("un peu plus lumineux").name == intents.INTENT_BRIGHTER
    assert intents.parse_intent("c'est trop clair, plus sombre").name == intents.INTENT_DIMMER
    # "mode nuit" prime sur un simple "sombre" (specificite)
    assert intents.parse_intent("mets le mode nuit").name == intents.INTENT_NIGHT


def test_intents_parse_next_et_sleep_et_greet():
    assert intents.parse_intent("grandeur suivante").name == intents.INTENT_NEXT_FOCUS
    assert intents.parse_intent("autre chose").name == intents.INTENT_NEXT_FOCUS
    assert intents.parse_intent("mets-toi en veille").name == intents.INTENT_SLEEP
    assert intents.parse_intent("tais-toi").name == intents.INTENT_SLEEP
    assert intents.parse_intent("bonjour").name == intents.INTENT_GREET
    assert intents.parse_intent("salut kiTT").name == intents.INTENT_GREET


def test_intents_parse_unknown():
    # Une phrase hors commandes tombe en UNKNOWN (a router vers un LLM).
    assert intents.parse_intent("raconte-moi une blague").name == intents.INTENT_UNKNOWN
    assert intents.parse_intent("").name == intents.INTENT_UNKNOWN
    # une commande DANS une phrase plus longue reste captee (priorite commande)
    assert intents.parse_intent("dis donc, montre la vitesse stp").metric == "speed"


def test_intents_route_focus_change_le_telemetry():
    # INTENT_FOCUS appelle telemetry.set_focus avec la bonne cle.
    lk = link_capture()
    disp = main.KittDisplay(lk)
    tele = obd.TelemetryController(disp, focus="speed")
    router = intents.IntentRouter(disp, telemetry=tele)
    res = router.route("montre la temperature moteur")
    assert res.handled is True
    assert tele.focus == "coolant"
    assert "Temperature" in res.reply


def test_intents_route_next_focus():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    tele = obd.TelemetryController(disp, focus="speed")
    router = intents.IntentRouter(disp, telemetry=tele)
    res = router.route("grandeur suivante")
    assert res.handled is True
    assert tele.focus == "rpm"          # speed -> rpm


def test_intents_route_focus_sans_telemetry():
    # Sans TelemetryController, la commande est comprise mais signalee indisponible.
    lk = link_capture()
    disp = main.KittDisplay(lk)
    router = intents.IntentRouter(disp)      # pas de telemetry
    res = router.route("montre la vitesse")
    assert res.handled is True
    assert "indisponible" in res.reply.lower()


def test_intents_route_luminosite_paliers():
    # brighter/dimmer bougent la luminosite par paliers et emettent B:.
    lk = link_capture()
    disp = main.KittDisplay(lk)
    router = intents.IntentRouter(disp, brightness=100, step=50)
    router.route("plus lumineux")
    assert router.brightness == 150
    assert lk.sent[-1] == "B:150\n"
    router.route("plus sombre")
    assert router.brightness == 100
    assert lk.sent[-1] == "B:100\n"
    # bornage : ne depasse jamais 255 ni ne descend sous 0
    router.brightness = 240
    router.route("encore plus lumineux")     # "plus lumineux" contenu
    assert router.brightness == 255


def test_intents_route_mode_nuit_jour():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    router = intents.IntentRouter(disp)
    router.route("mode nuit")
    assert router.brightness == main.BRIGHT_NIGHT
    assert lk.sent[-1] == f"B:{main.BRIGHT_NIGHT}\n"
    router.route("mode jour")
    assert router.brightness == main.BRIGHT_DAY
    assert lk.sent[-1] == f"B:{main.BRIGHT_DAY}\n"


def test_intents_route_sleep_via_voice():
    # SLEEP passe par VoiceController.sleep() quand il est branche.
    lk = link_capture()
    disp = main.KittDisplay(lk)
    vc = voice.VoiceController(disp)
    vc.wake()                                # en ecoute
    router = intents.IntentRouter(disp, voice=vc)
    res = router.route("mets-toi en veille")
    assert res.handled is True
    assert vc.state == voice.V_IDLE          # remis en veille
    assert res.reply == ""                   # kiTT se tait


def test_intents_route_sleep_sans_voice_force_idle():
    # Sans VoiceController, SLEEP force l'afficheur en IDLE.
    lk = link_capture()
    disp = main.KittDisplay(lk)
    disp.set_state("SPEAK")
    router = intents.IntentRouter(disp)
    router.route("en veille")
    assert lk.sent[-1] == "S:IDLE\n"


def test_intents_route_unknown_non_traite():
    # UNKNOWN : handled=False (le pipeline routera vers le LLM), aucune trame.
    lk = link_capture()
    disp = main.KittDisplay(lk)
    router = intents.IntentRouter(disp)
    before = len(lk.sent)
    res = router.route("raconte-moi ta journee")
    assert res.handled is False
    assert res.reply == ""
    assert len(lk.sent) == before            # rien envoye a l'afficheur


def test_intents_route_greet():
    lk = link_capture()
    disp = main.KittDisplay(lk)
    router = intents.IntentRouter(disp)
    res = router.route("bonjour")
    assert res.handled is True
    assert res.reply == "Bonjour."


def test_intents_demo_couvre_les_intentions():
    # La demo doit exercer focus, rotation, reglages, UNKNOWN et veille.
    lk = link_capture()
    disp = main.KittDisplay(lk)
    tele = obd.TelemetryController(disp, focus="speed")
    vc = voice.VoiceController(disp)
    results = intents.run_intent_demo(disp, telemetry=tele, voice=vc)
    noms = {r.intent.name for r in results}
    for expected in (intents.INTENT_FOCUS, intents.INTENT_NEXT_FOCUS,
                     intents.INTENT_NIGHT, intents.INTENT_BRIGHTER,
                     intents.INTENT_SLEEP, intents.INTENT_UNKNOWN,
                     intents.INTENT_GREET):
        assert expected in noms, f"intention {expected} absente de la demo"
    # au moins une commande a bien pousse une trame B: (reglage luminosite)
    assert any(t.startswith("B:") for t in lk.sent)


# ============================================================
#  Orchestrateur / service (service.py)                — iteration #14
# ============================================================

def _service(llm=None, tts=None, focus="speed", echo_command=False):
    """Construit un KittService dry-run avec lien capture (logs silencieux)."""
    lk = link_capture()
    disp = main.KittDisplay(lk)
    svc = service.build_service(disp, focus=focus, llm=llm, tts=tts,
                                echo_command=echo_command,
                                logger=service.ServiceLog(echo=False))
    return svc, lk


class _BoomTelemetry:
    """Telemetrie factice qui leve a chaque update (test du garde-fou)."""
    def update(self, *a):
        raise RuntimeError("boom")
    def feed_pid(self, *a):
        raise RuntimeError("boom")


def test_service_boot_emet_boot_idle():
    svc, lk = _service()
    svc.boot()
    assert lk.sent[:2] == ["S:BOOT\n", "S:IDLE\n"]
    assert svc.log.count("boot") == 1


def test_service_converse_commande_focus():
    svc, lk = _service()
    svc.voice.wake()                            # entre en ecoute
    reply = svc.converse("montre la temperature moteur")
    assert svc.telemetry.focus == "coolant"     # focus bien change
    assert reply == "Temperature moteur."
    assert "S:SPEAK\n" in lk.sent               # kiTT a "parle"
    assert lk.sent[-1] == "S:IDLE\n"            # ... puis retour veille


def test_service_feed_stt_wake_et_commande_meme_phrase():
    # "kiTT mode nuit" = reveil ET reglage (la commande n'est pas perdue).
    svc, lk = _service()
    r = svc.feed_stt("kiTT mode nuit")
    assert svc.log.count("wake") == 1
    assert svc.router.brightness == main.BRIGHT_NIGHT
    assert r == "Mode nuit."


def test_service_feed_stt_reveil_pur_sans_commande():
    # "ok kiTT" = simple reveil : on entre en ecoute, aucune commande traitee.
    svc, lk = _service()
    r = svc.feed_stt("ok kiTT")
    assert r is None
    assert svc.voice.state == voice.V_LISTEN
    assert svc.turns == 0


def test_service_feed_stt_ignore_hors_ecoute():
    # Phrase sans wake-word alors que kiTT dort : ignoree, rien ne casse.
    svc, lk = _service()
    r = svc.feed_stt("il fait beau aujourd hui")
    assert r is None
    assert svc.log.count("ignore") == 1
    assert svc.turns == 0


def test_service_unknown_va_au_llm():
    calls = []
    def llm(t):
        calls.append(t)
        return "il est midi"
    svc, lk = _service(llm=llm)
    svc.voice.wake()
    r = svc.converse("raconte une blague")
    assert calls == ["raconte une blague"]
    assert r == "il est midi"
    assert "S:SPEAK\n" in lk.sent


def test_service_unknown_sans_llm_reponse_defaut():
    svc, lk = _service()
    svc.voice.wake()
    r = svc.converse("raconte une blague")
    assert r == service.DEFAULT_LLM_REPLY


def test_service_llm_exception_geree():
    def llm(t):
        raise RuntimeError("reseau coupe")
    svc, lk = _service(llm=llm)
    svc.voice.wake()
    r = svc.converse("raconte une blague")
    assert r == service.LLM_ERROR_REPLY
    assert svc.log.count("error") >= 1


def test_service_veille_termine_en_idle():
    svc, lk = _service()
    svc.voice.wake()
    r = svc.converse("mets-toi en veille")
    assert r == ""                              # kiTT se tait
    assert svc.voice.state == voice.V_IDLE


def test_service_on_metric_pousse_cadran():
    svc, lk = _service(focus="speed")
    sev = svc.on_metric("speed", 90)
    assert sev == "ok"
    n, g = obd.dash_pair("speed", 90)
    assert lk.sent[-1] == f"D:{n},{g}\n"


def test_service_on_obd_frame_decode():
    svc, lk = _service(focus="speed")
    pid, data = obd.parse_obd_response("41 0D 5A")
    sev = svc.on_obd_frame(pid, data)
    assert sev == "ok"
    assert any(t.startswith("D:") for t in lk.sent)


def test_service_brightness_synchronise_le_routeur():
    svc, lk = _service()
    b = svc.apply_brightness_for_hour(3.0)
    assert b == main.BRIGHT_NIGHT
    assert svc.router.brightness == main.BRIGHT_NIGHT
    assert lk.sent[-1] == f"B:{main.BRIGHT_NIGHT}\n"


def test_service_next_focus():
    svc, lk = _service(focus="speed")
    assert svc.next_focus() == "rpm"
    assert svc.log.count("focus") == 1


def test_service_garde_fou_rattrape_exception():
    # Une brique qui plante : le service reste vivant, signale l'erreur (ecran).
    svc, lk = _service()
    svc.telemetry = _BoomTelemetry()
    svc.dispatch(("metric", ("speed", 90)))
    assert svc.errors == 1
    assert "S:ERROR\n" in lk.sent               # erreur rendue visible au volant
    assert svc.log.count("error") >= 1


def test_service_watchdog_apres_erreurs_repetees():
    svc, lk = _service()
    svc.telemetry = _BoomTelemetry()
    for _ in range(svc.watchdog_after):
        svc.dispatch(("metric", ("speed", 1)))
    assert svc.log.count("watchdog") == 1
    assert svc._consec_errors == 0              # compteur rearme apres reset


def test_service_serve_boucle_et_stop():
    svc, lk = _service()
    events = [("metric", ("speed", 90)),
              ("stt", "kiTT mode nuit"),
              ("stop", None),
              ("metric", ("speed", 100))]       # apres stop : non traite
    n = svc.serve(events)
    assert n == 3                               # boucle coupee par ("stop",)
    assert svc.log.count("boot") == 1
    assert svc.log.count("shutdown") == 1


def test_service_event_mal_forme_ne_casse_rien():
    svc, lk = _service()
    svc.dispatch("pas-un-tuple")
    svc.dispatch(("inconnu", 42))
    assert svc.log.count("error") >= 1
    assert svc.log.count("unknown_event") == 1


def test_service_demo_session_mixte():
    svc = service.run_service_demo(logger=service.ServiceLog(echo=False))
    assert svc.turns >= 3
    assert svc.errors == 0
    envoyes = "".join(svc.disp.link.sent)
    assert "S:SPEAK\n" in envoyes               # au moins un tour de parole
    assert "D:" in envoyes                      # au moins un cadran telemetrie
    assert "B:" in envoyes                      # dimming applique


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for t in tests:
        t()
        print(f"  OK  {t.__name__}")
        ok += 1
    print(f"\n### {ok}/{len(tests)} tests passes ###")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
