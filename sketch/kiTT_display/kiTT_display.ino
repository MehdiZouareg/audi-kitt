/*
 * ============================================================
 *  Audi kiTT  —  Affichage LED pilote par etat
 * ============================================================
 *  Cible  : Arduino UNO Q, matrice LED integree 13x8 (104 LEDs)
 *  Pilote : MCU STM32 (la matrice n'est PAS accessible cote Linux)
 *  Lib    : Arduino_LED_Matrix (niveaux de gris 8 bits = luminosite)
 *
 *  Historique :
 *   #2 - Machine a etats NON BLOQUANTE (base millis, plus de delay()),
 *        bibliotheque d'animations reutilisable (kiTT_anim.h), protocole
 *        serie pilote depuis le MPU Linux, mode DEMO auto.
 *   #4 - TRANSITIONS DOUCES : chaque changement d'etat se fait en fondu
 *        enchaine (crossfade) plutot que par une coupure nette. Le fondu
 *        reste NON BLOQUANT et s'appuie sur les helpers purs de kiTT_anim.h
 *        (kittBlend / kittTransSteps), donc valides par le simulateur host.
 *   #5 - LUMINOSITE GLOBALE (dimming jour/nuit) : commande serie `B:<0-255>`
 *        qui attenue l'image AFFICHEE sans toucher a l'animation interne
 *        (post-traitement pur kittApplyBrightness sur un tampon de sortie).
 *        Utile pour ne pas eblouir au volant la nuit.
 *   #6 - TEXTE DEFILANT : commande serie `T:<message>` qui fait defiler un
 *        message arbitraire (droite -> gauche) puis revient a IDLE. Rendu par
 *        la police pure kittRenderText ; profite du meme fondu enchaine et de
 *        la meme luminosite globale que les etats. Le logo "kiTT" (S:WORD)
 *        reste une signature dediee, inchangee.
 *   #7 - JAUGE PERSISTANTE : commande serie `G:<0-255>` qui affiche EN
 *        PERMANENCE une valeur sous forme de barre horizontale (piste de fond
 *        + tete qui respire), lisible d'un coup d'oeil au volant (vitesse /
 *        RPM / carburant via OBD plus tard). Contrairement au texte, la jauge
 *        reste affichee jusqu'a une autre commande. Les mises a jour de valeur
 *        (G: repete) sont instantanees (pas de re-fondu) => reactif ; le fondu
 *        n'a lieu qu'a l'ENTREE en mode jauge. Rendu pur kittRenderGauge.
 *   #8 - NOMBRE PERSISTANT : commande serie `N:<0-999>` qui affiche EN
 *        PERMANENCE une valeur NUMERIQUE EXACTE en gros chiffres centres
 *        (vitesse "90", temperature "72"...). Complete la jauge : la jauge
 *        donne une proportion glancable, le nombre donne la valeur precise.
 *        Comme la jauge : fondu a l'entree, mise a jour instantanee si `N:`
 *        est repete, et reste affiche jusqu'a une autre commande. Rendu pur
 *        kittRenderNumber.
 *   #9 - DASHBOARD COMBINE : commande serie `D:<0-999>,<0-255>` qui affiche EN
 *        PERMANENCE le NOMBRE (haut) ET la JAUGE (bas) sur le meme ecran, pour
 *        lire d'un coup la valeur exacte ET la proportion (ex. "90" + barre a
 *        45%). Reunit les briques #7 et #8. Meme comportement persistant :
 *        fondu a l'entree, mise a jour instantanee si `D:` est repete, reste
 *        affiche jusqu'a une autre commande. Rendu pur kittRenderDash.
 *
 *  Protocole serie (115200 bauds, lignes terminees par '\n') :
 *     S:IDLE | S:LISTEN | S:THINK | S:SPEAK | S:WORD | S:BOOT | S:ERROR
 *     L:<0-255>       -> niveau de modulation (amplitude voix / micro)
 *     B:<0-255>       -> luminosite globale de l'afficheur (255 = plein jour)
 *     T:<message>     -> fait defiler <message> une fois puis revient a IDLE
 *     G:<0-255>       -> affiche une jauge persistante (barre) de cette valeur
 *     N:<0-999>       -> affiche un nombre persistant (chiffres) de cette valeur
 *     D:<0-999>,<0-255> -> dashboard : nombre (haut) + jauge (bas), persistant
 *     P               -> ping (repond "kiTT ok")
 *  Exemple depuis Linux : printf "B:60\nD:90,115\n" > /dev/ttyXXX
 *
 *  Auteur : Claude (Cowork)
 * ============================================================
 */

#include <Arduino_LED_Matrix.h>
#include <string.h>            // memcpy / strchr
#include "kiTT_anim.h"

Arduino_LED_Matrix matrix;

