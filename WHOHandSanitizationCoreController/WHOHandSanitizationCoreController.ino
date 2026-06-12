// Date: 22 may 2026
// Hand Sanitizer Project
// Added:
// 1. UART transmission to secondary MCU
// 2. RGB LED status indication
//Huge App
//ESP32 DevModule
#include <WiFi.h>
#include <HTTPClient.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <map>
#include <vector>
#include <algorithm>
#include "ApConfigPortal.h"

// ================= WIFI =================
String ssid = "ST";
String password = "9876512345";
String serverHost = "192.168.10.195";
const char* serverPath = "/api/sanitization_log";
const uint16_t serverPort = 5000;

String espMac;

// ================= ULTRASONIC =================
#define RX_PIN 15
#define TX_PIN 14

#define DETECT_DISTANCE   250
#define STABLE_REQUIRED     2

// ================= PUMP =================
#define PUMP_PIN        13
#define PUMP_DURATION   1000

// ================= RGB LED =================
#define RED_PIN     32
#define GREEN_PIN   33
#define BLUE_PIN    34

// ================= MCU SERIAL =================
#define MCU_RX_PIN  16
#define MCU_TX_PIN  17

#define START_SYMBOL '<'
#define END_SYMBOL   '>'

// ================= BLE CONFIG =================
#define RSSI_THRESHOLD   -65
#define EMA_ALPHA         0.5f
#define TAG_STALE_MS     10000
#define TAG_LOCK_TIMEOUT 30000

// ================= HTTP RETRY =================
#define HTTP_MAX_RETRIES  3
#define HTTP_RETRY_DELAY  2000

// ================= HTTP STATE MACHINE =================
enum HTTPState {
  HTTP_IDLE,
  HTTP_SEND,
  HTTP_WAIT_RETRY
};

HTTPState     httpState      = HTTP_IDLE;
int           httpAttempt    = 0;
unsigned long httpRetryAt    = 0;
String        pendingPayload = "";

// =====================================================
//                     TAG STRUCT
// =====================================================
struct TagData {
  float ema = 0;
  int rssi = 0;
  bool initialized = false;
  unsigned long lastSeen = 0;
};

std::map<String, TagData> tags;

BLEScan* pBLEScan;

// =====================================================
//                     STATE
// =====================================================
bool systemReady      = false;
bool objectPresent    = false;
bool pumpActive       = false;
bool alreadySanitized = false;

unsigned long startTime = 0;
unsigned long endTime   = 0;
unsigned long pumpStart = 0;

int stableCount = 0;

String lockedTag = "none";
bool tagLocked   = false;

// =====================================================
//                    RGB LED
// =====================================================
void setLED(bool r, bool g, bool b) {
  digitalWrite(RED_PIN, r);
  digitalWrite(GREEN_PIN, g);
  digitalWrite(BLUE_PIN, b);
}

void setReadyLED() {
  // GREEN
  setLED(LOW, HIGH, LOW);
}

void setBusyLED() {
  // RED
  setLED(HIGH, LOW, LOW);
}

void setIdleLED() {
  // BLUE
  setLED(LOW, LOW, HIGH);
}

// =====================================================
//               BLE CALLBACKS
// =====================================================
class MyAdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {

  void onResult(BLEAdvertisedDevice advertisedDevice) {

    String tag = advertisedDevice.getAddress().toString().c_str();
    int rssi = advertisedDevice.getRSSI();

    TagData &t = tags[tag];

    if (!t.initialized) {
      t.ema = (float)rssi;
      t.initialized = true;
    } else {
      t.ema = EMA_ALPHA * rssi + (1.0f - EMA_ALPHA) * t.ema;
    }

    t.rssi = rssi;
    t.lastSeen = millis();
  }
};

// =====================================================
//        PURGE STALE BLE ENTRIES
// =====================================================
void purgeStaleTagsSafe() {

  unsigned long now = millis();

  for (auto it = tags.begin(); it != tags.end();) {

    if (now - it->second.lastSeen > TAG_STALE_MS) {
      it = tags.erase(it);
    } else {
      ++it;
    }
  }
}

// =====================================================
//               NEAREST TAG LOGIC
// =====================================================
String getNearestTag() {

  if (tagLocked) {

    if (millis() - startTime > TAG_LOCK_TIMEOUT) {

      Serial.println("[WARN] Tag lock timeout");

      tagLocked = false;
      lockedTag = "none";

    } else {
      return lockedTag;
    }
  }

  String bestTag = "none";
  float bestScore = -999.0f;

  unsigned long now = millis();

  for (auto &it : tags) {

    const TagData &t = it.second;

    if (now - t.lastSeen > 2000) continue;

    float score = (t.ema * 0.7f) + (t.rssi * 0.3f);

    if (score > bestScore) {
      bestScore = score;
      bestTag = it.first;
    }
  }

  Serial.print("[BLE] Nearest tag: ");
  Serial.println(bestTag);

  return bestTag;
}

