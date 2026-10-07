"""
Download Manager FS - Normalized Media Source Providers
Implements normalized media extraction for YouTube, Instagram, Facebook, and generic web streams.
Separates stream analysis from downloading, exposing genuine available video and audio streams.
"""

import os
import sys
import site
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

# Ensure user site packages are accessible
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.append(user_site)

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from src.engine.ytdlp_config import apply_ytdlp_options, friendly_error


@dataclass
class VideoStreamInfo:
    id: str
    resolution: str  # e.g., "1080p", "720p", "480p", "360p"
    height: int
    container: str   # e.g., "mp4", "webm"
    codec: str       # e.g., "avc1", "vp9", "h264"
    filesize_approx: int = 0
    bitrate_kbps: Optional[int] = None
    direct_url: Optional[str] = None
    has_audio: bool = False
    format_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AudioStreamInfo:
    id: str
    bitrate_kbps: int  # e.g., 320, 256, 192, 128
    container: str     # e.g., "m4a", "mp3", "webm", "aac"
    codec: str         # e.g., "aac", "opus", "mp3"
    filesize_approx: int = 0
    direct_url: Optional[str] = None
    quality_label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedMediaInfo:
    url: str
    title: str
    thumbnail: str = ""
    duration: int = 0
    uploader: str = ""
    source: str = "Web"
    is_direct_file: bool = False
    video_streams: List[VideoStreamInfo] = field(default_factory=list)
    audio_streams: List[AudioStreamInfo] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["video_streams"] = [s.to_dict() for s in self.video_streams]
        data["audio_streams"] = [s.to_dict() for s in self.audio_streams]
        return data


class MediaSourceProvider:
    """Base interface for media extraction providers."""

    name: str = "generic"

    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    def extract_info(self, url: str) -> NormalizedMediaInfo:
        raise NotImplementedError


def _normalize_resolution(height: int) -> str:
    if height >= 2160:
        return "2160p (4K)"
    elif height >= 1440:
        return "1440p (2K)"
    elif height >= 1080:
        return "1080p (Full HD)"
    elif height >= 720:
        return "720p (HD)"
    elif height >= 480:
        return "480p (SD)"
    elif height >= 360:
        return "360p (Data Saver)"
    elif height >= 240:
        return "240p"
    return f"{height}p"


