# Video version diff

Takes two video files that are different cuts of the same title and produces a
timecoded, plain-English report of what changed between them.

Content suppliers deliver the same title as several versions — full cut,
broadcast-safe, territory-specific, alternate audio — and the metadata saying
what differs between them is unreliable or absent. Today the only way to know
is for someone to watch both files side by side. This answers "what actually
changed between these two files" automatically.

Everything runs locally. No cloud services, no API keys, no network calls at
runtime except to a local Ollama server on `localhost:11434`, and that part is
optional.

## Install

Python 3.11 specifically — librosa depends on numba, whose support for newer
CPython releases lags.

```sh
uv venv --python 3.11 .venv
uv pip install -r requirements.txt
```

`ffmpeg` and `ffprobe` must be on PATH (`brew install ffmpeg`). On Apple
Silicon the pipeline uses `videotoolbox` for hardware decode and
`h264_videotoolbox` for the proxy encode; if either is unavailable it falls
back to software with a printed warning.

## Quickstart

```sh
# One source video -> a fixture set with known differences, and ground_truth.json
python make_variants.py <a-10-to-15-minute-short.mp4> -o fixtures/

# Fingerprint each version
python fingerprint.py fixtures/v_base.mp4 -o out/v_base.json
python fingerprint.py fixtures/v_cut.mp4  -o out/v_cut.json

# Diff them, then render the report
python diff.py out/v_base.json out/v_cut.json -o out/report.json
python report.py out/report.json -o out/report.html
```

Open `out/report.html`. Add `--explain` to `diff.py` for model-written
descriptions.

No video to hand? `python make_synthetic_source.py -o sample_source.mp4`
builds one, so the whole pipeline and every acceptance check can run with no
download.

## UI

There is also a local web app over the same pipeline, if you would rather not
drive four CLIs by hand:

```sh
uv pip install -r requirements-ui.txt
python app.py            # then open http://127.0.0.1:8765
```

Pick two files, watch the workflow run, read the result. A comparison is a
**job** made of named stages in two parallel lanes — one per video — that
converge at alignment:

```
  A:  probe -> proxy -> shots -> fingerprint --.
                                                >-- align -> thumbnails -> [describe] -> report
  B:  probe -> proxy -> shots -> fingerprint --'
```

The two lanes genuinely run at the same time, so the diagram is not decoration.
Each stage shows its own status and elapsed time, the pipeline's output streams
into the page over SSE as it runs, and finished jobs keep their history so you
can reopen any of them later. Each job has its own URL (`#job/<id>`).

Fingerprints are cached by (file signature, cut threshold), so comparing a
third version against one you have already fingerprinted skips that lane
entirely — it shows as `reused`. That makes the three-cut case (theatrical vs
special vs extended) three cheap pairwise jobs rather than six full runs.

Descriptions are best-effort in the UI: if Ollama is unreachable the `describe`
stage is marked failed with the reason and the job still finishes with a full
report. The CLI keeps the stricter behaviour the spec asked for, and exits.

**This is a single-user local tool.** It binds to `127.0.0.1`, has no
authentication, and its file browser can read anything the running user can.
Do not expose it on a network interface.

Note that this UI is deliberately outside the original POC scope, which ruled
out a web server and a job queue. The core CLIs still work with nothing from
`requirements-ui.txt` installed.

## Verifying

`verify.py` runs the five acceptance criteria end to end through the real CLIs
and prints the result of each, checking against `fixtures/ground_truth.json`:

```sh
python verify.py
```

It builds the fixtures and fingerprints if they are missing. Check 5 needs a
running Ollama; without one it reports `SKIP` rather than failing.

## The pipeline

| Stage | What it does |
| --- | --- |
| 0 | Transcode to a 480p proxy. Every later stage reads the proxy, never the source. |
| 1 | Split into shots with PySceneDetect's `ContentDetector`. |
| 2 | 64-bit `imagehash.phash` of each shot's midpoint frame. |
| 3 | 64-bit spectral hash of each shot's audio window. |
| 4 | Write one fingerprint JSON per video. |
| 5 | Needleman-Wunsch alignment of the two shot sequences, then merge into regions. |
| 6 | Optional per-region descriptions from a local Ollama model. |
| 7 | `report.json` and a self-contained `report.html`. |

**The proxy is not just an optimisation.** Perceptual hashes are
resolution-invariant, so the proxy fingerprints identically to the source —
acceptance check 1 demonstrates exactly that, and it mirrors production, where
a transcoder makes the proxy and the mezzanine is never decoded at all.

**The audio hash earns its place.** "Picture identical, audio replaced" is one
of the most common real differences between versions, and a visual hash alone
reports those two files as identical.

## Alignment

`align.py`, written directly against numpy — no bioinformatics library.

Similarity between two shots is the Hamming distance between their `phash`
values, scored:

