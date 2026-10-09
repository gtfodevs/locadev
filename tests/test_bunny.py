import httpx

from conftest import BUNNY, require_port

SLATE = "00000000-0000-4000-8000-000000000810"


def test_bunny_create_and_playlist():
    require_port(8101, "fake-bunny")
    with httpx.Client(timeout=10.0) as c:
        c.delete(f"{BUNNY}/captured")
        denied = c.post(f"{BUNNY}/library/1/videos", json={"title": "no key"})
        assert denied.status_code == 401
        created = c.post(
            f"{BUNNY}/library/1/videos",
            headers={"AccessKey": "local"},
            json={"title": "Localville kickoff"},
        )
        assert created.status_code == 200
        video = created.json()
        assert video["status"] == 4 and video["title"] == "Localville kickoff"
        guid = video["guid"]
        got = c.get(f"{BUNNY}/library/1/videos/{guid}", headers={"AccessKey": "local"})
        assert got.status_code == 200 and got.json()["guid"] == guid
        playlist = c.get(f"{BUNNY}/{guid}/playlist.m3u8")
        assert playlist.status_code == 200 and "#EXTM3U" in playlist.text
        segment = c.get(f"{BUNNY}/{guid}/segment0.ts")
        assert segment.status_code == 200 and len(segment.content) > 100
        slate = c.get(f"{BUNNY}/media/slate.mp4")
        assert slate.status_code == 200 and slate.headers["content-type"].startswith("video/mp4")
        seeded = c.get(f"{BUNNY}/{SLATE}/playlist.m3u8")
        assert seeded.status_code == 200
        cap = c.get(f"{BUNNY}/captured").json()
        assert any(row["title"] == "Localville kickoff" for row in cap)
