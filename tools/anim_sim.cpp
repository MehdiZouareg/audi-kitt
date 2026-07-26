/*
 * ============================================================
 *  Audi kiTT — Simulateur host des animations LED
 * ============================================================
 *  But : tester la bibliotheque d'animations (kiTT_anim.h) SANS carte.
 *  Inclut EXACTEMENT le meme header que le sketch MCU, donc valide la
 *  vraie logique de rendu. Chaque frame 13x8 est affichee en ASCII
 *  (niveaux de gris -> caracteres), ce qui permet de "voir" chaque etat.
 *
 *  Compilation : g++ -std=c++17 -O2 tools/anim_sim.cpp -o anim_sim -lm
 *  Usage       : ./anim_sim [STATE] [frames] [level]
 *                STATE parmi : BOOT IDLE LISTEN THINK SPEAK WORD ERROR
 *                              ALL    -> sanity + apercu de chaque etat
 *                              TRANS  -> rejoue les fondus enchaines (#4)
 *                              BRIGHT -> apercu du dimming jour/nuit (#5)
 *                              TEXT ["message"] -> defilement de texte (#6)
 *                              GAUGE [value] -> jauge persistante (#7)
 *                              NUMBER [value] -> nombre persistant (#8)
 *                              DASH [num] [gauge] -> dashboard combine (#9)
 *
 *  Auteur : Claude (Cowork) — #2 ; TRANS + sanity blend en #4 ;
 *                              BRIGHT + sanity luminosite en #5 ;
 *                              TEXT + sanity police/texte en #6 ;
 *                              GAUGE + sanity jauge en #7 ;
 *                              NUMBER + sanity nombre en #8 ;
 *                              DASH + sanity dashboard en #9
 * ============================================================
 */
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <cstdlib>
#include "../sketch/kiTT_display/kiTT_anim.h"

static const char* RAMP = " .:-=+*#%@";   // 10 niveaux de gris croissants

static void printFrame(const uint8_t* f) {
  printf("    +-------------+\n");
  for (int r = 0; r < KITT_ROWS; r++) {
    printf("    |");
    for (int c = 0; c < KITT_COLS; c++) {
      uint8_t v = f[r * KITT_COLS + c];
      int idx = (v * 9) / 255;           // 0..9
      putchar(RAMP[idx]);
    }
    printf("|\n");
  }
  printf("    +-------------+\n");
}

static const char* stateName(uint8_t s) {
  const char* n[] = {"BOOT","IDLE","LISTEN","THINK","SPEAK","WORD","ERROR"};
  return (s < KITT_STATE_COUNT) ? n[s] : "?";
}

static void renderState(uint8_t st, int frames, uint8_t level) {
  uint8_t f[KITT_NUM];
  printf("\n========== ETAT %s (level=%d) ==========\n", stateName(st), level);
  for (int t = 1; t <= frames; t++) {
    kittRender(st, (uint32_t)t, level, f);
    printf("-- %s  tick=%d --\n", stateName(st), t);
    printFrame(f);
  }
}

// Sanity-check : aucune ecriture hors du buffer, valeurs valides.
static bool sanity(uint8_t st, uint8_t level) {
  uint8_t f[KITT_NUM];
  bool anyLit = false;
  for (int t = 1; t <= 60; t++) {
    kittRender(st, (uint32_t)t, level, f);
    for (int i = 0; i < KITT_NUM; i++) if (f[i] > 0) anyLit = true;
  }
  return anyLit;   // chaque etat doit allumer au moins un pixel sur 60 pas
}

