"""Stage 7: render a self-contained HTML report from report.json.

    python report.py report.json -o report.html

Two horizontal timelines drawn to a shared time scale, one per version, with
changed regions colour-coded and clickable. Everything is inlined -- no
external assets, no framework, no build step -- so the file can be opened
straight from disk or mailed to someone.

The report must tell a clear story with every explanation null: the thumbnails
of the changed shots carry it on their own.
"""

import argparse
import html
import json
import os
import sys

from vdiff_common import ToolError, die, log

TYPE_COLOURS = {
    "delete": "#e5484d",
    "insert": "#46a758",
    "replace": "#f5a524",
    "audio_changed": "#8e6fd8",
}

TYPE_LABELS = {
    "delete": "Removed",
    "insert": "Added",
    "replace": "Replaced",
    "audio_changed": "Audio changed",
}

TYPE_BLURBS = {
    "delete": "Present in A, absent from B.",
    "insert": "Absent from A, present in B.",
    "replace": "Both versions have content here, but the picture differs.",
    "audio_changed": "Picture matches shot for shot; the audio does not.",
}

CSS = """
:root {
  --bg: #14161a; --panel: #1c1f25; --line: #2c313a;
  --text: #e6e8ec; --muted: #9aa2ae;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 28px 64px;
  background: var(--bg); color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1180px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; font-weight: 600; letter-spacing: -0.01em; }
.sub { color: var(--muted); margin: 0 0 28px; font-size: 13px; }
.panel {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 20px; margin-bottom: 20px;
}
.versions { display: flex; gap: 28px; flex-wrap: wrap; margin-bottom: 22px; }
.version-name { font-weight: 600; }
.version-meta { color: var(--muted); font-size: 12.5px; }
.tag {
  display: inline-block; width: 20px; height: 20px; line-height: 20px;
  text-align: center; border-radius: 5px; font-weight: 700; font-size: 12px;
  background: #333944; margin-right: 8px;
}
.legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12.5px; color: var(--muted); }
.legend span { display: flex; align-items: center; gap: 7px; }
.swatch { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }

.track { margin-bottom: 16px; }
.track-head {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: 12.5px; margin-bottom: 6px;
}
.track-head .dur { color: var(--muted); font-variant-numeric: tabular-nums; }
.bar {
  position: relative; height: 42px; background: #262b33;
  border: 1px solid var(--line); border-radius: 5px; overflow: hidden;
}
.region {
  position: absolute; top: 0; bottom: 0; min-width: 3px;
  cursor: pointer; opacity: 0.92;
}
.region:hover { opacity: 1; box-shadow: inset 0 0 0 2px rgba(255,255,255,0.85); }
.region.point { min-width: 4px; border-left: 1px solid rgba(255,255,255,0.6); }
.card.flash { box-shadow: 0 0 0 2px #7d879a; }
.ruler { position: relative; height: 20px; margin-top: 5px; }
.tick {
  position: absolute; top: 0; font-size: 11px; color: var(--muted);
  transform: translateX(-50%); font-variant-numeric: tabular-nums; white-space: nowrap;
}
.tick::before {
  content: ""; position: absolute; left: 50%; top: -5px;
  width: 1px; height: 4px; background: var(--line);
}

.card {
  background: var(--panel); border: 1px solid var(--line);
  border-left: 4px solid var(--line);
  border-radius: 8px; margin-bottom: 10px; overflow: hidden;
}
.card-head {
  display: flex; align-items: center; gap: 12px;
  padding: 13px 16px; cursor: pointer; user-select: none;
}
.card-head:hover { background: #22262d; }
.chev { color: var(--muted); font-size: 11px; width: 10px; transition: transform 0.12s; }
.card.open .chev { transform: rotate(90deg); }
.card-type { font-weight: 600; }
.card-times {
  margin-left: auto; color: var(--muted); font-size: 12.5px;
  font-variant-numeric: tabular-nums; text-align: right;
}
.card-body { display: none; padding: 4px 16px 18px; border-top: 1px solid var(--line); }
.card.open .card-body { display: block; }
.blurb { color: var(--muted); font-size: 12.5px; margin: 12px 0 16px; }
.explanation {
  background: #232833; border-left: 3px solid #5a6472;
  padding: 11px 14px; border-radius: 5px; margin: 0 0 18px;
}
.explanation.absent { color: var(--muted); font-style: italic; }
.sides { display: flex; gap: 22px; flex-wrap: wrap; }
.side { flex: 1 1 380px; min-width: 280px; }
.side-title {
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--muted); margin-bottom: 3px;
}
.side-time {
  font-size: 12.5px; font-variant-numeric: tabular-nums; margin-bottom: 9px;
}
.shots { display: flex; gap: 8px; flex-wrap: wrap; }
.shots img {
  width: 172px; border-radius: 4px; border: 1px solid var(--line); display: block;
}
.none { color: var(--muted); font-style: italic; font-size: 12.5px; }
.empty { text-align: center; padding: 40px 20px; }
.empty .big { font-size: 17px; font-weight: 600; margin-bottom: 8px; }
.empty .small { color: var(--muted); }
.desc-pair { font-size: 12.5px; color: var(--muted); margin-top: 9px; }
"""

