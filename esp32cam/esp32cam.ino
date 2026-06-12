#define FlashLightPin 4
#include "esp_camera.h"
#include <WiFi.h>
#include "ApConfigPortal.h"

// =========================
// WIFI
// =========================
String ssid = "ST";
String password = "9876512345";

// =========================
// TCP SERVER
// =========================
String bleId = "bleid01";
String deviceId = "esp32cam_01";
String serverHost = "192.168.10.195";
const uint16_t serverPort = 9000;

// =========================
// RECORDING SETTINGS
// =========================
const unsigned long recordDurationMs = 15000;
const unsigned long frameIntervalMs = 100;  // 100 ms = target 10 FPS
const int targetFps = 10;
bool cameraReady = false;
bool isRecording = false;
bool stopRecordingRequested = false;
unsigned long lastSerialPacketAt = 0;
String serialPacket = "";

// =========================
// CAMERA PINS (AI THINKER)
// =========================
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27

#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

// =========================
// CAMERA INIT
// =========================
bool initCamera() {
  camera_config_t config = {};

  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;

  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;

  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;

  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;

  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_HD;
  config.jpeg_quality = 10;
  config.fb_count = 1;

  if (psramFound()) {
    config.fb_count = 2;
  }

  return esp_camera_init(&config) == ESP_OK;
}

// =========================
// TCP HELPERS
// =========================
bool writeAll(WiFiClient& client, const uint8_t* data, size_t length) {
  size_t sent = 0;
  while (sent < length) {
    size_t written = client.write(data + sent, length - sent);
    if (written == 0) {
      return false;
    }
    sent += written;
    apPortalHandleClient();
    delay(0);
  }
  return true;
}

bool writeFrameLength(WiFiClient& client, uint32_t length) {
  uint8_t header[4];
  header[0] = (length >> 24) & 0xFF;
  header[1] = (length >> 16) & 0xFF;
  header[2] = (length >> 8) & 0xFF;
  header[3] = length & 0xFF;
  return writeAll(client, header, sizeof(header));
}

bool sendStreamHeader(WiFiClient& client) {
  String metadata = String("{\"device\":\"") + deviceId + "\",\"deviceId\":\"" + deviceId + "\",\"bleId\":\"" + bleId + "\",\"fps\":" + String(targetFps) + "}\n";
  if (!client.print("ESPVID1\n")) {
    return false;
  }
  if (!client.print(metadata)) {
    return false;
  }
  return true;
}

void recordAndStream();

// =========================
// SERIAL COMMANDS
// =========================
void handleSerialPacket(String packet) {
  packet.trim();
  if (packet.length() == 0) {
    return;
  }

  int firstPipe = packet.indexOf('|');
  int secondPipe = firstPipe >= 0 ? packet.indexOf('|', firstPipe + 1) : -1;
  int thirdPipe = secondPipe >= 0 ? packet.indexOf('|', secondPipe + 1) : -1;
  int fourthPipe = thirdPipe >= 0 ? packet.indexOf('|', thirdPipe + 1) : -1;
  int fifthPipe = fourthPipe >= 0 ? packet.indexOf('|', fourthPipe + 1) : -1;
  if (firstPipe != 0 || secondPipe < 0 || thirdPipe < 0 || fourthPipe != packet.length() - 1 || fifthPipe >= 0) {
    Serial.print("Invalid serial packet: ");
    Serial.println(packet);
    return;
  }

  String command = packet.substring(firstPipe + 1, secondPipe);
  String newBleId = packet.substring(secondPipe + 1, thirdPipe);
  String newDeviceId = packet.substring(thirdPipe + 1, fourthPipe);
  command.trim();
  command.toUpperCase();
  newBleId.trim();
  newDeviceId.trim();

  if (command.length() == 0 || newBleId.length() == 0 || newDeviceId.length() == 0) {
    Serial.print("Invalid serial packet: ");
    Serial.println(packet);
    return;
  }

  if (command == "START" && isRecording) {
    lastSerialPacketAt = millis();
    return;
  }

  bleId = newBleId;
  deviceId = newDeviceId;

  lastSerialPacketAt = millis();

  if (command == "START") {
    stopRecordingRequested = false;
    if (!isRecording) {
      recordAndStream();
    }
  } else if (command == "STOP") {
    stopRecordingRequested = true;
  } else {
    Serial.print("Unknown serial command: ");
    Serial.println(command);
  }
}