// ------------------------------------------------------------
//  Sanity du fondu enchaine (helpers purs kittEase / kittBlend)  — #4
// ------------------------------------------------------------
//  Verifie les proprietes attendues d'un crossfade correct :
//   - a=0 => sortie == image de depart ; a=1 => sortie == image d'arrivee
//   - la courbe kittEase est bien bornee et monotone croissante
//   - a mi-parcours, chaque pixel reste borne entre from et to (aucun overshoot)
static bool sanityBlend() {
  uint8_t from[KITT_NUM], to[KITT_NUM], out[KITT_NUM];
  // Deux images contrastees : from = degrade, to = complement.
  for (int i = 0; i < KITT_NUM; i++) {
    from[i] = (uint8_t)((i * 255) / (KITT_NUM - 1));
    to[i]   = (uint8_t)(255 - from[i]);
  }
  bool ok = true;

  // Bornes du fondu.
  kittBlend(from, to, 0.0f, out);
  for (int i = 0; i < KITT_NUM; i++) if (out[i] != from[i]) ok = false;
  kittBlend(from, to, 1.0f, out);
  for (int i = 0; i < KITT_NUM; i++) if (out[i] != to[i]) ok = false;

  // Aucun overshoot : le melange reste toujours entre from et to.
  for (int s = 0; s <= 10; s++) {
    float a = s / 10.0f;
    kittBlend(from, to, a, out);
    for (int i = 0; i < KITT_NUM; i++) {
      uint8_t lo = from[i] < to[i] ? from[i] : to[i];
      uint8_t hi = from[i] < to[i] ? to[i] : from[i];
      if (out[i] < lo || out[i] > hi) ok = false;
    }
  }

  // kittEase : bornee [0,1] et monotone.
  float prev = -1.0f;
  for (int s = 0; s <= 20; s++) {
    float e = kittEase(s / 20.0f);
    if (e < 0.0f || e > 1.0f) ok = false;
    if (e < prev - 1e-6f) ok = false;   // non decroissante
    prev = e;
  }
  return ok;
}

// ------------------------------------------------------------
//  Sanity de la luminosite globale (kittApplyBrightness)         — #5
// ------------------------------------------------------------
//  Proprietes attendues d'un dimming correct :
//   - B=255 => image inchangee ; B=0 => ecran eteint (tout a 0)
//   - jamais au-dessus de l'original (attenuation seulement)
//   - monotone en B : augmenter B ne diminue jamais un pixel
static bool sanityBrightness() {
  uint8_t base[KITT_NUM];
  for (int i = 0; i < KITT_NUM; i++) base[i] = (uint8_t)((i * 255) / (KITT_NUM - 1));
  bool ok = true;

  uint8_t f[KITT_NUM];

  // B = 255 : aucune modification.
  memcpy(f, base, KITT_NUM);
  kittApplyBrightness(f, 255);
  for (int i = 0; i < KITT_NUM; i++) if (f[i] != base[i]) ok = false;

  // B = 0 : ecran eteint.
  memcpy(f, base, KITT_NUM);
  kittApplyBrightness(f, 0);
  for (int i = 0; i < KITT_NUM; i++) if (f[i] != 0) ok = false;

  // Attenuation seule : jamais au-dessus de l'original, pour tout B.
  for (int b = 0; b <= 255; b++) {
    memcpy(f, base, KITT_NUM);
    kittApplyBrightness(f, (uint8_t)b);
    for (int i = 0; i < KITT_NUM; i++) if (f[i] > base[i]) ok = false;
  }

  // Monotonie en B : pixel(B+step) >= pixel(B).
  uint8_t g[KITT_NUM];
  for (int b = 0; b < 255; b += 5) {
    memcpy(f, base, KITT_NUM); kittApplyBrightness(f, (uint8_t)b);
    memcpy(g, base, KITT_NUM); kittApplyBrightness(g, (uint8_t)(b + 5));
    for (int i = 0; i < KITT_NUM; i++) if (g[i] < f[i]) ok = false;
  }
  return ok;
}

// ------------------------------------------------------------
//  Sanity de la police / texte defilant (kittRenderText & co)    — #6
// ------------------------------------------------------------
//  Proprietes attendues :
//   - mesures de largeur exactes ("" -> 0, "A" -> 5, "AB" -> 11)
//   - repli minuscule -> majuscule (glyphe 'a' == glyphe 'A')
//   - caractere inconnu = espace (glyphe entierement vide)
//   - pixels allumes exactement a la luminosite demandee, aucun hors-ecran
//   - defilement complet : vide aux extremites, non vide au milieu
static bool countLit(const uint8_t* f, uint8_t expectBright) {
  int lit = 0;
  for (int i = 0; i < KITT_NUM; i++) {
    if (f[i] == 0) continue;
    if (f[i] != expectBright) return false;   // seule la luminosite demandee
    lit++;
  }
  return lit > 0;
}

