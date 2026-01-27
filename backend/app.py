import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "docs"


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


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async def send_log(message: str) -> None:
        await ws.send_json({"type": "log", "message": message})

    await send_log("WebSocket connected. Ready to download subtitles.")

    async for msg in ws:
        if msg.type != web.WSMsgType.TEXT:
            continue
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            await send_log("Received invalid JSON payload.")
            continue

        if payload.get("action") != "download":
            await send_log("Unknown action received.")
            continue

        watch_id = payload.get("watch_id", "").strip()
        if not watch_id:
            await ws.send_json({"type": "error", "message": "Watch ID is required."})
            continue

        url = build_watch_url(watch_id)
        await send_log(f"Fetching metadata for {url}...")
        cookies_path = request.app.get("cookies_path")
        try:
            info = await run_yt_dlp_json(url, cookies_path)
        except Exception as exc:  # noqa: BLE001
            await ws.send_json({"type": "error", "message": str(exc)})
            continue

        language, use_auto = pick_subtitle_language(info)
        if not language:
            await ws.send_json({"type": "error", "message": "No subtitles available for this video."})
            continue

        await send_log(
            f"Selected language '{language}' ({'auto' if use_auto else 'manual'} captions)."
        )

        keep_temp = bool(request.app.get("keep_temp"))
        temp_dir_obj: Optional[tempfile.TemporaryDirectory[str]] = None
        if keep_temp:
            out_dir = Path(tempfile.mkdtemp(prefix="yt_subtitle_"))
            await send_log(f"Keeping temp files in {out_dir}.")
        else:
            temp_dir_obj = tempfile.TemporaryDirectory()
            out_dir = Path(temp_dir_obj.name)
        try:
            await send_log("Downloading subtitles with yt-dlp...")
            try:
                subtitle_path = await run_yt_dlp_subtitles(
                    url,
                    language,
                    use_auto,
                    out_dir,
                    cookies_path,
                )
            except Exception as exc:  # noqa: BLE001
                await ws.send_json({"type": "error", "message": str(exc)})
                continue

            await send_log("Parsing subtitle text...")
            text = parse_subtitle_text(subtitle_path, language)
        finally:
            if temp_dir_obj is not None:
                temp_dir_obj.cleanup()

        if not text:
            await ws.send_json({"type": "error", "message": "Subtitle text was empty."})
            continue

        summary = summarize_text(text)
        await send_log("Subtitle processing completed.")
        await ws.send_json(
            {
                "type": "result",
                "language": language,
                "summary": summary,
                "text": text,
            }
        )

    return ws


def frontend_file_response(frontend_dir: Path, file_path: Path) -> web.FileResponse:
    resolved = file_path.resolve()
    try:
        resolved.relative_to(frontend_dir.resolve())
    except ValueError as exc:
        raise web.HTTPNotFound() from exc
    if not resolved.exists() or not resolved.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(resolved)


def create_app(
    *,
    serve_frontend: bool = False,
    frontend_dir: Path = FRONTEND_DIR,
    keep_temp: bool = False,
    cookies_path: Optional[Path] = None,
) -> web.Application:
    app = web.Application()
    app["keep_temp"] = keep_temp
    app["cookies_path"] = cookies_path
    app.router.add_get("/ws", websocket_handler)
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
        default=int(os.environ.get("PORT", "8080")),
        help="Port to bind the web server (default: 8080 or PORT env var)",
    )
    parser.add_argument(
        "--serve-frontend",
        action="store_true",
        help="Serve the frontend static files from the backend server",
    )
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=FRONTEND_DIR,
        help="Directory containing frontend assets (default: ./docs)",
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
