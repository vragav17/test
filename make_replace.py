"""Build a `replace` variant from any single video, standalone.

    python make_replace.py sintel.mp4 -o sintel_replaced.mp4 \
        --start 00:04:10 --end 00:04:35

Alters the picture over one time range while leaving runtime, shot boundaries
and audio untouched. That combination is what the diff reports as `replace`
rather than a delete plus an insert: the shots still correspond one-to-one,
they just no longer look the same.

This is the Star Wars Special Edition case — a shot swapped for a different
version of itself — and it is the hardest change to fake convincingly, because
most "obvious" edits are invisible to a perceptual hash.

WHAT WORKS, AND WHAT DOES NOT

phash hashes a DCT of the *greyscale, downsampled* image, so it sees coarse
luminance structure and nothing else. Measured Hamming distances on a sample
frame (the `replace` threshold is > 20 out of 64):

    negate                          62   works, but nobody ships an inverted cut
    hflip                           28   works
    crop 30% + rescale (reframe)    28   works
    different footage entirely      28   works, and is the most realistic
    crop 15% + rescale              14   too weak -- lands in the "weak match" band
    burned-in lower third           14   too weak
    gaussian blur sigma=12           0   INVISIBLE
    hue rotate / saturation boost    0   INVISIBLE

Blur is invisible because it preserves low frequencies, which is exactly what
the DCT keeps. Colour is invisible because the image is greyscaled first. So a
regrade, a colourisation, or a soft-focus pass will produce a variant this tool
correctly reports as identical -- which is a real property of the system worth
knowing, not a bug to work around.

`--check` measures what the built file actually achieved and says plainly
whether it will register as a replace.
"""

import argparse
import io
import os
import subprocess
import sys

from vdiff_common import (
    Stage,
    ToolError,
    check_tools,
    die,
    ffprobe_duration,
    format_tc,
    log,
    run,
)

# Picture transforms, keyed by the distance they were measured to produce.
MODES = {
    "flip": {
        "filter": "hflip",
        "blurb": "mirror the picture (measured ~28)",
    },
    "reframe": {
        "filter": "crop=iw*0.7:ih*0.7,scale=iw/0.7:ih/0.7",
        "blurb": "punch in 30% and rescale, like a reframed delivery (measured ~28)",
    },
    "substitute": {
        "filter": None,  # handled separately: footage comes from elsewhere
        "blurb": "replace with footage from elsewhere in the same file (most realistic, "
                 "but see the warning it prints about shot structure)",
    },
}

MIN_USEFUL_DISTANCE = 20  # anything at or below this is not a mismatch


def parse_time(value):
    """Accept 90, 1:30, or 00:01:30.5."""
    text = str(value).strip()
    parts = text.split(":")
    if not text or len(parts) > 3:
        raise ToolError(
            f"Could not read {value!r} as a timecode. "
            f"Use seconds (90), MM:SS (1:30), or HH:MM:SS.mmm (00:01:30.500)."
        )
    seconds = 0.0
    for part in parts:
        try:
            seconds = seconds * 60 + float(part)
        except ValueError:
            raise ToolError(
                f"Could not read {value!r} as a timecode. "
                f"Use seconds (90), MM:SS (1:30), or HH:MM:SS.mmm (00:01:30.500)."
            ) from None
    return seconds


def snap_to_shots(video, start, end):
    """Move `start` and `end` onto the nearest detected shot boundaries.

    Altering a range that begins mid-shot introduces a *new* cut at that point,
    which the segmenter then reports as an extra shot -- so the diff shows a
    spurious insert alongside the replace. Snapping to real boundaries keeps
    the shot count identical on both sides, which is what makes the result a
    clean one-region replace.
    """
    from fingerprint import detect_shots
    from vdiff_common import build_proxy

    proxy = build_proxy(video)
    duration = ffprobe_duration(proxy)
    shots = detect_shots(proxy, 27.0, duration)
    if len(shots) < 2:
        log("    only one shot detected; leaving the range as given")
        return start, end

    boundaries = sorted({s for s, _ in shots} | {e for _, e in shots})
    snapped_start = min(boundaries, key=lambda b: abs(b - start))
    later = [b for b in boundaries if b > snapped_start]
    if not later:
        log("    no boundary after the snapped start; leaving the range as given")
        return start, end
    snapped_end = min(later, key=lambda b: abs(b - end))

    log(f"    snapped {format_tc(start)} -> {format_tc(snapped_start)} "
        f"and {format_tc(end)} -> {format_tc(snapped_end)} "
        f"({len(boundaries)} boundaries found)")
    return snapped_start, snapped_end


def build(src, out, start, end, mode, substitute_from=None):
    """Re-assemble the file with [start, end) altered in place."""
    duration = end - start

    if mode == "substitute":
        if substitute_from is None:
            raise ToolError("--mode substitute needs --from <timecode>")
        # Video comes from elsewhere; audio still comes from the original range,
        # so the audio hashes match and only the picture moves.
        middle_video = (
            f"[0:v]trim=start={substitute_from:.3f}:end={substitute_from + duration:.3f},"
            f"setpts=PTS-STARTPTS[v1]"
        )
    else:
        middle_video = (
            f"[0:v]trim=start={start:.3f}:end={end:.3f},"
            f"setpts=PTS-STARTPTS,{MODES[mode]['filter']}[v1]"
        )

    graph = (
        f"[0:v]trim=start=0:end={start:.3f},setpts=PTS-STARTPTS[v0];"
        f"[0:a]atrim=start=0:end={start:.3f},asetpts=PTS-STARTPTS[a0];"
        f"{middle_video};"
        f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a1];"
        f"[0:v]trim=start={end:.3f},setpts=PTS-STARTPTS[v2];"
        f"[0:a]atrim=start={end:.3f},asetpts=PTS-STARTPTS[a2];"
        f"[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
    )

    tools = check_tools()
    encoder = (["-c:v", "h264_videotoolbox", "-b:v", "4M"]
               if tools["videotoolbox_encode"]
               else ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])

    with Stage("build", f"{mode} over {format_tc(start)} -> {format_tc(end)}"):
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src,
             "-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
            + encoder + ["-c:a", "aac", "-b:a", "128k", out])


