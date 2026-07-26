/*
 * ============================================================
 *  Audi kiTT — Bibliotheque d'animations "vivantes"  (kiTT_anim.h)
 * ============================================================
 *  Rendu PUR de la matrice LED 13x8 (104 LEDs, niveaux de gris 8 bits).
 *  Aucune dependance Arduino : ce header est inclus a la fois par le
 *  sketch MCU (kiTT_display.ino) et par le simulateur host (anim_sim.cpp).
 *  => une seule source de verite pour la logique d'animation.
 *
 *  Idee : la matrice reflete l'ETAT de l'IA embarquee.
 *    BOOT   : reveil (respiration montante)
 *    IDLE   : scanner K2000 lent (signature KITT, "je veille")
 *    LISTEN : respiration douce plein ecran ("je t'ecoute")
 *    THINK  : comete circulaire ("je reflechis")
 *    SPEAK  : ondes verticales facon voix ("je parle")
 *    WORD   : defilement du mot "kiTT"
 *    ERROR  : cadre clignotant ("alerte")
 *
 *  Fonction unique de rendu :
 *    kittRender(state, tick, level, frame)
 *      - state : KittState
 *      - tick  : compteur de pas d'animation (deja quantifie dans le temps
 *                par l'appelant ; independant du materiel => testable)
 *      - level : modulation externe 0..255 (amplitude audio, niveau micro...)
 *      - frame : buffer de sortie uint8_t[104], index = row*13 + col
 *
 *  Nouveaute #4 : TRANSITIONS DOUCES (fondu enchaine entre etats).
 *    Le rendu par etat reste pur ; le fondu est un simple melange lineaire
 *    (avec courbe d'accel/decel "smoothstep") entre l'image figee de l'etat
 *    sortant et l'etat entrant. Helpers purs et testables (kittBlend / kittEase
 *    / kittTransSteps) partages sketch <-> simulateur.
 *
 *  Nouveaute #5 : LUMINOSITE GLOBALE / DIMMING JOUR-NUIT.
 *    Un afficheur embarque doit etre lisible en plein jour mais discret la
 *    nuit (eviter l'eblouissement au volant). `kittApplyBrightness` applique
 *    un facteur global 0..255 sur l'image finale, en post-traitement, sans
 *    toucher a la logique d'animation. Helper pur => testable a l'identique
 *    (sketch <-> simulateur). Pilote depuis Linux via la commande serie `B:`.
 *
 *  Nouveaute #6 : TEXTE DEFILANT ARBITRAIRE.
 *    La matrice sait desormais afficher n'importe quel message qui defile
 *    (pas seulement le logo "kiTT"). Police 5x7 pure (A-Z, 0-9, ponctuation)
 *    + renderer `kittRenderText` et helpers de mesure `kittTextCols` /
 *    `kittTextScrollSteps`. Le tout PUR => teste par le simulateur host.
 *    Pilote depuis Linux via la commande serie `T:<message>`. Utile pour
 *    afficher des notifications, des reponses courtes, plus tard la vitesse
 *    (télémétrie OBD) ou un mot dicte a la voix. Le logo "kiTT" (etat WORD)
 *    reste inchange : c'est une signature stylisee dediee.
 *
 *  Nouveaute #7 : JAUGE A BARRE PERSISTANTE (telemetrie glancable).
 *    Contrairement au texte (qui defile une fois puis revient a IDLE), la
 *    jauge est un affichage PERMANENT d'une valeur 0..255 : une barre
 *    horizontale qui se remplit proportionnellement, avec une piste de fond
 *    toujours visible et une tete qui "respire" (la jauge vit). Pensee pour
 *    etre lue d'un coup d'oeil au volant (vitesse, RPM, carburant... via OBD)
 *    plutot que d'avoir a dechiffrer un texte defilant. Tout est PUR
 *    (`kittRenderGauge` / `kittGaugeCols`) => valide par le simulateur host.
 *    Pilotee depuis Linux via la commande serie `G:<0-255>`. Profite du meme
 *    fondu enchaine (a l'entree) et de la meme luminosite globale que les etats.
 *
 *  Nouveaute #8 : NOMBRE PERSISTANT (valeur exacte glancable).
 *    Complement de la jauge : la jauge donne une PROPORTION d'un coup d'oeil,
 *    le nombre donne la VALEUR EXACTE (vitesse, temperature, RPM/100...). Gros
 *    chiffres centres, lisibles au volant, affiches EN PERMANENCE jusqu'a une
 *    autre commande. Police numerique compacte 3x5 (jusqu'a 3 chiffres, 0..999)
 *    pour tenir large sur la matrice 13 colonnes. Tout est PUR
 *    (`kittRenderNumber` / `kittNumberDigits` / `kittNumberCols`) => valide par
 *    le simulateur host. Pilote depuis Linux via la commande serie `N:<0-999>`.
 *    Profite du meme fondu enchaine (a l'entree) et de la meme luminosite globale.
 *
 *  Nouveaute #9 : DASHBOARD COMBINE (nombre + jauge sur un meme ecran).
 *    Fusion des briques #7 et #8 en un seul affichage de telemetrie : la
 *    VALEUR EXACTE en gros chiffres sur le haut de la matrice (lignes 0..4) ET
 *    la PROPORTION en barre compacte sur le bas (lignes 6..7). D'un seul coup
 *    d'oeil au volant, on lit a la fois "90" et "ou en est-on par rapport au
 *    max". Rendu PUR `kittRenderDash` compose de deux helpers de dessin sans
 *    effacement (`kittDrawNumber` / `kittDrawGaugeBar`) extraits de #7/#8 pour
 *    eviter toute duplication (une seule source de verite par primitive).
 *    Comme la jauge et le nombre : fondu a l'entree, mise a jour instantanee si
 *    repete (source OBD reactive), et reste affiche jusqu'a une autre commande.
 *    Pilote depuis Linux via la commande serie `D:<0-999>,<0-255>`.
 *
 *  Auteur : Claude (Cowork) — iteration #2, transitions en #4, dimming en #5,
 *                             texte defilant en #6, jauge persistante en #7,
 *                             nombre persistant en #8, dashboard combine en #9
 * ============================================================
 */