// ---------- Etat courant ----------
uint8_t  frame[KITT_NUM];         // image "logique" (apres fondu, pleine luminosite)
uint8_t  prevFrame[KITT_NUM];     // image figee au dernier changement d'etat
uint8_t  outFrame[KITT_NUM];      // copie ATTENUEE reellement envoyee a la matrice
uint8_t  gState   = KITT_BOOT;
uint8_t  gLevel   = 200;          // niveau par defaut (utile en demo)
uint8_t  gBright  = 255;          // luminosite globale (255 = plein jour)
uint32_t gTick    = 0;            // pas d'animation de l'etat courant
uint32_t gLastStep = 0;          // horodatage du dernier pas (millis)

// ---------- Transition (fondu enchaine) ----------
uint16_t gTransSteps = 0;         // duree du fondu en pas (0 = pas de fondu)
uint16_t gTransTick  = 0;         // progression du fondu (0..gTransSteps)

// ---------- Texte defilant (commande T:)  — #6 ----------
#define KITT_TEXT_MAX 40
char     gText[KITT_TEXT_MAX];    // message en cours de defilement
bool     gTextMode = false;       // true => on affiche gText au lieu d'un etat
const uint8_t KITT_TEXT_BRIGHT = 210;   // luminosite du texte (avant dimming global)

// ---------- Jauge persistante (commande G:)  — #7 ----------
bool     gGaugeMode  = false;     // true => on affiche une barre au lieu d'un etat
uint8_t  gGaugeValue = 0;         // valeur courante de la jauge (0..255)

// ---------- Nombre persistant (commande N:)  — #8 ----------
bool     gNumberMode  = false;    // true => on affiche des chiffres au lieu d'un etat
uint16_t gNumberValue = 0;        // valeur courante du nombre (0..KITT_NUM_MAX)

// ---------- Dashboard combine (commande D:)  — #9 ----------
bool     gDashMode   = false;     // true => on affiche nombre + jauge combines
uint16_t gDashNumber = 0;         // partie chiffre du dashboard (0..KITT_NUM_MAX)
uint8_t  gDashGauge  = 0;         // partie barre du dashboard (0..255)

// ---------- Mode demo (tant que Linux ne parle pas) ----------
bool     gDemo = true;
uint32_t gDemoLastSwitch = 0;
const uint32_t DEMO_HOLD_MS = 3200;                 // duree de chaque etat en demo
const uint8_t  DEMO_SEQ[] = { KITT_IDLE, KITT_LISTEN, KITT_THINK, KITT_SPEAK, KITT_WORD };
const uint8_t  DEMO_SEQ_LEN = sizeof(DEMO_SEQ);
uint8_t  gDemoIdx = 0;

// ---------- Reception serie ----------
// Buffer de ligne dimensionne pour accueillir "T:" + un message (KITT_TEXT_MAX).
char     gLine[KITT_TEXT_MAX + 4];
uint8_t  gLineLen = 0;

// Gel de l'image affichee (point de depart du fondu) + reset de la cadence,
// pour une transition de 'steps' pas. Facteur commun a tous les modes.
void beginTransitionSteps(uint16_t steps) {
  memcpy(prevFrame, frame, KITT_NUM);   // point de depart du crossfade
  gTick = 0;
  gTransTick = 0;
  gTransSteps = steps;
  gLastStep = millis();
}

// Variante "par etat" : dimensionne le fondu sur la cadence de 'cadenceState'.
void beginTransition(uint8_t cadenceState) {
  beginTransitionSteps(kittTransSteps(cadenceState));
}

void applyState(uint8_t s) {
  if (s >= KITT_STATE_COUNT) return;
  gTextMode = false;                // tout etat "normal" sort du mode texte...
  gGaugeMode = false;               // ...du mode jauge...
  gNumberMode = false;              // ...du mode nombre...
  gDashMode = false;                // ...et du mode dashboard
  gState = s;
  beginTransition(s);               // fondu dimensionne sur la cadence de 's'
}

// Demarre le defilement d'un message (mode texte). On emprunte la cadence de
// l'etat WORD pour la vitesse de defilement, et on beneficie du meme fondu.
void applyText(const char* msg) {
  strncpy(gText, msg, KITT_TEXT_MAX - 1);
  gText[KITT_TEXT_MAX - 1] = '\0';
  gTextMode = true;
  gGaugeMode = false;
  gNumberMode = false;
  gDashMode = false;
  gDemo = false;
  gState = KITT_WORD;               // cadence de defilement de reference
  beginTransition(KITT_WORD);
}

