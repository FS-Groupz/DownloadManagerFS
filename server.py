#!/usr/bin/env python3
"""
DownloadManagerFS - High Performance Python Engine & Desktop Web Server
Integrates yt-dlp to download YouTube, Instagram, Facebook, TikTok, etc.
Serves downloads over HTTP for Download Manager FS Android App and Desktop Web UI.
"""

import os
import sys
import json
import time
import uuid
import re
import shutil
import threading
import subprocess
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

# Automatically activate static-ffmpeg if installed (ensures ffmpeg/ffprobe exists on Render/cloud)
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

PORT = int(os.environ.get("PORT", 5000))
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "DownloadManagerFS")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def format_to_netscape_cookies(raw_cookies):
    if not raw_cookies or not isinstance(raw_cookies, str):
        return ""
    if "# Netscape" in raw_cookies or "\t" in raw_cookies:
        return raw_cookies
    lines = ["# Netscape HTTP Cookie File\n", "# Converted by DownloadManagerFS\n"]
    for part in raw_cookies.split(";"):
        part = part.strip()
        if "=" in part:
            name, val = part.split("=", 1)
            name = name.strip()
            val = val.strip()
            if name:
                lines.append(f".youtube.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{val}\n")
                lines.append(f".google.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{val}\n")
    return "".join(lines)

COOKIES_FILE = os.path.join(DOWNLOAD_DIR, "cookies.txt")

def get_active_cookies_file():
    # 1. Environment variable YOUTUBE_COOKIES (ideal for Render / Docker cloud)
    if os.environ.get("YOUTUBE_COOKIES"):
        try:
            with open(COOKIES_FILE, "w", encoding="utf-8") as f:
                f.write(os.environ["YOUTUBE_COOKIES"].strip())
            return COOKIES_FILE
        except Exception:
            pass
    # 2. Local cookies.txt in current directory
    if os.path.exists("cookies.txt"):
        return os.path.abspath("cookies.txt")
    # 3. cookies.txt in DOWNLOAD_DIR
    if os.path.exists(COOKIES_FILE):
        return COOKIES_FILE
    return None


# Add deno and node to PATH if available in standard user locations
deno_in_path = shutil.which("deno")
deno_user_dir = os.path.expanduser("~/.deno/bin")
if not deno_in_path and os.path.exists(deno_user_dir):
    os.environ["PATH"] = deno_user_dir + os.pathsep + os.environ.get("PATH", "")

def get_ytdlp_cmd():
    # 1. System PATH
    bin_path = shutil.which("yt-dlp")
    if bin_path:
        return [bin_path]
    # 2. Local user bin
    local_bin = os.path.expanduser("~/.local/bin/yt-dlp")
    if os.path.exists(local_bin):
        return [local_bin]
    # 3. Windows standard script path
    win_bin = os.path.expandvars(r"%APPDATA%\Python\Scripts\yt-dlp.exe")
    if os.path.exists(win_bin):
        return [win_bin]
    # 4. Fallback to python module
    return [sys.executable, "-m", "yt_dlp"]

def get_asset_file(filename):
    # 1. If packed with PyInstaller
    if hasattr(sys, '_MEIPASS'):
        p = os.path.join(sys._MEIPASS, "assets", filename)
        if os.path.exists(p):
            return p
        p = os.path.join(sys._MEIPASS, filename)
        if os.path.exists(p):
            return p

    # 2. Local repo structure
    base = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(base, "app", "src", "main", "assets", filename)
    if os.path.exists(p):
        return p
    p = os.path.join(base, "assets", filename)
    if os.path.exists(p):
        return p
    return None

# Store tasks in memory
tasks = {}
tasks_lock = threading.Lock()

def sanitize_filename(name):
    name = re.sub(r'[^\w\s\-\.]', '_', name)
    name = re.sub(r'[\s_]+', '_', name).strip('_')
    return name or "video"

