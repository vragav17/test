"""Shared helpers for the video version diff POC.

Everything here is local: subprocess calls to ffmpeg/ffprobe, a small on-disk
cache under .cache/, and pure-python hash utilities. No network.
"""

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

CACHE_DIR = os.path.join(os.getcwd(), ".cache")

# Proxy encode settings (stage 0). Height 480, width auto-rounded to even.
PROXY_HEIGHT = 480
PROXY_VIDEO_BITRATE = "1M"
PROXY_AUDIO_BITRATE = "128k"

# Audio fingerprint sample rate (stage 3).
AUDIO_SR = 22050


class ToolError(RuntimeError):
    """An external tool failed. Never swallowed -- always surfaced to the user."""


# --------------------------------------------------------------------------
# progress / logging
# --------------------------------------------------------------------------


# Per-thread log redirection. The CLIs print to stdout as always; the UI
# installs a sink so each job's progress reaches its own event stream instead.
# Keyed by thread so two videos can be processed concurrently without their
# output interleaving.
_LOG_SINKS = {}


def set_log_sink(fn):
    """Route log() on this thread to `fn` instead of stdout."""
    _LOG_SINKS[threading.get_ident()] = fn


def clear_log_sink():
    _LOG_SINKS.pop(threading.get_ident(), None)


def log(msg):
    sink = _LOG_SINKS.get(threading.get_ident())
    if sink is not None:
        sink(msg)
    else:
        print(msg, flush=True)


class Stage:
    """Context manager that prints a stage banner and its elapsed time.

    Silence looks like a hang, so every stage announces itself before doing
    any work and reports how long it took on the way out.
    """

    def __init__(self, name, detail=""):
        self.name = name
        self.detail = detail
        self.t0 = None

    def __enter__(self):
        self.t0 = time.time()
        suffix = f" {self.detail}" if self.detail else ""
        log(f"  [{self.name}]{suffix} ...")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.time() - self.t0
        if exc_type is None:
            log(f"  [{self.name}] done in {elapsed:.1f}s")
        else:
            log(f"  [{self.name}] FAILED after {elapsed:.1f}s")
        return False


# --------------------------------------------------------------------------
# tool discovery
# --------------------------------------------------------------------------

_TOOLS = None


def check_tools():
    """Verify ffmpeg/ffprobe exist and work out which codec path to use.

    Returns a dict with the hwaccel and encoder arguments to use for this
    machine. On Apple Silicon this resolves to videotoolbox for both decode
    and encode; elsewhere it degrades to software with a printed warning.
    """
    global _TOOLS
    if _TOOLS is not None:
        return _TOOLS

    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise ToolError(
            f"Required tool(s) not found on PATH: {', '.join(missing)}.\n"
            f"Install ffmpeg (which provides both), e.g. `brew install ffmpeg` on macOS."
        )

    encoders = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True,
    ).stdout
    hwaccels = subprocess.run(
        ["ffmpeg", "-hide_banner", "-hwaccels"],
        capture_output=True, text=True,
    ).stdout

    has_vt_encoder = "h264_videotoolbox" in encoders
    has_vt_decoder = "videotoolbox" in hwaccels

    if has_vt_encoder:
        video_encoder = ["-c:v", "h264_videotoolbox", "-b:v", PROXY_VIDEO_BITRATE]
    else:
        log(
            "  WARNING: h264_videotoolbox encoder not available; "
            "falling back to libx264 (slower, but identical fingerprints)."
        )
        video_encoder = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]

    if has_vt_decoder:
        hwaccel = ["-hwaccel", "videotoolbox"]
    else:
        log("  WARNING: videotoolbox hwaccel not available; decoding in software.")
        hwaccel = []

    _TOOLS = {
        "hwaccel": hwaccel,
        "video_encoder": video_encoder,
        "videotoolbox_encode": has_vt_encoder,
        "videotoolbox_decode": has_vt_decoder,
    }
    return _TOOLS


# --------------------------------------------------------------------------
# subprocess
# --------------------------------------------------------------------------


def run(cmd, capture_stdout=False, timeout=None):
    """Run a command, raising ToolError with real stderr on any failure.

    ffmpeg errors are never swallowed -- a broken transcode must stop the run,
    not silently produce an empty result.
    """
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        tail = "\n".join(stderr.splitlines()[-15:])
        raise ToolError(
            f"Command failed (exit {proc.returncode}):\n"
            f"  {' '.join(cmd)}\n"
            f"--- stderr ---\n{tail}"
        )
    return proc.stdout if capture_stdout else None


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------