class YtDlpMediaProvider(MediaSourceProvider):
    """Universal provider leveraging yt-dlp to inspect real streams without downloading."""

    name: str = "ytdlp_universal"

    def __init__(self, platform_name: str, domain_keywords: List[str]):
        self.platform_name = platform_name
        self.domain_keywords = [k.lower() for k in domain_keywords]

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            domain = urlparse(url).netloc.lower()
            return any(k in domain for k in self.domain_keywords)
        except Exception:
            return False

    def extract_info(self, url: str) -> NormalizedMediaInfo:
        if yt_dlp is None:
            return NormalizedMediaInfo(
                url=url,
                title="yt-dlp Unavailable",
                source=self.platform_name,
                error="Media extraction engine (yt-dlp) is not installed."
            )

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "ignoreerrors": True,
            "noplaylist": True,
        }
        # ignoreerrors would swallow the real reason (bot check etc.) and return None
        ydl_opts["ignoreerrors"] = False
        apply_ytdlp_options(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                raw = ydl.extract_info(url, download=False)
                if not raw:
                    return NormalizedMediaInfo(
                        url=url,
                        title="Failed to extract",
                        source=self.platform_name,
                        error="Unable to extract media information from this URL."
                    )

                title = raw.get("title") or "Video"
                thumbnail = raw.get("thumbnail") or ""
                duration = raw.get("duration") or 0
                uploader = raw.get("uploader") or raw.get("channel") or ""

                formats = raw.get("formats") or []
                video_map: Dict[str, VideoStreamInfo] = {}
                audio_map: Dict[int, AudioStreamInfo] = {}

                for f in formats:
                    vcodec = f.get("vcodec") or "none"
                    acodec = f.get("acodec") or "none"
                    ext = f.get("ext") or "mp4"
                    filesize = f.get("filesize") or f.get("filesize_approx") or 0
                    tbr = f.get("tbr") or f.get("vbr") or 0
                    height = f.get("height") or 0
                    format_id = str(f.get("format_id", ""))

                    # Video streams
                    if vcodec != "none" and height > 0:
                        res_label = _normalize_resolution(height)
                        # Keep best container (prefer mp4) or higher bitrate for same resolution
                        existing = video_map.get(res_label)
                        has_audio = (acodec != "none")
                        is_preferred = False
                        if existing is None:
                            is_preferred = True
                        else:
                            if existing.container != "mp4" and ext == "mp4":
                                is_preferred = True
                            elif ext == existing.container and (filesize > existing.filesize_approx or tbr > (existing.bitrate_kbps or 0)):
                                is_preferred = True

                        if is_preferred:
                            video_map[res_label] = VideoStreamInfo(
                                id=format_id,
                                resolution=res_label,
                                height=height,
                                container=ext,
                                codec=vcodec.split(".")[0],
                                filesize_approx=filesize,
                                bitrate_kbps=int(tbr) if tbr else None,
                                direct_url=f.get("url"),
                                has_audio=has_audio,
                                format_note=f.get("format_note", "")
                            )

                    # Audio-only streams
                    if acodec != "none" and vcodec == "none":
                        abr = f.get("abr") or tbr or 0
                        int_abr = int(abr) if abr else 128
                        # Normalize to common audio tiers
                        tier = 128
                        if int_abr >= 280:
                            tier = 320
                        elif int_abr >= 220:
                            tier = 256
                        elif int_abr >= 160:
                            tier = 192
                        elif int_abr >= 110:
                            tier = 128
                        else:
                            tier = 64

                        if tier not in audio_map or (filesize > audio_map[tier].filesize_approx):
                            audio_map[tier] = AudioStreamInfo(
                                id=format_id,
                                bitrate_kbps=tier,
                                container=ext if ext in ["m4a", "mp3", "aac"] else "m4a",
                                codec=acodec.split(".")[0],
                                filesize_approx=filesize,
                                direct_url=f.get("url"),
                                quality_label=f"{tier} kbps"
                            )

                # Sort video streams by height descending (e.g., 1080p, 720p, 480p)
                sorted_videos = sorted(video_map.values(), key=lambda v: v.height, reverse=True)

                # Fallback if no separate video streams found, use raw entry
                if not sorted_videos and formats:
                    best_f = formats[-1]
                    h = best_f.get("height") or 720
                    sorted_videos.append(VideoStreamInfo(
                        id=str(best_f.get("format_id", "best")),
                        resolution=_normalize_resolution(h),
                        height=h,
                        container=best_f.get("ext", "mp4"),
                        codec=best_f.get("vcodec", "h264"),
                        filesize_approx=best_f.get("filesize", 0) or 0,
                        has_audio=True
                    ))

                # Sort audio streams descending
                sorted_audios = sorted(audio_map.values(), key=lambda a: a.bitrate_kbps, reverse=True)
                if not sorted_audios:
                    # Provide standard available audio quality tiers if video has audio
                    sorted_audios.append(AudioStreamInfo(
                        id="bestaudio",
                        bitrate_kbps=256,
                        container="m4a",
                        codec="aac",
                        filesize_approx=int(duration * 256 * 1024 / 8) if duration else 0,
                        quality_label="256 kbps (High Quality M4A/MP3)"
                    ))
                    sorted_audios.append(AudioStreamInfo(
                        id="128k",
                        bitrate_kbps=128,
                        container="m4a",
                        codec="aac",
                        filesize_approx=int(duration * 128 * 1024 / 8) if duration else 0,
                        quality_label="128 kbps (Standard M4A/MP3)"
                    ))

                return NormalizedMediaInfo(
                    url=url,
                    title=title,
                    thumbnail=thumbnail,
                    duration=duration,
                    uploader=uploader,
                    source=self.platform_name,
                    video_streams=sorted_videos,
                    audio_streams=sorted_audios
                )

        except Exception as e:
            err_msg = str(e)
            if "DRM" in err_msg.upper():
                clean_err = "This media is protected by DRM and cannot be downloaded."
            elif "PRIVATE" in err_msg.upper():
                clean_err = "This content is private or requires authentication."
            else:
                clean_err = friendly_error(err_msg)
                if clean_err == err_msg:
                    clean_err = f"Unable to parse media: {err_msg}"
            return NormalizedMediaInfo(
                url=url,
                title="Extraction Error",
                source=self.platform_name,
                error=clean_err
            )


class GenericWebProvider(MediaSourceProvider):
    """Fallback provider for direct file downloads and arbitrary URLs."""

    name: str = "generic_web"

    def can_handle(self, url: str) -> bool:
        return True

    def extract_info(self, url: str) -> NormalizedMediaInfo:
        filename = "download"
        try:
            parsed = urlparse(url)
            path_part = os.path.basename(parsed.path)
            if path_part:
                filename = path_part
        except Exception:
            pass

        return NormalizedMediaInfo(
            url=url,
            title=filename,
            source="Direct Web",
            is_direct_file=True,
            video_streams=[],
            audio_streams=[]
        )


class MediaProviderRegistry:
    """Registry coordinating specialized and generic media source providers."""

    def __init__(self):
        self.providers: List[MediaSourceProvider] = [
            YtDlpMediaProvider("YouTube", ["youtube.com", "youtu.be"]),
            YtDlpMediaProvider("Instagram", ["instagram.com", "instagr.am"]),
            YtDlpMediaProvider("Facebook", ["facebook.com", "fb.watch", "fb.com"]),
            YtDlpMediaProvider("TikTok", ["tiktok.com"]),
            YtDlpMediaProvider("Twitter/X", ["twitter.com", "x.com"]),
            YtDlpMediaProvider("Reddit", ["reddit.com"]),
            YtDlpMediaProvider("Vimeo", ["vimeo.com"]),
            YtDlpMediaProvider("Pinterest", ["pinterest.com"]),
        ]
        self.generic_provider = GenericWebProvider()

    def get_provider_for_url(self, url: str) -> MediaSourceProvider:
        for p in self.providers:
            if p.can_handle(url):
                return p
        return self.generic_provider

    def is_social_url(self, url: str) -> bool:
        provider = self.get_provider_for_url(url)
        return provider != self.generic_provider

    def extract(self, url: str) -> NormalizedMediaInfo:
        provider = self.get_provider_for_url(url)
        return provider.extract_info(url)


# Global registry singleton
media_registry = MediaProviderRegistry()