static bool sanityText() {
  bool ok = true;

  // Mesures de largeur.
  if (kittTextCols("")   != 0)  ok = false;
  if (kittTextCols("A")  != KITT_GLYPH_W) ok = false;
  if (kittTextCols("AB") != 2 * KITT_GLYPH_W + KITT_GLYPH_GAP) ok = false;

  // Repli minuscule -> majuscule et robustesse "jamais NULL".
  if (kittGlyphRows('a') != kittGlyphRows('A')) ok = false;
  if (kittGlyphRows('z') != kittGlyphRows('Z')) ok = false;
  const uint8_t* unk = kittGlyphRows('~');       // inconnu -> espace
  if (unk == nullptr) ok = false;
  else { bool empty = true; for (int r = 0; r < KITT_GLYPH_H; r++) if (unk[r]) empty = false;
         if (!empty) ok = false; }

  uint8_t f[KITT_NUM];
  const uint8_t BR = 200;

  // Un glyphe visible a l'ecran : pixels allumes, tous a BR, aucun hors ecran.
  kittRenderText("A", 4, BR, f);
  if (!countLit(f, BR)) ok = false;

  // Entierement hors ecran (a gauche comme a droite) => rien d'allume.
  kittRenderText("HELLO", -100, BR, f);
  for (int i = 0; i < KITT_NUM; i++) if (f[i] != 0) ok = false;
  kittRenderText("HELLO", 100, BR, f);
  for (int i = 0; i < KITT_NUM; i++) if (f[i] != 0) ok = false;

  // Defilement complet d'un message : vide au tout debut et a la toute fin,
  // non vide quelque part au milieu.
  const char* msg = "KITT 2000";
  uint32_t steps = kittTextScrollSteps(msg);
  bool midLit = false;
  for (uint32_t t = 0; t <= steps; t++) {
    int off = kittTextOffsetAt(t);
    kittRenderText(msg, off, BR, f);
    int lit = 0; for (int i = 0; i < KITT_NUM; i++) if (f[i]) lit++;
    if (t == 0 && lit != 0) ok = false;         // texte encore hors ecran (droite)
    if (t == steps && lit != 0) ok = false;     // texte entierement sorti (gauche)
    if (lit > 0) midLit = true;
  }
  if (!midLit) ok = false;

  return ok;
}

// ------------------------------------------------------------
//  Sanity de la jauge persistante (kittGaugeCols/kittRenderGauge) — #7
// ------------------------------------------------------------
//  Proprietes attendues d'une jauge correcte :
//   - mesure de remplissage : 0 -> 0 colonne, 255 -> KITT_COLS, monotone
//   - piste de fond toujours visible => toutes les colonnes ont au moins
//     un pixel allume, sur exactement 4 lignes (2..5), rien hors de la barre
//   - tete qui "respire" dans [EDGE_MIN..EDGE_MAX] au fil de tick
//   - une valeur non nulle garantit au moins la tete (retour "faible mais vivant")
static bool sanityGauge() {
  bool ok = true;

  // Mesure de remplissage.
  if (kittGaugeCols(0)   != 0)         ok = false;
  if (kittGaugeCols(255) != KITT_COLS) ok = false;
  int prev = -1;                          // monotonie croissante
  for (int v = 0; v <= 255; v++) {
    int c = kittGaugeCols((uint8_t)v);
    if (c < 0 || c > KITT_COLS) ok = false;
    if (c < prev) ok = false;
    prev = c;
  }

  uint8_t f[KITT_NUM];
  const int ROW0 = KITT_GAUGE_ROW0, ROW1 = KITT_GAUGE_ROW1;

  // Pour un balayage de valeurs et quelques ticks : geometrie stricte.
  const uint8_t VALS[] = {0, 1, 64, 128, 200, 255};
  for (unsigned vi = 0; vi < sizeof(VALS); vi++) {
    for (uint32_t t = 0; t < 30; t++) {
      kittRenderGauge(VALS[vi], t, f);
      // (a) aucun pixel hors de la barre (lignes ROW0..ROW1).
      for (int r = 0; r < KITT_ROWS; r++)
        for (int c = 0; c < KITT_COLS; c++)
          if (r < ROW0 || r > ROW1) { if (f[r * KITT_COLS + c] != 0) ok = false; }
      // (b) piste de fond : CHAQUE colonne a au moins un pixel allume,
      //     et chaque ligne de la barre est identique (barre horizontale pleine).
      for (int c = 0; c < KITT_COLS; c++) {
        uint8_t v0 = f[ROW0 * KITT_COLS + c];
        if (v0 == 0) ok = false;                        // jamais totalement noir
        for (int r = ROW0; r <= ROW1; r++)
          if (f[r * KITT_COLS + c] != v0) ok = false;   // colonne uniforme
      }
    }
  }

  // (c) tete qui respire : la luminosite de la tete varie dans le temps et
  //     reste bornee [EDGE_MIN..EDGE_MAX]. On observe une valeur mi-jauge.
  uint8_t lo = 255, hi = 0;
  for (uint32_t t = 0; t < 200; t++) {
    kittRenderGauge(128, t, f);
    int filled = kittGaugeCols(128);           // colonne de tete = filled-1
    int head = (int)f[ROW0 * KITT_COLS + (filled - 1)];
    if (head < KITT_GAUGE_EDGE_MIN || head > KITT_GAUGE_EDGE_MAX) ok = false;
    if (head < lo) lo = head;
    if (head > hi) hi = head;
  }
  if (hi <= lo) ok = false;                     // doit reellement "respirer"

  // (d) corps rempli plus lumineux que la piste (contraste lisible) pour une
  //     jauge bien remplie ; et valeur nulle => que de la piste (pas de tete).
  kittRenderGauge(255, 0, f);
  if (f[ROW0 * KITT_COLS + 0] != KITT_GAUGE_FILL) ok = false;   // corps
  kittRenderGauge(0, 0, f);
  for (int c = 0; c < KITT_COLS; c++)
    if (f[ROW0 * KITT_COLS + c] != KITT_GAUGE_TRACK) ok = false; // piste seule

  return ok;
}