def ffprobe_duration(path):
    out = run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nokey=1:noprint_wrappers=1",
            path,
        ],
        capture_stdout=True,
    )
    text = out.decode().strip()
    try:
        return float(text)
    except ValueError:
        raise ToolError(f"Could not read duration from {path!r} (ffprobe said {text!r})")


# --------------------------------------------------------------------------
# technical properties (HDR/SDR, colour, codecs)
# --------------------------------------------------------------------------

# Transfer characteristics that mean high dynamic range.
HDR_TRANSFERS = {
    "smpte2084": "HDR10 (PQ)",
    "arib-std-b67": "HLG",
}

# Field order for reporting; the first entries are what a QC operator looks at.
TECHNICAL_FIELDS = [
    ("dynamic_range", "Dynamic range"),
    ("resolution", "Resolution"),
    ("frame_rate", "Frame rate"),
    ("bit_depth", "Bit depth"),
    ("color_transfer", "Transfer (TRC)"),
    ("color_primaries", "Colour primaries"),
    ("color_space", "Matrix"),
    ("color_range", "Range"),
    ("pix_fmt", "Pixel format"),
    ("video_codec", "Video codec"),
    ("audio_codec", "Audio codec"),
    ("audio_channels", "Audio channels"),
    ("audio_sample_rate", "Audio sample rate"),
]

# A difference in any of these changes what the file *is*, not merely how it
# was encoded, so the report calls them out separately.
MATERIAL_FIELDS = {
    "dynamic_range", "resolution", "frame_rate", "bit_depth",
    "color_transfer", "color_primaries", "audio_channels",
}


def _bit_depth(video):
    raw = video.get("bits_per_raw_sample")
    if raw and str(raw).isdigit():
        return int(raw)
    # Fall back to the pixel format, which encodes depth in its name.
    pix = video.get("pix_fmt") or ""
    for depth in ("12", "10", "9"):
        if depth in pix:
            return int(depth)
    return 8 if pix else None


def _frame_rate(video):
    text = video.get("r_frame_rate") or ""
    if "/" in text:
        num, den = text.split("/", 1)
        try:
            if float(den):
                return round(float(num) / float(den), 3)
        except ValueError:
            return None
    return None


def classify_dynamic_range(transfer, primaries, bit_depth):
    """Name the dynamic range from the colour tagging.

    Deliberately reports "untagged" rather than guessing SDR: an untagged file
    is a real QC finding, and silently calling it SDR would hide it.
    """
    if transfer in HDR_TRANSFERS:
        return HDR_TRANSFERS[transfer]
    if not transfer or transfer == "unknown":
        if primaries == "bt2020" or (bit_depth or 0) >= 10:
            return "untagged (wide gamut or 10-bit)"
        return "untagged"
    if primaries == "bt2020":
        return "SDR (BT.2020 primaries)"
    return "SDR (BT.709)"


def probe_technical(path):
    """Colour, codec and container properties of a video.

    Always probe the ORIGINAL file, never the 480p proxy: the proxy is
    transcoded to 8-bit BT.709, so every HDR property would read as SDR.
    """
    out = run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", path],
        capture_stdout=True,
    )
    try:
        data = json.loads(out)
    except ValueError as exc:
        raise ToolError(f"Could not parse ffprobe output for {path!r}: {exc}") from exc

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

    def tag(value):
        return None if value in (None, "", "unknown") else value

    transfer = tag(video.get("color_transfer"))
    primaries = tag(video.get("color_primaries"))
    depth = _bit_depth(video)

    width, height = video.get("width"), video.get("height")
    props = {
        "dynamic_range": classify_dynamic_range(transfer, primaries, depth),
        "resolution": f"{width}x{height}" if width and height else None,
        "frame_rate": _frame_rate(video),
        "bit_depth": depth,
        "color_transfer": transfer,
        "color_primaries": primaries,
        "color_space": tag(video.get("color_space")),
        "color_range": tag(video.get("color_range")),
        "pix_fmt": tag(video.get("pix_fmt")),
        "video_codec": tag(video.get("codec_name")),
        "audio_codec": tag(audio.get("codec_name")),
        "audio_channels": audio.get("channels"),
        "audio_sample_rate": (int(audio["sample_rate"])
                              if str(audio.get("sample_rate", "")).isdigit() else None),
    }

    # Mastering-display and content-light metadata, when the container carries it.
    for side in video.get("side_data_list") or []:
        kind = side.get("side_data_type", "")
        if "Mastering display" in kind:
            props["mastering_display"] = True
        elif "Content light level" in kind:
            props["max_cll"] = side.get("max_content")
            props["max_fall"] = side.get("max_average")
    return props


