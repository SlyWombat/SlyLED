/*
 * Rainbow Cycle — Arduino Giga R1 WiFi
 * Smooth rainbow on the onboard RGB LED using software PWM only.
 * No analogWrite (crashes Mbed on LED pins). Active-low: LOW = on.
 */

const int PIN_LEDR = LEDR;
const int PIN_LEDG = LEDG;
const int PIN_LEDB = LEDB;

const uint8_t HUE_STEP = 2;
const int DISPLAY_MS = 35;        // How long to show each hue step
const int PWM_CYCLE_US = 2048;   // Software PWM period (us)
const int PWM_STEPS = 256;
const int STEP_US = PWM_CYCLE_US / PWM_STEPS;  // 8 us per step

// Hue 0-255 -> R,G,B (0-255)
void hueToRGB(uint8_t hue, uint8_t& r, uint8_t& g, uint8_t& b) {
  if (hue < 43) {
    r = 255;
    g = hue * 6;
    b = 0;
  } else if (hue < 85) {
    r = 255 - (hue - 43) * 6;
    g = 255;
    b = 0;
  } else if (hue < 128) {
    r = 0;
    g = 255;
    b = (hue - 85) * 6;
  } else if (hue < 170) {
    r = 0;
    g = 255 - (hue - 128) * 6;
    b = 255;
  } else if (hue < 213) {
    r = (hue - 170) * 6;
    g = 0;
    b = 255;
  } else {
    r = 255;
    g = 0;
    b = 255 - (hue - 213) * 6;
  }
}

// One software PWM cycle: 256 steps, each step STEP_US long. Active-low.
void pwmCycle(uint8_t r, uint8_t g, uint8_t b) {
  for (int step = 0; step < PWM_STEPS; step++) {
    digitalWrite(PIN_LEDR, (r > step) ? LOW : HIGH);
    digitalWrite(PIN_LEDG, (g > step) ? LOW : HIGH);
    digitalWrite(PIN_LEDB, (b > step) ? LOW : HIGH);
    delayMicroseconds(STEP_US);
  }
}

// Show this RGB for DISPLAY_MS using software PWM (no analogWrite).
void setRGBFor(uint8_t r, uint8_t g, uint8_t b) {
  unsigned long start = millis();
  while (millis() - start < (unsigned long)DISPLAY_MS) {
    pwmCycle(r, g, b);
  }
}

void setup() {
  pinMode(PIN_LEDR, OUTPUT);
  pinMode(PIN_LEDG, OUTPUT);
  pinMode(PIN_LEDB, OUTPUT);
  digitalWrite(PIN_LEDR, HIGH);
  digitalWrite(PIN_LEDG, HIGH);
  digitalWrite(PIN_LEDB, HIGH);
}

void loop() {
  for (int hue = 0; hue < 256; hue += HUE_STEP) {
    uint8_t r, g, b;
    hueToRGB((uint8_t)hue, r, g, b);
    setRGBFor(r, g, b);
  }
}