JS = """
function toggle(id) {
  var card = document.getElementById(id);
  if (card) card.classList.toggle('open');
}
function openRegion(idx) {
  var card = document.getElementById('r' + idx);
  if (!card) return;
  card.classList.add('open');
  card.scrollIntoView({behavior: 'smooth', block: 'center'});
  // Brief flash so it is obvious which card the clicked region maps to.
  card.classList.add('flash');
  setTimeout(function () { card.classList.remove('flash'); }, 900);
}
"""


def esc(text):
    return html.escape(str(text), quote=True)


def short_tc(seconds):
    """mm:ss.s -- compact enough for a ruler tick."""
    seconds = max(0.0, float(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes):d}:{secs:04.1f}"


def full_tc(seconds):
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"


# --------------------------------------------------------------------------
# thumbnails
# --------------------------------------------------------------------------


def load_thumbnails(report_path, report):
    """Thumbnails come from the sidecar diff.py wrote; failing that, re-extract.

    report.json deliberately carries no images, so it stays readable and
    diffable. The sidecar keeps the HTML build a pure formatting step.
    """
    stem, _ = os.path.splitext(report_path)
    sidecar = f"{stem}.thumbs.json"
    n = len(report["regions"])

    if os.path.isfile(sidecar):
        try:
            with open(sidecar) as fh:
                thumbs = json.load(fh)["regions"]
            if len(thumbs) == n:
                return thumbs
            log(f"  WARNING: {sidecar} has {len(thumbs)} entries but the report has "
                f"{n} regions; ignoring it.")
        except (OSError, ValueError, KeyError) as exc:
            log(f"  WARNING: could not read {sidecar} ({exc}); re-extracting instead.")

    return reextract_thumbnails(report)


def reextract_thumbnails(report):
    """Fallback: pull frames straight from the cached proxies."""
    from vdiff_common import extract_frames, png_to_jpeg_b64

    empty = [{"thumbnails_a": [], "thumbnails_b": []} for _ in report["regions"]]
    proxies = {
        "a": report.get("version_a", {}).get("proxy"),
        "b": report.get("version_b", {}).get("proxy"),
    }
    if not any(p and os.path.isfile(p) for p in proxies.values()):
        log("  WARNING: no thumbnail sidecar and no proxies on disk -- the report "
            "will render without images.")
        return empty

    log("  rebuilding thumbnails from the cached proxies")
    for side in ("a", "b"):
        proxy = proxies[side]
        if not proxy or not os.path.isfile(proxy):
            continue
        duration = report[f"version_{side}"]["duration_seconds"]
        plan = []
        for idx, region in enumerate(report["regions"]):
            start, end = region[f"{side}_start"], region[f"{side}_end"]
            if end - start <= 0.01:
                continue  # a gap on this side has nothing to show
            count = min(3, max(1, region["shot_count"]))
            for k in range(count):
                frac = (k + 0.5) / count
                plan.append((idx, start + (end - start) * frac))
        if not plan:
            continue
        try:
            frames = extract_frames(proxy, [t for _, t in plan], duration=duration)
        except ToolError as exc:
            log(f"  WARNING: could not extract thumbnails for side {side.upper()}: {exc}")
            continue
        for (idx, _), png in zip(plan, frames):
            empty[idx][f"thumbnails_{side}"].append(png_to_jpeg_b64(png))
    return empty


