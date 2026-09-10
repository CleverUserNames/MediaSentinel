# MediaSentinel TV — Android TV App

Android TV app for the NVIDIA Shield (and other Android TV devices) that displays
the MediaSentinel monitoring report with full D-pad navigation.

The app is a thin WebView wrapper around the HTML report served by the MediaSentinel
HTTP server. No data is stored on the device — it loads live from your server every
time you open it or press Y to refresh.

---

## Prerequisites

Before installing the app you need:

- **MediaSentinel server running** — the Windows-side Python stack with the HTTP
  server listening on port 8765 (set up via `setup_scheduler.ps1`)
- **Android Studio** (free) — https://developer.android.com/studio
- **NVIDIA Shield** or other Android TV device with Developer Mode enabled

---

## Step 1 — Enable Developer Mode on the Shield

1. Shield home → Settings → Device Preferences → About
2. Scroll to **Build** and click it **7 times** until you see
   "You are now a developer"
3. Go back to Device Preferences → **Developer Options**
4. Turn on **USB Debugging**
5. Turn on **Network Debugging** (enables ADB over Wi-Fi)

---

## Step 2 — Open the project in Android Studio

1. Open Android Studio
2. **File → Open** → select the `android-app` folder (the one containing `build.gradle`)
3. Wait for Gradle sync to complete — it downloads dependencies automatically
   (first sync takes a few minutes)

---

## Step 3 — Connect Android Studio to the Shield

**Option A — Wireless (recommended, no cable needed)**

1. Find your Shield's IP: Settings → Device Preferences → Network & internet
2. Open a terminal in Android Studio (**View → Tool Windows → Terminal**) and run:
   ```
   adb connect YOUR_SHIELD_IP:5555
   ```
3. The Shield will show a popup asking to allow the connection — select **Always allow**
   and click OK

> **If `adb` is not recognised** the Android SDK platform-tools aren't on your PATH.
> Use the full path instead — replace `YOUR_USERNAME` and `YOUR_SHIELD_IP`:
> ```
> & "C:\Users\YOUR_USERNAME\AppData\Local\Android\Sdk\platform-tools\adb.exe" connect YOUR_SHIELD_IP:5555
> ```
> You can find your exact SDK path in Android Studio under
> **File → Project Structure → SDK Location**.

**Option B — USB**

1. Connect Shield to your PC with a USB cable
2. Shield will prompt to allow debugging — click OK

---

## Step 4 — Build and install

1. In Android Studio: **Run → Run 'app'**
2. Select your Shield from the device list
3. Click OK — Android Studio builds and installs the app automatically

The app appears in the Shield's app drawer as **MediaSentinel**.

---

## Step 5 — First launch setup

When you open the app for the first time it will show a **Connection Error**
screen because it doesn't know your server's IP address yet.

1. Navigate to the **⚙ Settings** button in the top-right corner of the app
   (use the D-pad: RIGHT from the tab bar to reach it, then OK to open)
2. Fill in both fields:

   | Field | What to enter |
   |-------|---------------|
   | **MediaSentinel Server URL** | `http://YOUR_SERVER_IP:8765` |
   | **Jellyseer URL** | `http://YOUR_SERVER_IP:5055` |

   Replace `YOUR_SERVER_IP` with the LAN IP of your Windows media server
   (the same IP used in `config.json`).

3. Press **Save**
4. The report loads automatically

These URLs are saved on the device and remembered across restarts. If your
server IP ever changes, come back to Settings to update them.

---

## D-pad navigation

| Button | Action |
|--------|--------|
| LEFT / RIGHT | Move between section tabs and header buttons |
| DOWN (from header) | Enter the report content |
| UP (from content) | Return to the header tab bar |
| OK / Center | Activate focused tab, button, or link |
| BACK | Return to header tab bar from content |
| Y button | Refresh the report from server |
| Page Up / L1 | Fast scroll up |
| Page Down / R1 | Fast scroll down |

**Two modes:**
- **Header mode** (default) — LEFT/RIGHT navigates tabs and the refresh/settings buttons
- **Content mode** — DOWN/UP moves focus between cards and links inside the report

When you tap a "Request in Jellyseer" button on a recommendation card,
the app opens a Jellyseer overlay on top of the report. Press BACK to
dismiss it and return to the report.

---

## Report sections

| Tab | Contents |
|-----|----------|
| **Issues** | AI-identified problems — CRITICAL / WARNING / INFO with fix steps |
| **Transcodes** | Active transcode sessions with source vs stream details |
| **Seerr** | Jellyseer requests not yet in your library |
| **Picks** | AI-curated recommendations with TMDB posters and request buttons |
| **Drives** | SMART health for all monitored drives |
| **Tips** | Proactive suggestions from the AI |

---

## Building a standalone APK (for sideloading without Android Studio)

After your first build in Android Studio:

1. **Build → Build Bundle(s) / APK(s) → Build APK(s)**
2. The APK is written to:
   ```
   app/build/outputs/apk/debug/app-debug.apk
   ```

You can install this APK on any Android TV device without Android Studio:
```
adb connect YOUR_SHIELD_IP:5555
adb install app-debug.apk
```
Or copy it to a USB drive, plug into the Shield, and install with a file manager app.

---

## Troubleshooting

**"Connection Error" on launch**
- Confirm the MediaSentinel HTTP server is running on your Windows machine
  (`setup_scheduler.ps1` creates a Task Scheduler task that starts it at boot)
- Confirm port 8765 is not blocked by Windows Firewall
- Double-check the URL in Settings matches your server IP exactly

**App icon shows as grey square on Shield home screen**
- Uninstall the app and reinstall — Shield caches the icon aggressively
- The banner image (`drawable-xhdpi/banner.png`) is the 320×180px image used
  on the home screen; the square mipmap icons are used in the app drawer

**Gradle sync fails in Android Studio**
- Make sure Android Studio is up to date
- File → Invalidate Caches → Invalidate and Restart, then try syncing again

**ADB connection refused**
- Confirm Network Debugging is enabled in Shield Developer Options
- Try `adb kill-server` then `adb connect YOUR_SHIELD_IP:5555` again
