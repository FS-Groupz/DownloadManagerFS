"""
Download Manager FS - Universal Media Extractor & Task
Extracts and downloads video/audio from YouTube, Instagram, Facebook, TikTok, Twitter, and other sources.
Maintains backward compatibility while leveraging the normalized MediaSourceProvider architecture.
"""

import os
import sys
import site
import uuid
import time
import threading
from typing import Dict, Any, Optional, Callable
from urllib.parse import urlparse

# Ensure user site packages are accessible
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.append(user_site)

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from src.engine.downloader import DownloadStatus
from src.engine.ytdlp_config import apply_ytdlp_options, friendly_error
from src.engine.media_providers import media_registry, NormalizedMediaInfo, VideoStreamInfo, AudioStreamInfo
from src.utils.helpers import sanitize_filename
from src.utils.logger import get_logger

logger = get_logger("media_extractor")


def is_social_media_url(url: str) -> bool:
    """Checks if URL belongs to a supported social/media streaming platform."""
    return media_registry.is_social_url(url)


def get_ffmpeg_path():
    """Finds ffmpeg executable from local bin or system PATH."""
    local_bin = os.path.expanduser("~/.local/bin/ffmpeg")
    if os.path.exists(local_bin):
        return local_bin
    
    # Check standard system locations
    for p in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/bin/ffmpeg"]:
        if os.path.exists(p):
            return p
    return None


def extract_media_info(url: str) -> Dict[str, Any]:
    """Extracts media title, thumbnail, duration, and dynamically probed video/audio formats."""
    info: NormalizedMediaInfo = media_registry.extract(url)
    data = info.to_dict()
    # Populate legacy 'formats' list for backwards compatibility
    legacy_formats = []
    for v in info.video_streams:
        legacy_formats.append(v.resolution.split(" ")[0])
    if info.audio_streams:
        legacy_formats.append("mp3")
    data["formats"] = legacy_formats or ["best", "720p", "360p", "mp3"]
    return data


