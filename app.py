"""Local web UI for the version diff pipeline.

    python app.py            # then open http://127.0.0.1:8765

Binds to 127.0.0.1 only. There is no authentication and the file browser can
reach anything the running user can read, so this is a single-user tool on your
own machine -- do not expose it on a network interface.
"""

import argparse
import asyncio
import json
import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jobs import STAGE_DEFS, JobStore, browse
from vdiff_common import ToolError, check_tools

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Version Diff")
store = JobStore()


class JobRequest(BaseModel):
    video_a: str
    video_b: str
    label_a: str = ""
    label_b: str = ""
    threshold: float = 27.0
    audio_threshold: int = 16
    explain: bool = False
    model: str = "qwen3-vl:8b"
    ollama_url: str = "http://localhost:11434"


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------


@app.get("/api/health")
def health():
    """What this machine can actually do -- shown in the UI header."""
    info = {"ffmpeg": False, "videotoolbox": False, "ollama": False, "models": []}
    try:
        tools = check_tools()
        info["ffmpeg"] = True
        info["videotoolbox"] = tools["videotoolbox_encode"]
    except ToolError as exc:
        info["ffmpeg_error"] = str(exc)

    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=(1, 2))
        resp.raise_for_status()
        info["ollama"] = True
        info["models"] = sorted(m["name"] for m in resp.json().get("models", []))
    except Exception:  # noqa: BLE001 - Ollama being absent is normal, not an error
        pass
    return info


@app.get("/api/stages")
def stages():
    return [{"id": s, "label": l, "lane": ln} for s, l, ln in STAGE_DEFS]


@app.get("/api/browse")
def api_browse(path: str = ""):
    try:
        return browse(path or os.getcwd())
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


@app.post("/api/jobs")
def create_job(req: JobRequest):
    for path in (req.video_a, req.video_b):
        if not os.path.isfile(path):
            raise HTTPException(status_code=400, detail=f"File not found: {path}")
    job = store.create(
        video_a=os.path.abspath(req.video_a),
        video_b=os.path.abspath(req.video_b),
        label_a=req.label_a or os.path.basename(req.video_a),
        label_b=req.label_b or os.path.basename(req.video_b),
        threshold=req.threshold,
        audio_threshold=req.audio_threshold,
        explain=req.explain,
        model=req.model,
        ollama_url=req.ollama_url,
    )
    return {"id": job.id}


@app.get("/api/jobs")
def list_jobs():
    return [
        {
            "id": j.id, "label_a": j.label_a, "label_b": j.label_b,
            "status": j.status, "created_at": j.created_at,
            "finished_at": j.finished_at, "summary": j.summary,
            "explain": j.explain,
        }
        for j in store.list()
    ]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    data = job.to_dict()
    data.pop("events", None)  # events come over the stream, not here
    return data


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str):
    """Replay a finished job's output; the live stream only covers running ones."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return [e for e in job.events if e.get("type") in ("log", "error")]


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if not store.cancel(job_id):
        raise HTTPException(status_code=400, detail="Job is not cancellable")
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    if not store.delete(job_id):
        raise HTTPException(status_code=404, detail="No such job")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, since: int = 0):
    """Server-sent events: stage transitions and log lines as they happen.

    The worker runs in a thread, so this polls the job's event list rather than
    bridging a queue across the thread/async boundary. At 200ms the latency is
    invisible and there is nothing to deadlock.
    """
    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="No such job")

    async def stream():
        index = since
        while True:
            job = store.get(job_id)
            if job is None:
                break
            events = job.events[index:]
            index += len(events)
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"
            if job.status in ("done", "failed", "cancelled") and index >= len(job.events):
                yield f"data: {json.dumps({'type': 'closed', 'status': job.status})}\n\n"
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _artifact(job_id, name, media_type):
    path = store.report_path(job_id, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"{name} not available yet")
    return FileResponse(path, media_type=media_type)


@app.get("/api/jobs/{job_id}/report.json")
def report_json(job_id: str):
    return _artifact(job_id, "report.json", "application/json")


@app.get("/api/jobs/{job_id}/thumbs.json")
def thumbs_json(job_id: str):
    return _artifact(job_id, "report.thumbs.json", "application/json")


@app.get("/api/jobs/{job_id}/report.html")
def report_html(job_id: str):
    return _artifact(job_id, "report.html", "text/html")


@app.get("/api/jobs/{job_id}/download")
def download_html(job_id: str):
    path = store.report_path(job_id, "report.html")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Report not available yet")
    return FileResponse(path, media_type="text/html",
                        filename=f"version-diff-{job_id}.html")


# --------------------------------------------------------------------------
# static
# --------------------------------------------------------------------------


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local UI for the video version diff pipeline.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default 127.0.0.1; do not expose publicly)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    import uvicorn
    print(f"\n  Version Diff UI  ->  http://{args.host}:{args.port}\n", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