# --------------------------------------------------------------------------
# html
# --------------------------------------------------------------------------


def render_track(label, info, regions, side, max_duration):
    """One timeline bar, scaled so both versions share a time axis."""
    duration = float(info["duration_seconds"]) or 1.0
    width_pct = 100.0 * duration / max_duration

    marks = []
    for idx, region in enumerate(regions):
        start = float(region[f"{side}_start"])
        end = float(region[f"{side}_end"])
        left = 100.0 * start / duration
        span = 100.0 * max(end - start, 0.0) / duration
        point = " point" if end - start <= 0.01 else ""
        colour = TYPE_COLOURS.get(region["type"], "#888")
        title = (
            f"{TYPE_LABELS.get(region['type'], region['type'])} - "
            f"{full_tc(start)} to {full_tc(end)} ({region['shot_count']} shot(s))"
        )
        marks.append(
            f'<div class="region{point}" data-region="{idx}" '
            f'style="left:{left:.4f}%;width:{span:.4f}%;background:{colour}" '
            f'title="{esc(title)}" onclick="openRegion({idx})"></div>'
        )

    return f"""
    <div class="track">
      <div class="track-head">
        <div><span class="tag">{esc(label)}</span><b>{esc(info['source'])}</b></div>
        <div class="dur">{full_tc(duration)} &middot; {info['shot_count']} shots</div>
      </div>
      <div class="bar" style="width:{width_pct:.4f}%">{''.join(marks)}</div>
    </div>"""


def render_ruler(max_duration, ticks=8):
    marks = []
    for i in range(ticks + 1):
        frac = i / ticks
        marks.append(
            f'<div class="tick" style="left:{frac * 100:.4f}%">'
            f"{short_tc(max_duration * frac)}</div>"
        )
    return f'<div class="ruler">{"".join(marks)}</div>'


def render_card(idx, region, thumbs):
    kind = region["type"]
    colour = TYPE_COLOURS.get(kind, "#888")
    label = TYPE_LABELS.get(kind, kind)

    explanation = region.get("explanation")
    if explanation:
        exp_html = f'<div class="explanation">{esc(explanation)}</div>'
    else:
        exp_html = (
            '<div class="explanation absent">No description '
            "(re-run diff.py with --explain to add one).</div>"
        )

    sides = []
    for side, name in (("a", "Version A"), ("b", "Version B")):
        start, end = region[f"{side}_start"], region[f"{side}_end"]
        images = thumbs.get(f"thumbnails_{side}") or []
        if end - start <= 0.01:
            time_html = f'<div class="side-time">at {full_tc(start)} &mdash; nothing here</div>'
        else:
            time_html = (
                f'<div class="side-time">{full_tc(start)} &rarr; {full_tc(end)} '
                f'<span class="none">({end - start:.2f}s)</span></div>'
            )
        if images:
            shots = "".join(
                f'<img src="data:image/jpeg;base64,{img}" alt="{esc(name)} frame">'
                for img in images
            )
        else:
            shots = '<div class="none">No frames on this side.</div>'
        desc = region.get(f"description_{side}")
        desc_html = f'<div class="desc-pair">{esc(desc)}</div>' if desc else ""
        sides.append(
            f'<div class="side"><div class="side-title">{esc(name)}</div>'
            f'{time_html}<div class="shots">{shots}</div>{desc_html}</div>'
        )

    # Open by default: the thumbnails are the report's substance when there are
    # no descriptions, so they should be on screen without a click.
    return f"""
    <div class="card open" id="r{idx}" style="border-left-color:{colour}">
      <div class="card-head" onclick="toggle('r{idx}')">
        <span class="chev">&#9654;</span>
        <span class="swatch" style="background:{colour}"></span>
        <span class="card-type">{esc(label)}</span>
        <span class="none">{region['shot_count']} shot(s)</span>
        <span class="card-times">
          A {full_tc(region['a_start'])} &rarr; {full_tc(region['a_end'])}<br>
          B {full_tc(region['b_start'])} &rarr; {full_tc(region['b_end'])}
        </span>
      </div>
      <div class="card-body">
        <div class="blurb">{esc(TYPE_BLURBS.get(kind, ''))}</div>
        {exp_html}
        <div class="sides">{''.join(sides)}</div>
      </div>
    </div>"""


