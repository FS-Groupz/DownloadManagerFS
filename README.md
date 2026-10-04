# 🚀 DownloadManagerFS

A modern, high-speed, cross-platform video & audio downloader app for **Android**, **Linux**, and **Windows**. Powered by `yt-dlp` and an elegant glassmorphism web UI.

---

## ✨ Features

- ⚡ **Ultra-Fast Downloads**: Supports multi-threaded downloading with real-time speed, ETA, and progress indicators.
- 📺 **Wide Platform Support & Multi-Dimensional Quality**:
  - **YouTube** (Shorts, 1080p, 4K, 60fps)
  - **Instagram** (Reels, Posts, Stories)
  - **Facebook** (Reels & Public HD Videos)
  - **TikTok** & 1000+ other supported platforms via `yt-dlp`
  - Accurate resolution matching for both horizontal and vertical videos (360p, 480p, 720p, 1080p, Best).
- 🎵 **MP3 Audio Extraction**: Extract clean, high-bitrate MP3 audio from any video link with a single tap.
- 🐾 **PetsHeaven Sponsored Integration**: Instant background download start paired with a 5-second fullscreen showcase.
- 📱 **Native Android Integration**:
  - Official release APK with Android `DownloadManager` support.
  - Automatic download notifications and direct **"▶ Open / Play"** into default Android Video Players.
- 💻 **Desktop Ready (Linux & Windows)**:
  - Standalone single-file binary for Linux.
  - Windows `.bat` / `.exe` launcher with automatic browser launch.
- 🎨 **Premium UI**: Cyberpunk neon dark theme, responsive glassmorphism tabs, toast alerts, and history tracking.

---

## 📦 Direct Downloads & Releases

> [!TIP]
> **Mobile Users:** Tap any of the direct download links below to start downloading immediately on your phone. If Chrome shows *"File might be harmful"*, tap **"Download anyway"**.

| Platform | Direct 1-Tap Download | Fast Raw Mirror |
| :--- | :--- | :--- |
| 📱 **Android (v1.2.2 Recommended)** | [📥 **Download APK (v1.2.2)**](https://github.com/FS-Groupz/DownloadManagerFS/releases/download/v1.2.2/DownloadManagerFS-v1.2.2.apk) | [Raw Link](https://github.com/FS-Groupz/DownloadManagerFS/raw/main/releases/DownloadManagerFS-v1.2.2.apk) |
| 📱 **Android (v1.2.1)** | [📥 **Download APK (v1.2.1)**](https://github.com/FS-Groupz/DownloadManagerFS/releases/download/v1.2.1/DownloadManagerFS-v1.2.1.apk) | [Raw Link](https://github.com/FS-Groupz/DownloadManagerFS/raw/main/releases/DownloadManagerFS-v1.2.1.apk) |
| 🐧 **Linux** | [📥 **Download Linux Binary**](https://github.com/FS-Groupz/DownloadManagerFS/releases/download/v1.2.2/DownloadManagerFS-Linux) | [Raw Link](https://github.com/FS-Groupz/DownloadManagerFS/raw/main/releases/DownloadManagerFS-Linux) |
| 🪟 **Windows** | [📥 **Download Launcher (.bat)**](https://github.com/FS-Groupz/DownloadManagerFS/releases/download/v1.2.2/DownloadManagerFS-Windows.bat) | [PowerShell Script](https://github.com/FS-Groupz/DownloadManagerFS/releases/download/v1.2.2/DownloadManagerFS-Windows.ps1) |

All versions are also published under **[GitHub Releases](https://github.com/FS-Groupz/DownloadManagerFS/releases)**.

---

## 🚀 Quick Start Guide

### 📱 Android
1. Download [`releases/DownloadManagerFS-v1.2.2.apk`](releases/DownloadManagerFS-v1.2.2.apk).
2. Install the APK on your device.
3. If using in standalone Wi-Fi engine mode, ensure `server.py` is running on your PC or local server.

### 🐧 Linux
Run the standalone binary directly:
```bash
chmod +x releases/DownloadManagerFS-Linux
./releases/DownloadManagerFS-Linux
```
*The web UI will automatically open at `http://localhost:5000`.*

### 🪟 Windows
1. Double-click `releases/DownloadManagerFS-Windows.bat`.
2. It will auto-detect Python & `yt-dlp` and open `http://localhost:5000` in your default browser.

---

## 🛠️ Project Structure

```
DownloadManagerFS/
├── app/                      # Android Native Application
│   ├── src/main/assets/      # Web UI assets (HTML, CSS, JS, logo)
│   ├── src/main/java/        # Java Native Android Bridge
│   ├── build.gradle          # Android Gradle build & signing config
│   └── release.keystore      # Release signing keystore
├── assets/                   # Bundled UI assets for desktop executable
├── releases/                 # Packaged Release Binaries
│   ├── DownloadManagerFS-v1.2.2.apk
│   ├── DownloadManagerFS-Linux
│   ├── DownloadManagerFS-Windows.bat
│   ├── DownloadManagerFS-Windows.ps1
│   └── README.md
├── .github/workflows/        # CI/CD GitHub Actions
│   └── release.yml           # Cross-platform automated build workflow
├── server.py                 # Core Python yt-dlp Engine & Web Server
├── build.gradle              # Top-level Gradle configuration
└── README.md
```

---

## 🧑‍💻 Author

- **FS Groupz / Zeeshan Ahmad**
- GitHub: [@ZeeshanAhmad-FS](https://github.com/ZeeshanAhmad-FS)
- Email: [zeeshan845401@gmail.com](mailto:zeeshan845401@gmail.com)

---

## 📄 License
This project is licensed under the MIT License.