#pragma once

#include <stdint.h>
#include <math.h>

// ---------- Geometrie ----------
#define KITT_ROWS 8
#define KITT_COLS 13
#define KITT_NUM  (KITT_ROWS * KITT_COLS)   // 104

// ---------- Etats de l'IA ----------
enum KittState {
  KITT_BOOT = 0,
  KITT_IDLE,
  KITT_LISTEN,
  KITT_THINK,
  KITT_SPEAK,
  KITT_WORD,
  KITT_ERROR,
  KITT_STATE_COUNT
};

// Intervalle (ms) entre deux pas d'animation, par etat.
// Centralise ici pour que le sketch et le simulateur soient coherents.
static inline uint16_t kittStepInterval(uint8_t state) {
  switch (state) {
    case KITT_BOOT:   return 28;
    case KITT_IDLE:   return 55;   // scanner lent = "au repos"
    case KITT_LISTEN: return 40;
    case KITT_THINK:  return 45;
    case KITT_SPEAK:  return 40;
    case KITT_WORD:   return 90;
    case KITT_ERROR:  return 130;
    default:          return 60;
  }
}

// ---------- Helpers buffer ----------
static inline void kittClear(uint8_t* f) {
  for (uint16_t i = 0; i < KITT_NUM; i++) f[i] = 0;
}
static inline void kittSet(uint8_t* f, int r, int c, uint8_t v) {
  if (r < 0 || r >= KITT_ROWS || c < 0 || c >= KITT_COLS) return;
  uint16_t idx = (uint16_t)r * KITT_COLS + c;
  if (v > f[idx]) f[idx] = v;   // additif "max" : les trainees se superposent proprement
}

// ---------- Ressources partagees ----------
// Trainee du scanner / comete : tete -> queue.
static const uint8_t KITT_TRAIL[]   = {255, 165, 95, 45, 18};
static const uint8_t KITT_TRAIL_LEN = (uint8_t)(sizeof(KITT_TRAIL));

// Police 7 lignes pour "kiTT". Chaque colonne = 1 octet ; bit r = ligne r
// (bit0 = ligne du haut). On respecte la casse "kiTT".
// (nomme ...FONT pour ne pas entrer en collision avec l'etat KITT_WORD)
static const uint8_t KITT_WORD_FONT[] = {
  0b1111111, 0b0010000, 0b0101000, 0b1000100,             // k : hampe + bras
  0b0000000,                                              // espace
  0b1111010,                                              // i : point + hampe
  0b0000000,                                              // espace
  0b0000001, 0b0000001, 0b1111111, 0b0000001, 0b0000001,  // T
  0b0000000,                                              // espace
  0b0000001, 0b0000001, 0b1111111, 0b0000001, 0b0000001   // T
};
static const int KITT_WORD_LEN = (int)(sizeof(KITT_WORD_FONT));

// ============================================================
//  Renderers par etat (statiques inline, sans effet de bord)
// ============================================================

// BOOT : respiration montante plein ecran (reveil).
static inline void kittRenderBoot(uint32_t tick, uint8_t* f) {
  // rampe douce 0 -> ~150 puis plateau ; l'appelant bascule ensuite en IDLE.
  int b = (int)(tick * 6);
  if (b > 150) b = 150;
  for (uint16_t i = 0; i < KITT_NUM; i++) f[i] = (uint8_t)b;
}

// IDLE : scanner K2000 lent sur les 2 lignes centrales.
static inline void kittRenderIdle(uint32_t tick, uint8_t* f) {
  kittClear(f);
  const int P = 2 * (KITT_COLS - 1);          // periode = 24 pas
  int ph = (int)(tick % (uint32_t)P);
  int head = (ph < KITT_COLS) ? ph : (P - ph); // triangle 0..12..1
  int dir  = (ph < KITT_COLS) ? +1 : -1;
  for (uint8_t t = 0; t < KITT_TRAIL_LEN; t++) {
    int c = head - dir * t;                    // trainee derriere la tete
    kittSet(f, 3, c, KITT_TRAIL[t]);
    kittSet(f, 4, c, KITT_TRAIL[t]);
  }
}