def render_html(report, thumbnails):
    a_info = report["version_a"]
    b_info = report["version_b"]
    regions = report["regions"]
    max_duration = max(
        float(a_info["duration_seconds"]), float(b_info["duration_seconds"]), 1.0
    )

    counts = report.get("summary", {})
    used = [k for k in ("delete", "insert", "replace", "audio_changed") if counts.get(k)]
    legend = "".join(
        f'<span><i class="swatch" style="background:{TYPE_COLOURS[k]}"></i>'
        f"{TYPE_LABELS[k]} ({counts[k]})</span>"
        for k in used
    ) or '<span>No changes detected.</span>'

    delta = float(b_info["duration_seconds"]) - float(a_info["duration_seconds"])
    delta_text = (
        f"B is {abs(delta):.2f}s {'longer' if delta > 0 else 'shorter'} than A"
        if abs(delta) >= 0.05 else "Both versions have the same runtime"
    )

    if regions:
        cards = "".join(
            render_card(i, r, thumbnails[i]) for i, r in enumerate(regions)
        )
        body = f'<div class="regions">{cards}</div>'
    else:
        body = """
        <div class="panel empty">
          <div class="big">No differences found</div>
          <div class="small">The two versions align shot for shot, with matching
          picture and audio throughout.</div>
        </div>"""

    return f"""<title>Version diff &mdash; {esc(a_info['source'])} vs {esc(b_info['source'])}</title>
<style>{CSS}</style>
<div class="wrap">
  <h1>Version diff</h1>
  <p class="sub">{esc(a_info['source'])} &nbsp;vs&nbsp; {esc(b_info['source'])}
     &nbsp;&middot;&nbsp; {len(regions)} changed region(s)
     &nbsp;&middot;&nbsp; {esc(delta_text)}</p>

  <div class="panel">
    {render_track("A", a_info, regions, "a", max_duration)}
    {render_track("B", b_info, regions, "b", max_duration)}
    {render_ruler(max_duration)}
    <div class="legend" style="margin-top:16px">{legend}</div>
  </div>

  {body}
</div>
<script>{JS}</script>
"""


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render a self-contained HTML report from a diff report.json."
    )
    parser.add_argument("report", help="report.json produced by diff.py")
    parser.add_argument("-o", "--output", required=True, help="HTML file to write")
    args = parser.parse_args(argv)

    try:
        if not os.path.isfile(args.report):
            raise ToolError(f"Report file not found: {args.report}")
        with open(args.report) as fh:
            report = json.load(fh)
        if "regions" not in report:
            raise ToolError(f"{args.report} is not a diff report (no 'regions' key).")

        thumbnails = load_thumbnails(args.report, report)
        html_text = render_html(report, thumbnails)

        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w") as fh:
            fh.write(html_text)
    except ToolError as exc:
        die(str(exc))

    size_kb = os.path.getsize(args.output) / 1024
    log(f"Wrote {args.output} ({size_kb:.0f} KB, self-contained) "
        f"- {len(report['regions'])} region(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
