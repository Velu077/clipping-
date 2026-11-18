import os, re, uuid, subprocess, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA = Path(os.getenv("DATA_DIR", "data"))
DATA.mkdir(exist_ok=True)
MAX_MIN = int(os.getenv("MAX_MINUTES", "90"))
MODEL = os.getenv("WHISPER_MODEL", "base")
app = FastAPI(title="ClipPulse")
pool = ThreadPoolExecutor(max_workers=1)  # one heavy job at a time
JOBS, RENDERS = {}, {}
_model = None


def whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(MODEL, compute_type="int8")
    return _model


KEYS = ("secret", "never", "crazy", "insane", "best", "worst", "mistake", "how to", "why",
        "actually", "wow", "amazing", "important", "free", "money", "truth")


def find_clips(S, total):
    """Heuristic highlight finder: speech density + questions/exclamations + hook words."""
    if not S:
        return []
    cands = []
    for i in range(len(S)):
        j = i
        while j < len(S) - 1 and S[j]["e"] - S[i]["s"] < 25:
            j += 1
        while j > i and S[j]["e"] - S[i]["s"] > 58:
            j -= 1
        st, en = S[i]["s"], min(S[j]["e"], S[i]["s"] + 60)
        text = " ".join(x["t"] for x in S[i:j + 1])
        score = (len(text.split()) / max(en - st, 1) + 2 * text.count("?") + text.count("!")
                 + sum(k in text.lower() for k in KEYS))
        cands.append((score, st, en, text))
    cands.sort(reverse=True)
    n = max(3, min(8, int(total // 300)))
    out = []
    for sc, st, en, text in cands:
        if all(en <= c["start"] or st >= c["end"] for c in out):
            out.append({"start": round(st, 2), "end": round(en, 2), "score": round(sc, 1),
                        "title": " ".join(text.split()[:9])})
        if len(out) >= n:
            break
    return sorted(out, key=lambda c: c["start"])


def process(jid, url):
    j = JOBS[jid]
    d = DATA / jid
    d.mkdir(exist_ok=True)
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as y:
            info = y.extract_info(url, download=False)
        if info.get("is_live"):
            raise RuntimeError("This stream is still live. Live support is coming next; try again after it ends.")
        if (info.get("duration") or 0) > MAX_MIN * 60:
            raise RuntimeError(f"Video is longer than {MAX_MIN} minutes.")
        j.update(title=info.get("title"), status="downloading", progress=2)

        def hook(h):
            if h.get("status") == "downloading":
                tot = h.get("total_bytes") or h.get("total_bytes_estimate")
                if tot:
                    j["progress"] = 2 + int(28 * h["downloaded_bytes"] / tot)

        opts = {"format": "bv*[height<=720]+ba/b[height<=720]/b", "merge_output_format": "mp4",
                "outtmpl": str(d / "source.%(ext)s"), "noplaylist": True, "quiet": True,
                "progress_hooks": [hook]}
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
        src = next(d.glob("source.*"))
        j.update(source=src.name, status="transcribing", progress=30)
        segs, tinfo = whisper().transcribe(str(src), word_timestamps=True, vad_filter=True)
        total = tinfo.duration or 1
        S, W = [], []
        for s in segs:
            S.append({"s": s.start, "e": s.end, "t": s.text.strip()})
            W += [[w.word.strip(), round(w.start, 2), round(w.end, 2)] for w in (s.words or []) if w.word.strip()]
            j["progress"] = 30 + int(60 * s.end / total)
        j.update(status="finding", progress=92)
        j.update(clips=find_clips(S, total), words=W, status="done", progress=100)
    except Exception as e:
        j.update(status="error", error=str(e)[:300])


class NewJob(BaseModel):
    url: str


@app.post("/api/jobs")
def new_job(b: NewJob):
    url = b.url.strip()
    if not re.match(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", url):
        raise HTTPException(400, "Paste a YouTube link.")
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"id": jid, "status": "queued", "progress": 0}
    pool.submit(process, jid, url)
    return {"id": jid}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "Job not found (the server may have restarted).")
    return JOBS[jid]


class Line(BaseModel):
    s: float
    e: float
    t: str


class RenderReq(BaseModel):
    start: float
    end: float
    lines: list[Line]
    cx: float = 0.5
    pos: str = "low"
    color: str = "white"
    size: int = 78


def ts(x):
    x = max(x, 0)
    return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"


def make_ass(r):
    col = {"white": "&H00FFFFFF", "yellow": "&H0000FFFF", "green": "&H0000FF00"}.get(r.color, "&H00FFFFFF")
    al, mv = (2, 420) if r.pos == "low" else (5, 0)
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
            "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,DejaVu Sans,{r.size},{col},&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,2,{al},60,60,{mv},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    ev = ""
    for l in r.lines:
        s, e = max(l.s - r.start, 0), min(l.e, r.end) - r.start
        if e <= s or not l.t.strip():
            continue
        t = l.t.upper().replace("{", "(").replace("}", ")").replace("\n", "\\N")
        ev += f"Dialogue: 0,{ts(s)},{ts(e)},Default,,0,0,0,,{t}\n"
    return head + ev


def do_render(rid, jid, r):
    d = DATA / jid
    try:
        src = next(d.glob("source.*")).resolve()
        (d / f"{rid}.ass").write_text(make_ass(r), encoding="utf-8")
        cx = min(max(r.cx, 0), 1)
        vf = ("crop='min(iw,ih*9/16)':ih:'(iw-min(iw,ih*9/16))*%.3f':0,"
              "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,ass=%s.ass") % (cx, rid)
        cmd = ["ffmpeg", "-y", "-ss", f"{r.start:.2f}", "-i", str(src), "-t", f"{r.end - r.start:.2f}",
               "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", f"{rid}.mp4"]
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(p.stderr[-300:])
        RENDERS[rid].update(status="done", url=f"/api/files/{jid}/{rid}.mp4")
    except Exception as e:
        RENDERS[rid].update(status="error", error=str(e))


@app.post("/api/jobs/{jid}/render")
def render(jid: str, r: RenderReq):
    j = JOBS.get(jid)
    if not j or j.get("status") != "done":
        raise HTTPException(400, "Job is not ready.")
    if not 1 <= r.end - r.start <= 90:
        raise HTTPException(400, "Clip must be between 1 and 90 seconds.")
    rid = uuid.uuid4().hex[:8]
    RENDERS[rid] = {"status": "rendering"}
    threading.Thread(target=do_render, args=(rid, jid, r), daemon=True).start()
    return {"id": rid}


@app.get("/api/renders/{rid}")
def get_render(rid: str):
    if rid not in RENDERS:
        raise HTTPException(404)
    return RENDERS[rid]


@app.get("/api/files/{jid}/{name}")
def files(jid: str, name: str):
    p = (DATA / jid / name).resolve()
    if DATA.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


app.mount("/", StaticFiles(directory="static", html=True), name="static")