// Affiche/actualise la jauge persistante. A l'ENTREE en mode jauge : fondu
// enchaine depuis l'image courante. Si on est DEJA en mode jauge : simple mise
// a jour de la valeur, sans re-fondu (une valeur qui bouge doit rester reactive
// et fluide, pas "laggy" a chaque rafraichissement OBD).
void applyGauge(uint8_t value) {
  bool wasGauge = gGaugeMode;
  gGaugeValue = value;
  gTextMode = false;
  gNumberMode = false;
  gDashMode = false;
  gDemo = false;
  if (!wasGauge) {
    gGaugeMode = true;
    // Cadence propre de la jauge => fondu dimensionne dessus.
    beginTransitionSteps(kittTransStepsForInterval(KITT_GAUGE_STEP_MS));
  }
}

// Affiche/actualise le nombre persistant. Meme logique que la jauge : fondu a
// l'ENTREE, puis mises a jour instantanees (sans re-fondu) tant qu'on reste en
// mode nombre => une vitesse qui change reste reactive. Reste affiche jusqu'a
// une autre commande.
void applyNumber(uint16_t value) {
  bool wasNumber = gNumberMode;
  if (value > KITT_NUM_MAX) value = KITT_NUM_MAX;
  gNumberValue = value;
  gTextMode = false;
  gGaugeMode = false;
  gDashMode = false;
  gDemo = false;
  if (!wasNumber) {
    gNumberMode = true;
    // Cadence propre du mode nombre => fondu dimensionne dessus.
    beginTransitionSteps(kittTransStepsForInterval(KITT_NUM_STEP_MS));
  }
}

// Affiche/actualise le dashboard combine (nombre + jauge). Meme logique de
// persistance que la jauge et le nombre : fondu UNIQUEMENT a l'entree en mode
// dashboard, puis mises a jour instantanees des deux valeurs (sans re-fondu)
// tant qu'on reste en mode dashboard => reactif pour une source OBD.
void applyDash(uint16_t number, uint8_t gauge) {
  bool wasDash = gDashMode;
  if (number > KITT_NUM_MAX) number = KITT_NUM_MAX;
  gDashNumber = number;
  gDashGauge = gauge;
  gTextMode = false;
  gGaugeMode = false;
  gNumberMode = false;
  gDemo = false;
  if (!wasDash) {
    gDashMode = true;
    // Cadence propre du dashboard => fondu dimensionne dessus.
    beginTransitionSteps(kittTransStepsForInterval(KITT_DASH_STEP_MS));
  }
}

void handleLine(const char* line) {
  // Toute commande valide d'etat/niveau/texte/jauge/nombre/dashboard sort du demo.
  if (line[0] == 'S' && line[1] == ':') {
    int st = kittStateFromName(line + 2);
    if (st >= 0) { gDemo = false; applyState((uint8_t)st); }
  } else if (line[0] == 'L' && line[1] == ':') {
    int v = atoi(line + 2);
    if (v < 0) v = 0; if (v > 255) v = 255;
    gLevel = (uint8_t)v;
    gDemo = false;
  } else if (line[0] == 'B' && line[1] == ':') {
    // Luminosite globale : n'interrompt PAS le mode demo (reglage transverse,
    // typiquement pilote par l'heure ou un capteur de luminosite ambiante).
    int v = atoi(line + 2);
    if (v < 0) v = 0; if (v > 255) v = 255;
    gBright = (uint8_t)v;
  } else if (line[0] == 'T' && line[1] == ':') {
    // Texte defilant arbitraire. Message vide => ignore (evite un no-op).
    if (line[2] != '\0') applyText(line + 2);
  } else if (line[0] == 'G' && line[1] == ':') {
    // Jauge persistante. Valeur bornee 0..255.
    int v = atoi(line + 2);
    if (v < 0) v = 0; if (v > 255) v = 255;
    gDemo = false;
    applyGauge((uint8_t)v);
  } else if (line[0] == 'N' && line[1] == ':') {
    // Nombre persistant. Valeur bornee 0..KITT_NUM_MAX.
    int v = atoi(line + 2);
    if (v < 0) v = 0; if (v > KITT_NUM_MAX) v = KITT_NUM_MAX;
    gDemo = false;
    applyNumber((uint16_t)v);
  } else if (line[0] == 'D' && line[1] == ':') {
    // Dashboard combine : "D:<nombre>,<jauge>". Le nombre (0..999) est avant la
    // virgule, la jauge (0..255) apres. Virgule absente => jauge = 0.
    int n = atoi(line + 2);
    const char* comma = strchr(line + 2, ',');
    int g = comma ? atoi(comma + 1) : 0;
    if (n < 0) n = 0; if (n > KITT_NUM_MAX) n = KITT_NUM_MAX;
    if (g < 0) g = 0; if (g > 255) g = 255;
    gDemo = false;
    applyDash((uint16_t)n, (uint8_t)g);
  } else if (line[0] == 'P') {
    Serial.println("kiTT ok");
  }
}

