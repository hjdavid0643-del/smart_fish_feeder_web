#include <WiFi.h>
#include <WebServer.h>

const char* ssid = "YOUR_WIFI_NAME";
const char* password = "YOUR_WIFI_PASSWORD";

int mosfetPin = 5;  // GPIO for MOSFET Gate

WebServer server(80);

void on() {
  digitalWrite(mosfetPin, HIGH);
  server.send(200, "text/plain", "MOSFET ON");
}

void off() {
  digitalWrite(mosfetPin, LOW);
  server.send(200, "text/plain", "MOSFET OFF");
}

void setup() {
  Serial.begin(115200);

  pinMode(mosfetPin, OUTPUT);
  digitalWrite(mosfetPin, LOW);

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");

  while(WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(500);
  }

  Serial.println("\nConnected");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());

  server.on("/on", on);
  server.on("/off", off);

  server.begin();
  Serial.println("Server started");
}

void loop() {
  server.handleClient();
}
