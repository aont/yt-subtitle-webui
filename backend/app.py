import argparse
import asyncio
import json
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


@dataclass
class JobState:
    watch_id: str
    events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None



def build_watch_url(watch_id: str) -> str:
    if watch_id.startswith("http://") or watch_id.startswith("https://"):
        return watch_id
    return f"https://www.youtube.com/watch?v={watch_id}"



def pick_subtitle_language(info: Dict[str, Any]) -> tuple[Optional[str], bool]:
    language = info.get("language")
    subtitles = info.get("subtitles") or {}
    auto_captions = info.get("automatic_captions") or {}
    if language:
        if language in subtitles:
            return language, False
        if language in auto_captions:
            return language, True
    if subtitles:
        return sorted(subtitles.keys())[0], False
    if auto_captions:
        return sorted(auto_captions.keys())[0], True
    return None, False


async def run_yt_dlp_json(url: str, cookies_path: Optional[Path]) -> Dict[str, Any]:
    args = [
        "yt-dlp",
        "--dump-single-json",
        "--no-warnings",
    ]
    if cookies_path:
        args.extend(["--cookies", str(cookies_path)])
    args.append(url)

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="ignore") or "yt-dlp failed")
    return json.loads(stdout.decode("utf-8"))


async def run_yt_dlp_subtitles(
    url: str,
    language: str,
    use_auto: bool,
    out_dir: Path,
    cookies_path: Optional[Path],
) -> Path:
    args = [
        "yt-dlp",
        "--skip-download",
        "--sub-lang",
        language,
        "--sub-format",
        "json3",
        "-o",
        str(out_dir / "subtitle.%(ext)s"),
    ]
    if cookies_path:
        args.extend(["--cookies", str(cookies_path)])
    args.append(url)
    if use_auto:
        args.insert(2, "--write-auto-subs")
    else:
        args.insert(2, "--write-sub")

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(error or "yt-dlp subtitle download failed")

    for file in out_dir.glob("subtitle*.json3"):
        return file
    debug = stdout.decode("utf-8", errors="ignore")
    raise FileNotFoundError(f"Subtitle file not found. Output: {debug}")



def is_cjk_language(language: Optional[str]) -> bool:
    if not language:
        return False
    lowered = language.lower()
    return lowered.startswith("ja") or lowered.startswith("ko") or lowered.startswith("zh")



def parse_vtt_text(path: Path, joiner: str) -> str:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    cleaned: list[str] = []
    skip_block = False
    for raw in lines:
        line = raw.strip("\ufeff").strip()
        if not line:
            continue
        if line.startswith("WEBVTT"):
            continue
        if line.startswith("NOTE") or line.startswith("STYLE"):
            skip_block = True
            continue
        if skip_block:
            if line == "":
                skip_block = False
            continue
        if "-->" in line:
            continue
        if line.isdigit():
            continue
        cleaned.append(line)
    return joiner.join(segment.strip() for segment in cleaned if segment.strip()).strip()



def parse_json3_text(path: Path, joiner: str) -> str:
    payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    events = payload.get("events", [])
    lines: list[str] = []
    for event in events:
        segments = event.get("segs") or []
        text = "".join(segment.get("utf8", "") for segment in segments).strip()
        if not text:
            continue
        lines.append(text.replace("\n", joiner).strip())
    return joiner.join(lines).strip()



def parse_subtitle_text(path: Path, language: Optional[str]) -> str:
    joiner = "" if is_cjk_language(language) else " "
    if path.suffix == ".json3":
        return parse_json3_text(path, joiner)
    return parse_vtt_text(path, joiner)



def summarize_text(text: str, size: int = 400) -> Dict[str, str]:
    if len(text) <= size * 2:
        return {"beginning": text, "ending": text}
    return {
        "beginning": text[:size].rstrip(),
        "ending": text[-size:].lstrip(),
    }


async def process_download_job(app: web.Application, reservation_id: str) -> None:
    jobs: dict[str, JobState] = app["jobs"]
    job = jobs.get(reservation_id)
    if job is None:
        return

    async def push_event(event_type: str, payload: dict[str, Any]) -> None:
        await job.events.put({"event": event_type, "data": payload})

    async def send_log(message: str) -> None:
        await push_event("log", {"message": message})

    try:
        url = build_watch_url(job.watch_id)
        await send_log(f"Fetching metadata for {url}...")
        cookies_path = app.get("cookies_path")
        info = await run_yt_dlp_json(url, cookies_path)

        language, use_auto = pick_subtitle_language(info)
        if not language:
            raise RuntimeError("No subtitles available for this video.")

        await send_log(f"Selected language '{language}' ({'auto' if use_auto else 'manual'} captions).")

        keep_temp = bool(app.get("keep_temp"))
        temp_dir_obj: Optional[tempfile.TemporaryDirectory[str]] = None
        if keep_temp:
            out_dir = Path(tempfile.mkdtemp(prefix="yt_subtitle_"))
            await send_log(f"Keeping temp files in {out_dir}.")
        else:
            temp_dir_obj = tempfile.TemporaryDirectory()
            out_dir = Path(temp_dir_obj.name)

        try:
            await send_log("Downloading subtitles with yt-dlp...")
            subtitle_path = await run_yt_dlp_subtitles(
                url,
                language,
                use_auto,
                out_dir,
                cookies_path,
            )

            await send_log("Parsing subtitle text...")
            text = parse_subtitle_text(subtitle_path, language)
        finally:
            if temp_dir_obj is not None:
                temp_dir_obj.cleanup()

        if not text:
            raise RuntimeError("Subtitle text was empty.")

        summary = summarize_text(text)
        job.result = {
            "type": "result",
            "language": language,
            "summary": summary,
            "text": text,
        }
        await send_log("Subtitle processing completed.")
        await push_event("completed", {"reservation_id": reservation_id})
    except Exception as exc:  # noqa: BLE001
        job.error = str(exc)
        await push_event("error", {"message": job.error})
    finally:
        job.done.set()