def compare_technical(a, b):
    """Field-by-field differences between two technical property sets."""
    if not a or not b:
        return []
    diffs = []
    for key, label in TECHNICAL_FIELDS:
        left, right = a.get(key), b.get(key)
        if left != right:
            diffs.append({
                "field": key,
                "label": label,
                "a": left,
                "b": right,
                "material": key in MATERIAL_FIELDS,
            })
    return diffs


def has_audio_stream(path):
    out = run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            path,
        ],
        capture_stdout=True,
    )
    return bool(out.decode().strip())


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


def ensure_cache(*parts):
    path = os.path.join(CACHE_DIR, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def file_signature(path):
    """Cheap identity for a file: size + mtime. Used to invalidate cache entries."""
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# stage 0 -- proxy
# --------------------------------------------------------------------------


def build_proxy(src, force=False):
    """Transcode `src` to a 480p working proxy and return the proxy path.

    Every downstream stage reads the proxy, never the source. Perceptual
    hashes are resolution-invariant, so the proxy fingerprints identically to
    the mezzanine -- which is also how this would run in production, where a
    transcoder makes the proxy and the source is never decoded again.

    Cached by input filename; the cached copy is reused unless the source
    file's size/mtime signature has changed.
    """
    if not os.path.isfile(src):
        raise ToolError(f"Input video not found: {src}")

    tools = check_tools()
    ensure_cache()
    stem = os.path.splitext(os.path.basename(src))[0]
    proxy = os.path.join(CACHE_DIR, f"{stem}_{PROXY_HEIGHT}p.mp4")
    meta_path = proxy + ".json"
    sig = file_signature(src)

    if not force and os.path.isfile(proxy) and os.path.isfile(meta_path):
        try:
            with open(meta_path) as fh:
                meta = json.load(fh)
            if meta.get("source_signature") == sig:
                log(f"  [stage 0] proxy cache hit -> {os.path.relpath(proxy)}")
                return proxy
        except (OSError, ValueError):
            pass  # unreadable cache metadata: fall through and rebuild

    with Stage("stage 0", f"proxy {os.path.basename(src)} -> {PROXY_HEIGHT}p"):
        tmp = proxy + ".partial.mp4"
        cmd = (
            ["ffmpeg", "-nostdin", "-v", "error", "-y"]
            + tools["hwaccel"]
            + ["-i", src, "-vf", f"scale=-2:{PROXY_HEIGHT}"]
            + tools["video_encoder"]
            + ["-c:a", "aac", "-b:a", PROXY_AUDIO_BITRATE, tmp]
        )
        run(cmd)
        os.replace(tmp, proxy)
        with open(meta_path, "w") as fh:
            json.dump({"source": os.path.abspath(src), "source_signature": sig}, fh)

    return proxy


# --------------------------------------------------------------------------
# stage 2 -- frame extraction
# --------------------------------------------------------------------------


def _frame_cache_path(proxy_sig, t):
    d = ensure_cache("frames", proxy_sig)
    return os.path.join(d, f"{int(round(t * 1000)):09d}.png")


def extract_frame(proxy, t, proxy_sig=None, duration=None):
    """Return PNG bytes for the frame at time `t` of `proxy`.

    Shells out to ffmpeg rather than seeking with OpenCV: OpenCV's seek is
    slow and imprecise on long files. Frames are cached on disk so re-runs and
    the later thumbnail pass never re-decode.
    """
    if proxy_sig is None:
        proxy_sig = file_signature(proxy)
    if duration is not None:
        # Seeking past the last frame yields an empty pipe; keep just inside.
        t = min(max(t, 0.0), max(duration - 0.05, 0.0))

    cached = _frame_cache_path(proxy_sig, t)
    if os.path.isfile(cached) and os.path.getsize(cached) > 0:
        with open(cached, "rb") as fh:
            return fh.read()

    tools = check_tools()
    cmd = (
        ["ffmpeg", "-nostdin", "-v", "error"]
        + tools["hwaccel"]
        + ["-ss", f"{t:.3f}", "-i", proxy, "-frames:v", "1",
           "-f", "image2pipe", "-vcodec", "png", "-"]
    )
    png = run(cmd, capture_stdout=True)
    if not png:
        raise ToolError(
            f"ffmpeg produced no frame at t={t:.3f}s in {proxy!r}. "
            f"The file may be shorter than expected or the seek landed past its end."
        )
    tmp = cached + f".{os.getpid()}.partial"
    with open(tmp, "wb") as fh:
        fh.write(png)
    os.replace(tmp, cached)
    return png


def extract_frames(proxy, times, duration=None, workers=None):
    """Extract many frames from one proxy, in parallel.

    Process startup dominates for short clips, so the per-frame ffmpeg calls
    are batched across a thread pool rather than run one at a time.
    """
    proxy_sig = file_signature(proxy)
    if workers is None:
        workers = min(8, (os.cpu_count() or 4))
    if len(times) <= 1:
        return [extract_frame(proxy, t, proxy_sig, duration) for t in times]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(lambda t: extract_frame(proxy, t, proxy_sig, duration), times)
        )