// ------------------------------------------------------------
//  Sanity du nombre persistant (kittNumberDigits/Cols/Render)    — #8
// ------------------------------------------------------------
//  Proprietes attendues d'un afficheur numerique correct :
//   - comptage de chiffres exact et borne (0->1, 9->1, 10->2, 100->3, 999->3,
//     tout depassement -> 3 chiffres, jamais plus large que la matrice)
//   - largeur en colonnes coherente et toujours <= KITT_COLS (centrable)
//   - rendu : pixels tous a la luminosite demandee, aucun hors ecran, confines
//     aux 5 lignes du bloc chiffres, jamais totalement vide
//   - centrage horizontal symetrique (marges gauche/droite egales a +-1 col)
//   - chaque chiffre 0..9 dessine une forme distincte (pas de doublon => police
//     lisible), et "8" (glyphe le plus dense) allume plus que "1" (le plus fin)
static bool sanityNumber() {
  bool ok = true;

  // Comptage de chiffres.
  if (kittNumberDigits(0)    != 1) ok = false;
  if (kittNumberDigits(9)    != 1) ok = false;
  if (kittNumberDigits(10)   != 2) ok = false;
  if (kittNumberDigits(99)   != 2) ok = false;
  if (kittNumberDigits(100)  != 3) ok = false;
  if (kittNumberDigits(999)  != 3) ok = false;
  if (kittNumberDigits(5000) != 3) ok = false;   // borne a KITT_NUM_MAX

  // Largeur : coherente et toujours affichable (centrable) sur 13 colonnes.
  const uint16_t WVALS[] = {0, 7, 12, 42, 99, 100, 555, 999, 4242};
  for (unsigned wi = 0; wi < sizeof(WVALS) / sizeof(WVALS[0]); wi++) {
    int w = kittNumberCols(WVALS[wi]);
    if (w <= 0 || w > KITT_COLS) ok = false;
  }

  uint8_t f[KITT_NUM];
  const uint8_t BR = 200;
  const int ROW0 = KITT_NUM_ROW0, ROW1 = KITT_NUM_ROW0 + KITT_NUM_DIGIT_H - 1;

  // Balayage de valeurs representatives : geometrie stricte du rendu.
  const uint16_t VALS[] = {0, 5, 10, 42, 88, 99, 100, 250, 888, 999};
  for (unsigned vi = 0; vi < sizeof(VALS) / sizeof(VALS[0]); vi++) {
    kittRenderNumber(VALS[vi], 0, BR, f);
    int lit = 0, minC = KITT_COLS, maxC = -1;
    for (int r = 0; r < KITT_ROWS; r++) {
      for (int c = 0; c < KITT_COLS; c++) {
        uint8_t v = f[r * KITT_COLS + c];
        if (v == 0) continue;
        if (v != BR) ok = false;                       // seule la lum. demandee
        if (r < ROW0 || r > ROW1) ok = false;          // confine au bloc chiffres
        lit++; if (c < minC) minC = c; if (c > maxC) maxC = c;
      }
    }
    if (lit == 0) ok = false;                           // jamais totalement vide
    // Centrage : marges gauche et droite (a +-1 col pres, parite de la largeur).
    int left = minC, right = KITT_COLS - 1 - maxC;
    if (left < 0 || right < 0) ok = false;
    if (left - right > 1 || right - left > 1) ok = false;
  }

  // 'tick' ne doit rien changer (affichage statique) : deux ticks == meme image.
  uint8_t a[KITT_NUM], b[KITT_NUM];
  kittRenderNumber(123, 0, BR, a);
  kittRenderNumber(123, 999, BR, b);
  for (int i = 0; i < KITT_NUM; i++) if (a[i] != b[i]) ok = false;

  // Chaque chiffre 0..9 => forme distincte (pas deux glyphes identiques).
  uint8_t glyph[10][KITT_NUM];
  for (int d = 0; d < 10; d++) kittRenderNumber((uint16_t)d, 0, BR, glyph[d]);
  for (int i = 0; i < 10; i++)
    for (int j = i + 1; j < 10; j++) {
      bool same = true;
      for (int k = 0; k < KITT_NUM; k++) if (glyph[i][k] != glyph[j][k]) same = false;
      if (same) ok = false;
    }

  // '8' (le plus dense) doit allumer strictement plus de pixels que '1' (le plus fin).
  auto litCount = [&](uint16_t v) { uint8_t t[KITT_NUM]; kittRenderNumber(v, 0, BR, t);
    int n = 0; for (int i = 0; i < KITT_NUM; i++) if (t[i]) n++; return n; };
  if (litCount(8) <= litCount(1)) ok = false;

  return ok;
}

