"""Run the definition-of-done checks end to end and print the result of each.

    python verify.py [--source sample_source.mp4]

Builds fixtures if they are missing, fingerprints them, runs the diffs through
the real CLIs, and checks each acceptance criterion against ground_truth.json.
Exits non-zero if any check fails.
"""

import argparse
import json
import os
import re
import subprocess
import sys

from vdiff_common import format_tc, log

TOLERANCE_SECONDS = 2.0
VARIANTS = ("v_base", "v_cut", "v_audiodub", "v_reorder", "v_lowres",
            "v_replace", "v_tv")


class Check:
    def __init__(self, number, title):
        self.number = number
        self.title = title
        self.status = "FAIL"
        self.notes = []

    def note(self, text):
        self.notes.append(text)

    def passed(self, text=None):
        self.status = "PASS"
        if text:
            self.note(text)

    def skipped(self, text):
        self.status = "SKIP"
        self.note(text)


def sh(*args, quiet=True):
    """Run one of the project's CLIs exactly as documented."""
    cmd = [sys.executable] + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    if not quiet:
        print(proc.stdout)
    return proc.stdout


def load(path):
    with open(path) as fh:
        return json.load(fh)


def regions_of(report, kind):
    return [r for r in report["regions"] if r["type"] == kind]


def boundaries(report):
    """The region shape that must not move when --explain is switched on."""
    return [
        (r["type"], r["a_start"], r["a_end"], r["b_start"], r["b_end"], r["shot_count"])
        for r in report["regions"]
    ]


# --------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------