class DownloadTask:
    def __init__(self, task_id, url, quality, audio_only):
        self.id = task_id
        self.url = url
        self.quality = quality
        self.audio_only = audio_only
        self.filename = "Fetching video info..."
        self.saved_filename = ""
        self.saved_filepath = ""
        self.status = "Downloading"
        self.progress = 0.0
        self.size = "0 B"
        self.speed = "Starting..."
        self.eta = "--:--"
        self.download_url = ""
        self.error = ""
        self.logs = []
        self.process = None

    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "quality": self.quality,
            "audio_only": self.audio_only,
            "filename": self.filename,
            "saved_filename": self.saved_filename,
            "status": self.status,
            "progress": self.progress,
            "size": self.size,
            "speed": self.speed,
            "eta": self.eta,
            "download_url": self.download_url,
            "error": self.error
        }

def run_download_worker(task, is_retry=False):
    env = os.environ.copy()
    if os.path.exists(deno_user_dir) and deno_user_dir not in env.get("PATH", ""):
        env["PATH"] = deno_user_dir + os.pathsep + env.get("PATH", "")

    ext = "mp3" if task.audio_only else "mp4"
    extra_args = [
        "--no-check-certificates",
        "--geo-bypass",
        "--extractor-args", "youtubetab:skip=authcheck",
        "--compat-options", "no-youtube-unavailable-videos"
    ]

    # Configure resilient player client
    if is_retry:
        extra_args.extend(["--extractor-args", "youtube:player_client=tv_embedded,android"])
    else:
        extra_args.extend(["--extractor-args", "youtube:player_client=visionos,android_vr,android"])

    # Attach cookies if available
    active_cookies = get_active_cookies_file()
    if active_cookies:
        extra_args.extend(["--cookies", active_cookies])

    if task.audio_only:
        fmt = "bestaudio/best"
        extra_args.extend(["-x", "--audio-format", "mp3"])
    else:
        q = (task.quality or "720p").lower()
        if q in ['best', '4k', '2160p']:
            fmt = "bestvideo+bestaudio/bestvideo+best/best"
        elif q == '1080p':
            fmt = "bestvideo[height<=1920][width<=1080]+bestaudio/bestvideo[height<=1080][width<=1920]+bestaudio/bestvideo[height<=1080]+bestaudio/bestvideo+bestaudio/best[height<=1080]/best"
        elif q == '720p':
            fmt = "bestvideo[height<=1280][width<=720]+bestaudio/bestvideo[height<=720][width<=1280]+bestaudio/bestvideo[height<=720]+bestaudio/bestvideo+bestaudio/best[height<=720]/best"
        elif q == '480p':
            fmt = "bestvideo[height<=1080][width<=608]+bestaudio/bestvideo[height<=480][width<=854]+bestaudio/bestvideo[height<=480]+bestaudio/bestvideo+bestaudio/best[height<=480]/best"
        elif q == '360p':
            fmt = "bestvideo[height<=640][width<=360]+bestaudio/bestvideo[height<=360][width<=640]+bestaudio/bestvideo[height<=360]+bestaudio/bestvideo+bestaudio/best[height<=360]/best"
        else:
            fmt = "bestvideo[height<=1280][width<=720]+bestaudio/bestvideo[height<=720][width<=1280]+bestaudio/bestvideo[height<=720]+bestaudio/bestvideo+bestaudio/best"

        extra_args.extend(["--merge-output-format", "mp4"])

    # Hook JavaScript runtime for YouTube signature deciphering
    node_bin = shutil.which("node")
    deno_bin = shutil.which("deno")
    if not deno_bin and os.path.exists(deno_user_dir):
        candidate = os.path.join(deno_user_dir, "deno")
        if os.path.exists(candidate):
            deno_bin = candidate

    if node_bin:
        extra_args.extend(["--js-runtimes", f"node:{node_bin}"])
    elif deno_bin:
        extra_args.extend(["--js-runtimes", f"deno:{deno_bin}"])

    out_template = os.path.join(DOWNLOAD_DIR, f"%(title).60s_%(id)s.{ext}")

    ytdlp_cmd = get_ytdlp_cmd()
    cmd = ytdlp_cmd + [
        "--newline",
        "--no-playlist",
        "-f", fmt,
        "-o", out_template,
        task.url
    ] + extra_args

    print(f"[{task.id}] Starting download: {task.url} (quality: {task.quality}, fmt: {fmt})")
    recent_lines = []

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env
        )
        task.process = proc

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            recent_lines.append(line)
            if len(recent_lines) > 30:
                recent_lines.pop(0)

            task.logs.append(line)
            if len(task.logs) > 50:
                task.logs.pop(0)

            print(f"[{task.id}] {line}")

            if "[download] Destination:" in line:
                raw_path = line.split("[download] Destination:", 1)[1].strip()
                base = os.path.basename(raw_path)
                task.saved_filepath = raw_path
                task.filename = re.sub(r'\.f[0-9]+\.[a-zA-Z0-9]+$', '', base)

            elif "[Merger] Merging formats into" in line:
                raw_path = line.split("into", 1)[1].strip().strip('"')
                task.saved_filepath = raw_path
                task.filename = os.path.basename(raw_path)

            elif "[download]" in line and "%" in line:
                match = re.search(r'([0-9\.]+)%\s+of\s+~?([0-9\.]+[a-zA-Z]+)\s+at\s+([0-9\.]+[a-zA-Z]+/s)\s+ETA\s+([0-9:]+)', line)
                if match:
                    task.progress = float(match.group(1))
                    task.size = match.group(2)
                    task.speed = match.group(3)
                    task.eta = match.group(4)
                else:
                    match_simple = re.search(r'([0-9\.]+)%', line)
                    if match_simple:
                        task.progress = float(match_simple.group(1))

            elif "has already been downloaded" in line:
                task.progress = 100.0
                task.status = "Completed"
                raw_path = line.split("[download]", 1)[1].replace("has already been downloaded", "").strip()
                if os.path.exists(raw_path):
                    task.saved_filepath = raw_path
                    task.filename = os.path.basename(raw_path)

        proc.wait()

        if proc.returncode == 0:
            task.status = "Completed"
            task.progress = 100.0
            task.speed = "Done"

            if not task.saved_filepath or not os.path.exists(task.saved_filepath):
                files = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if not f.endswith(".part") and not f.endswith(".ytdl")]
                if files:
                    task.saved_filepath = max(files, key=os.path.getmtime)
                    task.saved_filename = os.path.basename(task.saved_filepath)
                    task.filename = task.saved_filename

            if task.saved_filepath and os.path.exists(task.saved_filepath):
                bsize = os.path.getsize(task.saved_filepath)
                if bsize >= 1024 * 1024:
                    task.size = f"{bsize / (1024 * 1024):.2f} MB"
                elif bsize >= 1024:
                    task.size = f"{bsize / 1024:.1f} KB"
                else:
                    task.size = f"{bsize} B"

            clean_display_name = sanitize_filename(task.filename)
            if not clean_display_name.lower().endswith(f".{ext}"):
                clean_display_name += f".{ext}"

            task.download_url = f"/api/file/{task.id}/{clean_display_name}"
            print(f"[{task.id}] SUCCESS: {task.saved_filepath} -> {task.download_url}")
        else:
            if task.status not in ["Paused", "Cancelled"]:
                err_candidates = [l for l in recent_lines if "ERROR:" in l or "Error:" in l]
                err_msg = err_candidates[-1] if err_candidates else (recent_lines[-1] if recent_lines else f"Exit {proc.returncode}")
                err_msg = err_msg.replace("ERROR: ", "").strip()
                task.error = err_msg
                task.speed = err_msg[:60]

                # Automatic retry with fallback client if YouTube bot check / format issue encountered
                if not is_retry and ("bot" in err_msg.lower() or "sign in" in err_msg.lower() or "not available" in err_msg.lower()):
                    print(f"[{task.id}] Bot verification or format block detected. Auto-retrying with tv_embedded client fallback...")
                    task.status = "Downloading"
                    task.speed = "Retrying with fallback..."
                    task.error = ""
                    run_download_worker(task, is_retry=True)
                    return

                task.status = "Error"
            print(f"[{task.id}] FAILED (exit {proc.returncode}): {task.error}")

    except Exception as e:
        task.status = "Error"
        task.error = str(e)
        task.speed = str(e)[:60]
        print(f"[{task.id}] EXCEPTION: {e}")

