#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <SparkFun_Qwiic_OLED.h>

QwiicNarrowOLED myOLED;

// ====== EDIT THESE ======
const char* WIFI_SSID     = "HackUMass";
const char* WIFI_PASSWORD = "HackUMass26!";
const char* API_BASE = "https://noheartnocart.biz";
// ========================

String companyName = "Waiting...";
String priceText   = "";

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
}

void fetchLatestPayment() {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  String url = String(API_BASE) + "/api/payments?limit=1";
  http.begin(url);

  int httpCode = http.GET();

  if (httpCode == 200) {
    String payload = http.getString();
    Serial.println(payload);

    JsonDocument doc;
    if (!deserializeJson(doc, payload)) {
      if (doc.is<JsonArray>() && doc.size() > 0) {
        JsonObject p = doc[0];
        companyName = p["provider"] | "Unknown";
        float amount = p["amount"] | 0.0f;
        priceText = "$" + String(amount, 2);
      }
    }
  } else {
    Serial.printf("HTTP error: %d\n", httpCode);
  }
  http.end();
}

void updateDisplay() {
  myOLED.erase();
  myOLED.setCursor(0, 0);
  myOLED.print(companyName);
  myOLED.setCursor(0, 16);
  myOLED.print(priceText);
  myOLED.display();
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire1.begin();   // I2C2
  if (!myOLED.begin(Wire1, 0x3C)) {
    Serial.println("OLED init failed");
    while (1);
  }

  connectWiFi();
  fetchLatestPayment();
  updateDisplay();
}

void loop() {
  static unsigned long lastFetch = 0;
  if (millis() - lastFetch > 3000) {
    lastFetch = millis();
    fetchLatestPayment();
    updateDisplay();
  }
}