// ------------------------------------------------------------
//  Sanity du dashboard combine (kittRenderDash)                  — #9
// ------------------------------------------------------------
//  Proprietes attendues d'un cadran combine correct :
//   - les chiffres restent confines au bloc haut (lignes 0..4), a la seule
//     luminosite demandee ; la barre reste confinee au bloc bas (lignes 6..7)
//   - la ligne 5 (separation) est TOUJOURS vide => les deux zones ne se
//     chevauchent jamais
//   - la barre garde les proprietes de la jauge : piste de fond toujours
//     visible (chaque colonne allumee), colonnes uniformes, tete qui respire
//   - le remplissage de la barre suit kittGaugeCols, et le bloc chiffres n'est
//     jamais vide (le nombre est bien dessine)
static bool sanityDash() {
  bool ok = true;
  uint8_t f[KITT_NUM];
  const uint8_t BR = 210;
  const int NROW0 = KITT_DASH_NUM_ROW0, NROW1 = KITT_DASH_NUM_ROW0 + KITT_NUM_DIGIT_H - 1;
  const int BROW0 = KITT_DASH_BAR_ROW0, BROW1 = KITT_DASH_BAR_ROW1;

  const struct { uint16_t n; uint8_t g; } CASES[] = {
    {0, 0}, {7, 64}, {42, 128}, {90, 180}, {128, 200}, {999, 255}
  };
  for (unsigned ci = 0; ci < sizeof(CASES) / sizeof(CASES[0]); ci++) {
    for (uint32_t t = 0; t < 20; t++) {
      kittRenderDash(CASES[ci].n, CASES[ci].g, t, BR, f);

      // (a) ligne(s) de separation entre chiffres et barre : toujours vides.
      for (int r = NROW1 + 1; r < BROW0; r++)
        for (int c = 0; c < KITT_COLS; c++)
          if (f[r * KITT_COLS + c] != 0) ok = false;

      // (b) bloc chiffres (haut) : uniquement 0 ou BR, jamais ailleurs.
      for (int r = NROW0; r <= NROW1; r++)
        for (int c = 0; c < KITT_COLS; c++) {
          uint8_t v = f[r * KITT_COLS + c];
          if (v != 0 && v != BR) ok = false;
        }

      // (c) bloc barre (bas) : chaque colonne allumee (piste de fond) et
      //     uniforme verticalement (barre horizontale pleine).
      for (int c = 0; c < KITT_COLS; c++) {
        uint8_t vb = f[BROW0 * KITT_COLS + c];
        if (vb == 0) ok = false;
        for (int r = BROW0; r <= BROW1; r++)
          if (f[r * KITT_COLS + c] != vb) ok = false;
      }
    }
  }

  // (d) coherence : le bloc chiffres est non vide, la barre suit kittGaugeCols.
  kittRenderDash(90, 180, 0, BR, f);
  int litTop = 0;
  for (int r = NROW0; r <= NROW1; r++)
    for (int c = 0; c < KITT_COLS; c++) if (f[r * KITT_COLS + c]) litTop++;
  if (litTop == 0) ok = false;
  int filled = kittGaugeCols(180);
  if (filled > 1 && f[BROW0 * KITT_COLS + 0] != KITT_GAUGE_FILL) ok = false;  // corps

  // (e) la barre "respire" dans le temps, comme la jauge autonome.
  uint8_t lo = 255, hi = 0;
  for (uint32_t t = 0; t < 200; t++) {
    kittRenderDash(90, 128, t, BR, f);
    int fill = kittGaugeCols(128);
    int head = (int)f[BROW0 * KITT_COLS + (fill - 1)];
    if (head < lo) lo = head;
    if (head > hi) hi = head;
  }
  if (hi <= lo) ok = false;

  return ok;
}

