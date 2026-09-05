"""Stage 6: plain-English descriptions from a local Ollama server.

Everything here is optional and strictly best-effort. A failed or slow model
call attaches `null` and the report is built anyway -- a missing description
must never break the report.

Two deliberate choices:

* Plain HTTP via `requests`, not the `ollama` python package -- one less
  dependency for something that is three JSON fields.
* Each side of a region is described on its own, never both in one call. Small
  local vision models are markedly worse at cross-set comparison than at plain
  description, and asked to compare they will confabulate differences that are
  not there. The comparison is assembled in Python from two independent
  descriptions.
"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

import requests

from vdiff_common import ensure_cache, log

DEFAULT_MODEL = "qwen3-vl:8b"
DEFAULT_URL = "http://localhost:11434"

MAX_IN_FLIGHT = 2
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 180

DESCRIBE_PROMPT = (
    "Describe what is happening in these frames in one or two sentences. "
    "Be concrete and factual about what is visible - people, actions, setting, "
    "on-screen text. Do not speculate about story or intent. If the frames are "
    "too dark or ambiguous to describe, say exactly that."
)


class OllamaError(RuntimeError):
    """The model backend is unusable. Raised at startup, never mid-report."""


# --------------------------------------------------------------------------
# startup checks
# --------------------------------------------------------------------------


def check_server(model, base_url=DEFAULT_URL):
    """Confirm the server is up and the model is pulled, before doing any work.

    Fails fast with the exact command to run rather than hanging on the first
    generate call.
    """
    tags_url = f"{base_url.rstrip('/')}/api/tags"
    try:
        resp = requests.get(tags_url, timeout=(CONNECT_TIMEOUT, 10))
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise OllamaError(
            f"Could not reach the Ollama server at {base_url} ({exc.__class__.__name__}).\n"
            f"Start it with:  ollama serve\n"
            f"Or re-run without --explain to build the report without descriptions."
        ) from exc

    try:
        names = [m["name"] for m in resp.json().get("models", [])]
    except (ValueError, KeyError, TypeError) as exc:
        raise OllamaError(f"Unexpected response from {tags_url}: {exc}") from exc

    def normalise(name):
        return name[: -len(":latest")] if name.endswith(":latest") else name

    if model not in names and normalise(model) not in {normalise(n) for n in names}:
        available = ", ".join(sorted(names)) or "(none)"
        raise OllamaError(
            f"Model {model!r} is not available on the Ollama server.\n"
            f"Pull it with:  ollama pull {model}\n"
            f"Currently installed: {available}"
        )
    log(f"  [stage 6] Ollama ready at {base_url}, model {model}")


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


def _cache_key(model, prompt, images):
    """Keyed on the image bytes themselves, so re-runs are instant."""
    digest = hashlib.sha256()
    digest.update(model.encode())
    digest.update(b"\0")
    digest.update(prompt.encode())
    for img in images or ():
        digest.update(b"\0")
        digest.update(img.encode())
    return digest.hexdigest()[:32]


def _generate(model, prompt, images, base_url):
    """One /api/generate call. Returns the text, or None on any failure."""
    key = _cache_key(model, prompt, images)
    cache_path = os.path.join(ensure_cache("ollama"), f"{key}.json")
    if os.path.isfile(cache_path):
        try:
            with open(cache_path) as fh:
                return json.load(fh)["response"]
        except (OSError, ValueError, KeyError):
            pass  # corrupt cache entry: regenerate

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    if images:
        payload["images"] = list(images)

    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/generate",
            json=payload,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        resp.raise_for_status()
        text = (resp.json().get("response") or "").strip()
    except (requests.exceptions.RequestException, ValueError) as exc:
        log(f"    WARNING: description call failed ({exc.__class__.__name__}); "
            f"attaching null and continuing.")
        return None

    if not text:
        return None
    with open(cache_path, "w") as fh:
        json.dump({"response": text}, fh)
    return text


# --------------------------------------------------------------------------
# region descriptions
# --------------------------------------------------------------------------


def describe_regions(regions, thumbnails, model=DEFAULT_MODEL, base_url=DEFAULT_URL):
    """Describe both sides of every region, then assemble the explanations.

    `regions` are Region objects; `thumbnails` is a parallel list of
    {"thumbnails_a": [...], "thumbnails_b": [...]}.

    Returns a parallel list of {"description_a", "description_b", "explanation"},
    any of which may be None.
    """
    jobs = []
    for idx, thumbs in enumerate(thumbnails):
        for side in ("a", "b"):
            images = thumbs.get(f"thumbnails_{side}") or []
            if images:
                jobs.append((idx, side, images))

    log(f"  [stage 6] describing {len(jobs)} region sides "
        f"({MAX_IN_FLIGHT} calls in flight)")

    descriptions = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT) as pool:
            results = pool.map(
                lambda job: _generate(model, DESCRIBE_PROMPT, job[2], base_url), jobs
            )
            for (idx, side, _), text in zip(jobs, results):
                descriptions[(idx, side)] = text

    out = []
    replace_jobs = []
    for idx, region in enumerate(regions):
        desc_a = descriptions.get((idx, "a"))
        desc_b = descriptions.get((idx, "b"))
        entry = {"description_a": desc_a, "description_b": desc_b, "explanation": None}

        if region.type == "delete" and desc_a:
            entry["explanation"] = f"Removed: {desc_a}"
        elif region.type == "insert" and desc_b:
            entry["explanation"] = f"Added: {desc_b}"
        elif region.type == "audio_changed" and desc_a:
            entry["explanation"] = f"Picture unchanged ({desc_a}); audio differs."
        elif region.type == "replace" and desc_a and desc_b:
            replace_jobs.append((idx, desc_a, desc_b))
        out.append(entry)

    # `replace` needs one further text-only call to state the difference. The
    # model never sees both image sets -- only the two descriptions.
    if replace_jobs:
        log(f"  [stage 6] summarising {len(replace_jobs)} replaced region(s)")
        prompts = [
            f"Version A shows: {a} Version B shows: {b} "
            f"In one sentence, state what changed between them."
            for _, a, b in replace_jobs
        ]
        with ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT) as pool:
            results = pool.map(
                lambda p: _generate(model, p, None, base_url), prompts
            )
            for (idx, _, _), text in zip(replace_jobs, results):
                out[idx]["explanation"] = text

    described = sum(1 for e in out if e["explanation"])
    log(f"  [stage 6] {described}/{len(out)} regions explained")
    return out
