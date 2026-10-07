"""
Download Manager FS - Core Download Engine
Handles multi-threaded, segmented file downloads with verified resume, pause,
HTTP Range validation, automatic retry with backoff, bandwidth throttling, and safety checks.
"""

import os
import sys
import time
import math
import uuid
import shutil
import socket
import re
import http.client
import threading
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from enum import Enum
from typing import Optional, Callable, List, Dict, Tuple, Any

from src.utils.helpers import get_filename_from_url, sanitize_filename, categorize_file, format_size
from src.utils.logger import get_logger
from src.engine.limiter import BandwidthLimiter

logger = get_logger("downloader")


class DownloadStatus(Enum):
    QUEUED = "Queued"
    CONNECTING = "Connecting"
    DOWNLOADING = "Downloading"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


RECOVERABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}
PERMANENT_HTTP_CODES = {400, 401, 403, 404, 405, 410, 414, 415}


def is_recoverable_error(exc: Exception) -> bool:
    """Determines whether a network/HTTP exception is transient and can be retried."""
    if isinstance(exc, HTTPError):
        return exc.code in RECOVERABLE_HTTP_CODES
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    if isinstance(exc, (ConnectionResetError, ConnectionRefusedError, ConnectionAbortedError, BrokenPipeError)):
        return True
    if isinstance(exc, http.client.RemoteDisconnected):
        return True
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError, ConnectionResetError, BrokenPipeError, ConnectionRefusedError)):
            return True
        reason_str = str(reason).lower()
        if any(keyword in reason_str for keyword in [
            "timed out", "temporary", "connection reset", "connection refused",
            "broken pipe", "network is unreachable", "host is unreachable", "remote end closed"
        ]):
            return True
        return False
    return False


def parse_content_range(header_val: str) -> Optional[Tuple[int, int, Optional[int]]]:
    """
    Parses Content-Range header.
    Examples:
      'bytes 0-1023/2048' -> (0, 1023, 2048)
      'bytes 100-200/*'   -> (100, 200, None)
    """
    if not header_val:
        return None
    m = re.match(r"^bytes\s+(\d+)-(\d+)/(?:(\d+)|\*)$", header_val.strip(), re.IGNORECASE)
    if m:
        start = int(m.group(1))
        end = int(m.group(2))
        total = int(m.group(3)) if m.group(3) is not None else None
        return (start, end, total)
    return None


class Segment:
    """Represents an individual chunk of a file downloaded concurrently."""

    def __init__(self, segment_id: int, start_byte: int, end_byte: int, part_path: str):
        self.segment_id = segment_id
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.downloaded_bytes = 0
        self.part_path = part_path
        self.is_completed = False
        self.error_message: Optional[str] = None


