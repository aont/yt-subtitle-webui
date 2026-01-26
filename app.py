import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from aiohttp import web


YTDLP_BINARY = os.environ.get("YTDLP_BINARY", "yt-dlp")


def extract_video_id(value: str) -> Optional[str]:
    value = value.strip()
    if not value:
        return None
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    return None


def parse_vtt_text(vtt_content: str) -> tuple[str, str]:
    lines = []
    for raw_line in vtt_content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("WEBVTT"):
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        lines.append(line)
    if not lines:
        return "", ""
    return lines[0], lines[-1]


async def run_cmd(command: list[str], log_cb) -> None:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    async for raw in process.stdout:
        await log_cb(raw.decode().rstrip())
    code = await process.wait()
    if code != 0:
        raise RuntimeError(f"yt-dlp failed with exit code {code}")


async def fetch_language(video_id: str, log_cb) -> str:
    await log_cb("Fetching video metadata...")
    info_command = [YTDLP_BINARY, "-J", f"https://www.youtube.com/watch?v={video_id}"]
    process = await asyncio.create_subprocess_exec(
        *info_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode().strip() or "yt-dlp info failed")
    info = json.loads(stdout.decode())
    language = info.get("language")
    subtitles = info.get("subtitles") or {}
    auto_captions = info.get("automatic_captions") or {}
    if language and (language in subtitles or language in auto_captions):
        await log_cb(f"Detected language: {language}")
        return language
    available = list(subtitles.keys() or auto_captions.keys())
    if available:
        await log_cb(f"Using available subtitle language: {available[0]}")
        return available[0]
    await log_cb("No subtitles found; defaulting to English.")
    return "en"


async def download_subtitles(video_id: str, log_cb) -> tuple[str, str]:
    language = await fetch_language(video_id, log_cb)
    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = str(Path(tmpdir) / "subtitle")
        command = [
            YTDLP_BINARY,
            "--skip-download",
            "--write-auto-subs",
            "--write-subs",
            "--sub-lang",
            language,
            "--sub-format",
            "vtt",
            "-o",
            output_template,
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        await log_cb("Downloading subtitles...")
        await run_cmd(command, log_cb)
        vtt_files = list(Path(tmpdir).glob("subtitle*.vtt"))
        if not vtt_files:
            raise RuntimeError("No subtitle file produced.")
        vtt_content = vtt_files[0].read_text(encoding="utf-8")
    return parse_vtt_text(vtt_content)


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(Path(__file__).parent / "index.html")


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async def log(message: str) -> None:
        await ws.send_json({"type": "log", "message": message})

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON."})
                continue
            if payload.get("type") != "download":
                await ws.send_json({"type": "error", "message": "Unknown command."})
                continue
            video_id = extract_video_id(payload.get("videoId", ""))
            if not video_id:
                await ws.send_json({"type": "error", "message": "Invalid YouTube ID."})
                continue
            try:
                start, end = await download_subtitles(video_id, log)
                await ws.send_json(
                    {
                        "type": "result",
                        "start": start,
                        "end": end,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                await ws.send_json({"type": "error", "message": str(exc)})
        elif msg.type == web.WSMsgType.ERROR:
            await log(f"WebSocket error: {ws.exception()}")
    return ws


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket_handler)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=8080)
