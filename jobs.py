"""Workflow engine for the UI: jobs, stages, live events, and result storage.

A job is one comparison of two versions. It runs as a workflow of named stages
laid out in two parallel lanes -- one per video -- that converge at alignment:

    probe_a -> proxy_a -> shots_a -> hash_a --.
                                               >-- align -> thumbnails -> [explain] -> report
    probe_b -> proxy_b -> shots_b -> hash_b --'

The two lanes really do run concurrently, so the diagram is not decoration.
Each stage reports queued/running/done/cached/failed with its own elapsed time,
and the progress the CLIs normally print to stdout is routed into the job's
event stream instead.

Fingerprints are cached by (file signature, threshold), so comparing a third
version against one already fingerprinted skips that whole lane.
"""

import json
import os
import queue
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import align as align_mod
import diff as diff_mod
import report as report_mod
from fingerprint import audio_hashes, detect_shots, visual_hashes
from vdiff_common import (
    ToolError,
    build_proxy,
    check_tools,
    clear_log_sink,
    ensure_cache,
    ffprobe_duration,
    file_signature,
    set_log_sink,
)

JOBS_DIR = os.path.join(os.getcwd(), ".cache", "jobs")
FP_CACHE_DIR = os.path.join(os.getcwd(), ".cache", "fingerprints")

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".mpg", ".mpeg",
    ".ts", ".m2ts", ".mxf", ".wmv", ".flv",
}

# The workflow, in display order. lane "a"/"b" are the parallel branches;
# lane "merge" is the converged tail.
STAGE_DEFS = [
    ("probe_a", "Probe", "a"),
    ("proxy_a", "Proxy 480p", "a"),
    ("shots_a", "Shot detection", "a"),
    ("hash_a", "Fingerprint", "a"),
    ("probe_b", "Probe", "b"),
    ("proxy_b", "Proxy 480p", "b"),
    ("shots_b", "Shot detection", "b"),
    ("hash_b", "Fingerprint", "b"),
    ("align", "Align (Needleman-Wunsch)", "merge"),
    ("thumbnails", "Thumbnails", "merge"),
    ("explain", "Descriptions", "merge"),
    ("report", "Report", "merge"),
]


class JobCancelled(Exception):
    """Raised at a stage boundary when the user cancels."""


@dataclass
class Job:
    id: str
    video_a: str
    video_b: str
    label_a: str
    label_b: str
    threshold: float = 27.0
    audio_threshold: int = align_mod.AUDIO_CHANGE_DISTANCE
    explain: bool = False
    model: str = "qwen3-vl:8b"
    ollama_url: str = "http://localhost:11434"
    status: str = "queued"          # queued | running | done | failed | cancelled
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    stages: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    summary: dict | None = None

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------