// LISTEN : respiration douce plein ecran, amplitude modulee par level.
static inline void kittRenderListen(uint32_t tick, uint8_t level, uint8_t* f) {
  float s = 0.5f * (1.0f + sinf((float)tick * 0.22f));   // 0..1
  float amp = 70.0f + (level / 255.0f) * 150.0f;         // plus fort si niveau haut
  int b = (int)(25.0f + s * amp);
  if (b > 255) b = 255;
  for (uint16_t i = 0; i < KITT_NUM; i++) f[i] = (uint8_t)b;
}

// THINK : comete qui tourne en ellipse autour du centre.
static inline void kittRenderThink(uint32_t tick, uint8_t* f) {
  kittClear(f);
  const float cx = 6.0f, cy = 3.5f;   // centre de la matrice
  const float rx = 5.0f, ry = 3.0f;   // ellipse (ecran plus large que haut)
  float a0 = (float)tick * 0.32f;
  for (uint8_t t = 0; t < KITT_TRAIL_LEN; t++) {
    float a = a0 - (float)t * 0.55f;
    int x = (int)(cx + rx * cosf(a) + 0.5f);
    int y = (int)(cy + ry * sinf(a) + 0.5f);
    kittSet(f, y, x, KITT_TRAIL[t]);
  }
}

// SPEAK : ondes verticales symetriques facon "voix", hauteur modulee par level.
static inline void kittRenderSpeak(uint32_t tick, uint8_t level, uint8_t* f) {
  kittClear(f);
  float amp = 0.35f + (level / 255.0f) * 0.65f;     // 0.35..1.0 de la demi-hauteur
  for (int c = 0; c < KITT_COLS; c++) {
    // pseudo forme d'onde : somme de deux sinus decorreles
    float w = 0.5f * sinf((float)c * 0.9f + (float)tick * 0.5f)
            + 0.5f * sinf((float)c * 0.5f - (float)tick * 0.3f);
    float hf = fabsf(w) * amp * (KITT_ROWS / 2.0f);  // 0..4
    int h = (int)(hf + 0.5f);
    // barre symetrique autour des lignes centrales 3 & 4, degrade centre->bord
    for (int k = 0; k <= h; k++) {
      uint8_t v = (uint8_t)(230 - k * 40);
      kittSet(f, 3 - k, c, v);
      kittSet(f, 4 + k, c, v);
    }
  }
}

// WORD : defilement horizontal du mot "kiTT".
static inline void kittRenderWord(uint32_t tick, uint8_t* f) {
  kittClear(f);
  const int span = KITT_COLS + KITT_WORD_LEN + 1;   // cycle complet
  int pos = KITT_COLS - (int)(tick % (uint32_t)span);
  const uint8_t bright = 210;
  for (int c = 0; c < KITT_COLS; c++) {
    int wi = c - pos;
    if (wi < 0 || wi >= KITT_WORD_LEN) continue;
    uint8_t colByte = KITT_WORD_FONT[wi];
    for (int r = 0; r < 7; r++) {
      if ((colByte >> r) & 0x01) kittSet(f, r, c, bright);
    }
  }
}

// ERROR : cadre clignotant (alerte visible).
static inline void kittRenderError(uint32_t tick, uint8_t* f) {
  kittClear(f);
  bool on = ((tick / 3) % 2) == 0;
  if (!on) return;
  const uint8_t v = 185;
  for (int c = 0; c < KITT_COLS; c++) { kittSet(f, 0, c, v); kittSet(f, KITT_ROWS - 1, c, v); }
  for (int r = 0; r < KITT_ROWS; r++) { kittSet(f, r, 0, v); kittSet(f, r, KITT_COLS - 1, v); }
}

// ------------------------------------------------------------
//  Dispatcher unique
// ------------------------------------------------------------
static inline void kittRender(uint8_t state, uint32_t tick, uint8_t level, uint8_t* f) {
  switch (state) {
    case KITT_BOOT:   kittRenderBoot(tick, f);          break;
    case KITT_IDLE:   kittRenderIdle(tick, f);          break;
    case KITT_LISTEN: kittRenderListen(tick, level, f); break;
    case KITT_THINK:  kittRenderThink(tick, f);         break;
    case KITT_SPEAK:  kittRenderSpeak(tick, level, f);  break;
    case KITT_WORD:   kittRenderWord(tick, f);          break;
    case KITT_ERROR:  kittRenderError(tick, f);         break;
    default:          kittClear(f);                     break;
  }
}

// Duree (en pas) d'un cycle complet pour les etats "one-shot"
// (utilise par le sketch pour revenir a IDLE apres un WORD, p.ex.).
static inline uint32_t kittCycleSteps(uint8_t state) {
  switch (state) {
    case KITT_WORD: return (uint32_t)(KITT_COLS + KITT_WORD_LEN + 1);
    case KITT_BOOT: return 28;   // ~ le temps de la rampe
    default:        return 0;    // 0 = boucle indefiniment
  }
}

// ============================================================
//  TRANSITIONS DOUCES (fondu enchaine entre etats)   — iteration #4
// ============================================================
//  Principe : quand l'IA change d'etat, on ne coupe pas net. L'image
//  affichee au moment du changement est "figee" comme point de depart,
//  et l'etat entrant apparait en fondu par-dessus sur quelques pas.
//  Ces helpers sont PURS (aucune dependance Arduino) donc rejouables et
//  testables a l'identique dans le simulateur host.

