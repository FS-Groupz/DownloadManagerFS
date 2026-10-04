# 🚀 DownloadManagerFS

A modern, high-speed, cross-platform video & audio downloader app for **Android**, **Linux**, and **Windows**. Powered by `yt-dlp` and an elegant glassmorphism web UI.

---

## ✨ Features

- ⚡ **Ultra-Fast Downloads**: Supports multi-threaded downloading with real-time speed, ETA, and progress indicators.
- 📺 **Wide Platform Support**:
  - **YouTube** (Shorts, 1080p, 4K, 60fps)
  - **Instagram** (Reels, Posts, Stories)
  - **Facebook** (Reels & Public HD Videos)
  - **TikTok** & 1000+ other supported platforms via `yt-dlp`
- 🎵 **MP3 Audio Extraction**: Extract clean, high-bitrate MP3 audio from any video link with a single tap.
- 📱 **Native Android Integration**:
  - Official release APK with Android `DownloadManager` support.
  - Automatic download notifications and direct **"▶ Open / Play"** into default Android Video Players.
- 💻 **Desktop Ready (Linux & Windows)**:
  - Standalone single-file binary for Linux.
  - Windows `.bat` / `.exe` launcher with automatic browser launch.
- 🎨 **Premium UI**: Cyberpunk neon dark theme, responsive glassmorphism tabs, toast alerts, and history tracking.

---

## 📦 Downloads & Releases

Pre-built binaries and installation packages are available in the [`releases/`](releases/) folder:

| Platform | Download Link | Type |
| :--- | :--- | :--- |
| **Android** | [DownloadManagerFS-v1.2.0.apk](releases/DownloadManagerFS-v1.2.0.apk) | Signed Release APK (143 KB) |
| **Linux** | [DownloadManagerFS-Linux](releases/DownloadManagerFS-Linux) | Standalone Executable (9.6 MB) |
| **Windows** | [DownloadManagerFS-Windows.bat](releases/DownloadManagerFS-Windows.bat) | Batch Launcher / GitHub Release `.exe` |

For automated continuous releases, visit the [GitHub Releases](https://github.com/ZeeshanAhmad-FS/DownloadManagerFS/releases) page.

---

## 🚀 Quick Start Guide

### 📱 Android
1. Download [`releases/DownloadManagerFS-v1.2.0.apk`](releases/DownloadManagerFS-v1.2.0.apk).
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
│   ├── src/main/assets/      # Web UI assets (HTML, CSS, JS)
│   ├── src/main/java/        # Java Native Android Bridge
│   ├── build.gradle          # Android Gradle build & signing config
│   └── release.keystore      # Release signing keystore
├── assets/                   # Bundled UI assets for desktop executable
├── releases/                 # Packaged Release Binaries
│   ├── DownloadManagerFS-v1.2.0.apk
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