// =====================================================
//             ULTRASONIC (UART)
// =====================================================
void flushUART() {

  unsigned long deadline = millis() + 20;

  while (Serial1.available() && millis() < deadline) {
    Serial1.read();
  }
}

int readDistance() {

  unsigned long deadline = millis() + 30;

  while (millis() < deadline) {

    if (Serial1.available() < 4) break;

    if (Serial1.read() != 0xFF) continue;

    unsigned long byteDeadline = millis() + 10;

    while (Serial1.available() < 3 && millis() < byteDeadline) {}

    if (Serial1.available() < 3) return -1;

    int h = Serial1.read();
    int l = Serial1.read();
    int c = Serial1.read();

    int d = (h << 8) + l;

    if (((0xFF + h + l) & 0xFF) != c) continue;

    if (d <= 0 || d > 5000 || d < 50) continue;

    return d;
  }

  return -1;
}

int getDistance() {

  const int SAMPLES = 3;
  const int TIME_CAP = 100;

  flushUART();

  int vals[SAMPLES];
  int count = 0;

  unsigned long deadline = millis() + TIME_CAP;

  while (count < SAMPLES && millis() < deadline) {

    int d = readDistance();

    if (d > 0) vals[count++] = d;
  }

  if (count == 0) return -1;

  for (int i = 1; i < count; i++) {

    int key = vals[i];
    int j = i - 1;

    while (j >= 0 && vals[j] > key) {
      vals[j + 1] = vals[j];
      j--;
    }

    vals[j + 1] = key;
  }

  return vals[count / 2];
}

// =====================================================
//                 PUMP CONTROL
// =====================================================
void pumpOn() {

  digitalWrite(PUMP_PIN, LOW);

  pumpActive = true;
  pumpStart = millis();

  Serial.println("[PUMP] ON");
}

void pumpOff() {

  digitalWrite(PUMP_PIN, HIGH);

  pumpActive = false;

  Serial.println("[PUMP] OFF");
}

// =====================================================
//          SEND BLE TAG TO OTHER MCU
// =====================================================
void sendTagToMCU(String bleTag, String espMAC) {

  // Format:
  // <DATA,BLE_TAG,ESP32_MAC>

  String packet =
    String(START_SYMBOL) +
    "DATA," +
    bleTag + "," +
    espMAC +
    String(END_SYMBOL);

  Serial2.println(packet);
  Serial2.flush();

  Serial.println("[MCU] Packet sent:");
  Serial.println(packet);
}

// =====================================================
//                 SEND DATA
// =====================================================
void sendData() {

  if (httpState != HTTP_IDLE) {

    Serial.println("[HTTP] Previous send in progress");
    return;
  }

  unsigned long duration = endTime - startTime;

  pendingPayload  = "{";
  pendingPayload += "\"doctorRFIDTag\":\"" + lockedTag + "\",";
  pendingPayload += "\"sanitizerMAC\":\"" + espMac + "\",";
  pendingPayload += "\"duration\":" + String(duration / 1000);
  pendingPayload += "}";

  Serial.println("------ API PAYLOAD ------");
  Serial.println(pendingPayload);
  Serial.println("-------------------------");

  httpAttempt = 0;

  httpState = HTTP_SEND;
}

// =====================================================
//                 HTTP HANDLER
// =====================================================
void handleHTTP() {

  if (httpState == HTTP_IDLE) return;

  if (httpState == HTTP_WAIT_RETRY) {

    if (millis() >= httpRetryAt) {
      httpState = HTTP_SEND;
    }

    return;
  }

  if (httpState == HTTP_SEND) {

    httpAttempt++;

    Serial.print("[HTTP] Attempt ");
    Serial.println(httpAttempt);

    if (WiFi.status() != WL_CONNECTED) {

      Serial.println("[HTTP] WiFi disconnected");

      setIdleLED();

      if (httpAttempt >= HTTP_MAX_RETRIES) {

        Serial.println("[HTTP] Failed");

        setReadyLED();

        httpState = HTTP_IDLE;

      } else {

        httpRetryAt = millis() + HTTP_RETRY_DELAY;
        httpState = HTTP_WAIT_RETRY;
      }

      return;
    }

    HTTPClient http;

    String serverURL = String("http://") + serverHost + ":" + String(serverPort) + serverPath;
    http.begin(serverURL);

    http.addHeader("Content-Type", "application/json");

    http.setTimeout(4000);

    int code = http.POST(pendingPayload);

    http.end();

    Serial.print("[HTTP] Response: ");
    Serial.println(code);

    if (code >= 200 && code < 300) {

      Serial.println("[HTTP] Success");

    sendTagToMCU(lockedTag, espMac);

      tagLocked = false;
      lockedTag = "none";

      setReadyLED();

      httpState = HTTP_IDLE;

    } else if (httpAttempt >= HTTP_MAX_RETRIES) {

      Serial.println("[HTTP] All retries failed");

      setReadyLED();

      httpState = HTTP_IDLE;

    } else {

      Serial.println("[HTTP] Retry scheduled");

      httpRetryAt = millis() + HTTP_RETRY_DELAY;

      httpState = HTTP_WAIT_RETRY;
    }
  }
}