// Duree cible d'un fondu, en millisecondes (ressenti "vif mais doux").
#define KITT_TRANS_MS 260

// Courbe d'accel/decel "smoothstep" : adoucit le debut et la fin du fondu.
// Entree/sortie dans [0..1]. Rend le melange plus organique qu'un lineaire.
static inline float kittEase(float a) {
  if (a <= 0.0f) return 0.0f;
  if (a >= 1.0f) return 1.0f;
  return a * a * (3.0f - 2.0f * a);
}

// Nombre de pas d'un fondu pour une cadence donnee (intervalle en ms),
// derive pour viser ~KITT_TRANS_MS quelle que soit la vitesse. Factorise
// ici pour etre reutilise par les etats ET par la jauge (#7) / le nombre (#8)
// / le dashboard (#9), qui ne sont pas des etats de l'enum mais ont leur
// propre cadence.
static inline uint16_t kittTransStepsForInterval(uint16_t iv) {
  if (iv == 0) return 1;
  uint16_t n = (uint16_t)((KITT_TRANS_MS + iv / 2) / iv);   // arrondi
  return (n < 1) ? 1 : n;
}

// Nombre de pas d'animation d'un fondu vers 'state', derive de la cadence
// propre de cet etat. 0 => pas de fondu (BOOT fait deja sa propre montee).
static inline uint16_t kittTransSteps(uint8_t state) {
  if (state == KITT_BOOT) return 0;
  return kittTransStepsForInterval(kittStepInterval(state));
}

// Melange lineaire per-pixel de deux images 'from' -> 'to', avec la
// progression 'a' (0..1, non lissee : le lissage smoothstep est applique
// ici). Ecrit le resultat 13x8 dans 'out'. 'out' peut differer de from/to.
static inline void kittBlend(const uint8_t* from, const uint8_t* to,
                             float a, uint8_t* out) {
  float e = kittEase(a);
  for (uint16_t i = 0; i < KITT_NUM; i++) {
    float v = (1.0f - e) * (float)from[i] + e * (float)to[i];
    if (v < 0.0f) v = 0.0f;
    if (v > 255.0f) v = 255.0f;
    out[i] = (uint8_t)(v + 0.5f);
  }
}

// ============================================================
//  LUMINOSITE GLOBALE / DIMMING JOUR-NUIT             — iteration #5
// ============================================================
//  Post-traitement PUR applique a l'image finale, juste avant l'envoi a
//  la matrice. On garde les tampons internes (frame/prevFrame) en pleine
//  luminosite : seul l'echantillon affiche est attenue. Ainsi la logique
//  d'animation et de fondu reste inchangee, et le reglage est reversible
//  a tout instant (ex. un capteur de luminosite ou l'heure fait varier B).

// Palier minimal recommande pour rester lisible sans eblouir (mode nuit).
// Purement indicatif : l'appelant reste libre d'envoyer 0..255.
#define KITT_BRIGHT_NIGHT 40
#define KITT_BRIGHT_DAY   255

// Applique un facteur de luminosite globale 'brightness' (0..255) au buffer,
// en place. 255 = pleine luminosite (aucune modification), 0 = ecran eteint.
// Mise a l'echelle proportionnelle et arrondie ; jamais au-dessus de l'origine.
static inline void kittApplyBrightness(uint8_t* f, uint8_t brightness) {
  if (brightness >= 255) return;            // plein jour : rien a faire (rapide)
  for (uint16_t i = 0; i < KITT_NUM; i++) {
    // +127 => arrondi au plus proche ; borne haute garantie car brightness<=254.
    f[i] = (uint8_t)(((uint16_t)f[i] * brightness + 127) / 255);
  }
}

// ============================================================
//  TEXTE DEFILANT ARBITRAIRE                          — iteration #6
// ============================================================
//  Objectif : afficher n'importe quel message court qui defile de droite
//  a gauche (notification, reponse, plus tard la vitesse OBD ou un mot
//  dicte a la voix). Tout est PUR (aucune dependance Arduino) => valide
//  a l'identique par le simulateur host.
//
//  Police 5 colonnes x 7 lignes. Chaque glyphe = 7 octets (une ligne par
//  octet, du haut r=0 vers le bas r=6). Dans chaque octet, on n'utilise
//  que les 5 bits de poids faible : bit4 = colonne de GAUCHE, bit0 = droite.
//  Authoring "en lignes" (0b01110...) => beaucoup plus lisible et moins
//  d'erreurs qu'un stockage par colonnes. La 8e ligne de la matrice reste
//  libre (marge basse), le texte occupe les lignes 0..6.

#define KITT_GLYPH_W    5    // largeur d'un glyphe (colonnes)
#define KITT_GLYPH_H    7    // hauteur d'un glyphe (lignes)
#define KITT_GLYPH_GAP  1    // colonnes vides entre deux glyphes

typedef struct { char ch; uint8_t rows[KITT_GLYPH_H]; } KittGlyph;

