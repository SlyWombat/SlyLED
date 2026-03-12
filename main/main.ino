/*
 * Rainbow Cycle — Arduino Giga R1 WiFi
 * Cycles the onboard RGB LED through rainbow colors.
 * Pins LEDR, LEDG, LEDB are active-low (LOW = ON).
 */

#include <FastLED.h>

const int PIN_LEDR = LEDR;
const int PIN_LEDG = LEDG;
const int PIN_LEDB = LEDB;

// Rainbow step size and delay (smooth cycle, ~8s per full rainbow)
const uint8_t HUE_STEP = 2;
const int DELAY_MS = 35;

void setRGB(uint8_t r, uint8_t g, uint8_t b) {
  // Active-low: write 255 - value to turn ON by that amount
  analogWrite(PIN_LEDR, 255 - r);
  analogWrite(PIN_LEDG, 255 - g);
  analogWrite(PIN_LEDB, 255 - b);
}

void setup() {
  pinMode(PIN_LEDR, OUTPUT);
  pinMode(PIN_LEDG, OUTPUT);
  pinMode(PIN_LEDB, OUTPUT);
  setRGB(0, 0, 0);
}

void loop() {
  // Cycle hue 0..255 (one full rainbow)
  for (int hue = 0; hue < 256; hue += HUE_STEP) {
    CRGB color = CHSV(hue, 255, 255);
    setRGB(color.r, color.g, color.b);
    delay(DELAY_MS);
  }
}