// ------------------------------------------------------------
//  Mode TRANS : rejoue la MEME logique de fondu que le sketch    — #4
//  (rendu de l'etat entrant + kittBlend sur l'image figee) pour
//  "voir" un enchainement d'etats avec transitions douces.
// ------------------------------------------------------------
static void runTransitions(uint8_t level) {
  const uint8_t SEQ[] = { KITT_IDLE, KITT_LISTEN, KITT_THINK, KITT_SPEAK, KITT_WORD, KITT_IDLE };
  const int SEQ_LEN = (int)(sizeof(SEQ));
  const int HOLD = 6;   // pas "stables" affiches apres la fin de chaque fondu

  uint8_t frame[KITT_NUM];   // image affichee (comme sur le MCU)
  uint8_t prev[KITT_NUM];    // image figee au changement d'etat
  uint8_t cur[KITT_NUM];
  memset(frame, 0, KITT_NUM);

  printf("\n########## DEMO TRANSITIONS (fondu enchaine) ##########\n");
  printf("Sequence : IDLE -> LISTEN -> THINK -> SPEAK -> WORD -> IDLE\n");

  for (int s = 0; s < SEQ_LEN; s++) {
    uint8_t st = SEQ[s];
    memcpy(prev, frame, KITT_NUM);            // gel comme dans applyState()
    uint16_t steps = kittTransSteps(st);
    uint32_t tick = 0;

    // Phase de fondu : on affiche chaque pas du crossfade.
    for (uint16_t k = 1; k <= steps; k++) {
      tick++;
      kittRender(st, tick, level, cur);
      float a = (float)k / (float)steps;
      kittBlend(prev, cur, a, frame);
      printf("-- ->%s  FONDU %u/%u (a=%.2f) --\n", stateName(st), k, steps, a);
      printFrame(frame);
    }

    // Phase stable : etat entrant en plein regime (fondu termine).
    for (int k = 0; k < HOLD; k++) {
      tick++;
      kittRender(st, tick, level, frame);
    }
    printf("-- %s  STABLE (tick=%u) --\n", stateName(st), tick);
    printFrame(frame);
  }
}

// ------------------------------------------------------------
//  Mode BRIGHT : "voir" l'effet du dimming jour/nuit             — #5
//  Prend un etat lisible (SPEAK) et applique differents paliers
//  de luminosite globale sur l'image affichee.
// ------------------------------------------------------------
static void runBrightness(uint8_t level) {
  const uint8_t PALIERS[] = { 255, 180, 120, 70, KITT_BRIGHT_NIGHT, 0 };
  const int N = (int)(sizeof(PALIERS));
  uint8_t f[KITT_NUM];

  printf("\n########## DEMO LUMINOSITE (dimming jour/nuit) ##########\n");
  printf("Etat SPEAK fige, luminosite globale B decroissante :\n");
  for (int i = 0; i < N; i++) {
    kittRender(KITT_SPEAK, 4, level, f);      // meme frame de reference
    kittApplyBrightness(f, PALIERS[i]);
    printf("-- SPEAK  B=%d --\n", PALIERS[i]);
    printFrame(f);
  }
}

// ------------------------------------------------------------
//  Mode TEXT : "voir" le defilement d'un message arbitraire      — #6
//  Rejoue la MEME logique que le sketch (kittRenderText a l'offset
//  du pas courant), du texte entrant par la droite a sa sortie.
// ------------------------------------------------------------
static void runText(const char* msg) {
  uint8_t f[KITT_NUM];
  const uint8_t BR = 210;                      // luminosite du texte (comme le sketch)
  uint32_t steps = kittTextScrollSteps(msg);
  printf("\n########## DEMO TEXTE DEFILANT ##########\n");
  printf("Message : \"%s\"  (largeur %d col, %u pas)\n",
         msg, kittTextCols(msg), steps);
  for (uint32_t t = 0; t <= steps; t++) {
    int off = kittTextOffsetAt(t);
    kittRenderText(msg, off, BR, f);
    printf("-- \"%s\"  pas=%u/%u  offset=%d --\n", msg, t, steps, off);
    printFrame(f);
  }
}

