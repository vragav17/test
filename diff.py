"""Stages 5-6: align two fingerprints and report what changed.

    python diff.py <fp_a.json> <fp_b.json> -o report.json [--explain] [--model qwen3-vl:8b]

Writes report.json (regions, no thumbnails) plus a report.thumbs.json sidecar
holding the inlined JPEGs, which report.py folds back in.
"""

import argparse
import json
import os
import sys
import time

from align import (
    AUDIO_CHANGE_DISTANCE,
    align,
    merge_regions,
    retag_audio_changes,
)
from vdiff_common import (
    Stage,
    ToolError,
    build_proxy,
    check_tools,
    die,
    extract_frames,
    format_tc,
    log,
    png_to_jpeg_b64,
)

MAX_THUMBNAILS = 3

REGION_LABELS = {
    "delete": "removed from B",
    "insert": "added in B",
    "replace": "replaced",
    "audio_changed": "audio differs",
}


def load_fingerprint(path):
    if not os.path.isfile(path):
        raise ToolError(f"Fingerprint file not found: {path}")
    with open(path) as fh:
        data = json.load(fh)
    for key in ("shots", "duration_seconds", "source"):
        if key not in data:
            raise ToolError(f"{path} is not a fingerprint file (missing {key!r}).")
    if not data["shots"]:
        raise ToolError(f"{path} contains no shots.")
    return data


def resolve_proxy(fp, label):
    """Find the proxy this fingerprint was built from, rebuilding if it was cleared."""
    proxy = fp.get("proxy")
    if proxy and os.path.isfile(proxy):
        return proxy
    source = fp.get("source_path")
    if source and os.path.isfile(source):
        log(f"    proxy for {label} is missing from the cache; rebuilding it")
        return build_proxy(source)
    raise ToolError(
        f"Cannot find the proxy for {label} ({proxy!r}) and the source "
        f"({source!r}) is gone too. Re-run fingerprint.py for that video."
    )


def pick_thumbnail_shots(indices, limit=MAX_THUMBNAILS):
    """Up to `limit` shot indices, spread evenly across the region."""
    if len(indices) <= limit:
        return list(indices)
    step = (len(indices) - 1) / (limit - 1)
    return [indices[round(i * step)] for i in range(limit)]


def build_thumbnails(regions, a_fp, b_fp, a_proxy, b_proxy):
    """Inline up to three JPEGs per side per region.

    These are what make the report readable with no model output at all, so
    they are built unconditionally. Frames for a whole side are extracted in
    one batched, cached pass rather than one ffmpeg process per thumbnail.
    """
    plan = {"a": [], "b": []}
    for idx, region in enumerate(regions):
        for side, indices, fp in (
            ("a", region.a_indices, a_fp),
            ("b", region.b_indices, b_fp),
        ):
            for shot_idx in pick_thumbnail_shots(indices):
                shot = fp["shots"][shot_idx]
                plan[side].append((idx, (shot["start"] + shot["end"]) / 2.0))

    frames = {}
    for side, proxy, fp in (("a", a_proxy, a_fp), ("b", b_proxy, b_fp)):
        times = [t for _, t in plan[side]]
        if times:
            frames[side] = extract_frames(
                proxy, times, duration=fp["duration_seconds"]
            )
        else:
            frames[side] = []

    thumbs = [{"thumbnails_a": [], "thumbnails_b": []} for _ in regions]
    for side in ("a", "b"):
        for (region_idx, _), png in zip(plan[side], frames[side]):
            thumbs[region_idx][f"thumbnails_{side}"].append(png_to_jpeg_b64(png))
    return thumbs


def print_summary(regions, a_fp, b_fp, explanations=None):
    log("\n" + "=" * 78)
    log(f"DIFF  A: {a_fp['source']}  ({format_tc(a_fp['duration_seconds'])}, "
        f"{a_fp['shot_count']} shots)")
    log(f"      B: {b_fp['source']}  ({format_tc(b_fp['duration_seconds'])}, "
        f"{b_fp['shot_count']} shots)")
    log("=" * 78)
    if not regions:
        log("  No differences found. The two versions align shot for shot,")
        log("  with matching picture and audio throughout.")
        log("=" * 78)
        return
    for i, region in enumerate(regions):
        label = REGION_LABELS.get(region.type, region.type)
        log(f"  [{i + 1}] {region.type.upper():<14} ({label}), {region.shot_count} shot(s)")
        log(f"      A  {format_tc(region.a_start)} -> {format_tc(region.a_end)}"
            f"   ({region.a_end - region.a_start:6.2f}s)")
        log(f"      B  {format_tc(region.b_start)} -> {format_tc(region.b_end)}"
            f"   ({region.b_end - region.b_start:6.2f}s)")
        if explanations and explanations[i].get("explanation"):
            log(f"      {explanations[i]['explanation']}")
    log("=" * 78)