// Jeu de caracteres : espace, A-Z, 0-9 et une poignee de ponctuations
// utiles. Les minuscules sont repliees sur les majuscules a la volee
// (voir kittGlyphRows). Tout caractere inconnu => espace (rien d'affiche).
static const KittGlyph KITT_FONT[] = {
  {' ', {0,0,0,0,0,0,0}},
  {'A', {0b01110,0b10001,0b10001,0b11111,0b10001,0b10001,0b10001}},
  {'B', {0b11110,0b10001,0b10001,0b11110,0b10001,0b10001,0b11110}},
  {'C', {0b01110,0b10001,0b10000,0b10000,0b10000,0b10001,0b01110}},
  {'D', {0b11110,0b10001,0b10001,0b10001,0b10001,0b10001,0b11110}},
  {'E', {0b11111,0b10000,0b10000,0b11110,0b10000,0b10000,0b11111}},
  {'F', {0b11111,0b10000,0b10000,0b11110,0b10000,0b10000,0b10000}},
  {'G', {0b01110,0b10001,0b10000,0b10111,0b10001,0b10001,0b01111}},
  {'H', {0b10001,0b10001,0b10001,0b11111,0b10001,0b10001,0b10001}},
  {'I', {0b11111,0b00100,0b00100,0b00100,0b00100,0b00100,0b11111}},
  {'J', {0b00111,0b00010,0b00010,0b00010,0b00010,0b10010,0b01100}},
  {'K', {0b10001,0b10010,0b10100,0b11000,0b10100,0b10010,0b10001}},
  {'L', {0b10000,0b10000,0b10000,0b10000,0b10000,0b10000,0b11111}},
  {'M', {0b10001,0b11011,0b10101,0b10101,0b10001,0b10001,0b10001}},
  {'N', {0b10001,0b10001,0b11001,0b10101,0b10011,0b10001,0b10001}},
  {'O', {0b01110,0b10001,0b10001,0b10001,0b10001,0b10001,0b01110}},
  {'P', {0b11110,0b10001,0b10001,0b11110,0b10000,0b10000,0b10000}},
  {'Q', {0b01110,0b10001,0b10001,0b10001,0b10101,0b10010,0b01101}},
  {'R', {0b11110,0b10001,0b10001,0b11110,0b10100,0b10010,0b10001}},
  {'S', {0b01111,0b10000,0b10000,0b01110,0b00001,0b00001,0b11110}},
  {'T', {0b11111,0b00100,0b00100,0b00100,0b00100,0b00100,0b00100}},
  {'U', {0b10001,0b10001,0b10001,0b10001,0b10001,0b10001,0b01110}},
  {'V', {0b10001,0b10001,0b10001,0b10001,0b10001,0b01010,0b00100}},
  {'W', {0b10001,0b10001,0b10001,0b10101,0b10101,0b10101,0b01010}},
  {'X', {0b10001,0b10001,0b01010,0b00100,0b01010,0b10001,0b10001}},
  {'Y', {0b10001,0b10001,0b01010,0b00100,0b00100,0b00100,0b00100}},
  {'Z', {0b11111,0b00001,0b00010,0b00100,0b01000,0b10000,0b11111}},
  {'0', {0b01110,0b10001,0b10011,0b10101,0b11001,0b10001,0b01110}},
  {'1', {0b00100,0b01100,0b00100,0b00100,0b00100,0b00100,0b01110}},
  {'2', {0b01110,0b10001,0b00001,0b00110,0b01000,0b10000,0b11111}},
  {'3', {0b11111,0b00010,0b00100,0b00010,0b00001,0b10001,0b01110}},
  {'4', {0b00010,0b00110,0b01010,0b10010,0b11111,0b00010,0b00010}},
  {'5', {0b11111,0b10000,0b11110,0b00001,0b00001,0b10001,0b01110}},
  {'6', {0b00110,0b01000,0b10000,0b11110,0b10001,0b10001,0b01110}},
  {'7', {0b11111,0b00001,0b00010,0b00100,0b01000,0b01000,0b01000}},
  {'8', {0b01110,0b10001,0b10001,0b01110,0b10001,0b10001,0b01110}},
  {'9', {0b01110,0b10001,0b10001,0b01111,0b00001,0b00010,0b01100}},
  {':', {0b00000,0b00100,0b00100,0b00000,0b00100,0b00100,0b00000}},
  {'-', {0b00000,0b00000,0b00000,0b01110,0b00000,0b00000,0b00000}},
  {'.', {0b00000,0b00000,0b00000,0b00000,0b00000,0b00110,0b00110}},
  {'!', {0b00100,0b00100,0b00100,0b00100,0b00100,0b00000,0b00100}},
  {'?', {0b01110,0b10001,0b00001,0b00110,0b00100,0b00000,0b00100}},
  {'/', {0b00001,0b00001,0b00010,0b00100,0b01000,0b10000,0b10000}},
  {'\'',{0b00100,0b00100,0b01000,0b00000,0b00000,0b00000,0b00000}}
};
static const int KITT_FONT_LEN = (int)(sizeof(KITT_FONT) / sizeof(KITT_FONT[0]));

