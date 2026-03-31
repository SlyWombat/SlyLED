/*
 * SlyLED — QuinLED Dig-Uno (ESP32)
 * Simple test sketch: WiFi web UI to turn a small rainbow strip on/off.
 *
 * - Board: QuinLED Dig-Uno (ESP32-based)
 * - Data pin: GPIO2
 * - LEDs: 8 (WS2812 / NeoPixel-style)
 * - Hostname: slyled
 *
 * API (minimal, for testing):
 *   GET  /        — HTML page with buttons
 *   GET  /status  — JSON { "on": true|false }
 *   POST /led/on  — turn rainbow on
 *   POST /led/off — turn rainbow off
 *
 * WiFi credentials are taken from main/arduino_secrets.h (not tracked in git).
 */

#include <WiFi.h>
#include <WebServer.h>
#include <FastLED.h>

#include "main/arduino_secrets.h"

// ── LED strip config ───────────────────────────────────────────────────────────

constexpr uint8_t DATA_PIN  = 2;     // QuinLED Dig-Uno data pin for testing
constexpr uint16_t NUM_LEDS = 8;    // Small test strip

CRGB leds[NUM_LEDS];

// ── WiFi & server ─────────────────────────────────────────────────────────────

constexpr char HOSTNAME[] = "slyled";

WebServer server(80);
bool rainbowOn = true;

// ── LED pattern ───────────────────────────────────────────────────────────────

void updateLeds() {
  static uint8_t baseHue = 0;

  if (rainbowOn) {
    fill_rainbow(leds, NUM_LEDS, baseHue, 255 / NUM_LEDS);
    baseHue++;
  } else {
    fill_solid(leds, NUM_LEDS, CRGB::Black);
  }

  FastLED.show();
}

// ── Web handlers ──────────────────────────────────────────────────────────────

void handleStatus() {
  server.send(200, "application/json", rainbowOn ? "{\"on\":true}" : "{\"on\":false}");
}

void handleOn() {
  rainbowOn = true;
  handleStatus();
}

void handleOff() {
  rainbowOn = false;
  handleStatus();
}

void handleRoot() {
  String html;
  html.reserve(2048);
  html += F(
    "<!DOCTYPE html><html><head>"
    "<meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>SlyLED - QuinLED</title>"
    "<style>"
    "body{font-family:sans-serif;background:#111;color:#eee;text-align:center;padding:2em;margin:0}"
    "h1{font-size:2em;margin-bottom:.2em}"
    "p{color:#aaa;margin:.5em 0 1.5em}"
    ".btn{display:inline-block;margin:.4em;padding:.7em 1.8em;border-radius:999px;"
    "border:none;cursor:pointer;font-size:1em;font-weight:bold;font-family:inherit}"
    ".on{background:#2a2;color:#fff}"
    ".off{background:#a22;color:#fff}"
    "</style>"
    "</head><body>"
    "<h1>SlyLED</h1>"
    "<p>QuinLED Dig-Uno (ESP32) — test strip on GPIO2, 8 LEDs.</p>"
    "<button class='btn on' onclick='setOn()'>Rainbow ON</button>"
    "<button class='btn off' onclick='setOff()'>Rainbow OFF</button>"
    "<p id='status'>Status: ...</p>"
    "<script>"
    "function upd(){fetch('/status').then(r=>r.json()).then(d=>{"
    "document.getElementById('status').textContent='Status: '+(d.on?'ON':'OFF');"
    "}).catch(()=>{});}"
    "function setOn(){fetch('/led/on',{method:'POST'}).then(upd);}"
    "function setOff(){fetch('/led/off',{method:'POST'}).then(upd);}"
    "upd();setInterval(upd,3000);"
    "</script>"
    "</body></html>"
  );
  server.send(200, "text/html", html);
}

void handleNotFound() {
  server.send(404, "text/plain", "Not found");
}

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);

  FastLED.addLeds<NEOPIXEL, DATA_PIN>(leds, NUM_LEDS);
  FastLED.clear(true);

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.begin(SECRET_SSID, SECRET_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();
  Serial.print("Connected, IP: ");
  Serial.println(WiFi.localIP());

  server.on("/", HTTP_GET, handleRoot);
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/led/on", HTTP_POST, handleOn);
  server.on("/led/off", HTTP_POST, handleOff);
  server.onNotFound(handleNotFound);
  server.begin();
}

void loop() {
  server.handleClient();
  updateLeds();
  delay(10);
}