class JobStore:
    """In-memory jobs with JSON persistence, plus a single background worker.

    One job runs at a time on purpose: video work is CPU-bound and running
    several at once would just thrash. Within a job the two lanes are parallel.
    """

    def __init__(self):
        self._jobs = {}
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self._cancelled = set()
        os.makedirs(JOBS_DIR, exist_ok=True)
        os.makedirs(FP_CACHE_DIR, exist_ok=True)
        self._load_existing()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    # -- persistence -------------------------------------------------------

    def _job_dir(self, job_id):
        path = os.path.join(JOBS_DIR, job_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _load_existing(self):
        if not os.path.isdir(JOBS_DIR):
            return
        for entry in sorted(os.listdir(JOBS_DIR)):
            meta = os.path.join(JOBS_DIR, entry, "job.json")
            if not os.path.isfile(meta):
                continue
            try:
                with open(meta) as fh:
                    data = json.load(fh)
                job = Job(**data)
                # A job that was mid-flight when the server stopped is not
                # running any more; don't show it as though it were.
                if job.status in ("running", "queued"):
                    job.status = "failed"
                    job.error = "Interrupted -- the server stopped while this job was running."
                self._jobs[job.id] = job
            except (OSError, ValueError, TypeError):
                continue  # skip unreadable job records rather than refuse to start

    def _persist(self, job):
        try:
            with open(os.path.join(self._job_dir(job.id), "job.json"), "w") as fh:
                json.dump(job.to_dict(), fh)
        except OSError:
            pass  # a failed history write must never break a running job

    # -- api ---------------------------------------------------------------

    def create(self, **kwargs):
        job = Job(id=uuid.uuid4().hex[:12], **kwargs)
        for stage_id, label, lane in STAGE_DEFS:
            skipped = stage_id == "explain" and not job.explain
            job.stages[stage_id] = {
                "id": stage_id,
                "label": label,
                "lane": lane,
                "status": "skipped" if skipped else "queued",
                "elapsed": None,
                "detail": "",
            }
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)
        self._queue.put(job.id)
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def list(self):
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status in ("queued", "running"):
                self._cancelled.add(job_id)
                return True
        return False

    def delete(self, job_id):
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        import shutil
        shutil.rmtree(os.path.join(JOBS_DIR, job_id), ignore_errors=True)
        return True

    def report_path(self, job_id, name):
        return os.path.join(JOBS_DIR, job_id, name)

    # -- events ------------------------------------------------------------

    def _emit(self, job, event):
        event["t"] = time.time()
        with self._lock:
            job.events.append(event)
            if len(job.events) > 4000:  # keep memory bounded on long jobs
                del job.events[:1000]

    def _log(self, job, lane, line):
        self._emit(job, {"type": "log", "lane": lane, "line": line.rstrip()})

    def _stage(self, job, stage_id, status, detail="", elapsed=None):
        with self._lock:
            stage = job.stages.get(stage_id)
            if stage is None:
                return
            stage["status"] = status
            if detail:
                stage["detail"] = detail
            if elapsed is not None:
                stage["elapsed"] = round(elapsed, 2)
        self._emit(job, {
            "type": "stage", "stage": stage_id, "status": status,
            "detail": detail, "elapsed": stage["elapsed"],
        })

    def _check_cancelled(self, job):
        with self._lock:
            if job.id in self._cancelled:
                raise JobCancelled()

    # -- worker ------------------------------------------------------------

    def _run_worker(self):
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if job is None:
                continue
            try:
                self._run_job(job)
            except Exception:  # noqa: BLE001 - worker must never die
                job.status = "failed"
                job.error = traceback.format_exc(limit=3)
                self._emit(job, {"type": "error", "message": job.error})
            finally:
                job.finished_at = time.time()
                self._persist(job)
                self._emit(job, {"type": "finished", "status": job.status})
                with self._lock:
                    self._cancelled.discard(job.id)

    # -- the workflow ------------------------------------------------------

    def _run_job(self, job):
        job.status = "running"
        job.started_at = time.time()
        self._persist(job)
        self._emit(job, {"type": "started"})

        try:
            check_tools()
            self._check_cancelled(job)

            # Both lanes at once -- this is the fork in the workflow diagram.
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(self._run_lane, job, "a", job.video_a)
                future_b = pool.submit(self._run_lane, job, "b", job.video_b)
                fp_a = future_a.result()
                fp_b = future_b.result()

            self._check_cancelled(job)
            self._run_merge(job, fp_a, fp_b)
            job.status = "done"

        except JobCancelled:
            job.status = "cancelled"
            job.error = "Cancelled."
            for stage in job.stages.values():
                if stage["status"] in ("queued", "running"):
                    stage["status"] = "cancelled"
        except (ToolError, Exception) as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc) if isinstance(exc, ToolError) else traceback.format_exc(limit=4)
            for stage in job.stages.values():
                if stage["status"] == "running":
                    stage["status"] = "failed"
                elif stage["status"] == "queued":
                    stage["status"] = "cancelled"
            self._emit(job, {"type": "error", "message": job.error})

    def _fp_cache_path(self, video, threshold):
        sig = file_signature(video)
        return os.path.join(FP_CACHE_DIR, f"{sig}_t{threshold:g}.json")

    def _run_lane(self, job, lane, video):
        """One video: probe, proxy, shot detection, fingerprints."""
        set_log_sink(lambda line: self._log(job, lane, line))
        try:
            ids = {k: f"{k}_{lane}" for k in ("probe", "proxy", "shots", "hash")}

            # Probe
            t0 = time.time()
            self._stage(job, ids["probe"], "running")
            if not os.path.isfile(video):
                raise ToolError(f"Video not found: {video}")
            source_duration = ffprobe_duration(video)
            self._stage(job, ids["probe"], "done",
                        f"{source_duration / 60:.1f} min", time.time() - t0)
            self._check_cancelled(job)

            # A fingerprint already computed for this exact file and threshold
            # makes the whole lane a no-op.
            cache_path = self._fp_cache_path(video, job.threshold)
            if os.path.isfile(cache_path):
                try:
                    with open(cache_path) as fh:
                        data = json.load(fh)
                    if os.path.isfile(data.get("proxy", "")):
                        for key in ("proxy", "shots", "hash"):
                            self._stage(job, ids[key], "cached", "reused", 0.0)
                        self._log(job, lane, f"  reusing cached fingerprint "
                                             f"({data['shot_count']} shots)")
                        return data
                except (OSError, ValueError, KeyError):
                    pass  # stale or corrupt cache entry: recompute

            # Proxy
            t0 = time.time()
            self._stage(job, ids["proxy"], "running")
            proxy = build_proxy(video)
            duration = ffprobe_duration(proxy)
            self._stage(job, ids["proxy"], "done",
                        os.path.basename(proxy), time.time() - t0)
            self._check_cancelled(job)

            # Shots
            t0 = time.time()
            self._stage(job, ids["shots"], "running")
            shots = detect_shots(proxy, job.threshold, duration)
            self._stage(job, ids["shots"], "done",
                        f"{len(shots)} shots", time.time() - t0)
            self._check_cancelled(job)

            # Fingerprints
            t0 = time.time()
            self._stage(job, ids["hash"], "running", f"0/{len(shots)} shots")
            phashes = visual_hashes(proxy, shots, duration)
            ahashes = audio_hashes(proxy, shots)
            self._stage(job, ids["hash"], "done",
                        f"{len(shots)} picture + audio hashes", time.time() - t0)

            data = {
                "source": os.path.basename(video),
                "source_path": os.path.abspath(video),
                "proxy": os.path.relpath(proxy),
                "duration_seconds": round(duration, 3),
                "shot_count": len(shots),
                "threshold": job.threshold,
                "shots": [
                    {"index": i, "start": round(s, 3), "end": round(e, 3),
                     "phash": phashes[i], "ahash": ahashes[i]}
                    for i, (s, e) in enumerate(shots)
                ],
            }
            with open(cache_path, "w") as fh:
                json.dump(data, fh)
            return data
        finally:
            clear_log_sink()

    def _run_merge(self, job, fp_a, fp_b):
        """The converged tail: align, thumbnails, optional descriptions, report."""
        set_log_sink(lambda line: self._log(job, "merge", line))
        try:
            # Align
            t0 = time.time()
            self._stage(job, "align", "running")
            ops, score = align_mod.align(fp_a["shots"], fp_b["shots"])
            retagged = align_mod.retag_audio_changes(
                ops, fp_a["shots"], fp_b["shots"], threshold=job.audio_threshold
            )
            regions = align_mod.merge_regions(
                ops, fp_a["shots"], fp_b["shots"],
                fp_a["duration_seconds"], fp_b["duration_seconds"],
            )
            self._stage(job, "align", "done",
                        f"{len(regions)} region(s), score {score}", time.time() - t0)
            self._log(job, "merge",
                      f"  aligned {fp_a['shot_count']} x {fp_b['shot_count']} shots, "
                      f"{retagged} audio retag(s)")
            self._check_cancelled(job)

            # Thumbnails
            t0 = time.time()
            self._stage(job, "thumbnails", "running")
            a_proxy = diff_mod.resolve_proxy(fp_a, fp_a["source"])
            b_proxy = diff_mod.resolve_proxy(fp_b, fp_b["source"])
            thumbs = diff_mod.build_thumbnails(regions, fp_a, fp_b, a_proxy, b_proxy)
            n_thumbs = sum(len(t["thumbnails_a"]) + len(t["thumbnails_b"]) for t in thumbs)
            self._stage(job, "thumbnails", "done", f"{n_thumbs} frames", time.time() - t0)
            self._check_cancelled(job)

            # Descriptions
            explanations = None
            if job.explain:
                t0 = time.time()
                self._stage(job, "explain", "running")
                try:
                    from explain import check_server, describe_regions
                    check_server(job.model, job.ollama_url)
                    explanations = describe_regions(
                        regions, thumbs, job.model, job.ollama_url
                    )
                    described = sum(1 for e in explanations if e["explanation"])
                    self._stage(job, "explain", "done",
                                f"{described}/{len(regions)} explained", time.time() - t0)
                except Exception as exc:  # noqa: BLE001
                    # Descriptions are best-effort; never fail the job for them.
                    self._stage(job, "explain", "failed",
                                str(exc).splitlines()[0][:120], time.time() - t0)
                    self._log(job, "merge", f"  WARNING: {exc}")
                    explanations = None

            # Report
            t0 = time.time()
            self._stage(job, "report", "running")
            report = self._build_report(job, fp_a, fp_b, regions, thumbs,
                                        explanations, score)
            job_dir = self._job_dir(job.id)
            with open(os.path.join(job_dir, "report.json"), "w") as fh:
                json.dump(report, fh, indent=2)
            with open(os.path.join(job_dir, "report.thumbs.json"), "w") as fh:
                json.dump({"regions": thumbs}, fh)
            html = report_mod.render_html(report, thumbs)
            with open(os.path.join(job_dir, "report.html"), "w") as fh:
                fh.write(html)
            self._stage(job, "report", "done",
                        f"{len(html) / 1024:.0f} KB", time.time() - t0)

            job.summary = {
                "region_count": len(regions),
                "alignment_score": score,
                "counts": report["summary"],
                "duration_a": fp_a["duration_seconds"],
                "duration_b": fp_b["duration_seconds"],
                "shots_a": fp_a["shot_count"],
                "shots_b": fp_b["shot_count"],
            }
        finally:
            clear_log_sink()

    def _build_report(self, job, fp_a, fp_b, regions, thumbs, explanations, score):
        from vdiff_common import format_tc

        report = {
            "version_a": {
                "source": job.label_a or fp_a["source"],
                "proxy": fp_a.get("proxy"),
                "duration_seconds": fp_a["duration_seconds"],
                "shot_count": fp_a["shot_count"],
            },
            "version_b": {
                "source": job.label_b or fp_b["source"],
                "proxy": fp_b.get("proxy"),
                "duration_seconds": fp_b["duration_seconds"],
                "shot_count": fp_b["shot_count"],
            },
            "alignment_score": score,
            "audio_change_threshold": job.audio_threshold,
            "explained": bool(explanations),
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
                "a_start": region.a_start, "a_end": region.a_end,
                "b_start": region.b_start, "b_end": region.b_end,
                "a_timecode": f"{format_tc(region.a_start)} - {format_tc(region.a_end)}",
                "b_timecode": f"{format_tc(region.b_start)} - {format_tc(region.b_end)}",
                "shot_count": region.shot_count,
                "thumbnail_count_a": len(thumbs[i]["thumbnails_a"]),
                "thumbnail_count_b": len(thumbs[i]["thumbnails_b"]),
                "description_a": None, "description_b": None, "explanation": None,
            }
            if explanations:
                entry.update(explanations[i])
            report["regions"].append(entry)
        return report


# --------------------------------------------------------------------------
# filesystem browsing for the file picker
# --------------------------------------------------------------------------


def browse(path):
    """List directories and video files at `path`, for the picker dialog."""
    path = os.path.abspath(os.path.expanduser(path or os.getcwd()))
    if not os.path.isdir(path):
        raise ToolError(f"Not a directory: {path}")

    dirs, videos = [], []
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except PermissionError as exc:
        raise ToolError(f"Permission denied: {path}") from exc

    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": entry.path})
            elif os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS:
                videos.append({
                    "name": entry.name,
                    "path": entry.path,
                    "size": entry.stat().st_size,
                })
        except OSError:
            continue  # unreadable entry: just leave it out of the listing

    parent = os.path.dirname(path)
    return {
        "path": path,
        "parent": parent if parent != path else None,
        "dirs": dirs,
        "videos": videos,
    }
