"""Generate a synthetic multi-scene source video, for testing without a download.

    python make_synthetic_source.py -o sample_source.mp4 [--scenes 16]

The real demo should use a 10-15 minute public domain short. This exists so the
pipeline and the definition-of-done checks can be run on a machine with no
network access, and so the fixtures have exactly known shot boundaries.

Each scene is a distinct still composition with a distinct three-tone audio
bed, joined by hard cuts -- the easiest possible case for shot segmentation,
which is the point: it isolates bugs in the pipeline from bugs in detection.
"""

import argparse
import os
import random
import sys
import tempfile

from vdiff_common import Stage, ToolError, check_tools, die, log, run

WIDTH, HEIGHT = 1280, 720
FPS = 25
SAMPLE_RATE = 44100


def make_scene_image(path, seed):
    """A static composition with enough low-frequency structure for phash.

    Flat colour fields are avoided deliberately: phash works on a DCT of the
    greyscale image, so two different solid colours can hash identically.
    """
    from PIL import Image, ImageDraw

    rng = random.Random(seed)
    base = (rng.randint(20, 90), rng.randint(20, 90), rng.randint(20, 90))
    img = Image.new("RGB", (WIDTH, HEIGHT), base)
    draw = ImageDraw.Draw(img)

    # A few large blocks give strong, well-separated low-frequency content.
    for _ in range(rng.randint(5, 9)):
        x0 = rng.randint(-100, WIDTH - 100)
        y0 = rng.randint(-100, HEIGHT - 100)
        w = rng.randint(180, 620)
        h = rng.randint(140, 460)
        grey = rng.randint(70, 245)
        colour = (
            min(255, grey + rng.randint(-40, 40)),
            min(255, grey + rng.randint(-40, 40)),
            min(255, grey + rng.randint(-40, 40)),
        )
        if rng.random() < 0.5:
            draw.rectangle([x0, y0, x0 + w, y0 + h], fill=colour)
        else:
            draw.ellipse([x0, y0, x0 + w, y0 + h], fill=colour)

    # Bars across one edge add high-contrast detail that survives downscaling.
    bar_h = rng.randint(40, 90)
    for i in range(10):
        shade = 255 if i % 2 == 0 else 15
        draw.rectangle(
            [i * WIDTH // 10, HEIGHT - bar_h, (i + 1) * WIDTH // 10, HEIGHT],
            fill=(shade, shade, shade),
        )
    img.save(path)


def scene_audio_expr(seed, duration):
    """Three tones per scene, well away from the 1 kHz dub tone."""
    rng = random.Random(seed * 977 + 13)
    freqs = sorted(rng.sample([120, 180, 240, 330, 420, 550, 660, 780, 1400, 1800], 3))
    left = "+".join(f"0.25*sin(2*PI*{f}*t)" for f in freqs)
    right = "+".join(f"0.25*sin(2*PI*{int(f * 1.02)}*t)" for f in freqs)
    return f"aevalsrc={left}|{right}:s={SAMPLE_RATE}:d={duration:.3f}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a synthetic multi-scene video for testing the diff pipeline."
    )
    parser.add_argument("-o", "--output", default="sample_source.mp4")
    parser.add_argument("--scenes", type=int, default=16, help="number of shots")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    try:
        check_tools()
        rng = random.Random(args.seed)
        durations = [round(rng.uniform(5.0, 13.0), 2) for _ in range(args.scenes)]

        with tempfile.TemporaryDirectory() as tmp:
            parts = []
            with Stage("scenes", f"rendering {args.scenes} shots"):
                for i, dur in enumerate(durations):
                    png = os.path.join(tmp, f"s{i:03d}.png")
                    mp4 = os.path.join(tmp, f"s{i:03d}.mp4")
                    make_scene_image(png, args.seed * 1000 + i)
                    run([
                        "ffmpeg", "-nostdin", "-v", "error", "-y",
                        "-loop", "1", "-framerate", str(FPS), "-i", png,
                        "-f", "lavfi", "-i", scene_audio_expr(i, dur),
                        "-t", f"{dur:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-r", str(FPS),
                        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                        "-shortest", mp4,
                    ])
                    parts.append(mp4)

            with Stage("concat", "joining shots with hard cuts"):
                list_path = os.path.join(tmp, "list.txt")
                with open(list_path, "w") as fh:
                    for p in parts:
                        fh.write(f"file '{p}'\n")
                out_dir = os.path.dirname(os.path.abspath(args.output))
                os.makedirs(out_dir, exist_ok=True)
                run([
                    "ffmpeg", "-nostdin", "-v", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", list_path,
                    "-c", "copy", args.output,
                ])
    except ToolError as exc:
        die(str(exc))

    total = sum(durations)
    log(f"\nWrote {args.output}: {args.scenes} shots, {total:.1f}s total")
    log("Shot boundaries (seconds):")
    t = 0.0
    for i, d in enumerate(durations):
        log(f"  shot {i:2d}  {t:7.2f} -> {t + d:7.2f}")
        t += d
    return 0


if __name__ == "__main__":
    sys.exit(main())