class DownloadTask:
    """Represents a download task with segmented multi-threaded transfer and robustness features."""

    def __init__(
        self,
        url: str,
        dest_dir: str,
        filename: Optional[str] = None,
        num_segments: int = 8,
        on_progress: Optional[Callable[["DownloadTask"], None]] = None,
        on_status_change: Optional[Callable[["DownloadTask"], None]] = None,
        limiter: Optional[BandwidthLimiter] = None,
        max_retries: int = 5,
        task_id: Optional[str] = None,
        repository: Optional[Any] = None,
    ):
        self.id = task_id or str(uuid.uuid4())[:8]
        self.url = url
        self.dest_dir = dest_dir
        self.custom_filename = filename
        self.filename = filename or "detecting..."
        self.final_file_path: Optional[str] = None
        self.num_segments = max(1, min(num_segments, 32))
        self.repository = repository
        self._last_db_persist = 0.0
        
        self.status = DownloadStatus.QUEUED
        self.error_reason = ""
        self.total_size = 0
        self.downloaded_size = 0
        self.supports_range = False
        self.category = "Others"
        
        # Performance & Stats
        self.speed = 0.0  # Bytes per second
        self.eta = 0.0    # Seconds remaining
        self.progress_percent = 0.0
        self.start_time: Optional[float] = None
        self.last_calc_time = 0.0
        self.last_calc_bytes = 0

        # Retries & Throttling
        self.limiter = limiter
        self.max_retries = max_retries
        self.retry_count = 0
        self.retry_message = ""
        self._backoff_schedule = [1, 2, 4, 8, 16]
        
        # Internal state
        self.segments: List[Segment] = []
        self._threads: List[threading.Thread] = []
        self._pause_event = threading.Event()
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._range_refused = False
        
        # Callbacks
        self.on_progress = on_progress
        self.on_status_change = on_status_change

    def _set_status(self, status: DownloadStatus, error: str = ""):
        self.status = status
        self.error_reason = error
        if status in [DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.PAUSED, DownloadStatus.CANCELLED]:
            self.speed = 0.0
            self.eta = 0.0

        if self.repository:
            try:
                completed_at = time.time() if status == DownloadStatus.COMPLETED else None
                self.repository.update_download_status(
                    self.id, status.value, error, completed_at=completed_at
                )
                if status == DownloadStatus.COMPLETED:
                    from src.storage.models import HistoryRecord
                    self.repository.add_to_history(HistoryRecord(
                        download_id=self.id,
                        filename=self.filename,
                        url=self.url,
                        destination=self.final_file_path or os.path.join(self.dest_dir, self.filename),
                        size=self.total_size or self.downloaded_size,
                        status="Completed",
                        completed_at=completed_at or time.time()
                    ))
            except Exception as ex:
                logger.error(f"[{self.id}] Failed persisting status {status.value}: {ex}")

        if self.on_status_change:
            try:
                self.on_status_change(self)
            except Exception as e:
                logger.error(f"[{self.id}] Status callback error: {e}")

    def start(self):
        """Starts the download process in a background thread."""
        self._pause_event.clear()
        self._cancel_event.clear()
        self._set_status(DownloadStatus.CONNECTING)
        worker = threading.Thread(target=self._run, daemon=True)
        worker.start()

    def pause(self):
        """Pauses the download task."""
        if self.status in [DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING, DownloadStatus.QUEUED]:
            self._pause_event.set()
            self._set_status(DownloadStatus.PAUSED)
            self.speed = 0.0
            logger.info(f"[{self.id}] Download paused by user.")

    def resume(self):
        """Resumes a paused download."""
        if self.status == DownloadStatus.PAUSED:
            self._pause_event.clear()
            self._set_status(DownloadStatus.CONNECTING)
            worker = threading.Thread(target=self._run_resume, daemon=True)
            worker.start()
            logger.info(f"[{self.id}] Download resumed by user.")

    def cancel(self):
        """Cancels and purges any incomplete download files."""
        self._cancel_event.set()
        self._pause_event.set()
        self._set_status(DownloadStatus.CANCELLED)
        self.speed = 0.0
        self._cleanup_parts()
        logger.info(f"[{self.id}] Download cancelled and parts cleaned up.")

    def _get_headers(self, additional: Optional[Dict] = None) -> Dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) DownloadManagerFS/1.1",
            "Accept": "*/*"
        }
        if additional:
            headers.update(additional)
        return headers

    def _check_disk_space(self, required_bytes: int) -> bool:
        """Verifies destination filesystem has sufficient free space."""
        if required_bytes <= 0:
            return True
        try:
            usage = shutil.disk_usage(self.dest_dir)
            margin = 20 * 1024 * 1024  # 20 MB safety buffer
            needed = required_bytes + margin
            if usage.free < needed:
                msg = f"Insufficient disk space: need {format_size(needed)}, only {format_size(usage.free)} free."
                logger.error(f"[{self.id}] {msg}")
                self._set_status(DownloadStatus.FAILED, msg)
                return False
            return True
        except Exception as e:
            logger.warning(f"[{self.id}] Could not check disk space ({e}), continuing.")
            return True

    def _probe_url(self) -> bool:
        """Inspects remote URL for file size, resume capability, and content disposition cleanly."""
        for attempt in range(self.max_retries + 1):
            if self._cancel_event.is_set() or self._pause_event.is_set():
                return False

            try:
                # 1. Probing with Range: bytes=0-0 to test Range support and read 1 byte to prevent broken pipe
                req = Request(self.url, headers=self._get_headers({"Range": "bytes=0-0"}))
                with urlopen(req, timeout=15) as resp:
                    try:
                        resp.read(1)
                    except Exception:
                        pass

                    headers = resp.headers
                    status = getattr(resp, "status", 200)
                    content_range = headers.get("Content-Range", "")
                    accept_ranges = headers.get("Accept-Ranges", "")

                    if status == 206 or "bytes" in accept_ranges or "bytes" in content_range:
                        self.supports_range = True

                    if content_range and "/" in content_range:
                        total_str = content_range.split("/")[-1].strip()
                        if total_str.isdigit():
                            self.total_size = int(total_str)

                    if self.total_size == 0:
                        cl = headers.get("Content-Length")
                        if cl and cl.isdigit():
                            self.total_size = int(cl)

                    final_url = resp.geturl() or self.url
                    if not self.custom_filename or self.filename == "detecting...":
                        disposition = headers.get("Content-Disposition")
                        self.filename = get_filename_from_url(final_url, disposition)

                    self.category = categorize_file(self.filename)
                    if self.repository:
                        try:
                            rec = self.repository.get_download(self.id)
                            if rec:
                                rec.filename = self.filename
                                rec.total_size = self.total_size
                                rec.supports_range = 1 if self.supports_range else 0
                                rec.category = self.category
                                rec.destination = os.path.join(self.dest_dir, self.filename)
                                self.repository.update_download(rec)
                        except Exception as ex:
                            logger.debug(f"[{self.id}] Probe persist note: {ex}")
                    logger.info(f"[{self.id}] Probe succeeded: file='{self.filename}', size={self.total_size}, range={self.supports_range}")
                    return True

            except HTTPError as e:
                if e.code in PERMANENT_HTTP_CODES:
                    err_msg = f"HTTP Error {e.code}: {e.reason}"
                    logger.error(f"[{self.id}] Permanent HTTP error on probe: {err_msg}")
                    self._set_status(DownloadStatus.FAILED, err_msg)
                    return False
                # If server rejects Range probe (e.g. 416), fall back to standard GET probe below
                logger.warning(f"[{self.id}] Range probe HTTP {e.code}, attempting standard GET probe...")
                break

            except Exception as e:
                recoverable = is_recoverable_error(e)
                if not recoverable or attempt >= self.max_retries:
                    err_msg = f"Connection error: {str(e)}"
                    logger.error(f"[{self.id}] Probe failed: {err_msg}")
                    self._set_status(DownloadStatus.FAILED, err_msg)
                    return False

                delay = self._backoff_schedule[min(attempt, len(self._backoff_schedule) - 1)]
                self.retry_count = attempt + 1
                self.retry_message = f"Probe retry {self.retry_count}/{self.max_retries}: waiting {delay}s..."
                logger.warning(f"[{self.id}] {self.retry_message} (reason: {e})")
                self._pause_event.wait(delay)

        # 2. Standard GET probe fallback
        try:
            req = Request(self.url, headers=self._get_headers())
            with urlopen(req, timeout=15) as resp:
                headers = resp.headers
                cl = headers.get("Content-Length")
                if cl and cl.isdigit():
                    self.total_size = int(cl)

                final_url = resp.geturl() or self.url
                if not self.custom_filename or self.filename == "detecting...":
                    disposition = headers.get("Content-Disposition")
                    self.filename = get_filename_from_url(final_url, disposition)

                self.category = categorize_file(self.filename)
                if self.repository:
                    try:
                        rec = self.repository.get_download(self.id)
                        if rec:
                            rec.filename = self.filename
                            rec.total_size = self.total_size
                            rec.supports_range = 1 if self.supports_range else 0
                            rec.category = self.category
                            rec.destination = os.path.join(self.dest_dir, self.filename)
                            self.repository.update_download(rec)
                    except Exception as ex:
                        logger.debug(f"[{self.id}] Fallback probe persist note: {ex}")
                logger.info(f"[{self.id}] Fallback GET probe succeeded: file='{self.filename}', size={self.total_size}")
                return True
        except HTTPError as e:
            err_msg = f"HTTP Error {e.code}: {e.reason}"
            logger.error(f"[{self.id}] Fallback probe HTTP error: {err_msg}")
            self._set_status(DownloadStatus.FAILED, err_msg)
            return False
        except Exception as e:
            err_msg = f"Connection error: {str(e)}"
            logger.error(f"[{self.id}] Fallback probe failed: {err_msg}")
            self._set_status(DownloadStatus.FAILED, err_msg)
            return False

    def _prepare_paths(self):
        os.makedirs(self.dest_dir, exist_ok=True)
        base_name, ext = os.path.splitext(self.filename)
        candidate = os.path.join(self.dest_dir, self.filename)
        idx = 1
        while os.path.exists(candidate) and not self.segments:
            candidate = os.path.join(self.dest_dir, f"{base_name}_{idx}{ext}")
            idx += 1
        self.final_file_path = candidate
        self.filename = os.path.basename(candidate)

    def _run(self):
        if not self._probe_url():
            return

        self._prepare_paths()

        # Check free disk space before beginning download
        if not self._check_disk_space(self.total_size):
            return

        self.start_time = time.time()
        self.last_calc_time = self.start_time
        self.last_calc_bytes = 0
        self._set_status(DownloadStatus.DOWNLOADING)

        # Decide whether to use multi-segment or single-stream
        if self.supports_range and self.total_size > 1024 * 1024 and self.num_segments > 1:
            self._download_segmented()
        else:
            self._download_single()

    def _run_resume(self):
        self.start_time = time.time()
        self.last_calc_time = self.start_time
        self.last_calc_bytes = self.downloaded_size
        self._set_status(DownloadStatus.DOWNLOADING)

        if self.supports_range and self.segments:
            self._resume_segmented()
        else:
            self._download_single()

    def _create_segments(self):
        part_size = self.total_size // self.num_segments
        self.segments = []
        
        for i in range(self.num_segments):
            start = i * part_size
            end = (i + 1) * part_size - 1 if i < self.num_segments - 1 else self.total_size - 1
            part_path = f"{self.final_file_path}.part{i}"
            seg = Segment(i, start, end, part_path)
            self.segments.append(seg)

        if self.repository:
            try:
                from src.storage.models import SegmentRecord
                seg_records = [
                    SegmentRecord(
                        download_id=self.id,
                        segment_index=s.segment_id,
                        start_byte=s.start_byte,
                        end_byte=s.end_byte,
                        downloaded_bytes=s.downloaded_bytes,
                        status="completed" if s.is_completed else "downloading",
                        part_path=s.part_path,
                        retry_count=0,
                        error_message=""
                    )
                    for s in self.segments
                ]
                self.repository.save_segments(self.id, seg_records)
            except Exception as ex:
                logger.error(f"[{self.id}] Failed persisting segments: {ex}")

    def _download_segmented(self):
        self._create_segments()
        self._resume_segmented()

    def _resume_segmented(self):
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads = []
        self._range_refused = False

        for seg in self.segments:
            if not seg.is_completed:
                t = threading.Thread(target=self._download_segment_worker, args=(seg,), daemon=True)
                self._threads.append(t)
                t.start()

        # Monitor loop
        while any(t.is_alive() for t in self._threads):
            if self._cancel_event.is_set():
                return
            if self._pause_event.is_set():
                return
            if self._range_refused:
                break
            self._update_progress_stats()
            time.sleep(0.5)

        # Handle Range Refusal: Server returned 200 to a range request
        if self._range_refused:
            logger.warning(
                f"[{self.id}] Server refused byte range (HTTP 200). "
                "Safely switching to single-stream download mode."
            )
            # Wait for any lingering worker threads to exit
            for t in self._threads:
                t.join(timeout=1.0)
            self._cleanup_parts()
            self.supports_range = False
            self.segments = []
            self._download_single()
            return

        # Check completion
        if self._pause_event.is_set() or self._cancel_event.is_set():
            return

        all_done = all(seg.is_completed for seg in self.segments)
        if all_done:
            self._assemble_file()
        else:
            failed_err = next((seg.error_message for seg in self.segments if seg.error_message), "Download interrupted")
            self._set_status(DownloadStatus.FAILED, failed_err)

    def _download_segment_worker(self, segment: Segment):
        existing_bytes = 0
        if os.path.exists(segment.part_path):
            existing_bytes = os.path.getsize(segment.part_path)
            segment.downloaded_bytes = existing_bytes

        expected_total = segment.end_byte - segment.start_byte + 1
        if segment.downloaded_bytes >= expected_total:
            segment.is_completed = True
            return

        for attempt in range(self.max_retries + 1):
            if self._pause_event.is_set() or self._cancel_event.is_set() or self._range_refused:
                return

            current_start = segment.start_byte + segment.downloaded_bytes
            req = Request(
                self.url,
                headers=self._get_headers({"Range": f"bytes={current_start}-{segment.end_byte}"})
            )

            try:
                with urlopen(req, timeout=20) as resp:
                    status = getattr(resp, "status", 200)

                    # CRITICAL: Validate HTTP Range response status
                    if status == 200:
                        # Server does not support ranges for this request!
                        # Do NOT append byte 0 data to part file!
                        logger.warning(
                            f"[{self.id}] Segment {segment.segment_id} received HTTP 200 OK instead of 206 Partial Content. "
                            "Triggering safe single-stream fallback."
                        )
                        self._range_refused = True
                        return

                    if status != 206:
                        raise HTTPError(
                            self.url,
                            status,
                            f"Unexpected HTTP status {status} for Range request",
                            resp.headers,
                            None
                        )

                    # Validate Content-Range header
                    cr_header = resp.headers.get("Content-Range", "")
                    parsed_cr = parse_content_range(cr_header)
                    if parsed_cr:
                        cr_start, cr_end, cr_total = parsed_cr
                        if cr_start != current_start:
                            raise ValueError(
                                f"Content-Range start mismatch: server returned start {cr_start}, expected {current_start}"
                            )

                    # Safe write/append
                    mode = "ab" if segment.downloaded_bytes > 0 else "wb"
                    with open(segment.part_path, mode) as f:
                        while not self._pause_event.is_set() and not self._cancel_event.is_set() and not self._range_refused:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            segment.downloaded_bytes += len(chunk)

                            # Bandwidth limiter throttling
                            if self.limiter:
                                self.limiter.throttle(len(chunk), self._cancel_event, self._pause_event)

                if segment.downloaded_bytes >= expected_total:
                    segment.is_completed = True
                    return

            except Exception as e:
                if self._pause_event.is_set() or self._cancel_event.is_set() or self._range_refused:
                    return

                recoverable = is_recoverable_error(e)
                logger.warning(
                    f"[{self.id}] Segment {segment.segment_id} attempt {attempt + 1}/{self.max_retries + 1} "
                    f"failed: {e} (recoverable={recoverable})"
                )

                if recoverable and attempt < self.max_retries:
                    delay = self._backoff_schedule[min(attempt, len(self._backoff_schedule) - 1)]
                    self.retry_count = attempt + 1
                    self.retry_message = f"Segment {segment.segment_id} retry {self.retry_count}/{self.max_retries}: waiting {delay}s..."
                    self._pause_event.wait(delay)
                else:
                    segment.error_message = str(e)
                    return

    def _download_single(self):
        temp_path = f"{self.final_file_path}.part"
        existing = 0
        if os.path.exists(temp_path):
            existing = os.path.getsize(temp_path)
            self.downloaded_size = existing

        for attempt in range(self.max_retries + 1):
            if self._pause_event.is_set() or self._cancel_event.is_set():
                return

            headers = self._get_headers()
            if self.supports_range and existing > 0:
                headers["Range"] = f"bytes={existing}-"

            try:
                req = Request(self.url, headers=headers)
                with urlopen(req, timeout=20) as resp:
                    status = getattr(resp, "status", 200)

                    # Only append if server acknowledged range with 206
                    if status == 206 and self.supports_range and existing > 0:
                        mode = "ab"
                    else:
                        mode = "wb"
                        existing = 0
                        self.downloaded_size = 0

                    with open(temp_path, mode) as f:
                        while not self._pause_event.is_set() and not self._cancel_event.is_set():
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            self.downloaded_size += len(chunk)
                            self._update_progress_stats()

                            # Bandwidth limiter throttling
                            if self.limiter:
                                self.limiter.throttle(len(chunk), self._cancel_event, self._pause_event)

                if self._pause_event.is_set() or self._cancel_event.is_set():
                    return

                # Download finished: verify and move
                if os.path.exists(temp_path):
                    final_size = os.path.getsize(temp_path)
                    if self.total_size > 0 and final_size != self.total_size:
                        err = f"Size mismatch: downloaded {final_size} bytes, expected {self.total_size} bytes"
                        logger.error(f"[{self.id}] {err}")
                        self._set_status(DownloadStatus.FAILED, err)
                        return

                    if os.path.exists(self.final_file_path):
                        os.remove(self.final_file_path)
                    os.replace(temp_path, self.final_file_path)
                    self.progress_percent = 100.0
                    self.speed = 0.0
                    self.eta = 0.0
                    self._set_status(DownloadStatus.COMPLETED)
                    logger.info(f"[{self.id}] Single-stream download completed successfully: {self.final_file_path}")
                    return

            except Exception as e:
                if self._pause_event.is_set() or self._cancel_event.is_set():
                    return

                recoverable = is_recoverable_error(e)
                logger.warning(
                    f"[{self.id}] Single-stream attempt {attempt + 1}/{self.max_retries + 1} "
                    f"failed: {e} (recoverable={recoverable})"
                )

                if recoverable and attempt < self.max_retries:
                    if os.path.exists(temp_path):
                        existing = os.path.getsize(temp_path)
                        self.downloaded_size = existing
                    delay = self._backoff_schedule[min(attempt, len(self._backoff_schedule) - 1)]
                    self.retry_count = attempt + 1
                    self.retry_message = f"Retry {self.retry_count}/{self.max_retries}: waiting {delay}s..."
                    self._pause_event.wait(delay)
                else:
                    self._set_status(DownloadStatus.FAILED, str(e))
                    return

    def _assemble_file(self):
        """Combines all downloaded segment parts into destination file with comprehensive validation."""
        logger.info(f"[{self.id}] Starting assembly verification for {self.filename} ({len(self.segments)} parts)")

        # 1. Verify expected segment count
        if len(self.segments) != self.num_segments:
            err = f"Assembly failed: segment count mismatch (found {len(self.segments)}, expected {self.num_segments})"
            logger.error(f"[{self.id}] {err}")
            self._set_status(DownloadStatus.FAILED, err)
            return

        # 2. Verify all part files exist and are readable
        for seg in self.segments:
            if not os.path.exists(seg.part_path):
                err = f"Assembly failed: missing segment part file: {os.path.basename(seg.part_path)}"
                logger.error(f"[{self.id}] {err}")
                self._set_status(DownloadStatus.FAILED, err)
                return
            if not os.access(seg.part_path, os.R_OK):
                err = f"Assembly failed: segment part file not readable: {os.path.basename(seg.part_path)}"
                logger.error(f"[{self.id}] {err}")
                self._set_status(DownloadStatus.FAILED, err)
                return

        # 3. Verify each part file size matches its assigned range
        total_part_size = 0
        for seg in self.segments:
            expected_seg_size = seg.end_byte - seg.start_byte + 1
            actual_size = os.path.getsize(seg.part_path)
            total_part_size += actual_size
            if actual_size != expected_seg_size:
                err = f"Assembly failed: segment {seg.segment_id} size mismatch ({actual_size} bytes vs expected {expected_seg_size} bytes)"
                logger.error(f"[{self.id}] {err}")
                self._set_status(DownloadStatus.FAILED, err)
                return

        # 4. Compare total parts size with expected total size
        if self.total_size > 0 and total_part_size != self.total_size:
            err = f"Assembly failed: total parts size ({total_part_size}) does not match expected total size ({self.total_size})"
            logger.error(f"[{self.id}] {err}")
            self._set_status(DownloadStatus.FAILED, err)
            return

        # 5. Check disk space before assembly
        if not self._check_disk_space(self.total_size or total_part_size):
            return

        # 6. Stream parts into temporary assembly file
        assembly_temp = f"{self.final_file_path}.assembling"
        try:
            with open(assembly_temp, "wb") as outfile:
                for seg in self.segments:
                    with open(seg.part_path, "rb") as infile:
                        while True:
                            data = infile.read(1048576)  # 1MB buffer
                            if not data:
                                break
                            outfile.write(data)

            # 7. Verify final assembled file size
            final_size = os.path.getsize(assembly_temp)
            if self.total_size > 0 and final_size != self.total_size:
                err = f"Assembly verification failed: final file size {final_size} != expected {self.total_size}"
                logger.error(f"[{self.id}] {err}")
                if os.path.exists(assembly_temp):
                    os.remove(assembly_temp)
                self._set_status(DownloadStatus.FAILED, err)
                return

            # Atomic replace
            if os.path.exists(self.final_file_path):
                os.remove(self.final_file_path)
            os.replace(assembly_temp, self.final_file_path)

            # Clean up segment parts only on success
            self._cleanup_parts()
            self.progress_percent = 100.0
            self.speed = 0.0
            self.eta = 0.0
            self._set_status(DownloadStatus.COMPLETED)
            logger.info(f"[{self.id}] Assembled final file successfully: {self.final_file_path}")

        except Exception as e:
            if os.path.exists(assembly_temp):
                try:
                    os.remove(assembly_temp)
                except Exception:
                    pass
            err = f"Failed assembling file: {str(e)}"
            logger.error(f"[{self.id}] {err}")
            self._set_status(DownloadStatus.FAILED, err)

    def _cleanup_parts(self):
        """Removes temporary segment part files."""
        for seg in self.segments:
            if os.path.exists(seg.part_path):
                try:
                    os.remove(seg.part_path)
                except Exception as e:
                    logger.debug(f"[{self.id}] Part removal note for {seg.part_path}: {e}")
        single_part = f"{self.final_file_path}.part" if self.final_file_path else None
        if single_part and os.path.exists(single_part):
            try:
                os.remove(single_part)
            except Exception as e:
                logger.debug(f"[{self.id}] Single part removal note: {e}")

    def _update_progress_stats(self):
        now = time.time()
        with self._lock:
            if self.segments:
                self.downloaded_size = sum(seg.downloaded_bytes for seg in self.segments)

            if self.total_size > 0:
                self.progress_percent = min(100.0, (self.downloaded_size / self.total_size) * 100.0)

            dt = now - self.last_calc_time
            if dt >= 0.5:
                bytes_diff = self.downloaded_size - self.last_calc_bytes
                instant_speed = bytes_diff / dt if dt > 0 else 0
                self.speed = (0.7 * self.speed) + (0.3 * instant_speed) if self.speed > 0 else instant_speed
                self.last_calc_time = now
                self.last_calc_bytes = self.downloaded_size

                if self.speed > 0 and self.total_size > self.downloaded_size:
                    self.eta = (self.total_size - self.downloaded_size) / self.speed
                else:
                    self.eta = 0.0

            # Debounced SQLite progress updates (~1.5s interval)
            if self.repository and (now - self._last_db_persist >= 1.5):
                try:
                    self.repository.update_download_progress(self.id, self.downloaded_size, self.progress_percent)
                    if self.segments:
                        for seg in self.segments:
                            self.repository.update_segment_progress(
                                self.id, seg.segment_id, seg.downloaded_bytes, seg.is_completed
                            )
                except Exception as ex:
                    logger.debug(f"[{self.id}] Progress persist note: {ex}")
                self._last_db_persist = now

        if self.on_progress:
            try:
                self.on_progress(self)
            except Exception as e:
                logger.debug(f"[{self.id}] Progress callback error: {e}")
