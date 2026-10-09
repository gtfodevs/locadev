"""Fake Bunny Stream — create a video and play a local HLS slate.

Speaks the create/get video calls documented at
https://docs.bunny.net/reference/video_createvideo

  POST /library/{libraryId}/videos     AccessKey + JSON {"title"}
  GET  /library/{libraryId}/videos/{guid}
  GET  /{guid}/playlist.m3u8           one-second local slate
  GET  /{guid}/segment0.ts

No RTMP ingest and no call to video.bunnycdn.com. Status 4 means Finished
in Bunny's enum, so a player can fetch the playlist immediately.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

app = FastAPI(title="locadev-fake-bunny")

MEDIA = Path(__file__).resolve().parent / "media"
SLATE_GUID = "00000000-0000-4000-8000-000000000810"
PLAYLIST = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:3\n"
    "#EXT-X-TARGETDURATION:2\n"
    "#EXT-X-MEDIA-SEQUENCE:0\n"
    "#EXTINF:1.0,\n"
    "segment0.ts\n"
    "#EXT-X-ENDLIST\n"
)

_videos: dict[str, dict[str, Any]] = {}
_captured: list[dict[str, Any]] = []


def _video(library_id: str, title: str, guid: str | None = None) -> dict[str, Any]:
    try:
        library_num: int | str = int(library_id)
    except ValueError:
        library_num = library_id
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "videoLibraryId": library_num,
        "guid": guid or str(uuid.uuid4()),
        "title": title,
        "dateUploaded": now,
        "views": 0,
        "isPublic": True,
        "length": 1,
        "status": 4,
        "framerate": 25,
        "rotation": 0,
        "width": 160,
        "height": 90,
        "availableResolutions": "160x90",
        "thumbnailCount": 0,
        "encodeProgress": 100,
        "storageSize": 3597,
        "captions": [],
        "hasMP4Fallback": True,
        "collectionId": "",
        "thumbnailFileName": "",
        "averageWatchTime": 0,
        "totalWatchTime": 0,
        "category": "unknown",
        "chapters": [],
        "moments": [],
        "metaTags": [],
        "transcodingMessages": [],
    }


_videos[SLATE_GUID] = _video("1", "locadev slate", SLATE_GUID)


def _cors(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "AccessKey, Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response


@app.middleware("http")
async def cors_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.method == "OPTIONS":
        return _cors(Response(status_code=204))
    return _cors(await call_next(request))


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "Success": False,
            "Message": "Authentication has been denied for this request.",
            "StatusCode": 401,
        },
    )


def _require_key(access_key: str | None) -> JSONResponse | None:
    if access_key and access_key.strip():
        return None
    return _unauthorized()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "videos": len(_videos), "captured": len(_captured)}


@app.get("/preview", response_class=HTMLResponse)
def preview() -> str:
    return f"""<!doctype html>
<meta charset="utf-8">
<title>locadev bunny slate</title>
<body style="font-family: sans-serif; background: #111; color: #fff;">
<video id="slate" controls playsinline width="320" src="/media/slate.mp4"></video>
<p>Playlist <a href="/{SLATE_GUID}/playlist.m3u8">/{SLATE_GUID}/playlist.m3u8</a></p>
</body>
"""


@app.get("/media/slate.mp4")
def slate_mp4() -> FileResponse:
    return FileResponse(MEDIA / "slate.mp4", media_type="video/mp4")


@app.get("/captured")
def captured() -> list[dict[str, Any]]:
    return list(_captured)


@app.delete("/captured")
def clear_captured() -> dict[str, int]:
    n = len(_captured)
    _captured.clear()
    return {"cleared": n}


@app.post("/library/{library_id}/videos")
async def create_video(
    library_id: str,
    request: Request,
    accesskey: str | None = Header(default=None, alias="AccessKey"),
) -> JSONResponse:
    denied = _require_key(accesskey)
    if denied:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    title = str(body.get("title") or "").strip()
    if not title:
        return JSONResponse(
            status_code=400,
            content={"Success": False, "Message": "title is required", "StatusCode": 400},
        )
    video = _video(library_id, title)
    _videos[video["guid"]] = video
    _captured.append({
        "guid": video["guid"],
        "libraryId": library_id,
        "title": title,
        "status": video["status"],
    })
    return JSONResponse(status_code=200, content=video)


def _same_library(video: dict[str, Any], library_id: str) -> bool:
    stored = str(video["videoLibraryId"])
    if stored == library_id:
        return True
    return library_id.isdigit() and stored == str(int(library_id))


@app.get("/library/{library_id}/videos/{guid}")
def get_video(
    library_id: str,
    guid: str,
    accesskey: str | None = Header(default=None, alias="AccessKey"),
) -> JSONResponse:
    denied = _require_key(accesskey)
    if denied:
        return denied
    video = _videos.get(guid)
    if not video or not _same_library(video, library_id):
        return JSONResponse(
            status_code=404,
            content={"Success": False, "Message": "Not found", "StatusCode": 404},
        )
    return JSONResponse(content=video)


@app.get("/{guid}/playlist.m3u8")
def playlist(guid: str) -> Response:
    if guid not in _videos:
        return JSONResponse(status_code=404, content={"Success": False, "Message": "Not found", "StatusCode": 404})
    return PlainTextResponse(PLAYLIST, media_type="application/vnd.apple.mpegurl")


@app.get("/{guid}/segment0.ts")
def segment(guid: str) -> Response:
    if guid not in _videos:
        return JSONResponse(status_code=404, content={"Success": False, "Message": "Not found", "StatusCode": 404})
    return FileResponse(MEDIA / "segment0.ts", media_type="video/mp2t")