| phash distance | score |
| --- | --- |
| ≤ 10 | +2 (match) |
| 11–20 | 0 (weak match) |
| > 20 | −1 (mismatch) |
| gap | −1 |

Traceback yields `equal`, `replace`, `delete` (in A, not in B) and `insert`
(in B, not in A). Consecutive operations of the same type merge into regions,
so the output is a handful of meaningful changes rather than hundreds of
per-shot rows. Runs of `equal` are dropped — two identical files produce zero
regions.

The row recurrence is vectorised. The left-neighbour term
`H[i][j] = max(…, H[i][j-1] + gap)` looks inherently sequential, but with a
linear gap penalty it is a running maximum: substituting `G[j] = H[i][j] - j*gap`
turns it into `G[j] = max(best[j] - j*gap, G[j-1])`, which is exactly
`np.maximum.accumulate`. Scores are integers, so the traceback's equality tests
are exact.

**Audio-only detection** is a separate pass over the aligned pairs, never part
of the scoring function: audio should decide what we *say* about two
corresponding shots, never *which* shots correspond. For every pair marked
`equal`, if the `ahash` Hamming distance exceeds 16 the pair is retagged
`audio_changed` before regions are merged.

A reordered pair of shots surfaces as an `insert` plus a `delete` of the same
shot — which is what a swap is under a global alignment, and the report makes
it legible by showing the same thumbnail on both sides.

## Descriptions (`--explain`)

Off by default. The report is meant to be useful without it — the thumbnails of
the changed shots carry it on their own.

Plain HTTP to `http://localhost:11434/api/generate` via `requests`, not the
`ollama` package. Default model `qwen3-vl:8b`, overridable with `--model`. At
startup it GETs `/api/tags` and fails with the exact `ollama pull …` command if
the model is missing, or says so and exits if the server is unreachable.

Each side of a region is described on its own — the model is never asked to
compare two sets of frames in one call. Small local vision models are markedly
worse at cross-set comparison than at plain description, and asked to compare
they confabulate differences that are not there. The comparison is assembled in
Python from two independent descriptions:

- `delete` → `Removed: {description_a}`
- `insert` → `Added: {description_b}`
- `audio_changed` → `Picture unchanged ({description_a}); audio differs.`
- `replace` → one further text-only call over the two descriptions

Calls run two at a time, cached on a hash of the image bytes so re-runs are
instant. Any call that fails or times out attaches `null` and the report is
built anyway.

## Files

| File | |
| --- | --- |
| `vdiff_common.py` | ffmpeg wrappers, proxy build, frame cache, hash helpers |
| `fingerprint.py` | stages 0–4 |
| `align.py` | stage 5 — Needleman-Wunsch, audio pass, region merging |
| `diff.py` | drives stages 5–6, writes `report.json` |
| `explain.py` | stage 6 — Ollama client |
| `report.py` | stage 7 — HTML |
| `make_variants.py` | fixture generator + `ground_truth.json` |
| `make_synthetic_source.py` | synthetic source video, for testing without a download |
| `verify.py` | the five acceptance checks |
| `app.py` | local web UI — FastAPI routes, SSE, static serving |
| `jobs.py` | workflow engine — job model, stages, parallel lanes, history |
| `static/` | the UI itself (plain HTML/CSS/JS, no framework, no build step) |

`report.json` carries the regions with thumbnails omitted, so it stays readable
and diffable; the JPEGs go in a `report.thumbs.json` sidecar that `report.py`
folds back in. If the sidecar is missing, `report.py` re-extracts frames from
the cached proxies instead.

Proxies, extracted frames, decoded audio and model responses are all cached
under `.cache/`, keyed so that a changed input invalidates its entry.

## Fixtures

`make_variants.py` produces `v_base` (720p reference), `v_cut` (~12 s removed
around the 40% mark), `v_audiodub` (one window's audio replaced with a 1 kHz
tone, picture stream-copied), `v_reorder` (two adjacent shots swapped) and
`v_lowres` (`v_base` at 360p). Exact timecodes are printed and written to
`ground_truth.json`.

Edits snap to detected shot boundaries. That is deliberate: a real version
difference is a whole beat lifted out, not 12 seconds sliced from the middle of
a take, and snapping makes the ground truth unambiguous. The removed length is
therefore *near* 12 s rather than exactly 12 s — `ground_truth.json` records
what was actually done.

Use a 10–15 minute short rather than a feature. Fingerprinting iterates in a
minute or two rather than fifteen, and short runtimes render far better on the
two-timeline report — on a two-hour bar the diff regions are hairlines.

## Out of scope

No cloud services or API keys. No fingerprint database or dedup index. No web
server, API, auth or job queue. No packaging, Docker or CI. Two versions at a
time. No embedding-based matching — the perceptual hash is the point.