class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Range')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_HEAD(self):
        self.do_GET(head_only=True)

    def do_GET(self, head_only=False):
        parsed = urlparse(self.path)
        path = parsed.path

        # 1. API: List tasks
        if path == "/api/tasks":
            with tasks_lock:
                task_list = [t.to_dict() for t in tasks.values()]
                active_speeds = [t.speed for t in tasks.values() if t.status == 'Downloading' and 'B/s' in t.speed]
                total_speed = active_speeds[0] if active_speeds else "0 KB/s"

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if not head_only:
                self.wfile.write(json.dumps({"tasks": task_list, "total_speed": total_speed}).encode("utf-8"))
            return

        # 2. Diagnostic & Health Check Endpoint
        if path == "/api/diag":
            ytdlp_ver = "unknown"
            try:
                out = subprocess.check_output(get_ytdlp_cmd() + ["--version"], text=True, timeout=5)
                ytdlp_ver = out.strip()
            except Exception as ex:
                ytdlp_ver = f"Error: {ex}"

            diag_info = {
                "status": "online",
                "python": sys.version,
                "yt_dlp_version": ytdlp_ver,
                "has_cookies": bool(get_active_cookies_file()),
                "ffmpeg": shutil.which("ffmpeg"),
                "node": shutil.which("node"),
                "deno": shutil.which("deno"),
                "download_dir": DOWNLOAD_DIR,
                "tasks_count": len(tasks),
                "tasks": [t.to_dict() for t in tasks.values()]
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if not head_only:
                self.wfile.write(json.dumps(diag_info, indent=2).encode("utf-8"))
            return

        # 3. Serve static files (HTML, icons, manifest, service worker, ad logo)
        if path in ["/", "/index.html"]:
            index_file = get_asset_file("index.html")
            if index_file and os.path.exists(index_file):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                if not head_only:
                    with open(index_file, "rb") as f:
                        self.wfile.write(f.read())
                return

        static_file = None
        mime = "application/octet-stream"
        if path in ["/icon.png", "/assets/icon.png"]:
            static_file = get_asset_file("icon.png")
            mime = "image/png"
        elif path in ["/petsheaven_logo.webp", "/assets/petsheaven_logo.webp"]:
            static_file = get_asset_file("petsheaven_logo.webp")
            mime = "image/webp"
        elif path == "/manifest.json":
            static_file = get_asset_file("manifest.json")
            mime = "application/json"
        elif path == "/sw.js":
            static_file = get_asset_file("sw.js")
            mime = "application/javascript"

        if static_file and os.path.exists(static_file):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.end_headers()
            if not head_only:
                with open(static_file, "rb") as f:
                    self.wfile.write(f.read())
            return

        # 4. Serve downloaded file by task ID: /api/file/<task_id>/<filename>
        if path.startswith("/api/file/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 3:
                task_id = parts[2]
                with tasks_lock:
                    task = tasks.get(task_id)

                if task and task.saved_filepath and os.path.exists(task.saved_filepath):
                    self.serve_file(task.saved_filepath, head_only=head_only)
                    return

            self.send_response(404)
            self.end_headers()
            if not head_only:
                self.wfile.write(b"File not found")
            return

        # 5. Legacy downloads endpoint
        if path.startswith("/downloads/"):
            filename = unquote(path[len("/downloads/"):])
            filepath = os.path.join(DOWNLOAD_DIR, filename)

            if not os.path.exists(filepath):
                found = False
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.startswith(filename) or filename in f:
                        filepath = os.path.join(DOWNLOAD_DIR, f)
                        found = True
                        break
                if not found:
                    self.send_response(404)
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(b"File not found")
                    return

            self.serve_file(filepath, head_only=head_only)
            return

        self.send_response(404)
        self.end_headers()

    def serve_file(self, filepath, head_only=False):
        size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        safe_name = sanitize_filename(filename)

        mime = "video/mp4"
        if filepath.endswith(".mp3"):
            mime = "audio/mpeg"
        elif filepath.endswith(".webm"):
            mime = "video/webm"

        range_header = self.headers.get("Range")
        start = 0
        end = size - 1

        if range_header and range_header.startswith("bytes="):
            parts = range_header[6:].split("-")
            if parts[0]:
                start = int(parts[0])
            if len(parts) > 1 and parts[1]:
                end = int(parts[1])

            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)

        chunk_length = end - start + 1
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(chunk_length))
        self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        if head_only:
            return
        with open(filepath, "rb") as f:
            f.seek(start)
            remaining = chunk_length
            while remaining > 0:
                to_read = min(65536, remaining)
                data = f.read(to_read)
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/download":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                data = {}

            url = data.get("url", "").strip()
            if not url:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing url")
                return

            cookies_str = data.get("cookies", "").strip()
            if cookies_str:
                netscape = format_to_netscape_cookies(cookies_str)
                if netscape:
                    try:
                        with open(COOKIES_FILE, "w", encoding="utf-8") as cf:
                            cf.write(netscape)
                        with open("cookies.txt", "w", encoding="utf-8") as cf:
                            cf.write(netscape)
                    except Exception:
                        pass

            task_id = "task_" + str(int(time.time() * 1000))
            task = DownloadTask(
                task_id=task_id,
                url=url,
                quality=data.get("quality", "720p"),
                audio_only=data.get("audio_only", False)
            )

            with tasks_lock:
                tasks[task_id] = task

            t = threading.Thread(target=run_download_worker, args=(task,), daemon=True)
            t.start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "task_id": task_id}).encode("utf-8"))
            return

        if path == "/api/cookies":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                cookies_content = data.get("cookies", "").strip()
                if cookies_content:
                    netscape = format_to_netscape_cookies(cookies_content)
                    with open(COOKIES_FILE, "w", encoding="utf-8") as cf:
                        cf.write(netscape)
                    try:
                        with open("cookies.txt", "w", encoding="utf-8") as cf:
                            cf.write(netscape)
                    except Exception:
                        pass
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok","message":"Cookies saved successfully"}')
                    return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        if path.startswith("/api/tasks/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 4:
                task_id = parts[2]
                action = parts[3]
                with tasks_lock:
                    if task_id in tasks:
                        t = tasks[task_id]
                        if action == "cancel":
                            if t.process and t.process.poll() is None:
                                t.process.terminate()
                            t.status = "Cancelled"
                        elif action == "delete":
                            if t.process and t.process.poll() is None:
                                t.process.terminate()
                            del tasks[task_id]

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return

        self.send_response(404)
        self.end_headers()

def open_browser():
    time.sleep(1.0)
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass

def main():
    server = HTTPServer(("0.0.0.0", PORT), RequestHandler)
    print("=" * 60)
    print(f" DownloadManagerFS Engine & Web UI")
    print(f" Server running at: http://localhost:{PORT}")
    print(f" Downloads folder:  {DOWNLOAD_DIR}")
    print("=" * 60)

    if "--no-browser" not in sys.argv and not os.environ.get("HEADLESS"):
        threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DownloadManagerFS server.")
        server.server_close()

if __name__ == "__main__":
    main()
