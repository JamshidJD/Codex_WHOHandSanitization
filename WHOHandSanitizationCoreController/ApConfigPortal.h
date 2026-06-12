#ifndef AP_CONFIG_PORTAL_H
#define AP_CONFIG_PORTAL_H

#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>

static const char* AP_PASSWORD = "9876512345";
static const char* DEFAULT_ADMIN_PASSWORD = "admin";
static const IPAddress AP_IP(192, 168, 0, 1);
static const IPAddress AP_GATEWAY(192, 168, 0, 1);
static const IPAddress AP_SUBNET(255, 255, 255, 0);

static WebServer apPortalServer(80);
static Preferences apPortalPrefs;
static String* apPortalStaSsid = nullptr;
static String* apPortalStaPassword = nullptr;
static String* apPortalServerHost = nullptr;
static String apPortalAdminPassword = DEFAULT_ADMIN_PASSWORD;
static bool apPortalLoggedIn = false;

String apPortalHtmlPage(const String& body) {
  return String("<!doctype html><html><head><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    + "<title>ESP32 Setup</title><style>"
    + "body{font-family:Arial,sans-serif;margin:0;background:#f5f7fb;color:#172033}"
    + "main{max-width:420px;margin:48px auto;padding:24px;background:#fff;border:1px solid #d9e0ea;border-radius:8px}"
    + "h1{font-size:22px;margin:0 0 18px}label{display:block;margin:14px 0 6px;font-weight:600}"
    + "input{box-sizing:border-box;width:100%;padding:10px;border:1px solid #b8c2d1;border-radius:6px;font-size:16px}"
    + "button{margin-top:18px;width:100%;padding:11px;border:0;border-radius:6px;background:#1769e0;color:#fff;font-size:16px;font-weight:700}"
    + ".msg{padding:10px 12px;background:#edf7ed;border:1px solid #b7dfb9;border-radius:6px;margin-bottom:14px}"
    + "</style></head><body><main>" + body + "</main></body></html>";
}

void apPortalSendLogin(const String& message = "") {
  String body;
  if (message.length() > 0) {
    body += "<div class=\"msg\">" + message + "</div>";
  }
  body += "<h1>Login</h1><form method=\"POST\" action=\"/login\">"
          "<label>Password</label><input name=\"password\" type=\"password\" required>"
          "<button type=\"submit\">Login</button></form>";
  apPortalServer.send(200, "text/html", apPortalHtmlPage(body));
}

void apPortalSendSettings(const String& message = "") {
  if (!apPortalLoggedIn) {
    apPortalServer.sendHeader("Location", "/", true);
    apPortalServer.send(302, "text/plain", "");
    return;
  }

  String body;
  if (message.length() > 0) {
    body += "<div class=\"msg\">" + message + "</div>";
  }
  body += "<h1>WiFi Settings</h1><form method=\"POST\" action=\"/save\">"
          "<label>Admin Password</label><input name=\"admin\" type=\"password\" value=\"";
  body += apPortalAdminPassword;
  body += "\"><label>SSID</label><input name=\"ssid\" type=\"text\" value=\"";
  body += apPortalStaSsid != nullptr ? *apPortalStaSsid : "";
  body += "\" required><label>WEP</label><input name=\"password\" type=\"password\" value=\"";
  body += apPortalStaPassword != nullptr ? *apPortalStaPassword : "";
  body += "\" required><label>Server Host</label><input name=\"server\" type=\"text\" value=\"";
  body += apPortalServerHost != nullptr ? *apPortalServerHost : "";
  body += "\" required><button type=\"submit\">Save</button></form>";
  apPortalServer.send(200, "text/html", apPortalHtmlPage(body));
}

String apPortalAccessPointSsid() {
  String mac = WiFi.macAddress();
  mac.replace(":", "");
  if (mac.length() <= 6) {
    return mac;
  }
  return mac.substring(mac.length() - 6);
}

void apPortalLoadSettings(String& staSsid, String& staPassword, String& serverHost) {
  apPortalPrefs.begin("apcfg", false);
  staSsid = apPortalPrefs.getString("ssid", staSsid);
  staPassword = apPortalPrefs.getString("pass", staPassword);
  serverHost = apPortalPrefs.getString("server", serverHost);
  apPortalAdminPassword = apPortalPrefs.getString("admin", DEFAULT_ADMIN_PASSWORD);
}

void apPortalBegin(String& staSsid, String& staPassword, String& serverHost) {
  apPortalStaSsid = &staSsid;
  apPortalStaPassword = &staPassword;
  apPortalServerHost = &serverHost;
  apPortalLoadSettings(staSsid, staPassword, serverHost);

  WiFi.mode(WIFI_AP_STA);
  WiFi.softAPConfig(AP_IP, AP_GATEWAY, AP_SUBNET);
  WiFi.softAP(apPortalAccessPointSsid().c_str(), AP_PASSWORD);

  apPortalServer.on("/", HTTP_GET, []() {
    if (apPortalLoggedIn) {
      apPortalSendSettings();
    } else {
      apPortalSendLogin();
    }
  });

  apPortalServer.on("/login", HTTP_POST, []() {
    if (apPortalServer.arg("password") == apPortalAdminPassword) {
      apPortalLoggedIn = true;
      apPortalSendSettings();
    } else {
      apPortalSendLogin("Invalid password");
    }
  });

  apPortalServer.on("/save", HTTP_POST, []() {
    if (!apPortalLoggedIn) {
      apPortalServer.sendHeader("Location", "/", true);
      apPortalServer.send(302, "text/plain", "");
      return;
    }

    String newAdmin = apPortalServer.arg("admin");
    String newSsid = apPortalServer.arg("ssid");
    String newPassword = apPortalServer.arg("password");
    String newServerHost = apPortalServer.arg("server");
    newAdmin.trim();
    newSsid.trim();
    newPassword.trim();
    newServerHost.trim();

    if (newAdmin.length() == 0 || newSsid.length() == 0 || newPassword.length() == 0 || newServerHost.length() == 0) {
      apPortalSendSettings("All fields are required");
      return;
    }

    apPortalPrefs.putString("admin", newAdmin);
    apPortalPrefs.putString("ssid", newSsid);
    apPortalPrefs.putString("pass", newPassword);
    apPortalPrefs.putString("server", newServerHost);
    apPortalAdminPassword = newAdmin;
    *apPortalStaSsid = newSsid;
    *apPortalStaPassword = newPassword;
    *apPortalServerHost = newServerHost;
    apPortalSendSettings("Settings saved. They will be effective on next restart.");
  });

  apPortalServer.begin();
}

void apPortalHandleClient() {
  apPortalServer.handleClient();
}

#endif