void pollSerial() {
  while (Serial.available() > 0) {
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (gLineLen > 0) { gLine[gLineLen] = '\0'; handleLine(gLine); gLineLen = 0; }
    } else if (gLineLen < sizeof(gLine) - 1) {
      gLine[gLineLen++] = ch;
    }
  }
}

void updateDemo(uint32_t now) {
  if (!gDemo) return;
  if (now - gDemoLastSwitch >= DEMO_HOLD_MS) {
    gDemoLastSwitch = now;
    gDemoIdx = (uint8_t)((gDemoIdx + 1) % DEMO_SEQ_LEN);
    applyState(DEMO_SEQ[gDemoIdx]);
  }
}

// Envoie l'image logique 'frame' a la matrice, apres attenuation globale.
// On n'altere jamais 'frame' lui-meme (garde la pleine luminosite pour le
// prochain fondu) : la luminosite est appliquee sur une copie 'outFrame'.
void pushFrame() {
  memcpy(outFrame, frame, KITT_NUM);
  kittApplyBrightness(outFrame, gBright);
  matrix.draw(outFrame);
}

// ============================================================

void setup() {
  Serial.begin(115200);
  matrix.begin();
  matrix.setGrayscaleBits(8);      // luminosite 0..255
  memset(frame, 0, KITT_NUM);      // ecran noir : le BOOT montera en fondu propre
  memset(prevFrame, 0, KITT_NUM);
  memset(outFrame, 0, KITT_NUM);
  gText[0] = '\0';
  uint32_t t = millis();
  gLastStep = t;
  gDemoLastSwitch = t;
  applyState(KITT_BOOT);
}

void loop() {
  uint32_t now = millis();

  // 1) Entrees : commandes du MPU Linux (prioritaire, non bloquant)
  pollSerial();

  // 2) Transitions automatiques
  if (gTextMode) {
    // Fin du defilement (message entierement sorti par la gauche) -> IDLE.
    // En demo on ne devrait pas etre en mode texte (T: sort du demo).
    if (!gDemo && gTick >= kittTextScrollSteps(gText)) applyState(KITT_IDLE);
  } else if (gGaugeMode || gNumberMode || gDashMode) {
    // Modes PERSISTANTS (jauge / nombre / dashboard) : aucun retour automatique.
    // On reste affiche jusqu'a une autre commande (S:/T: ou nouvelle valeur).
  } else if (gState == KITT_BOOT) {
    // fin du reveil -> IDLE (ou demarre le cycle demo)
    if (gTick >= kittCycleSteps(KITT_BOOT)) {
      applyState(gDemo ? DEMO_SEQ[0] : KITT_IDLE);
      gDemoLastSwitch = now;
    }
  } else if (gState == KITT_WORD && !gDemo) {
    // en mode pilote, on revient a IDLE apres un defilement complet du logo
    if (gTick >= kittCycleSteps(KITT_WORD)) applyState(KITT_IDLE);
  }
  updateDemo(now);

  // 3) Avance de l'animation a la cadence du mode courant
  uint16_t interval;
  if (gDashMode)        interval = KITT_DASH_STEP_MS;
  else if (gGaugeMode)  interval = KITT_GAUGE_STEP_MS;
  else if (gNumberMode) interval = KITT_NUM_STEP_MS;
  else                  interval = kittStepInterval(gState);
  if (now - gLastStep >= interval) {
    gLastStep += interval;
    gTick++;

    // Rendu pur dans un tampon temporaire : dashboard OU nombre OU jauge OU
    // texte OU etat.
    uint8_t cur[KITT_NUM];
    if (gDashMode) {
      kittRenderDash(gDashNumber, gDashGauge, gTick, KITT_DASH_BRIGHT, cur);
    } else if (gNumberMode) {
      kittRenderNumber(gNumberValue, gTick, KITT_TEXT_BRIGHT, cur);
    } else if (gGaugeMode) {
      kittRenderGauge(gGaugeValue, gTick, cur);
    } else if (gTextMode) {
      int off = kittTextOffsetAt(gTick);            // defilement droite -> gauche
      kittRenderText(gText, off, KITT_TEXT_BRIGHT, cur);
    } else {
      kittRender(gState, gTick, gLevel, cur);
    }

    // ...puis fondu enchaine par-dessus l'image figee, tant qu'il reste des
    // pas de transition. Une fois le fondu termine, on affiche le rendu brut.
    if (gTransSteps > 0 && gTransTick < gTransSteps) {
      gTransTick++;
      float a = (float)gTransTick / (float)gTransSteps;   // 0..1
      kittBlend(prevFrame, cur, a, frame);
    } else {
      memcpy(frame, cur, KITT_NUM);
    }
    pushFrame();   // envoi a la matrice avec luminosite globale appliquee
  }
}