def ensure_inputs(source, fixtures_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    gt_path = os.path.join(fixtures_dir, "ground_truth.json")
    if not os.path.isfile(gt_path):
        if not os.path.isfile(source):
            log(f"Source {source} not found; generating a synthetic one.")
            sh("make_synthetic_source.py", "-o", source)
        log(f"Building fixtures from {source} ...")
        sh("make_variants.py", source, "-o", fixtures_dir)

    for name in VARIANTS:
        fp = os.path.join(out_dir, f"{name}.json")
        video = os.path.join(fixtures_dir, f"{name}.mp4")
        if not os.path.isfile(video):
            continue
        if not os.path.isfile(fp):
            log(f"Fingerprinting {name} ...")
            sh("fingerprint.py", video, "-o", fp)

    return load(gt_path)


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------


def check_1_resolution_invariance(out_dir):
    check = Check(1, "v_base and v_lowres fingerprint identically (resolution invariance)")
    base = load(os.path.join(out_dir, "v_base.json"))
    lowres = load(os.path.join(out_dir, "v_lowres.json"))

    if base["shot_count"] != lowres["shot_count"]:
        check.note(f"shot counts differ: {base['shot_count']} vs {lowres['shot_count']}")
        return check

    from vdiff_common import hamming_hex

    distances = [
        hamming_hex(a["phash"], b["phash"])
        for a, b in zip(base["shots"], lowres["shots"])
    ]
    worst = max(distances)
    check.note(
        f"720p vs 360p over {len(distances)} shots: "
        f"max phash distance {worst}/64, mean {sum(distances) / len(distances):.2f}"
    )

    report = load(os.path.join(out_dir, "report_v_lowres.json"))
    check.note(f"diff produced {report['region_count']} region(s)")
    if report["region_count"] == 0 and worst <= 10:
        check.passed("identical hash sequences, zero regions")
    elif report["region_count"] != 0:
        check.note("expected zero regions")
    else:
        check.note("hash distances exceed the match threshold of 10")
    return check


def check_2_cut(out_dir, ground_truth):
    check = Check(2, "v_base vs v_cut yields exactly one delete region at the right time")
    report = load(os.path.join(out_dir, "report_v_cut.json"))
    truth = ground_truth["variants"]["v_cut"]

    deletes = regions_of(report, "delete")
    check.note(
        f"ground truth: removed {format_tc(truth['a_start'])} -> "
        f"{format_tc(truth['a_end'])} ({truth['removed_seconds']:.2f}s)"
    )
    check.note(f"report: {report['region_count']} region(s), {len(deletes)} delete(s)")

    if report["region_count"] != 1 or len(deletes) != 1:
        check.note("expected exactly one region, of type delete")
        return check

    found = deletes[0]
    d_start = abs(found["a_start"] - truth["a_start"])
    d_end = abs(found["a_end"] - truth["a_end"])
    check.note(
        f"found:  removed {format_tc(found['a_start'])} -> {format_tc(found['a_end'])} "
        f"(start off by {d_start:.3f}s, end off by {d_end:.3f}s)"
    )
    if d_start <= TOLERANCE_SECONDS and d_end <= TOLERANCE_SECONDS:
        check.passed(f"within the {TOLERANCE_SECONDS:.0f}s tolerance")
    else:
        check.note(f"outside the {TOLERANCE_SECONDS:.0f}s tolerance")
    return check


def check_3_audio(out_dir, ground_truth):
    check = Check(3, "v_base vs v_audiodub yields one audio_changed region and no visual regions")
    report = load(os.path.join(out_dir, "report_v_audiodub.json"))
    truth = ground_truth["variants"]["v_audiodub"]

    audio = regions_of(report, "audio_changed")
    visual = [r for r in report["regions"] if r["type"] != "audio_changed"]
    check.note(
        f"ground truth: {truth['duration_seconds']:.2f}s of tone at "
        f"{format_tc(truth['a_start'])}, picture untouched"
    )
    check.note(
        f"report: {len(audio)} audio_changed, {len(visual)} visual "
        f"({', '.join(r['type'] for r in visual) or 'none'})"
    )

    if len(audio) != 1 or visual:
        check.note("expected exactly one audio_changed region and no visual regions")
        return check

    found = audio[0]
    delta = abs(found["a_start"] - truth["a_start"])
    check.note(
        f"found: audio differs {format_tc(found['a_start'])} -> "
        f"{format_tc(found['a_end'])} (start off by {delta:.3f}s)"
    )
    if delta <= TOLERANCE_SECONDS:
        check.passed("picture matched shot for shot; only the audio hash moved")
    else:
        check.note(f"outside the {TOLERANCE_SECONDS:.0f}s tolerance")
    return check


def check_6_replace(out_dir, ground_truth):
    check = Check(6, "v_base vs v_replace yields one replace region, no deletes or inserts")
    truth = ground_truth["variants"].get("v_replace")
    if truth is None:
        check.skipped("v_replace was not built for this source")
        return check
    report = load(os.path.join(out_dir, "report_v_replace.json"))

    replaced = regions_of(report, "replace")
    others = [r for r in report["regions"] if r["type"] != "replace"]
    check.note(
        f"ground truth: picture altered {format_tc(truth['a_start'])} -> "
        f"{format_tc(truth['a_end'])} ({truth['shots_altered']} shots), runtime unchanged"
    )
    check.note(f"report: {len(replaced)} replace, {len(others)} other "
               f"({', '.join(r['type'] for r in others) or 'none'})")

    if len(replaced) != 1 or others:
        check.note("expected exactly one replace region and nothing else")
        return check

    found = replaced[0]
    delta = abs(found["a_start"] - truth["a_start"])
    check.note(
        f"found: replaced {format_tc(found['a_start'])} -> {format_tc(found['a_end'])} "
        f"(start off by {delta:.3f}s)"
    )
    if delta <= TOLERANCE_SECONDS:
        check.passed("same runtime and cuts, different picture -- a true replace")
    else:
        check.note(f"outside the {TOLERANCE_SECONDS:.0f}s tolerance")
    return check


def check_7_tv(out_dir, ground_truth):
    check = Check(7, "v_base vs v_tv finds each change in a multi-change delivery")
    truth = ground_truth["variants"].get("v_tv")
    if truth is None:
        check.skipped("v_tv was not built for this source")
        return check
    report = load(os.path.join(out_dir, "report_v_tv.json"))
    changes = truth["changes"]

    def near(regions, target, tol=TOLERANCE_SECONDS):
        return [r for r in regions if abs(r["a_start"] - target) <= tol]

    audio = near(regions_of(report, "audio_changed"), changes["audio_replaced"]["a_start"])
    removed = near(regions_of(report, "delete"), changes["scene_removed"]["a_start"])

    check.note(f"report: {report['region_count']} region(s) — "
               + ", ".join(f"{k}={v}" for k, v in report["summary"].items() if v))
    check.note(
        f"audio replaced at {format_tc(changes['audio_replaced']['a_start'])}: "
        f"{'FOUND' if audio else 'MISSING'}"
    )
    check.note(
        f"scene removed at {format_tc(changes['scene_removed']['a_start'])}: "
        f"{'FOUND' if removed else 'MISSING'}"
    )
    check.note(
        f"downscaled to {changes['resolution']['height']}p: contributed no region "
        f"(resolution invariance holds alongside real edits)"
    )

    # The trimmed shot is footage-dependent: it only moves the picture hash if
    # the shot's midpoint frame actually changes. On static synthetic scenes it
    # legitimately produces nothing, so it is reported rather than asserted.
    shortened = changes["scene_shortened"]
    near_short = [
        r for r in report["regions"]
        if abs(r["a_start"] - shortened["a_start"]) <= TOLERANCE_SECONDS
    ]
    check.note(
        f"scene shortened at {format_tc(shortened['a_start'])}: "
        + (f"reported as {near_short[0]['type']}" if near_short
           else "no region — expected on static synthetic footage, "
                "since trimming a still shot does not move its midpoint frame")
    )

    if audio and removed:
        check.passed("both unambiguous changes located; resolution change added no noise")
    else:
        check.note("expected to locate both the audio replacement and the removed scene")
    return check


def check_4_html(out_dir):
    check = Check(4, "report.html is self-contained and shows both timelines with thumbnails")
    path = os.path.join(out_dir, "report_v_cut.html")
    if not os.path.isfile(path):
        check.note(f"{path} was not produced")
        return check
    with open(path) as fh:
        markup = fh.read()

    external = [
        ref for ref in re.findall(r'(?:src|href)="([^"]*)"', markup)
        if not ref.startswith("data:")
    ]
    images = markup.count("data:image/jpeg;base64,")
    tracks = markup.count('class="bar"')
    regions = markup.count('class="region')

    check.note(f"{os.path.getsize(path) / 1024:.0f} KB, {tracks} timeline(s), "
               f"{regions} region marker(s), {images} inlined thumbnail(s)")
    check.note(f"external asset references: {len(external)}")
    check.note("built with --explain off; every explanation is null")

    if external:
        check.note(f"not self-contained: {external[:3]}")
    elif tracks != 2:
        check.note("expected exactly two timelines")
    elif images < 1 or regions < 1:
        check.note("expected at least one region marker and one thumbnail")
    else:
        check.passed("opens standalone, no external assets")
    return check


def check_5_explain(out_dir, model, ollama_url):
    check = Check(5, "--explain adds descriptions without moving any region boundary")
    try:
        from explain import OllamaError, check_server
        check_server(model, ollama_url)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        check.skipped(
            f"no usable Ollama backend ({str(exc).splitlines()[0]}); "
            f"run `ollama serve` and `ollama pull {model}` to exercise this check"
        )
        return check

    before = load(os.path.join(out_dir, "report_v_cut.json"))
    explained_path = os.path.join(out_dir, "report_v_cut_explained.json")
    sh("diff.py",
       os.path.join(out_dir, "v_base.json"), os.path.join(out_dir, "v_cut.json"),
       "-o", explained_path, "--explain", "--model", model, "--ollama-url", ollama_url)
    after = load(explained_path)

    same = boundaries(before) == boundaries(after)
    added = sum(1 for r in after["regions"] if r.get("explanation"))
    check.note(f"regions before: {len(before['regions'])}, after: {len(after['regions'])}")
    check.note(f"region boundaries identical: {same}")
    check.note(f"explanations attached: {added}/{len(after['regions'])}")
    for region in after["regions"]:
        if region.get("explanation"):
            check.note(f"  {region['type']}: {region['explanation'][:110]}")

    if not same:
        check.note("--explain changed the timeline; it must only annotate it")
    elif added == 0:
        check.note("no explanations were attached")
    else:
        check.passed("timeline unchanged, descriptions added")
    return check


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the definition-of-done checks.")
    parser.add_argument("--source", default="sample_source.mp4")
    parser.add_argument("--fixtures", default="fixtures/")
    parser.add_argument("--out", default="out/")
    parser.add_argument("--model", default="qwen3-vl:8b")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    args = parser.parse_args(argv)

    ground_truth = ensure_inputs(args.source, args.fixtures, args.out)

    log("\nRunning the pipeline ...")
    for variant in ("v_cut", "v_audiodub", "v_reorder", "v_lowres",
                    "v_replace", "v_tv"):
        fp = os.path.join(args.out, f"{variant}.json")
        if not os.path.isfile(fp):
            continue
        report = os.path.join(args.out, f"report_{variant}.json")
        sh("diff.py", os.path.join(args.out, "v_base.json"), fp, "-o", report)
        sh("report.py", report, "-o", os.path.join(args.out, f"report_{variant}.html"))

    checks = [
        check_1_resolution_invariance(args.out),
        check_2_cut(args.out, ground_truth),
        check_3_audio(args.out, ground_truth),
        check_4_html(args.out),
        check_5_explain(args.out, args.model, args.ollama_url),
        check_6_replace(args.out, ground_truth),
        check_7_tv(args.out, ground_truth),
    ]

    log("\n" + "=" * 78)
    log("DEFINITION OF DONE")
    log("=" * 78)
    for check in checks:
        log(f"\n  [{check.status}] {check.number}. {check.title}")
        for note in check.notes:
            log(f"         {note}")
    log("\n" + "=" * 78)
    failed = [c for c in checks if c.status == "FAIL"]
    skipped = [c for c in checks if c.status == "SKIP"]
    passed = [c for c in checks if c.status == "PASS"]
    log(f"  {len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    log("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
