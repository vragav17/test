"""Build a fixture set of known-different versions from one source video.

    python make_variants.py <source> -o fixtures/

Produces v_base, v_cut, v_audiodub, v_reorder and v_lowres, plus a
ground_truth.json recording the exact timecodes of every edit so diff output
can be checked against what was actually done.

Edits are snapped to detected shot boundaries. That is deliberate: a real
version difference is a whole beat lifted out, not 12 seconds sliced from the
middle of a take, and snapping makes the ground truth unambiguous.
"""

import argparse
import json
import os
import sys
import time

from vdiff_common import (
    Stage,
    ToolError,
    build_proxy,
    check_tools,
    die,
    ffprobe_duration,
    format_tc,
    log,
    run,
)

CUT_SECONDS = 12.0        # target length of the removed section in v_cut
AUDIO_SECONDS = 8.0       # target length of the dubbed window in v_audiodub
CUT_MARK = 0.40           # remove from around the 40% mark
AUDIO_MARK = 0.65         # dub audio around the 65% mark
REORDER_MARK = 0.25       # swap a pair of shots around the 25% mark
TONE_HZ = 1000


def fixture_encoder():
    """Encoder settings for the fixtures themselves.

    Higher quality than the working proxy -- these files stand in for supplied
    masters, so they should not be the thing that introduces hash noise.
    """
    tools = check_tools()
    if tools["videotoolbox_encode"]:
        return ["-c:v", "h264_videotoolbox", "-b:v", "4M"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]


def shot_boundaries(video, threshold=27.0):
    """Detect shots on a cached proxy of `video`; return boundary times."""
    from fingerprint import detect_shots

    proxy = build_proxy(video)
    duration = ffprobe_duration(proxy)
    shots = detect_shots(proxy, threshold, duration)
    return shots, duration


def _nearest(values, target):
    return min(values, key=lambda v: abs(v - target))


def plan_cut(shots, duration):
    """Choose a whole number of shots to remove, ~CUT_SECONDS from ~CUT_MARK."""
    starts = [s for s, _ in shots][1:]  # never cut from the very first shot
    if len(starts) < 2:
        log("    WARNING: too few shots to snap the cut; using raw timecodes.")
        t0 = duration * CUT_MARK
        return t0, min(t0 + CUT_SECONDS, duration), 0

    t0 = _nearest(starts, duration * CUT_MARK)
    later = [s for s in starts if s > t0]
    if not later:
        t0 = starts[0]
        later = [s for s in starts if s > t0]
    if not later:
        t0 = duration * CUT_MARK
        return t0, min(t0 + CUT_SECONDS, duration), 0

    t1 = _nearest(later, t0 + CUT_SECONDS)
    removed = t1 - t0
    if removed < 1.0 or removed > 4 * CUT_SECONDS:
        log(f"    WARNING: nearest shot boundary gives a {removed:.1f}s removal; "
            f"falling back to an exact {CUT_SECONDS:.0f}s cut.")
        t0 = duration * CUT_MARK
        return t0, min(t0 + CUT_SECONDS, duration), 0

    n_shots = sum(1 for s, _ in shots if t0 <= s < t1)
    return t0, t1, n_shots


def plan_audio_window(shots, duration):
    """Choose a window of whole shots, ~AUDIO_SECONDS from ~AUDIO_MARK."""
    starts = [s for s, _ in shots]
    if len(starts) < 2:
        t0 = duration * AUDIO_MARK
        return t0, min(t0 + AUDIO_SECONDS, duration), 0

    t0 = _nearest(starts, duration * AUDIO_MARK)
    later = [s for s in starts if s > t0] + [duration]
    t1 = _nearest(later, t0 + AUDIO_SECONDS)
    if t1 - t0 < 1.0:
        t0 = duration * AUDIO_MARK
        t1 = min(t0 + AUDIO_SECONDS, duration)
    n_shots = sum(1 for s, _ in shots if t0 <= s < t1)
    return t0, t1, n_shots


def plan_reorder(shots, duration):
    """Pick two adjacent shots to swap, both long enough to be visible."""
    target = duration * REORDER_MARK
    candidates = [
        i for i in range(len(shots) - 1)
        if shots[i][1] - shots[i][0] >= 1.5 and shots[i + 1][1] - shots[i + 1][0] >= 1.5
    ]
    if not candidates:
        return None
    idx = min(candidates, key=lambda i: abs(shots[i][0] - target))
    return idx, shots[idx][0], shots[idx][1], shots[idx + 1][1]