// Renvoie les 7 lignes du glyphe de 'c' (minuscules repliees sur majuscules).
// Caractere inconnu => glyphe espace (KITT_FONT[0]). Ne renvoie jamais NULL.
static inline const uint8_t* kittGlyphRows(char c) {
  if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');   // fold minuscule -> majuscule
  for (int i = 0; i < KITT_FONT_LEN; i++) {
    if (KITT_FONT[i].ch == c) return KITT_FONT[i].rows;
  }
  return KITT_FONT[0].rows;   // inconnu : espace
}

// Largeur totale (en colonnes) d'un message rendu, gaps inter-glyphes inclus.
// Chaine vide => 0.
static inline int kittTextCols(const char* s) {
  int n = 0;
  for (const char* p = s; *p; ++p) n++;
  if (n <= 0) return 0;
  return n * KITT_GLYPH_W + (n - 1) * KITT_GLYPH_GAP;
}

// Nombre de pas pour un defilement complet : le texte entre par la droite
// (colonne KITT_COLS) et sort entierement par la gauche. Sert au sketch pour
// savoir quand revenir a IDLE apres un message.
static inline uint32_t kittTextScrollSteps(const char* s) {
  return (uint32_t)(KITT_COLS + kittTextCols(s) + 1);
}

// Rendu PUR d'un message : le bord gauche du texte est place a la colonne
// 'offsetX' (peut etre negatif ou > KITT_COLS pour un defilement). Les pixels
// hors ecran sont naturellement ignores (garde-fous dans kittSet). Le texte
// occupe les lignes 0..6 (marge basse libre). 'bright' = luminosite du texte.
static inline void kittRenderText(const char* s, int offsetX,
                                  uint8_t bright, uint8_t* f) {
  kittClear(f);
  int x = offsetX;
  for (const char* p = s; *p; ++p) {
    const uint8_t* rows = kittGlyphRows(*p);
    for (int col = 0; col < KITT_GLYPH_W; col++) {
      int cx = x + col;
      if (cx < 0 || cx >= KITT_COLS) continue;         // colonne hors ecran
      for (int r = 0; r < KITT_GLYPH_H; r++) {
        if ((rows[r] >> (KITT_GLYPH_W - 1 - col)) & 0x01)  // bit4 = colonne gauche
          kittSet(f, r, cx, bright);
      }
    }
    x += KITT_GLYPH_W + KITT_GLYPH_GAP;                 // glyphe suivant
  }
}

// Position 'offsetX' du bord gauche du texte au pas 'tick' d'un defilement
// droite->gauche (part de la colonne KITT_COLS, avance de 1 col / pas).
static inline int kittTextOffsetAt(uint32_t tick) {
  return KITT_COLS - (int)tick;
}

// ============================================================
//  JAUGE A BARRE PERSISTANTE (telemetrie glancable)   — iteration #7
// ============================================================
//  Objectif : afficher EN PERMANENCE une valeur 0..255 (vitesse, RPM,
//  carburant... typiquement via OBD-II plus tard) sous forme d'une barre
//  horizontale, lisible d'un coup d'oeil au volant. Contrairement au texte
//  (`T:`, une passe puis IDLE), la jauge reste affichee jusqu'a une autre
//  commande. Rendu PUR => valide a l'identique par le simulateur host.
//
//  Design :
//   - la barre occupe les lignes centrales KITT_GAUGE_ROW0..KITT_GAUGE_ROW1
//     (marges haute et basse libres pour un rendu propre) ;
//   - une PISTE de fond faible (KITT_GAUGE_TRACK) est toujours visible sur
//     toute la largeur => on percoit l'etendue de la jauge meme a vide ;
//   - le CORPS rempli est a KITT_GAUGE_FILL ;
//   - la TETE (derniere colonne remplie) "respire" entre EDGE_MIN et
//     EDGE_MAX au fil de 'tick' => la jauge est vivante, pas figee.

#define KITT_GAUGE_STEP_MS  45   // cadence du pouls de la jauge (ms)
#define KITT_GAUGE_ROW0      2   // premiere ligne de la barre (incluse)
#define KITT_GAUGE_ROW1      5   // derniere ligne de la barre (incluse) => 4 lignes
#define KITT_GAUGE_TRACK    14   // luminosite de la piste de fond (toujours visible)
#define KITT_GAUGE_FILL    170   // luminosite du corps rempli
#define KITT_GAUGE_EDGE_MIN 150  // luminosite mini de la tete (respiration)
#define KITT_GAUGE_EDGE_MAX 255  // luminosite maxi de la tete (respiration)

// Nombre de colonnes remplies (0..KITT_COLS) pour une valeur 0..255,
// arrondi au plus proche. 0 -> 0 colonne, 255 -> KITT_COLS. Pur => testable.
static inline int kittGaugeCols(uint8_t value) {
  return (int)(((uint16_t)value * KITT_COLS + 127) / 255);
}