// ------------------------------------------------------------
//  Mode GAUGE : "voir" la jauge persistante                      — #7
//  Balaye quelques valeurs (barre qui se remplit) puis montre la
//  respiration de la tete sur une valeur fixe (comme une vitesse tenue).
// ------------------------------------------------------------
static void runGauge(uint8_t value, bool sweep) {
  uint8_t f[KITT_NUM];
  printf("\n########## DEMO JAUGE PERSISTANTE ##########\n");
  if (sweep) {
    const uint8_t VALS[] = {0, 26, 64, 128, 191, 230, 255};
    printf("Balayage de remplissage (0 -> plein) :\n");
    for (unsigned i = 0; i < sizeof(VALS); i++) {
      kittRenderGauge(VALS[i], 0, f);
      printf("-- G=%d  (%d/%d colonnes) --\n", VALS[i], kittGaugeCols(VALS[i]), KITT_COLS);
      printFrame(f);
    }
  }
  printf("Respiration de la tete a G=%d (valeur tenue) :\n", value);
  for (uint32_t t = 0; t < 8; t++) {
    kittRenderGauge(value, t, f);
    printf("-- G=%d  tick=%u --\n", value, t);
    printFrame(f);
  }
}

// ------------------------------------------------------------
//  Mode NUMBER : "voir" le nombre persistant                     — #8
//  Balaye 1, 2 et 3 chiffres pour verifier centrage et lisibilite,
//  puis affiche une valeur precise (ex. une vitesse tenue).
// ------------------------------------------------------------
static void runNumber(uint16_t value, bool sweep) {
  uint8_t f[KITT_NUM];
  printf("\n########## DEMO NOMBRE PERSISTANT ##########\n");
  if (sweep) {
    const uint16_t VALS[] = {0, 7, 42, 90, 128, 250, 888, 999};
    printf("Balayage (1 -> 3 chiffres, centre) :\n");
    for (unsigned i = 0; i < sizeof(VALS) / sizeof(VALS[0]); i++) {
      kittRenderNumber(VALS[i], 0, KITT_NUM_BRIGHT, f);
      printf("-- N=%u  (%d chiffres, %d col) --\n",
             VALS[i], kittNumberDigits(VALS[i]), kittNumberCols(VALS[i]));
      printFrame(f);
    }
  }
  printf("Valeur tenue N=%u :\n", value);
  kittRenderNumber(value, 0, KITT_NUM_BRIGHT, f);
  printFrame(f);
}

// ------------------------------------------------------------
//  Mode DASH : "voir" le dashboard combine (nombre + jauge)      — #9
//  Balaye quelques couples (chiffre, barre) representatifs d'un cadran
//  OBD, puis montre la respiration de la barre sur une valeur tenue.
// ------------------------------------------------------------
static void runDash(uint16_t number, uint8_t gauge, bool sweep) {
  uint8_t f[KITT_NUM];
  printf("\n########## DEMO DASHBOARD COMBINE ##########\n");
  if (sweep) {
    const struct { uint16_t n; uint8_t g; } CASES[] = {
      {0, 0}, {30, 38}, {90, 115}, {130, 166}, {200, 255}
    };
    printf("Balayage cadran (chiffre en haut, barre en bas) :\n");
    for (unsigned i = 0; i < sizeof(CASES) / sizeof(CASES[0]); i++) {
      kittRenderDash(CASES[i].n, CASES[i].g, 0, KITT_DASH_BRIGHT, f);
      printf("-- D=%u,%u  (%d col de barre) --\n",
             CASES[i].n, CASES[i].g, kittGaugeCols(CASES[i].g));
      printFrame(f);
    }
  }
  printf("Cadran tenu D=%u,%u (barre qui respire) :\n", number, gauge);
  for (uint32_t t = 0; t < 6; t++) {
    kittRenderDash(number, gauge, t, KITT_DASH_BRIGHT, f);
    printf("-- D=%u,%u  tick=%u --\n", number, gauge, t);
    printFrame(f);
  }
}

