OBD Web Emulator 

Files:
- obd_web_emulator.py
- web/index.html

Run:
  python3 obd_web_emulator.py

Open in browser:
  http://localhost:8080

ESP32 example (Arduino-style WiFi client):
---------------------------------------------------
#include <WiFi.h>
WiFiClient client;

void setup() {
  Serial.begin(115200);
  WiFi.begin("YOUR_SSID","YOUR_PASS");
  while (WiFi.status()!=WL_CONNECTED) { delay(200); Serial.print("."); }
  Serial.println("WiFi connected");
  if (client.connect("YOUR_MAC_IP", 35000)) {
    Serial.println("Connected to emulator");
    client.print("ATZ\r");
    delay(100);
    client.print("010C\r"); // request RPM
    delay(100);
    while (client.available()) { Serial.write(client.read()); }
  }
}

void loop(){ /* poll or parse as needed */ }

---------------------------------------------------
Notes:
- Export trip CSV: press Export CSV on the web UI (downloads trip_log.csv)
- Adds extra PIDs: MAF(0110), AFR proxy (012F), Boost (015C, custom)
- Random Drive toggles server-side animation; server continues animating even if browser disconnects.