def midpoint_phash(path, t):
    """phash of the frame at time `t`, straight from the file."""
    import imagehash
    from PIL import Image

    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", path,
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    if not proc.stdout:
        raise ToolError(
            f"Could not read a frame at {format_tc(t)} from {path!r}:\n"
            + proc.stderr.decode("utf-8", "replace")[-400:]
        )
    return imagehash.phash(Image.open(io.BytesIO(proc.stdout)))


def check(src, out, start, end):
    """Report whether the built file will actually register as a replace."""
    mid = (start + end) / 2.0
    with Stage("check", f"comparing the altered range at {format_tc(mid)}"):
        distance = midpoint_phash(src, mid) - midpoint_phash(out, mid)

    log("")
    log(f"  phash distance over the altered range: {distance}/64")
    if distance > MIN_USEFUL_DISTANCE:
        log(f"  OK -- above {MIN_USEFUL_DISTANCE}, so this will be reported as `replace`.")
        return True
    if distance > 10:
        log(f"  WEAK -- {distance} lands in the 11-20 'weak match' band. The shots will "
            f"still be aligned to each other, so this may be reported as `replace`, but "
            f"it is close to the line. Use a stronger transform.")
        return False
    log(f"  INVISIBLE -- at {distance}, this reads as an exact match and the diff will "
        f"report NO change. phash sees coarse greyscale structure only, so colour, "
        f"grade and blur changes do not register. Try --mode flip, reframe, or "
        f"substitute.")
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a `replace` variant of one video: same runtime and cuts, "
                    "different picture over a chosen range.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="modes:\n" + "\n".join(
            f"  {name:<12} {cfg['blurb']}" for name, cfg in MODES.items()
        ),
    )
    parser.add_argument("video", help="source video")
    parser.add_argument("-o", "--output", required=True, help="variant to write")
    parser.add_argument("--start", required=True,
                        help="start of the altered range (90, 1:30, or 00:01:30.5)")
    parser.add_argument("--end", required=True, help="end of the altered range")
    parser.add_argument("--mode", default="flip", choices=sorted(MODES),
                        help="how to alter the picture (default flip)")
    parser.add_argument("--from", dest="substitute_from", default=None,
                        help="for --mode substitute: where to take the replacement "
                             "footage from")
    parser.add_argument("--no-check", action="store_true",
                        help="skip the phash verification of the result")
    parser.add_argument("--no-snap", action="store_true",
                        help="use the given timecodes verbatim instead of moving them "
                             "onto shot boundaries (expect an extra insert region)")
    args = parser.parse_args(argv)

    try:
        check_tools()
        if not os.path.isfile(args.video):
            raise ToolError(f"Video not found: {args.video}")

        start = parse_time(args.start)
        end = parse_time(args.end)
        substitute_from = (parse_time(args.substitute_from)
                           if args.substitute_from else None)

        duration = ffprobe_duration(args.video)
        if end <= start:
            raise ToolError(f"--end ({format_tc(end)}) must be after --start "
                            f"({format_tc(start)}).")
        if end > duration:
            raise ToolError(f"--end ({format_tc(end)}) is past the end of the video "
                            f"({format_tc(duration)}).")
        if args.mode == "substitute":
            if substitute_from is None:
                raise ToolError("--mode substitute needs --from <timecode>")
            if substitute_from + (end - start) > duration:
                raise ToolError(
                    f"--from {format_tc(substitute_from)} plus {end - start:.2f}s runs "
                    f"past the end of the video ({format_tc(duration)}). Pick an "
                    f"earlier --from."
                )

        log(f"Source     {args.video}  ({format_tc(duration)})")

        if not args.no_snap:
            with Stage("snap", "aligning the range to shot boundaries"):
                start, end = snap_to_shots(args.video, start, end)
            if end <= start:
                raise ToolError(
                    "Snapping collapsed the range. Pass --no-snap, or pick a range "
                    "that spans at least one whole shot."
                )

        log(f"Altering   {format_tc(start)} -> {format_tc(end)} "
            f"({end - start:.2f}s), mode={args.mode}")
        if substitute_from is not None:
            log(f"Taking     footage from {format_tc(substitute_from)}")
            log("")
            log("  NOTE: substituted footage brings its own cuts with it. Unless the "
                "range starting")
            log("  at --from is a single continuous shot of the same length, the "
                "variant will have")
            log("  a different shot count and the diff will report an insert or delete "
                "alongside")
            log("  the replace. For exactly one clean replace region, use --mode flip "
                "or reframe.")
            log("")

        build(args.video, args.output, start, end, args.mode, substitute_from)

        ok = True
        if not args.no_check:
            ok = check(args.video, args.output, start, end)

    except (ToolError, ValueError) as exc:
        die(str(exc))

    log("")
    log(f"Wrote {args.output}")
    log("")
    log("Diff it against the original:")
    log(f"  python fingerprint.py {args.video} -o out/a.json")
    log(f"  python fingerprint.py {args.output} -o out/b.json")
    log(f"  python diff.py out/a.json out/b.json -o out/report.json")
    log(f"  python report.py out/report.json -o out/report.html")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