// =====================================================
//            SENSOR LOGIC ENGINE
// =====================================================
void handleSensor(int d) {

  if (!systemReady) return;

  // DETECTION
  if (d > 0 && d <= DETECT_DISTANCE) {

    stableCount++;

    if (!objectPresent && stableCount >= STABLE_REQUIRED) {

      objectPresent = true;

      alreadySanitized = false;

      startTime = millis();

      lockedTag = getNearestTag();

      tagLocked = true;

      setBusyLED();

      Serial.print("[SENSOR] Object detected. Locked tag: ");
      Serial.println(lockedTag);
    }

  } else {

    stableCount = 0;
  }

  // PUMP
  if (objectPresent && !pumpActive && !alreadySanitized) {

    pumpOn();

    alreadySanitized = true;
  }

  // REMOVAL
  static unsigned long removeDetectStart = 0;

  if (objectPresent && d > DETECT_DISTANCE) {

    if (removeDetectStart == 0) {
      removeDetectStart = millis();
    }

    if (millis() - removeDetectStart > 200) {

      objectPresent = false;

      stableCount = 0;

      endTime = millis();

      removeDetectStart = 0;

      sendData();
    }

  } else {

    removeDetectStart = 0;
  }

  // SAFETY UNLOCK
  if (tagLocked && (millis() - startTime > TAG_LOCK_TIMEOUT)) {

    Serial.println("[WARN] Safety unlock");

    tagLocked = false;
    lockedTag = "none";

    setReadyLED();
  }
}

// =====================================================
//           BLE SCAN COMPLETE CALLBACK
// =====================================================
void bleScanComplete(BLEScanResults results) {

  pBLEScan->start(1, bleScanComplete, false);
}

// =====================================================
//                      SETUP
// =====================================================
void setup() {

  Serial.begin(115200);

  // RGB LED
  pinMode(RED_PIN, OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(BLUE_PIN, OUTPUT);

  setBusyLED();

  // PUMP
  pinMode(PUMP_PIN, OUTPUT);

  digitalWrite(PUMP_PIN, HIGH);

  // MCU UART
  Serial2.begin(115200, SERIAL_8N1, MCU_RX_PIN, MCU_TX_PIN);

  // Ultrasonic UART
  Serial1.begin(9600, SERIAL_8N1, RX_PIN, TX_PIN);

  // WiFi
  apPortalBegin(ssid, password, serverHost);

  WiFi.begin(ssid.c_str(), password.c_str());

  Serial.print("[WiFi] Connecting");

  while (WiFi.status() != WL_CONNECTED) {

    apPortalHandleClient();

    delay(500);

    Serial.print(".");
  }

  Serial.println();

  espMac = WiFi.macAddress();

  Serial.print("[WiFi] Connected | MAC: ");
  Serial.println(espMac);

  // BLE
  BLEDevice::init("");

  pBLEScan = BLEDevice::getScan();

  pBLEScan->setAdvertisedDeviceCallbacks(
    new MyAdvertisedDeviceCallbacks()
  );

  pBLEScan->setActiveScan(true);

  pBLEScan->setInterval(100);

  pBLEScan->setWindow(99);

  pBLEScan->start(1, bleScanComplete, false);

  Serial.println("[SYS] Stabilising...");

  delay(2000);

  systemReady = true;

  setReadyLED();

  Serial.println("[SYS] Ready");
}

// =====================================================
//                       LOOP
// =====================================================
unsigned long lastPurge = 0;

#define PURGE_INTERVAL 15000

#define SENSOR_INTERVAL 50

unsigned long lastSensor = 0;

void loop() {

  unsigned long now = millis();

  apPortalHandleClient();

  // SENSOR
  if (now - lastSensor >= SENSOR_INTERVAL) {

    lastSensor = now;

    int distance = getDistance();

    Serial.print("[DIST] ");
    Serial.println(distance);

    handleSensor(distance);
  }

  // HTTP
  handleHTTP();

  // PUMP TIMEOUT
  if (pumpActive && (millis() - pumpStart >= PUMP_DURATION)) {

    pumpOff();
  }

  // BLE PURGE
  if (now - lastPurge > PURGE_INTERVAL) {

    purgeStaleTagsSafe();

    lastPurge = millis();

    Serial.print("[BLE] Tags after purge: ");
    Serial.println(tags.size());
  }
}
