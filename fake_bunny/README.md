# fake-bunny (profile `bunny`)

Local stand-in for the [Bunny Stream create-video API](https://docs.bunny.net/reference/video_createvideo) on **http://127.0.0.1:8101**. Nothing leaves the machine. Any `AccessKey` is accepted. A missing key is rejected.

`POST /library/{libraryId}/videos` with `{"title":"..."}` stores a video and returns Bunny's video object (`guid`, `videoLibraryId`, `title`, `status`). Status is `4` (Finished) immediately. Playback is a one-second local slate, not a live RTMP ingest:

- `GET /{guid}/playlist.m3u8`
- `GET /{guid}/segment0.ts`
- `GET /media/slate.mp4`
- `GET /preview` plays the slate in a browser

A seeded video is always present: `00000000-0000-4000-8000-000000000810`.

```bash
curl -s -X POST http://127.0.0.1:8101/library/1/videos \
  -H 'AccessKey: local' -H 'Content-Type: application/json' \
  -d '{"title":"Localville"}'
curl -s http://127.0.0.1:8101/captured
```

Consumer wiring: `BUNNY_API_BASE=http://127.0.0.1:8101` (origin only) and `BUNNY_PULL_ZONE=http://127.0.0.1:8101`. From another compose service the host is `http://bunny:8101`.