// Dessine (SANS effacer 'f') une barre de jauge pour 'value' (0..255) sur les
// lignes row0..row1, avec la respiration de la tete au pas 'tick'. Factorise
// pour etre reutilise par le mode jauge (#7) ET le dashboard combine (#9) sans
// dupliquer la logique. Une valeur non nulle garantit au moins la tete visible.
static inline void kittDrawGaugeBar(uint8_t value, int row0, int row1,
                                    uint32_t tick, uint8_t* f) {
  int filled = kittGaugeCols(value);
  if (value > 0 && filled == 0) filled = 1;   // au moins la tete si non nul

  // Respiration de la tete : sinus borne dans [EDGE_MIN..EDGE_MAX].
  float s = 0.5f * (1.0f + sinf((float)tick * 0.25f));   // 0..1
  uint8_t edge = (uint8_t)(KITT_GAUGE_EDGE_MIN
                 + s * (float)(KITT_GAUGE_EDGE_MAX - KITT_GAUGE_EDGE_MIN) + 0.5f);

  for (int c = 0; c < KITT_COLS; c++) {
    uint8_t v;
    if (c < filled - 1)        v = KITT_GAUGE_FILL;   // corps rempli
    else if (c == filled - 1)  v = edge;              // tete qui respire
    else                       v = KITT_GAUGE_TRACK;  // piste de fond
    for (int r = row0; r <= row1; r++) kittSet(f, r, c, v);
  }
}

// Rendu PUR de la jauge pour 'value' (0..255) au pas 'tick' (pour la
// respiration de la tete). Ecrit dans 'f'. Une valeur non nulle garantit
// au moins la tete visible (retour visuel "faible mais vivant").
static inline void kittRenderGauge(uint8_t value, uint32_t tick, uint8_t* f) {
  kittClear(f);
  kittDrawGaugeBar(value, KITT_GAUGE_ROW0, KITT_GAUGE_ROW1, tick, f);
}

// ============================================================
//  NOMBRE PERSISTANT (valeur exacte glancable)        — iteration #8
// ============================================================
//  Objectif : afficher EN PERMANENCE une valeur numerique EXACTE (0..999)
//  en gros chiffres centres, lisible d'un coup d'oeil au volant. Complete la
//  jauge #7 : la jauge donne une proportion (barre), le nombre donne la valeur
//  precise (vitesse "90", temperature "72", RPM/100 "35"...). Comme la jauge,
//  il reste affiche jusqu'a une autre commande. Rendu PUR => valide par le
//  simulateur host. Pilote depuis Linux via la commande serie `N:<0-999>`.
//
//  Police numerique compacte 3 colonnes x 5 lignes : jusqu'a 3 chiffres
//  tiennent large sur les 13 colonnes (3 chiffres = 3*3 + 2*1 = 11 col). Les
//  chiffres sont centres horizontalement, et verticalement sur les lignes
//  KITT_NUM_ROW0..(+4) (marges haute/basse libres pour un rendu aere).

#define KITT_NUM_STEP_MS  60    // cadence (ms) du mode nombre (statique : sert au fondu)
#define KITT_NUM_MAX     999    // valeur max affichable (3 chiffres)
#define KITT_NUM_DIGIT_W   3    // largeur d'un chiffre (colonnes)
#define KITT_NUM_DIGIT_H   5    // hauteur d'un chiffre (lignes)
#define KITT_NUM_DIGIT_GAP 1    // colonnes vides entre deux chiffres
#define KITT_NUM_ROW0      1    // premiere ligne des chiffres (5 lignes => 1..5)
#define KITT_NUM_BRIGHT  210    // luminosite des chiffres (avant dimming global)

// Police 3x5 des chiffres 0..9. Chaque glyphe = 5 octets (ligne du haut r=0
// vers le bas r=4) ; on n'utilise que les 3 bits faibles : bit2 = colonne
// gauche, bit0 = colonne droite. Authoring "en lignes" pour rester lisible.
static const uint8_t KITT_DIGIT3x5[10][KITT_NUM_DIGIT_H] = {
  {0b111,0b101,0b101,0b101,0b111},   // 0
  {0b010,0b110,0b010,0b010,0b111},   // 1
  {0b111,0b001,0b111,0b100,0b111},   // 2
  {0b111,0b001,0b111,0b001,0b111},   // 3
  {0b101,0b101,0b111,0b001,0b001},   // 4
  {0b111,0b100,0b111,0b001,0b111},   // 5
  {0b111,0b100,0b111,0b101,0b111},   // 6
  {0b111,0b001,0b010,0b010,0b010},   // 7
  {0b111,0b101,0b111,0b101,0b111},   // 8
  {0b111,0b101,0b111,0b001,0b111}    // 9
};

// Nombre de chiffres a afficher pour 'value' (borne a KITT_NUM_MAX).
// 0 => 1 chiffre ("0"). Pur => testable.
static inline int kittNumberDigits(uint16_t value) {
  if (value > KITT_NUM_MAX) value = KITT_NUM_MAX;
  if (value >= 100) return 3;
  if (value >= 10)  return 2;
  return 1;
}

// Largeur totale (colonnes) du nombre rendu, gaps inter-chiffres inclus.
static inline int kittNumberCols(uint16_t value) {
  int d = kittNumberDigits(value);
  return d * KITT_NUM_DIGIT_W + (d - 1) * KITT_NUM_DIGIT_GAP;
}