# --------------------------------------------------------------------------
# ffmpeg builders
# --------------------------------------------------------------------------


def make_base(src, out):
    with Stage("v_base", "re-encode to 720p (the reference)"):
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src,
             "-vf", "scale=-2:720"] + fixture_encoder()
            + ["-c:a", "aac", "-b:a", "128k", out])


def make_cut(base, out, t0, t1):
    """Remove [t0, t1) from picture and sound together, frame-accurately."""
    with Stage("v_cut", f"remove {t1 - t0:.2f}s at {format_tc(t0)}"):
        graph = (
            f"[0:v]trim=start=0:end={t0:.3f},setpts=PTS-STARTPTS[v0];"
            f"[0:a]atrim=start=0:end={t0:.3f},asetpts=PTS-STARTPTS[a0];"
            f"[0:v]trim=start={t1:.3f},setpts=PTS-STARTPTS[v1];"
            f"[0:a]atrim=start={t1:.3f},asetpts=PTS-STARTPTS[a1];"
            f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
        )
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", base,
             "-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
            + fixture_encoder() + ["-c:a", "aac", "-b:a", "128k", out])


def make_audiodub(base, out, t0, t1):
    """Replace the audio in [t0, t1) with a 1 kHz tone. Picture is stream-copied."""
    with Stage("v_audiodub", f"1 kHz tone over {t1 - t0:.2f}s at {format_tc(t0)}"):
        dur = t1 - t0
        delay_ms = int(round(t0 * 1000))
        graph = (
            f"[0:a]aformat=channel_layouts=stereo,"
            f"volume=0:enable='between(t,{t0:.3f},{t1:.3f})'[base];"
            f"[1:a]aformat=channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms},apad[tone];"
            f"[base][tone]amix=inputs=2:duration=first:normalize=0[a]"
        )
        run(["ffmpeg", "-nostdin", "-v", "error", "-y",
             "-i", base,
             "-f", "lavfi", "-i",
             f"sine=frequency={TONE_HZ}:sample_rate=44100:duration={dur:.3f}",
             "-filter_complex", graph,
             # Picture is copied, not re-encoded: "picture untouched" means
             # bit-identical, so any visual region the diff reports is a real bug.
             "-map", "0:v", "-c:v", "copy",
             "-map", "[a]", "-c:a", "aac", "-b:a", "128k",
             out])


def make_reorder(base, out, t0, t1, t2):
    """Swap the two adjacent shots [t0,t1) and [t1,t2)."""
    with Stage("v_reorder", f"swap shots at {format_tc(t0)} and {format_tc(t1)}"):
        segments = [(0.0, t0), (t1, t2), (t0, t1), (t2, None)]
        parts, labels = [], []
        for i, (a, b) in enumerate(segments):
            end_v = f":end={b:.3f}" if b is not None else ""
            parts.append(
                f"[0:v]trim=start={a:.3f}{end_v},setpts=PTS-STARTPTS[v{i}];"
                f"[0:a]atrim=start={a:.3f}{end_v},asetpts=PTS-STARTPTS[a{i}]"
            )
            labels.append(f"[v{i}][a{i}]")
        graph = ";".join(parts) + ";" + "".join(labels) + \
            f"concat=n={len(segments)}:v=1:a=1[v][a]"
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", base,
             "-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
            + fixture_encoder() + ["-c:a", "aac", "-b:a", "128k", out])


