#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <SparkFun_Qwiic_OLED.h>
#include <SparkFun_ATECCX08a_Arduino_Library.h>

ATECCX08A atecc;
QwiicNarrowOLED myOLED;

const char* WIFI_SSID = "HackUMass";
const char* WIFI_PASSWORD = "HackUMass26!";
const char* API_BASE = "https://noheartnocart.biz";

String companyName = "Waiting...";
String priceText = "";

void initializeATECC() {
  Serial.println("Checking ATECC on Wire...");

  Wire.setBufferSize(256);
  Wire.begin();
  Wire.setClock(100000);
  delay(500);

  bool found = false;

  for (int i = 0; i < 5 && !found; i++) {
    found = atecc.begin();

    if (!found) {
      Serial.println("ATECC not responding, retrying...");
      delay(300);
    }
  }

  if (!found) {
    Serial.println("ATECC not found. Check its wiring.");
    while (true) {
      delay(1000);
    }
  }

  Serial.println("ATECC detected");
}

void initializeOLED() {
  Wire1.begin();

  if (!myOLED.begin(Wire1, 0x3C)) {
    Serial.println("OLED init failed");

    while (true) {
      delay(1000);
    }
  }

  Serial.println("OLED detected");
}

void updateDisplay() {
  myOLED.erase();
  myOLED.display();
  delay(50);

  myOLED.setCursor(0, 0);
  myOLED.print(companyName);

  myOLED.setCursor(0, 16);
  myOLED.print(priceText);

  myOLED.display();
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to WiFi");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi connected");
}

void fetchLatestPayment() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected");
    return;
  }

  HTTPClient http;
  String url = String(API_BASE) + "/api/payments?limit=1";

  if (!http.begin(url)) {
    Serial.println("Could not initialize HTTP request");
    return;
  }

  int httpCode = http.GET();

  if (httpCode == 200) {
    String payload = http.getString();
    Serial.println(payload);

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, payload);

    if (error) {
      Serial.print("JSON parse failed: ");
      Serial.println(error.c_str());
    } else if (doc.is<JsonArray>() && doc.size() > 0) {
      JsonObject payment = doc[0].as<JsonObject>();

      companyName = payment["provider"] | "Unknown";

      float amount = payment["amount"] | 0.0f;
      priceText = "$" + String(amount, 2);
    } else {
      companyName = "No payments";
      priceText = "";
    }
  } else {
    Serial.printf("HTTP error: %d\n", httpCode);
  }

  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  initializeATECC();
  initializeOLED();

  companyName = "Connecting...";
  priceText = "";
  updateDisplay();

  connectWiFi();
  fetchLatestPayment();
  updateDisplay();
}

void loop() {
  static unsigned long lastFetch = 0;

  if (millis() - lastFetch >= 3000) {
    lastFetch = millis();

    fetchLatestPayment();
    updateDisplay();
  }
}