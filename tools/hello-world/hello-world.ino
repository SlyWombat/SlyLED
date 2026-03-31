/*
 * hello-world.ino — Minimal test sketch for ESP32 and D1 Mini.
 *
 * Blinks the onboard LED and prints to Serial.
 * Use this to flash a "clean" board before testing OTA updates
 * back to the SlyLED firmware.
 *
 * Compile:
 *   ESP32:   arduino-cli compile --fqbn esp32:esp32:esp32 tools/hello-world
 *   D1 Mini: arduino-cli compile --fqbn esp8266:esp8266:d1_mini tools/hello-world
 *
 * Upload:
 *   arduino-cli upload --port COMx --fqbn <fqbn> tools/hello-world
 */

#if defined(ESP32)
  #define LED_PIN 2
  #define BOARD_NAME "ESP32"
#elif defined(ESP8266)
  #define LED_PIN LED_BUILTIN  // GPIO2 on D1 Mini (active LOW)
  #define BOARD_NAME "D1 Mini"
#else
  #error "Unsupported board"
#endif

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(LED_PIN, OUTPUT);
  Serial.println();
  Serial.println("=========================");
  Serial.println("  Hello World - SlyLED");
  Serial.print("  Board: ");
  Serial.println(BOARD_NAME);
  Serial.println("  This is a clean test sketch.");
  Serial.println("  Flash SlyLED firmware via USB");
  Serial.println("  or OTA to replace this.");
  Serial.println("=========================");
  Serial.println();
}

void loop() {
  digitalWrite(LED_PIN, HIGH);
  Serial.print("[");
  Serial.print(BOARD_NAME);
  Serial.print("] alive — uptime: ");
  Serial.print(millis() / 1000);
  Serial.println("s");
  delay(500);
  digitalWrite(LED_PIN, LOW);
  delay(500);
}
