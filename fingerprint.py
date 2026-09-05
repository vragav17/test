"""Stages 0-4: proxy, shot segmentation, visual + audio fingerprints.

    python fingerprint.py <video> -o <out.json> [--threshold 27.0]

Produces one JSON file describing a video as an ordered list of shots, each
carrying a 64-bit perceptual hash of its picture and a 64-bit hash of its
audio. That file is the only thing diff.py needs.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

from vdiff_common import (
    AUDIO_SR,
    Stage,
    ToolError,
    build_proxy,
    check_tools,
    die,
    ensure_cache,
    extract_frames,
    ffprobe_duration,
    file_signature,
    format_tc,
    has_audio_stream,
    log,
    run,
)

DEFAULT_THRESHOLD = 27.0


# --------------------------------------------------------------------------
# stage 1 -- shot segmentation
# --------------------------------------------------------------------------


def detect_shots(proxy, threshold, duration):
    """Split the proxy into shots with PySceneDetect's ContentDetector.

    Returns a list of (start_seconds, end_seconds). A video with no detected
    cuts is a single shot spanning the whole file.
    """
    from scenedetect import ContentDetector, SceneManager, open_video

    video = open_video(proxy)
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video, show_progress=False)
    scenes = manager.get_scene_list()

    if not scenes:
        log("    no cuts detected -- treating the whole file as one shot")
        return [(0.0, duration)]

    # `.seconds` on newer PySceneDetect, `.get_seconds()` on 0.6.x.
    def secs(tc):
        return tc.seconds if hasattr(tc, "seconds") else tc.get_seconds()

    shots = [(secs(s), secs(e)) for s, e in scenes]
    # PySceneDetect stops at the last decoded frame; extend to the real end.
    if shots[-1][1] < duration:
        shots[-1] = (shots[-1][0], duration)
    return shots


# --------------------------------------------------------------------------
# stage 2 -- visual fingerprint
# --------------------------------------------------------------------------


def visual_hashes(proxy, shots, duration):
    """Perceptual hash of the midpoint frame of every shot.

    phash downsamples to a 32x32 DCT before hashing, so the hash is
    resolution-invariant: the 480p proxy fingerprints identically to the
    source it was made from.
    """
    import imagehash
    from PIL import Image
    import io

    midpoints = [(s + e) / 2.0 for s, e in shots]
    frames = extract_frames(proxy, midpoints, duration=duration)
    hashes = []
    for png in frames:
        img = Image.open(io.BytesIO(png))
        hashes.append(str(imagehash.phash(img)))
    return hashes


# --------------------------------------------------------------------------
# stage 3 -- audio fingerprint
# --------------------------------------------------------------------------


def extract_audio_wav(proxy):
    """Decode the proxy's audio once to a cached 22.05 kHz mono WAV.

    Decoding once and slicing per shot is far cheaper than asking librosa to
    seek into the mp4 for every shot, and avoids depending on audioread.
    """
    sig = file_signature(proxy)
    wav = os.path.join(ensure_cache("audio"), f"{sig}_{AUDIO_SR}_mono.wav")
    if os.path.isfile(wav) and os.path.getsize(wav) > 0:
        return wav
    tmp = wav + ".partial"
    run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-i", proxy, "-vn", "-ac", "1", "-ar", str(AUDIO_SR),
        "-c:a", "pcm_s16le", "-f", "wav", tmp,
    ])
    os.replace(tmp, wav)
    return wav


def _pool_to_8x8(spec):
    """Average a (64 mel x T) spectrogram down to an 8x8 grid."""
    n_mels, n_frames = spec.shape
    if n_frames < 8:
        # Very short shot: nearest-neighbour resample up to 8 columns so the
        # grid is always well defined.
        idx = np.clip((np.arange(8) * n_frames) // 8, 0, n_frames - 1)
        spec = spec[:, idx]
        n_frames = 8
    # 64 mel bands collapse to 8 bands of 8.
    spec = spec.reshape(8, n_mels // 8, n_frames).mean(axis=1)
    # Time axis collapses to 8 bins.
    cols = np.array_split(np.arange(n_frames), 8)
    return np.stack([spec[:, c].mean(axis=1) for c in cols], axis=1)


def _grid_to_hex(grid):
    """Threshold an 8x8 grid against its median to make a 64-bit hex hash."""
    median = np.median(grid)
    value = 0
    for bit in (grid > median).flatten():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def audio_hashes(proxy, shots):
    """64-bit spectral hash per shot window.

    "Picture identical, audio replaced" is one of the most common real
    differences between versions, and a visual hash alone reports those two
    files as identical -- hence this second hash.
    """
    silent = "0" * 16
    if not has_audio_stream(proxy):
        log("    WARNING: no audio stream in this file -- all audio hashes are zero, "
            "so audio-only differences cannot be detected for it.")
        return [silent] * len(shots)

    import librosa

    wav = extract_audio_wav(proxy)
    y, sr = librosa.load(wav, sr=AUDIO_SR, mono=True)

    hashes = []
    for start, end in shots:
        i0 = max(0, int(start * sr))
        i1 = min(len(y), int(end * sr))
        seg = y[i0:i1]
        if seg.size < 256 or not np.any(seg):
            hashes.append(silent)
            continue
        mel = librosa.feature.melspectrogram(
            y=seg, sr=sr, n_mels=64, n_fft=2048, hop_length=512
        )
        # Per-shot dB normalisation keeps the hash stable across re-encodes
        # that shift gain slightly, while still reacting to spectral change.
        mel_db = librosa.power_to_db(mel, ref=np.max)
        hashes.append(_grid_to_hex(_pool_to_8x8(mel_db)))
    return hashes


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def fingerprint(src, threshold=DEFAULT_THRESHOLD):
    t_start = time.time()
    log(f"Fingerprinting {src}")
    check_tools()

    proxy = build_proxy(src)
    duration = ffprobe_duration(proxy)

    with Stage("stage 1", f"shot segmentation (threshold {threshold})"):
        shots = detect_shots(proxy, threshold, duration)
        log(f"    {len(shots)} shots over {format_tc(duration)}")

    with Stage("stage 2", f"visual fingerprints ({len(shots)} frames)"):
        phashes = visual_hashes(proxy, shots, duration)

    with Stage("stage 3", f"audio fingerprints ({len(shots)} windows)"):
        ahashes = audio_hashes(proxy, shots)

    data = {
        "source": os.path.basename(src),
        "source_path": os.path.abspath(src),
        "proxy": os.path.relpath(proxy),
        "duration_seconds": round(duration, 3),
        "shot_count": len(shots),
        "threshold": threshold,
        "shots": [
            {
                "index": i,
                "start": round(start, 3),
                "end": round(end, 3),
                "phash": phashes[i],
                "ahash": ahashes[i],
            }
            for i, (start, end) in enumerate(shots)
        ],
    }
    log(f"Fingerprint complete in {time.time() - t_start:.1f}s "
        f"({len(shots)} shots)\n")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fingerprint a video as a sequence of shot-level picture and audio hashes."
    )
    parser.add_argument("video", help="input video file")
    parser.add_argument("-o", "--output", required=True, help="fingerprint JSON to write")
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"ContentDetector cut threshold (default {DEFAULT_THRESHOLD})",
    )
    args = parser.parse_args(argv)

    try:
        data = fingerprint(args.video, args.threshold)
    except ToolError as exc:
        die(str(exc))

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w") as fh:
        json.dump(data, fh, indent=2)
    log(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