class SocialMediaDownloadTask:
    """Universal social media download task conforming to the DownloadTask interface."""

    def __init__(
        self,
        url: str,
        dest_dir: str,
        filename: Optional[str] = None,
        num_segments: int = 8,
        quality: Optional[str] = "best",
        audio_only: bool = False,
        audio_bitrate: Optional[str] = "192",
        on_progress: Optional[Callable[["SocialMediaDownloadTask"], None]] = None,
        on_status_change: Optional[Callable[["SocialMediaDownloadTask"], None]] = None,
        repository: Optional[Any] = None,
        task_id: Optional[str] = None,
    ):
        self.id = task_id or str(uuid.uuid4())[:8]
        self.url = url
        self.dest_dir = dest_dir
        self.filename = filename or "Resolving Media Stream..."
        self.final_file_path: Optional[str] = None
        self.num_segments = num_segments
        self.quality = quality or "best"
        self.audio_only = audio_only or (self.quality == "mp3") or (isinstance(self.quality, str) and "kbps" in self.quality)
        self.audio_bitrate = str(audio_bitrate or "192").replace("kbps", "").strip()
        self.category = "Music" if self.audio_only else "Videos"
        
        self.status = DownloadStatus.QUEUED
        self.error_reason = ""
        self.total_size = 0
        self.downloaded_size = 0
        self.supports_range = True
        self.speed = 0.0
        self.eta = 0.0
        self.progress_percent = 0.0
        self.repository = repository
        
        self.on_progress = on_progress
        self.on_status_change = on_status_change
        self._cancel_flag = False
        self._pause_flag = False
        self._last_db_update = 0.0

    def _set_status(self, status: DownloadStatus, error: str = ""):
        self.status = status
        self.error_reason = error
        if self.repository:
            try:
                self.repository.update_download_status(self.id, status.value, error)
            except Exception as e:
                logger.warning(f"Failed to persist status {status.value}: {e}")

        if self.on_status_change:
            try:
                self.on_status_change(self)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    def start(self):
        self._cancel_flag = False
        self._pause_flag = False
        self._set_status(DownloadStatus.CONNECTING)
        worker = threading.Thread(target=self._run, daemon=True)
        worker.start()

    def pause(self):
        self._pause_flag = True
        self._set_status(DownloadStatus.PAUSED)
        self.speed = 0.0

    def resume(self):
        if self.status == DownloadStatus.PAUSED:
            self.start()

    def cancel(self):
        self._cancel_flag = True
        self._set_status(DownloadStatus.CANCELLED)
        self.speed = 0.0

    def _progress_hook(self, d: Dict[str, Any]):
        if self._cancel_flag:
            raise Exception("Cancelled by user")

        status = d.get("status")
        if status == "downloading":
            self.status = DownloadStatus.DOWNLOADING
            self.downloaded_size = d.get("downloaded_bytes", 0)
            self.total_size = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            self.speed = float(d.get("speed", 0.0) or 0.0)
            self.eta = float(d.get("eta", 0) or 0.0)
            
            if self.total_size > 0:
                self.progress_percent = min(100.0, (self.downloaded_size / self.total_size) * 100.0)
            else:
                pct_str = d.get("_percent_str", "0%").strip().replace("%", "")
                try:
                    self.progress_percent = float(pct_str)
                except ValueError:
                    pass

            now = time.time()
            if self.repository and (now - self._last_db_update >= 1.5):
                try:
                    self.repository.update_download_progress(self.id, self.downloaded_size, self.progress_percent)
                    self._last_db_update = now
                except Exception:
                    pass

            if self.on_progress:
                try:
                    self.on_progress(self)
                except Exception:
                    pass

        elif status == "finished":
            self.downloaded_size = self.total_size or self.downloaded_size
            self.progress_percent = 100.0
            self.speed = 0.0
            self.eta = 0.0
            self.final_file_path = d.get("filename")
            if self.final_file_path:
                self.filename = os.path.basename(self.final_file_path)

    def _run(self):
        if yt_dlp is None:
            self._set_status(DownloadStatus.FAILED, "yt-dlp library is missing.")
            return

        os.makedirs(self.dest_dir, exist_ok=True)
        out_template = os.path.join(self.dest_dir, "%(title)s.%(ext)s")

        ydl_opts = {
            "outtmpl": out_template,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
        }

        apply_ytdlp_options(ydl_opts)

        ffmpeg_bin = get_ffmpeg_path()
        if ffmpeg_bin:
            ydl_opts["ffmpeg_location"] = ffmpeg_bin

        # Quality selection logic
        if self.audio_only:
            ydl_opts["format"] = "bestaudio/best"
            preferred_bitrate = self.audio_bitrate if self.audio_bitrate in ["320", "256", "192", "128", "64"] else "192"
            if ffmpeg_bin:
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": preferred_bitrate,
                }]
        else:
            ydl_opts["merge_output_format"] = "mp4"
            q = str(self.quality).lower()
            height_limit = None
            for h in [2160, 1440, 1080, 720, 480, 360, 240]:
                if f"{h}p" in q:
                    height_limit = h
                    break

            if height_limit:
                ydl_opts["format"] = (
                    f"bestvideo[height<={height_limit}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={height_limit}]+bestaudio/"
                    f"best[height<={height_limit}]/best"
                )
            else:
                ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"

        def _postprocessor_hook(d):
            if d.get("status") == "finished":
                info = d.get("info_dict", {})
                fp = info.get("filepath") or d.get("filename")
                if fp and os.path.exists(fp):
                    self.final_file_path = fp
                    self.filename = os.path.basename(fp)

        ydl_opts["postprocessor_hooks"] = [_postprocessor_hook]

        try:
            self._set_status(DownloadStatus.CONNECTING)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = None
                try:
                    info = ydl.extract_info(self.url, download=False)
                    if info:
                        self.filename = info.get("title", self.filename)
                        if self.on_status_change:
                            self.on_status_change(self)
                except Exception:
                    pass

                self._set_status(DownloadStatus.DOWNLOADING)
                ydl.download([self.url])

                # Authoritative resolution of final file path
                if not self.final_file_path or not os.path.exists(self.final_file_path):
                    if info:
                        try:
                            expected = ydl.prepare_filename(info)
                            if os.path.exists(expected):
                                self.final_file_path = expected
                                self.filename = os.path.basename(expected)
                            else:
                                base, _ = os.path.splitext(expected)
                                for cand_ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus"]:
                                    cand = base + cand_ext
                                    if os.path.exists(cand):
                                        self.final_file_path = cand
                                        self.filename = os.path.basename(cand)
                                        break
                        except Exception:
                            pass

                self.progress_percent = 100.0
                self.speed = 0.0
                self.eta = 0.0
                self._set_status(DownloadStatus.COMPLETED)

                if self.repository:
                    try:
                        from src.storage.models import HistoryRecord
                        hist = HistoryRecord(
                            download_id=self.id,
                            filename=self.filename,
                            url=self.url,
                            destination=self.final_file_path or os.path.join(self.dest_dir, self.filename),
                            size=self.total_size or self.downloaded_size,
                            status="Completed",
                            completed_at=time.time()
                        )
                        self.repository.add_to_history(hist)
                    except Exception as e:
                        logger.warning(f"Failed to record history for {self.id}: {e}")

        except Exception as e:
            if not self._cancel_flag:
                self._set_status(DownloadStatus.FAILED, friendly_error(str(e)))


UniversalMediaTask = SocialMediaDownloadTask
