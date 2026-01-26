import asyncio
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import websockets

HOST = os.getenv("YT_SUBTITLE_HOST", "0.0.0.0")
PORT = int(os.getenv("YT_SUBTITLE_PORT", "8765"))


def _run_command(command, log_callback):
    log_callback(f"Running: {' '.join(command)}")
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.stdout:
        for line in result.stdout.splitlines():
            log_callback(line)
    return result.returncode, result.stdout


def _select_language(metadata, log_callback):
    language = metadata.get("language")
    automatic = metadata.get("automatic_captions") or {}
    subtitles = metadata.get("subtitles") or {}

    available = list(automatic.keys()) or list(subtitles.keys())
    if language and language in available:
        log_callback(f"Selected language from metadata: {language}")
        return language
    if available:
        chosen = available[0]
        log_callback(f"Selected first available subtitle language: {chosen}")
        return chosen
    log_callback("No subtitles available.")
    return None


def _strip_vtt(text):
    cleaned = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("WEBVTT"):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3} -->", line):
            continue
        if line.strip().isdigit():
            continue
        cleaned.append(line.strip())
    return " ".join(cleaned)


def _extract_text(vtt_path, log_callback):
    if not vtt_path.exists():
        log_callback("Subtitle file not found after download.")
        return ""
    text = vtt_path.read_text(encoding="utf-8", errors="ignore")
    stripped = _strip_vtt(text)
    return stripped


async def handle_connection(websocket):
    async for message in websocket:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            await websocket.send(json.dumps({"type": "error", "message": "Invalid JSON."}))
            continue

        if payload.get("type") != "download":
            await websocket.send(
                json.dumps({"type": "error", "message": "Unknown message type."})
            )
            continue

        video_id = (payload.get("id") or "").strip()
        if not video_id:
            await websocket.send(
                json.dumps({"type": "error", "message": "Video ID is required."})
            )
            continue

        def log_callback(line):
            asyncio.create_task(
                websocket.send(json.dumps({"type": "log", "message": line}))
            )

        log_callback(f"Starting subtitle download for {video_id}...")
        with tempfile.TemporaryDirectory() as tmpdir:
            info_cmd = [
                "yt-dlp",
                "-J",
                f"https://www.youtube.com/watch?v={video_id}",
            ]
            code, output = _run_command(info_cmd, log_callback)
            if code != 0:
                await websocket.send(
                    json.dumps({"type": "error", "message": "yt-dlp metadata failed."})
                )
                continue

            try:
                metadata = json.loads(output)
            except json.JSONDecodeError:
                await websocket.send(
                    json.dumps({"type": "error", "message": "Failed to parse metadata."})
                )
                continue

            language = _select_language(metadata, log_callback)
            if not language:
                await websocket.send(
                    json.dumps({"type": "error", "message": "No subtitle language found."})
                )
                continue

            output_template = str(Path(tmpdir) / "%(id)s.%(ext)s")
            subtitle_cmd = [
                "yt-dlp",
                "--skip-download",
                "--write-auto-sub",
                "--sub-lang",
                language,
                "--sub-format",
                "vtt",
                "-o",
                output_template,
                f"https://www.youtube.com/watch?v={video_id}",
            ]

            code, _ = _run_command(subtitle_cmd, log_callback)
            if code != 0:
                await websocket.send(
                    json.dumps({"type": "error", "message": "Subtitle download failed."})
                )
                continue

            vtt_path = next(Path(tmpdir).glob("*.vtt"), None)
            if not vtt_path:
                await websocket.send(
                    json.dumps({"type": "error", "message": "No VTT file generated."})
                )
                continue

            full_text = _extract_text(vtt_path, log_callback)
            if not full_text:
                await websocket.send(
                    json.dumps({"type": "error", "message": "Subtitle text is empty."})
                )
                continue

            await websocket.send(
                json.dumps(
                    {
                        "type": "result",
                        "language": language,
                        "text": full_text,
                    }
                )
            )


async def main():
    async with websockets.serve(handle_connection, HOST, PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