def run_diff(fp_a_path, fp_b_path, out_path, explain=False,
             model=None, ollama_url=None, audio_threshold=AUDIO_CHANGE_DISTANCE):
    t_start = time.time()
    check_tools()

    a_fp = load_fingerprint(fp_a_path)
    b_fp = load_fingerprint(fp_b_path)
    log(f"Diffing {a_fp['source']} against {b_fp['source']}")

    with Stage("stage 5", "Needleman-Wunsch alignment"):
        ops, score = align(a_fp["shots"], b_fp["shots"])
        log(f"    aligned {a_fp['shot_count']} x {b_fp['shot_count']} shots, "
            f"score {score}")
        retagged = retag_audio_changes(ops, a_fp["shots"], b_fp["shots"],
                                       threshold=audio_threshold)
        if retagged:
            log(f"    audio pass retagged {retagged} matched shot(s) as audio_changed")
        regions = merge_regions(
            ops, a_fp["shots"], b_fp["shots"],
            a_fp["duration_seconds"], b_fp["duration_seconds"],
        )
        log(f"    {len(regions)} changed region(s) from "
            f"{len(ops)} aligned position(s)")

    with Stage("thumbnails", f"up to {MAX_THUMBNAILS} per side per region"):
        a_proxy = resolve_proxy(a_fp, a_fp["source"])
        b_proxy = resolve_proxy(b_fp, b_fp["source"])
        thumbs = build_thumbnails(regions, a_fp, b_fp, a_proxy, b_proxy)

    explanations = None
    if explain:
        from explain import DEFAULT_MODEL, DEFAULT_URL, OllamaError, check_server, describe_regions

        model = model or DEFAULT_MODEL
        ollama_url = ollama_url or DEFAULT_URL
        # Checked before the stage banner opens, so a missing model or a dead
        # server reports cleanly instead of under a "FAILED" heading.
        try:
            check_server(model, ollama_url)
        except OllamaError as exc:
            die(str(exc))
        with Stage("stage 6", f"local descriptions via {model}"):
            explanations = describe_regions(regions, thumbs, model, ollama_url)

    report = {
        "version_a": {
            "source": a_fp["source"],
            "proxy": a_fp.get("proxy"),
            "duration_seconds": a_fp["duration_seconds"],
            "shot_count": a_fp["shot_count"],
        },
        "version_b": {
            "source": b_fp["source"],
            "proxy": b_fp.get("proxy"),
            "duration_seconds": b_fp["duration_seconds"],
            "shot_count": b_fp["shot_count"],
        },
        "alignment_score": score,
        "audio_change_threshold": audio_threshold,
        "explained": bool(explain),
        "region_count": len(regions),
        "summary": {
            kind: sum(1 for r in regions if r.type == kind)
            for kind in ("delete", "insert", "replace", "audio_changed")
        },
        "regions": [],
    }

    for i, region in enumerate(regions):
        entry = {
            "type": region.type,
            "a_start": region.a_start,
            "a_end": region.a_end,
            "b_start": region.b_start,
            "b_end": region.b_end,
            "a_timecode": f"{format_tc(region.a_start)} - {format_tc(region.a_end)}",
            "b_timecode": f"{format_tc(region.b_start)} - {format_tc(region.b_end)}",
            "shot_count": region.shot_count,
            # Thumbnails are omitted here on purpose; they live in the sidecar.
            "thumbnail_count_a": len(thumbs[i]["thumbnails_a"]),
            "thumbnail_count_b": len(thumbs[i]["thumbnails_b"]),
            "description_a": None,
            "description_b": None,
            "explanation": None,
        }
        if explanations:
            entry.update(explanations[i])
        report["regions"].append(entry)

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)

    sidecar = thumbnail_sidecar_path(out_path)
    with open(sidecar, "w") as fh:
        json.dump({"regions": thumbs}, fh)

    print_summary(regions, a_fp, b_fp, explanations)
    log(f"\nWrote {out_path} and {sidecar} in {time.time() - t_start:.1f}s")
    return report


def thumbnail_sidecar_path(report_path):
    stem, _ = os.path.splitext(report_path)
    return f"{stem}.thumbs.json"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Diff two video fingerprints and report what changed between them."
    )
    parser.add_argument("fingerprint_a", help="fingerprint JSON for version A")
    parser.add_argument("fingerprint_b", help="fingerprint JSON for version B")
    parser.add_argument("-o", "--output", required=True, help="report JSON to write")
    parser.add_argument("--explain", action="store_true",
                        help="add plain-English descriptions using a local Ollama model")
    parser.add_argument("--model", default=None,
                        help="Ollama model for --explain (default qwen3-vl:8b)")
    parser.add_argument("--ollama-url", default=None,
                        help="Ollama base URL (default http://localhost:11434)")
    parser.add_argument("--audio-threshold", type=int, default=AUDIO_CHANGE_DISTANCE,
                        help=f"audio hash distance above which a matched shot counts "
                             f"as audio_changed (default {AUDIO_CHANGE_DISTANCE})")
    args = parser.parse_args(argv)

    try:
        run_diff(
            args.fingerprint_a, args.fingerprint_b, args.output,
            explain=args.explain, model=args.model, ollama_url=args.ollama_url,
            audio_threshold=args.audio_threshold,
        )
    except ToolError as exc:
        die(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