async def reservation_handler(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"message": "Invalid JSON payload."}, status=400)

    watch_id = str(payload.get("watch_id", "")).strip()
    if not watch_id:
        return web.json_response({"message": "Watch ID is required."}, status=400)

    reservation_id = uuid.uuid4().hex
    jobs: dict[str, JobState] = request.app["jobs"]
    jobs[reservation_id] = JobState(watch_id=watch_id)
    asyncio.create_task(process_download_job(request.app, reservation_id))

    return web.json_response({"reservation_id": reservation_id})


async def events_handler(request: web.Request) -> web.StreamResponse:
    reservation_id = request.match_info.get("reservation_id", "")
    jobs: dict[str, JobState] = request.app["jobs"]
    job = jobs.get(reservation_id)
    if job is None:
        raise web.HTTPNotFound(text="reservation not found")

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)

    while True:
        if job.done.is_set() and job.events.empty():
            break
        event = await job.events.get()
        event_type = event["event"]
        data = json.dumps(event["data"], ensure_ascii=False)
        await response.write(f"event: {event_type}\ndata: {data}\n\n".encode("utf-8"))

    await response.write_eof()
    return response


async def result_handler(request: web.Request) -> web.Response:
    reservation_id = request.match_info.get("reservation_id", "")
    jobs: dict[str, JobState] = request.app["jobs"]
    job = jobs.get(reservation_id)
    if job is None:
        raise web.HTTPNotFound(text="reservation not found")

    if not job.done.is_set():
        return web.json_response({"status": "processing"}, status=202)
    if job.error:
        return web.json_response({"type": "error", "message": job.error}, status=500)
    if job.result is None:
        return web.json_response({"type": "error", "message": "Result not available."}, status=500)
    return web.json_response(job.result)


def frontend_file_response(frontend_dir: Path, file_path: Path) -> web.FileResponse:
    resolved = file_path.resolve()
    try:
        resolved.relative_to(frontend_dir.resolve())
    except ValueError as exc:
        raise web.HTTPNotFound() from exc
    if not resolved.exists() or not resolved.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(resolved)


@web.middleware
async def cors_middleware(
    request: web.Request, handler: web.RequestHandler
) -> web.StreamResponse:
    if request.method == "OPTIONS":
        response = web.Response(status=200)
    else:
        response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response



def create_app(
    *,
    serve_frontend: bool = True,
    frontend_dir: Path = FRONTEND_DIR,
    keep_temp: bool = False,
    cookies_path: Optional[Path] = None,
) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app["keep_temp"] = keep_temp
    app["cookies_path"] = cookies_path
    app["jobs"] = {}

    app.router.add_post("/reservation", reservation_handler)
    app.router.add_get("/events/{reservation_id}", events_handler)
    app.router.add_get("/result/{reservation_id}", result_handler)

    if serve_frontend:
        app["frontend_dir"] = frontend_dir

        async def frontend_handler(request: web.Request) -> web.FileResponse:
            raw_path = request.match_info.get("path", "")
            target = raw_path or "index.html"
            return frontend_file_response(frontend_dir, frontend_dir / target)

        app.router.add_get("/", frontend_handler)
        app.router.add_get("/{path:.*}", frontend_handler)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YT Subtitle Downloader backend")
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind the web server (default: 8080)",
    )
    parser.add_argument(
        "--no-serve-frontend",
        action="store_false",
        dest="serve_frontend",
        help="Disable serving frontend static files from the backend server",
    )
    parser.set_defaults(serve_frontend=True)
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=FRONTEND_DIR,
        help="Directory containing frontend assets (default: ./frontend)",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep intermediate temp files on disk instead of cleaning them up",
    )
    parser.add_argument(
        "--cookies",
        type=Path,
        help="Path to a cookies.txt file to pass to yt-dlp",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    web.run_app(
        create_app(
            serve_frontend=args.serve_frontend,
            frontend_dir=args.frontend_dir,
            keep_temp=args.keep_temp,
            cookies_path=args.cookies,
        ),
        port=args.port,
    )
