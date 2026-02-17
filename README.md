# YT Subtitle Web UI

A lightweight web UI + backend for fetching YouTube subtitles via `yt-dlp` and exporting them as clean, plain text. The backend now uses separate HTTP endpoints (POST/GET/SSE) to reserve work, stream processing logs, and fetch final subtitle results.

## Features

- Paste a YouTube watch ID or full URL to fetch subtitles.
- Automatically selects an available subtitle language (manual preferred, auto captions as fallback).
- Normalizes subtitle output (JSON3 or VTT) into readable plain text.
- Frontend includes copy actions for full text or a prompt template.
- Event stream (`text/event-stream`) for real-time backend logs.

## Repository layout

- `backend/`: Aiohttp-based server that orchestrates `yt-dlp`, subtitle parsing, and SSE progress events.
- `frontend/`: Static frontend (HTML/CSS/JS). The backend serves this directory by default.

## Requirements

- Python 3.10+ (for `aiohttp` and async subprocess usage)
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) installed and available in your `PATH`

Install backend dependencies:

```bash
pip install -r backend/requirements.txt
```

## Running locally

Start the backend (serves the frontend from `frontend/` by default):

```bash
python backend/app.py --port 8080
```

Then open the app at:

```
http://localhost:8080
```

## API overview

All paths are at root-level (no `/api` prefix):

- `POST /reservation`
  - body: `{ "watch_id": "..." }`
  - response: `{ "reservation_id": "..." }`
- `GET /events/{reservation_id}`
  - `text/event-stream`
  - emits `log`, `error`, `completed` events
- `GET /result/{reservation_id}`
  - returns final subtitle payload after processing

If frontend backend URL is set to `https://host/path`, it will call:

- `https://host/path/reservation`
- `https://host/path/events/{reservation_id}`
- `https://host/path/result/{reservation_id}`

## Usage

1. Enter the backend base URL (defaults to current origin, e.g. `http://localhost:8080`).
2. Paste a YouTube watch ID (e.g. `dQw4w9WgXcQ`) or a full YouTube URL.
3. Click **Download Subtitles** to fetch and display the text.

## Configuration options

`backend/app.py` accepts a few optional flags:

```bash
python backend/app.py \
  --port 8080 \
  --frontend-dir ./frontend \
  --keep-temp \
  --cookies /path/to/cookies.txt
```

- `--no-serve-frontend`: Run only the API without serving static files.
- `--keep-temp`: Keep downloaded subtitle artifacts on disk for inspection.
- `--cookies`: Provide a cookies.txt file to pass through to `yt-dlp`.

## Notes

- Subtitle availability depends on the YouTube video and language availability.
- If subtitles are missing, the backend returns an error to the UI.

## License

MIT (see `LICENSE`).