void readSerialCommands() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      handleSerialPacket(serialPacket);
      serialPacket = "";
    } else {
      serialPacket += c;
      if (serialPacket.length() > 128) {
        Serial.println("Serial packet too long, clearing buffer");
        serialPacket = "";
      }
    }
  }
  apPortalHandleClient();
}

// =========================
// STREAM RECORDING
// =========================
void recordAndStream() {
  if (!cameraReady) {
    Serial.println("Cannot record: camera is not ready");
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Cannot record: WiFi is disconnected");
    return;
  }

  isRecording = true;
  stopRecordingRequested = false;
  digitalWrite(FlashLightPin, HIGH);
  WiFiClient client;
  client.setTimeout(10);

  Serial.println("Connecting to TCP video server");
  if (!client.connect(serverHost.c_str(), serverPort)) {
    Serial.println("TCP server connection failed");
    isRecording = false;
    digitalWrite(FlashLightPin, LOW);
    return;
  }

  if (!sendStreamHeader(client)) {
    Serial.println("Could not send stream header");
    client.stop();
    isRecording = false;
    digitalWrite(FlashLightPin, LOW);
    return;
  }

  Serial.println("Streaming started");

  unsigned long nextFrameAt = millis();
  int frameNo = 0;
  int droppedFrames = 0;

  while (!stopRecordingRequested && client.connected()) {
    readSerialCommands();

    if (millis() - lastSerialPacketAt >= recordDurationMs) {
      Serial.println("Serial packet timeout, stopping stream");
      break;
    }

    unsigned long now = millis();
    if ((long)(now - nextFrameAt) < 0) {
      apPortalHandleClient();
      delay(2);
      continue;
    }

    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Capture failed");
      droppedFrames++;
      nextFrameAt += frameIntervalMs;
      continue;
    }

    bool ok = writeFrameLength(client, fb->len) && writeAll(client, fb->buf, fb->len);
    esp_camera_fb_return(fb);

    if (!ok) {
      Serial.println("Frame send failed");
      break;
    }

    frameNo++;
    nextFrameAt += frameIntervalMs;
  }

  writeFrameLength(client, 0);

  unsigned long waitStart = millis();
  while (client.connected() && !client.available() && millis() - waitStart < 3000) {
    apPortalHandleClient();
    delay(10);
  }

  if (client.available()) {
    String response = client.readStringUntil('\n');
    Serial.print("Server response: ");
    Serial.println(response);
  }

  client.stop();
  isRecording = false;
  digitalWrite(FlashLightPin, LOW);
  stopRecordingRequested = false;
  Serial.printf("Streaming finished, sent=%d dropped=%d\n", frameNo, droppedFrames);
}

// =========================
// SETUP
// =========================

void setup() {
  pinMode(FlashLightPin, OUTPUT);  // Initialize GPIO 4 as an output
  digitalWrite(FlashLightPin, LOW);
  Serial.begin(115200);
  lastSerialPacketAt = millis();

  apPortalBegin(ssid, password, serverHost);

  WiFi.begin(ssid.c_str(), password.c_str());
  while (WiFi.status() != WL_CONNECTED) {
    apPortalHandleClient();
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi connected");
  Serial.print("ESP IP: ");
  Serial.println(WiFi.localIP());

  cameraReady = initCamera();
  if (!cameraReady) {
    Serial.println("Camera init failed");
    return;
  }

  Serial.println("ESP32 Ready");
}

// =========================
// LOOP
// =========================
void loop() {
  readSerialCommands();
  apPortalHandleClient();
  delay(2);
}