int main(int argc, char** argv) {
  const char* want = (argc > 1) ? argv[1] : "ALL";

  if (strcmp(want, "TEXT") == 0) {
    const char* msg = (argc > 2) ? argv[2] : "KITT 2000";
    runText(msg);
    return 0;
  }

  if (strcmp(want, "GAUGE") == 0) {
    int v = (argc > 2) ? atoi(argv[2]) : 128;
    if (v < 0) v = 0;
    if (v > 255) v = 255;
    runGauge((uint8_t)v, /*sweep=*/argc <= 2);   // sans arg => balayage complet
    return 0;
  }

  if (strcmp(want, "NUMBER") == 0) {
    int v = (argc > 2) ? atoi(argv[2]) : 90;
    if (v < 0) v = 0;
    if (v > KITT_NUM_MAX) v = KITT_NUM_MAX;
    runNumber((uint16_t)v, /*sweep=*/argc <= 2); // sans arg => balayage complet
    return 0;
  }

  if (strcmp(want, "DASH") == 0) {
    // ./anim_sim DASH [nombre] [jauge] ; sans arg => balayage complet.
    int n = (argc > 2) ? atoi(argv[2]) : 90;
    int g = (argc > 3) ? atoi(argv[3]) : 115;
    if (n < 0) n = 0;
    if (n > KITT_NUM_MAX) n = KITT_NUM_MAX;
    if (g < 0) g = 0;
    if (g > 255) g = 255;
    runDash((uint16_t)n, (uint8_t)g, /*sweep=*/argc <= 2);
    return 0;
  }

  int   frames = (argc > 2) ? atoi(argv[2]) : 6;
  uint8_t level = (argc > 3) ? (uint8_t)atoi(argv[3]) : 200;

  if (strcmp(want, "TRANS") == 0) {
    runTransitions(level);
    return 0;
  }
  if (strcmp(want, "BRIGHT") == 0) {
    runBrightness(level);
    return 0;
  }

  if (strcmp(want, "ALL") == 0) {
    // 1) Sanity global des etats
    printf("### SANITY (chaque etat allume des pixels sur 60 pas) ###\n");
    bool ok = true;
    for (uint8_t s = 0; s < KITT_STATE_COUNT; s++) {
      bool lit = sanity(s, level);
      printf("  %-7s : %s\n", stateName(s), lit ? "OK" : "VIDE (!!)");
      if (!lit) ok = false;
    }
    // 1bis) Sanity du fondu enchaine (helpers purs)
    bool blendOk = sanityBlend();
    printf("  %-7s : %s\n", "BLEND", blendOk ? "OK" : "ECHEC (!!)");
    if (!blendOk) ok = false;
    // 1ter) Sanity du dimming jour/nuit (helper pur)
    bool brightOk = sanityBrightness();
    printf("  %-7s : %s\n", "BRIGHT", brightOk ? "OK" : "ECHEC (!!)");
    if (!brightOk) ok = false;
    // 1quater) Sanity de la police / texte defilant (#6)
    bool textOk = sanityText();
    printf("  %-7s : %s\n", "TEXT", textOk ? "OK" : "ECHEC (!!)");
    if (!textOk) ok = false;
    // 1quinquies) Sanity de la jauge persistante (#7)
    bool gaugeOk = sanityGauge();
    printf("  %-7s : %s\n", "GAUGE", gaugeOk ? "OK" : "ECHEC (!!)");
    if (!gaugeOk) ok = false;
    // 1sexies) Sanity du nombre persistant (#8)
    bool numberOk = sanityNumber();
    printf("  %-7s : %s\n", "NUMBER", numberOk ? "OK" : "ECHEC (!!)");
    if (!numberOk) ok = false;
    // 1septies) Sanity du dashboard combine (#9)
    bool dashOk = sanityDash();
    printf("  %-7s : %s\n", "DASH", dashOk ? "OK" : "ECHEC (!!)");
    if (!dashOk) ok = false;
    printf("### RESULTAT : %s ###\n", ok ? "TOUS OK" : "ECHEC");
    // 2) Apercu de quelques frames par etat
    for (uint8_t s = 0; s < KITT_STATE_COUNT; s++) renderState(s, frames, level);
    return ok ? 0 : 1;
  }

  int st = kittStateFromName(want);
  if (st < 0) { printf("Etat inconnu: %s\n", want); return 2; }
  renderState((uint8_t)st, frames, level);
  return 0;
}