# --------------------------------------------------------------------------
# thumbnails
# --------------------------------------------------------------------------


def extract_audio_clip(proxy, start, end, bitrate="96k", max_seconds=30.0):
    """Cut [start, end) of the proxy's audio to a small MP3 clip; return its path.

    An `audio_changed` region shows the *same* picture on both sides -- the
    thumbnails are identical by definition. Hearing the two clips is the only
    way for a person to confirm the finding, so the report offers them.

    MP3 rather than AAC deliberately. AAC is the better codec, but it is
    patent-encumbered and open-source Chromium builds ship without it -- an
    <audio> element fed AAC there fails with DEMUXER_ERROR_NO_SUPPORTED_STREAMS
    (measured). MP3 plays in every browser, which matters because the
    standalone report is meant to be emailable to someone whose browser we do
    not control.

    Returns None when the file has no audio, rather than raising: a missing
    clip should degrade the player, not fail the report.
    """
    duration = min(max(end - start, 0.0), max_seconds)
    if duration <= 0.05:
        return None
    if not has_audio_stream(proxy):
        return None

    sig = file_signature(proxy)
    folder = ensure_cache("clips", sig)
    path = os.path.join(
        folder, f"{int(start * 1000):09d}_{int(duration * 1000):09d}.mp3"
    )
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path

    tmp = path + f".{os.getpid()}.partial.mp3"
    try:
        run([
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", proxy,
            "-vn", "-c:a", "libmp3lame", "-b:a", bitrate, tmp,
        ])
    except ToolError as exc:
        log(f"    WARNING: could not cut audio at {start:.2f}s: {exc}")
        return None
    os.replace(tmp, path)
    return path


def audio_clip_b64(proxy, start, end, **kwargs):
    """The same clip, base64-encoded for inlining into a standalone report."""
    path = extract_audio_clip(proxy, start, end, **kwargs)
    if not path:
        return None
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def png_to_jpeg_b64(png_bytes, max_width=320, quality=70):
    """Downscale a PNG frame and return it as a base64 JPEG string."""
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    img = img.convert("RGB")
    if img.width > max_width:
        height = max(1, round(img.height * max_width / img.width))
        img = img.resize((max_width, height), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --------------------------------------------------------------------------
# hashes
# --------------------------------------------------------------------------


def bits_from_hex(hex_str, nbits=64):
    """Expand a hex hash string into a list of 0/1 ints, most significant first."""
    value = int(hex_str, 16)
    return [(value >> (nbits - 1 - i)) & 1 for i in range(nbits)]


def hamming_hex(a, b):
    """Hamming distance between two equal-length hex hash strings."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------


def format_tc(seconds):
    """Seconds -> HH:MM:SS.mmm timecode."""
    if seconds is None:
        return "--:--:--.---"
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"


def die(msg, code=1):
    print(f"\nERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)
