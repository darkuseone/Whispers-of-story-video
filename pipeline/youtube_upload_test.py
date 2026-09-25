"""
youtube_upload_test.py — проверка заливки на YouTube без ключей и без сети.

    python pipeline/youtube_upload_test.py [jobs/<slug>.json]

Поддельный YouTube API вместо requests: собирает тело videos.insert из
настоящей спецификации и синтетических субтитров (первая фраза каждой
главы), гоняет заливку кусками с обрывом посередине, обложку больше
2 МБ, повторный запуск (второго ролика быть не должно) и запуск без
секретов (выход 0, «пропуск»). Ничего наружу не отправляет.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import youtube                 # noqa: E402
import youtube_upload as yu    # noqa: E402


class R:
    def __init__(self, code, js=None, headers=None, text=""):
        self.status_code, self._js = code, js or {}
        self.headers, self.text = headers or {}, text or json.dumps(js or {})

    def json(self):
        return self._js


class FakeYouTube:
    """Минимум API, который трогает загрузчик."""

    def __init__(self):
        self.videos, self.sessions, self.calls = {}, {}, []
        self.broke = False

    def request(self, method, url, headers=None, params=None, json=None,
                data=None, timeout=None):
        self.calls.append((method, url.split("googleapis.com")[-1], params))
        if url == yu.TOKEN_URL:
            return R(200, {"access_token": "t", "expires_in": 3600})
        if url.endswith("/channels"):
            return R(200, {"items": [{"contentDetails": {
                "relatedPlaylists": {"uploads": "UU1"}}}]})
        if url.endswith("/playlistItems"):
            return R(200, {"items": [{"snippet": {
                "title": v["snippet"]["title"],
                "resourceId": {"videoId": k}}} for k, v in self.videos.items()]})
        if url == f"{yu.UPLOAD}/videos" and method == "POST":
            assert headers["X-Upload-Content-Type"] == "video/mp4"
            assert params["notifySubscribers"] == "false"
            s = f"sess{len(self.sessions)}"
            self.sessions[s] = dict(body=json, size=int(
                headers["X-Upload-Content-Length"]), got=0)
            return R(200, headers={"Location": s})
        if url == f"{yu.API}/videos" and method == "GET":
            v = self.videos.get(params["id"])
            return R(200, {"items": [{"id": params["id"],
                                      "status": v["status"]}] if v else []})
        if url == f"{yu.API}/videos" and method == "PUT":
            if json["id"] not in self.videos:
                return R(404)
            self.videos[json["id"]]["snippet"] = json["snippet"]
            return R(200, json)
        if url.endswith("/thumbnails/set"):
            assert len(data) <= yu.THUMB_MAX, len(data)
            self.videos[params["videoId"]]["thumb"] = len(data)
            return R(200, {})
        if url.endswith("/captions"):
            assert b'"language": "en"' in data
            return R(200, {"id": "cap1"})
        raise AssertionError((method, url))

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def put(self, session, data=None, timeout=None, headers=None):
        s = self.sessions[session]
        rng = headers["Content-Range"]
        if rng.startswith("bytes */"):
            return R(308, headers={"Range": f"bytes=0-{s['got'] - 1}"}
                     if s["got"] else {})
        a, b = map(int, rng.split(" ")[1].split("/")[0].split("-"))
        if a > 0 and not self.broke:            # обрыв на втором куске
            self.broke = True
            raise yu.requests.ConnectionError("обрыв")
        assert a == s["got"], (a, s["got"])
        s["got"] = b + 1
        if s["got"] >= s["size"]:
            vid = f"vid{len(self.videos) + 1}"
            self.videos[vid] = dict(s["body"])
            return R(200, {"id": vid, "status": s["body"]["status"]})
        return R(308, headers={"Range": f"bytes=0-{b}"})


def fake_srt(job) -> str:
    """
    Субтитры: первая фраза каждой главы, главы по минуте. Между ними —
    нейтральные реплики: chapters() ищет по окну из трёх соседних реплик,
    и без прокладки начало второй главы нашлось бы на нулевой секунде.
    """
    rows, n = [], 0
    for i, block in enumerate(job["script_blocks"]):
        cues = [youtube.first_sentence(block)] + ["and so it went on."] * 3
        for k, text in enumerate(cues):
            n += 1
            sec = i * 60 + k * 10
            rows.append(f"{n}\n00:{sec // 60:02d}:{sec % 60:02d},000 --> "
                        f"00:{sec // 60:02d}:{sec % 60:02d},900\n{text}\n")
    return "\n".join(rows)


def main(job_path="jobs/cahokia-01.json"):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    job["youtube"]["tags"] = list(job["youtube"].get("tags") or []) + ["<bad>"]
    fake = FakeYouTube()
    yu.requests.request, yu.requests.post, yu.requests.put = \
        fake.request, fake.post, fake.put
    yu.CHUNK = 256 * 1024
    yu.time.sleep = lambda s: None

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        jp = out / "job.json"
        jp.write_text(json.dumps(job), encoding="utf-8")
        (out / "final.mp4").write_bytes(os.urandom(256 * 1024 * 3 + 777))
        (out / "subs.srt").write_text(fake_srt(job), encoding="utf-8")
        from PIL import Image
        noise = Image.frombytes("RGB", (1920, 1080), os.urandom(1920 * 1080 * 3))
        noise.save(out / "cover_1.jpg", quality=100)
        assert (out / "cover_1.jpg").stat().st_size > yu.THUMB_MAX

        # тело videos.insert
        body = yu.build_body(job, out)
        sn, st = body["snippet"], body["status"]
        assert st["privacyStatus"] == "private" and "publishAt" not in st
        assert st["selfDeclaredMadeForKids"] is False and st["embeddable"]
        assert sn["categoryId"] == "27" and sn["defaultLanguage"] == "en"
        assert sn["title"] == job["youtube"]["title"][:100]
        assert "00:00" in sn["description"], "главы не попали в описание"
        assert "Chapters" in sn["description"]
        assert all("<" not in t and ">" not in t for t in sn["tags"])
        assert len(sn["description"].encode()) <= yu.DESC_MAX
        print(f"  тело: {len(sn['tags'])} тегов, описание "
              f"{len(sn['description'])} знаков, категория {sn['categoryId']}")

        job2 = json.loads(json.dumps(job))
        job2["youtube"]["publish_at"] = "2026-10-01T18:00:00Z"
        st2 = yu.build_body(job2, out, "unlisted")["status"]
        assert st2["privacyStatus"] == "private" and st2["publishAt"]

        # без секретов — пропуск, выход 0
        for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
            os.environ.pop(k, None)
        assert yu.upload(str(jp), out) == 0 and not fake.videos

        # первая заливка: обрыв посреди файла, большая обложка
        os.environ.update(YT_CLIENT_ID="a", YT_CLIENT_SECRET="b",
                          YT_REFRESH_TOKEN="c")
        assert yu.upload(str(jp), out) == 0
        assert list(fake.videos) == ["vid1"], fake.videos.keys()
        rec = json.loads((out / yu.RECORD).read_text(encoding="utf-8"))
        assert rec["video_id"] == "vid1" and rec["thumbnail_ok"]
        assert rec["privacy"] == "private"

        # повторный запуск с записью — второго ролика нет
        assert yu.upload(str(jp), out) == 0 and len(fake.videos) == 1

        # новый раннер, записи нет — дубль ловится по названию на канале
        (out / yu.RECORD).unlink()
        assert yu.upload(str(jp), out) == 0 and len(fake.videos) == 1

        # ролик удалён в Studio — заливается заново, а не падает
        fake.videos.clear()
        assert yu.upload(str(jp), out) == 0 and len(fake.videos) == 1
    print("youtube_upload: ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")


if __name__ == "__main__":
    main(*sys.argv[1:2])