def make_lowres(base, out):
    with Stage("v_lowres", "re-encode v_base at 360p (picture otherwise identical)"):
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", base,
             "-vf", "scale=-2:360"] + fixture_encoder()
            + ["-c:a", "aac", "-b:a", "128k", out])


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a fixture set of known-different versions of one video."
    )
    parser.add_argument("source", help="source video (a 10-15 minute short works best)")
    parser.add_argument("-o", "--output", default="fixtures/", help="output directory")
    parser.add_argument("--threshold", type=float, default=27.0,
                        help="ContentDetector threshold used to find edit points")
    args = parser.parse_args(argv)

    t_start = time.time()
    try:
        check_tools()
        if not os.path.isfile(args.source):
            raise ToolError(f"Source video not found: {args.source}")
        out_dir = args.output
        os.makedirs(out_dir, exist_ok=True)

        paths = {name: os.path.join(out_dir, f"{name}.mp4")
                 for name in ("v_base", "v_cut", "v_audiodub", "v_reorder", "v_lowres")}

        log(f"Building fixtures from {args.source}")
        make_base(args.source, paths["v_base"])
        base_duration = ffprobe_duration(paths["v_base"])

        with Stage("plan", "detecting shot boundaries to snap edits to"):
            shots, _ = shot_boundaries(paths["v_base"], args.threshold)
            log(f"    {len(shots)} shots over {format_tc(base_duration)}")

        cut_t0, cut_t1, cut_shots = plan_cut(shots, base_duration)
        aud_t0, aud_t1, aud_shots = plan_audio_window(shots, base_duration)
        reorder = plan_reorder(shots, base_duration)

        make_cut(paths["v_base"], paths["v_cut"], cut_t0, cut_t1)
        make_audiodub(paths["v_base"], paths["v_audiodub"], aud_t0, aud_t1)
        if reorder:
            _, r_t0, r_t1, r_t2 = reorder
            make_reorder(paths["v_base"], paths["v_reorder"], r_t0, r_t1, r_t2)
        else:
            log("  [v_reorder] SKIPPED: no two adjacent shots long enough to swap.")
        make_lowres(paths["v_base"], paths["v_lowres"])

        ground_truth = {
            "source": os.path.abspath(args.source),
            "base": os.path.relpath(paths["v_base"]),
            "base_duration_seconds": round(base_duration, 3),
            "shot_count": len(shots),
            "variants": {
                "v_cut": {
                    "expected_type": "delete",
                    "a_start": round(cut_t0, 3),
                    "a_end": round(cut_t1, 3),
                    "removed_seconds": round(cut_t1 - cut_t0, 3),
                    "shots_removed": cut_shots,
                    "note": "Content between a_start and a_end is absent from v_cut.",
                },
                "v_audiodub": {
                    "expected_type": "audio_changed",
                    "a_start": round(aud_t0, 3),
                    "a_end": round(aud_t1, 3),
                    "duration_seconds": round(aud_t1 - aud_t0, 3),
                    "shots_affected": aud_shots,
                    "note": f"Audio replaced with a {TONE_HZ} Hz tone; picture stream-copied.",
                },
                "v_lowres": {
                    "expected_type": "none",
                    "note": "360p re-encode of v_base; picture and audio otherwise identical.",
                },
            },
        }
        if reorder:
            _, r_t0, r_t1, r_t2 = reorder
            ground_truth["variants"]["v_reorder"] = {
                "expected_type": "reorder",
                "first_shot": {"start": round(r_t0, 3), "end": round(r_t1, 3)},
                "second_shot": {"start": round(r_t1, 3), "end": round(r_t2, 3)},
                "note": "The two adjacent shots above appear in the opposite order.",
            }

        gt_path = os.path.join(out_dir, "ground_truth.json")
        with open(gt_path, "w") as fh:
            json.dump(ground_truth, fh, indent=2)

    except ToolError as exc:
        die(str(exc))

    log("\n" + "=" * 68)
    log("GROUND TRUTH")
    log("=" * 68)
    log(f"  source           {args.source}")
    log(f"  v_base           720p reference, {format_tc(base_duration)}, {len(shots)} shots")
    log(f"  v_cut            removed {format_tc(cut_t0)} -> {format_tc(cut_t1)} "
        f"({cut_t1 - cut_t0:.2f}s, {cut_shots} shots)")
    log(f"  v_audiodub       {TONE_HZ} Hz tone over {format_tc(aud_t0)} -> {format_tc(aud_t1)} "
        f"({aud_t1 - aud_t0:.2f}s), picture untouched")
    if reorder:
        log(f"  v_reorder        swapped {format_tc(r_t0)}-{format_tc(r_t1)} "
            f"with {format_tc(r_t1)}-{format_tc(r_t2)}")
    log(f"  v_lowres         360p re-encode, no content change")
    log("=" * 68)
    log(f"\nWrote {gt_path}")
    log(f"Fixtures built in {time.time() - t_start:.1f}s -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