// Dessine (SANS effacer 'f') le nombre 'value' (0..KITT_NUM_MAX) centre
// horizontalement, en gros chiffres 3x5, la ligne du haut des chiffres a
// 'row0'. 'bright' = luminosite des segments. Factorise pour etre reutilise
// par le mode nombre (#8) ET le dashboard combine (#9) sans dupliquer la
// logique de centrage/extraction des chiffres.
static inline void kittDrawNumber(uint16_t value, int row0,
                                  uint8_t bright, uint8_t* f) {
  if (value > KITT_NUM_MAX) value = KITT_NUM_MAX;

  int d = kittNumberDigits(value);
  int width = kittNumberCols(value);
  int x = (KITT_COLS - width) / 2;              // centrage horizontal

  // Extrait les 'd' chiffres du plus significatif au moins significatif.
  int digits[3];
  uint16_t v = value;
  for (int i = d - 1; i >= 0; i--) { digits[i] = v % 10; v /= 10; }

  for (int i = 0; i < d; i++) {
    const uint8_t* g = KITT_DIGIT3x5[digits[i]];
    for (int col = 0; col < KITT_NUM_DIGIT_W; col++) {
      int cx = x + col;
      for (int r = 0; r < KITT_NUM_DIGIT_H; r++) {
        if ((g[r] >> (KITT_NUM_DIGIT_W - 1 - col)) & 0x01)   // bit2 = col gauche
          kittSet(f, row0 + r, cx, bright);
      }
    }
    x += KITT_NUM_DIGIT_W + KITT_NUM_DIGIT_GAP;              // chiffre suivant
  }
}

// Rendu PUR du nombre 'value' (0..KITT_NUM_MAX) centre sur la matrice, en
// gros chiffres 3x5. 'bright' = luminosite des segments. Ecrit dans 'f'.
// 'tick' n'est pas utilise (affichage statique, lisibilite maximale) mais la
// signature le garde pour l'homogeneite avec les autres modes persistants.
static inline void kittRenderNumber(uint16_t value, uint32_t tick,
                                    uint8_t bright, uint8_t* f) {
  (void)tick;                                   // statique : pas d'animation
  kittClear(f);
  kittDrawNumber(value, KITT_NUM_ROW0, bright, f);
}

// ============================================================
//  DASHBOARD COMBINE (nombre + jauge sur un ecran)    — iteration #9
// ============================================================
//  Objectif : lire d'un seul coup d'oeil au volant a la fois la VALEUR
//  EXACTE et la PROPORTION d'une grandeur (vitesse, RPM, carburant...). On
//  compose les primitives #7 et #8 : les gros chiffres en HAUT (lignes 0..4)
//  et une barre compacte en BAS (lignes 6..7), separees par une ligne vide.
//  Rendu PUR (kittRenderDash) => valide a l'identique par le simulateur host.
//  Pilote depuis Linux via la commande serie `D:<0-999>,<0-255>`. Comme la
//  jauge et le nombre : reste affiche jusqu'a une autre commande, fondu a
//  l'entree, mise a jour instantanee si repete (source OBD reactive).

#define KITT_DASH_STEP_MS   60   // cadence (respiration de la barre + fondu)
#define KITT_DASH_NUM_ROW0   0   // chiffres 3x5 sur les lignes 0..4 (haut)
#define KITT_DASH_BAR_ROW0   6   // barre compacte sur les lignes 6..7 (bas)
#define KITT_DASH_BAR_ROW1   7   // (la ligne 5 reste vide : separation nette)
#define KITT_DASH_BRIGHT   210   // luminosite des chiffres (avant dimming global)

// Rendu PUR du dashboard : 'number' (0..KITT_NUM_MAX) en gros chiffres centres
// en haut + 'gauge' (0..255) en barre compacte en bas, 'tick' anime la
// respiration de la tete de barre. 'bright' = luminosite des chiffres. Ecrit
// dans 'f'. Zero duplication : reutilise kittDrawNumber + kittDrawGaugeBar.
static inline void kittRenderDash(uint16_t number, uint8_t gauge, uint32_t tick,
                                  uint8_t bright, uint8_t* f) {
  kittClear(f);
  kittDrawNumber(number, KITT_DASH_NUM_ROW0, bright, f);
  kittDrawGaugeBar(gauge, KITT_DASH_BAR_ROW0, KITT_DASH_BAR_ROW1, tick, f);
}

// Parse le nom textuel d'un etat (protocole serie). Renvoie -1 si inconnu.
static inline int kittStateFromName(const char* s) {
  struct { const char* n; int v; } map[] = {
    {"BOOT", KITT_BOOT}, {"IDLE", KITT_IDLE}, {"LISTEN", KITT_LISTEN},
    {"THINK", KITT_THINK}, {"SPEAK", KITT_SPEAK}, {"WORD", KITT_WORD},
    {"ERROR", KITT_ERROR}
  };
  for (unsigned i = 0; i < sizeof(map) / sizeof(map[0]); i++) {
    const char* a = s; const char* b = map[i].n;
    while (*a && *b && *a == *b) { a++; b++; }
    if (*b == '\0' && (*a == '\0' || *a == '\n' || *a == '\r' || *a == ' '))
      return map[i].v;
  }
  return -1;
}
