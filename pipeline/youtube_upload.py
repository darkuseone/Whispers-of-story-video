"""
youtube_upload.py — заливка готового длинного ролика на YouTube как PRIVATE.

Зачем отдельно от youtube.py. youtube.py — постпродакшн-пакет (главы,
описание, обложки) и ничего не заливает; здесь только доставка. Описание
не пересобирается своими силами: берутся те же description() и chapters()
из youtube.py, чтобы текст на YouTube и в youtube.txt не разъехались.

Раннер сам льёт final.mp4 в YouTube Data API v3 кусками (resumable
upload), как drive.py льёт в Drive: через чат/MCP видео не идёт, токены
модели на полгигабайта не тратятся.

    python pipeline/youtube_upload.py upload jobs/<slug>.json work/<id>/out [--privacy private|unlisted]
    python pipeline/youtube_upload.py status jobs/<slug>.json [work/<id>/out]
    python pipeline/youtube_upload.py auth        # refresh token на своём ПК

ЧТО УХОДИТ:
  - final.mp4, snippet (title, description с главами, tags, categoryId,
    defaultLanguage=en), status (private, embeddable, не для детей,
    containsSyntheticMedia — в ролике есть сгенерированные картинки);
  - обложка cover_1.jpg (запасные: cover_2.jpg, thumbnail.jpg), больше
    2 МБ — пережимается до 1280x720;
  - субтитры subs.srt как дорожка English. Не вышло — WARNING, ролик уже
    залит и остаётся.

ПУБЛИКАЦИИ ИЗ API НЕТ НАМЕРЕННО. Всегда private (или unlisted по флагу):
кнопку Publish автор жмёт сам в Studio, посмотрев ролик. Есть в спеке
youtube.publish_at (ISO-8601) — ставится отложенная публикация, статус
при этом обязан остаться private, иначе YouTube отвечает invalidPublishAt.
И ещё: ролик, залитый из неверифицированного проекта Google Cloud, YouTube
держит private при любом запросе — здесь это не ограничение, а совпадение.

ПОВТОРНЫЙ ЗАПУСК НЕ ДЕЛАЕТ ВТОРОЙ РОЛИК. Раннер каждый раз новый, поэтому
одного локального work/<id>/out/youtube_upload.json мало: перед заливкой
смотрятся последние загрузки канала, и ролик с тем же названием считается
уже залитым — ему обновляются snippet и обложка (videos.update), статус не
трогается, чтобы не спрятать уже опубликованное.

Доступ — OAuth, scope youtube.force-ssl (upload, update, обложки,
субтитры одним разрешением). Три секрета Actions: YT_CLIENT_ID,
YT_CLIENT_SECRET, YT_REFRESH_TOKEN — отдельные от GDRIVE_*. Нет секретов —
выход 0 со строкой «пропуск», есть секреты и заливка сорвалась — выход 1.
Настройка — docs/youtube-upload.md.
"""

import io
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
CHUNK = 32 * 1024 * 1024          # кратно 256 КБ, как требует API
TRIES = 6
CATEGORY_DEFAULT = "27"           # Education
THUMB_MAX = 2 * 1024 * 1024       # потолок YouTube на обложку
TITLE_MAX = 100
DESC_MAX = 5000                   # в БАЙТАХ, не в символах
TAGS_MAX = 500
RECORD = "youtube_upload.json"
SEEN_UPLOADS = 50                 # сколько последних загрузок смотреть на дубль


def log(*a):
    print(*a, flush=True)


def studio(vid):
    return f"https://studio.youtube.com/video/{vid}/edit"


def creds():
    c = {k: (os.environ.get(f"YT_{k.upper()}") or "").strip()
         for k in ("client_id", "client_secret", "refresh_token")}
    return c if all(c.values()) else None


def summary(line: str):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class YouTube:
    def __init__(self, c):
        self.c = c
        self.token = None
        self.expires = 0.0

    def auth(self):
        if self.token and time.time() < self.expires - 60:
            return {"Authorization": f"Bearer {self.token}"}
        r = requests.post(TOKEN_URL, timeout=30, data={
            "client_id": self.c["client_id"],
            "client_secret": self.c["client_secret"],
            "refresh_token": self.c["refresh_token"],
            "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise SystemExit(
                f"Google не выдал токен ({r.status_code}): {r.text[:200]}\n"
                f"invalid_grant почти всегда значит протухший refresh token: "
                f"приложение в режиме Testing выдаёт его на 7 дней. "
                f"См. docs/youtube-upload.md.")
        d = r.json()
        self.token = d["access_token"]
        self.expires = time.time() + int(d.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self.token}"}

    def req(self, method, url, **kw):
        """Запрос с повтором на 429/5xx и обрыв сети."""
        base = dict(kw.pop("headers", {}) or {})
        r = None
        for attempt in range(TRIES):
            try:
                r = requests.request(method, url, timeout=120,
                                     headers={**base, **self.auth()}, **kw)
            except requests.RequestException as e:
                if attempt + 1 == TRIES:
                    raise
                log(f"    сеть: {e} — повтор")
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt + 1 < TRIES:
                time.sleep(2 ** attempt)
                continue
            return r
        return r

    # ── поиск уже залитого ──────────────────────────────────────────
    def recent_uploads(self):
        """[(video_id, title)] последних загрузок канала — 2 единицы квоты."""
        r = self.req("GET", f"{API}/channels",
                     params={"part": "contentDetails", "mine": "true"})
        if r.status_code != 200:
            log(f"  ! список загрузок недоступен ({r.status_code}) — "
                f"проверка на дубль только по локальной записи")
            return []
        items = r.json().get("items") or []
        if not items:
            return []
        pl = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        r = self.req("GET", f"{API}/playlistItems", params={
            "part": "snippet", "playlistId": pl, "maxResults": SEEN_UPLOADS})
        if r.status_code != 200:
            return []
        return [(it["snippet"]["resourceId"]["videoId"], it["snippet"]["title"])
                for it in r.json().get("items") or []]

    # ── ролик ───────────────────────────────────────────────────────
    def insert(self, video: Path, body: dict, notify: bool):
        size = video.stat().st_size
        r = self.req("POST", f"{UPLOAD}/videos", json=body, params={
            "uploadType": "resumable", "part": "snippet,status",
            "notifySubscribers": "true" if notify else "false"},
            headers={"X-Upload-Content-Type": "video/mp4",
                     "X-Upload-Content-Length": str(size)})
        if r.status_code != 200 or "Location" not in r.headers:
            raise SystemExit(explain(r, "сессия загрузки не открылась"))
        return self._send(r.headers["Location"], video, size)

    def _send(self, session, path: Path, size: int):
        """Куски по CHUNK; обрыв — спросить сервер, сколько принято, и дальше."""
        sent, started, fails = 0, time.time(), 0
        with path.open("rb") as f:
            while True:
                f.seek(sent)
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                r = None
                try:
                    r = requests.put(session, data=chunk, timeout=300, headers={
                        **self.auth(), "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {sent}-{end}/{size}"})
                except requests.RequestException as e:
                    log(f"    обрыв на {sent // 1048576} МБ: {e}")
                if r is not None and r.status_code in (200, 201):
                    log(f"  + final.mp4: {size / 1048576:.1f} МБ за "
                        f"{time.time() - started:.0f} с")
                    return r.json()
                if r is not None and r.status_code == 308:
                    sent = _next_byte(r)
                    fails = 0
                    continue
                if r is not None and r.status_code < 500:
                    raise SystemExit(explain(r, "кусок отвергнут"))
                fails += 1
                if fails > TRIES:
                    raise SystemExit("загрузка сорвалась: сервер не принимает "
                                     "куски после повторов")
                time.sleep(min(60, 2 ** fails))
                q = requests.put(session, timeout=60, headers={
                    **self.auth(), "Content-Length": "0",
                    "Content-Range": f"bytes */{size}"})
                if q.status_code in (200, 201):
                    return q.json()
                if q.status_code == 308:
                    sent = _next_byte(q)
                    continue
                raise SystemExit(explain(q, "сессия загрузки потеряна"))

    def update_snippet(self, vid: str, snippet: dict) -> bool:
        """Ложь — ролика с таким id больше нет (удалён в Studio)."""
        r = self.req("PUT", f"{API}/videos", params={"part": "snippet"},
                     json={"id": vid, "snippet": snippet})
        if r.status_code == 404:
            return False
        if r.status_code != 200:
            raise SystemExit(explain(r, "videos.update не прошёл"))
        return True

    def thumbnail(self, vid: str, img: Path) -> bool:
        data, mime = thumb_bytes(img)
        r = self.req("POST", f"{UPLOAD}/thumbnails/set",
                     params={"videoId": vid, "uploadType": "media"},
                     data=data, headers={"Content-Type": mime})
        if r.status_code == 200:
            log(f"  + обложка: {img.name} ({len(data) // 1024} КБ)")
            return True
        # Своя обложка доступна только подтверждённому каналу (телефон в
        # youtube.com/verify). Ролик от этого не хуже — предупреждаем.
        log(f"  ! WARNING обложка не встала: {explain(r, 'thumbnails.set')}")
        return False

    def captions(self, vid: str, srt: Path) -> bool:
        meta = {"snippet": {"videoId": vid, "language": "en",
                            "name": "English", "isDraft": False}}
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8"
                f"\r\n\r\n{json.dumps(meta)}\r\n--{boundary}\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() \
            + srt.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        try:
            r = self.req("POST", f"{UPLOAD}/captions",
                         params={"part": "snippet", "uploadType": "multipart"},
                         data=body, headers={
                             "Content-Type": f"multipart/related; boundary={boundary}"})
        except requests.RequestException as e:
            log(f"  ! WARNING субтитры не встали: {e}")
            return False
        if r.status_code == 200:
            log("  + субтитры: English")
            return True
        log(f"  ! WARNING субтитры не встали: {explain(r, 'captions.insert')}")
        return False

    def status(self, vid: str):
        r = self.req("GET", f"{API}/videos", params={
            "part": "status,processingDetails,snippet", "id": vid})
        if r.status_code != 200:
            raise SystemExit(explain(r, "videos.list"))
        items = r.json().get("items") or []
        return items[0] if items else None


def _next_byte(r) -> int:
    rng = r.headers.get("Range")
    return int(rng.split("-")[1]) + 1 if rng else 0


def explain(r, what: str) -> str:
    """Причина отказа словами, без токенов: reason из ответа API + подсказка."""
    reason, msg = "", ""
    try:
        err = r.json().get("error") or {}
        msg = err.get("message") or ""
        reason = ((err.get("errors") or [{}])[0]).get("reason") or ""
    except ValueError:
        msg = (r.text or "")[:200]
    hint = {
        "quotaExceeded": "дневная квота API кончилась (заливка ролика стоит "
                         "1600 из 10 000) — перезалей завтра workflow «Upload "
                         "to YouTube»",
        "uploadLimitExceeded": "YouTube ограничил число загрузок канала на "
                               "сегодня — перезалей завтра",
        "invalidPublishAt": "publish_at в прошлом или не ISO-8601",
        "forbidden": "свои обложки только у подтверждённого канала: "
                     "youtube.com/verify",
        "youtubeSignupRequired": "у аккаунта нет канала или выбран не тот "
                                 "канал при авторизации",
    }.get(reason, "")
    return (f"{what}: {r.status_code} {reason} {msg[:160]}".strip()
            + (f" — {hint}" if hint else ""))


# ─────────────────────── содержимое ───────────────────────

def clean(text: str) -> str:
    """YouTube не пускает < и > ни в название, ни в описание (invalidTitle)."""
    return (text or "").replace("<", "‹").replace(">", "›")


def clip_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore").rsplit("\n", 1)[0]


def description_text(job, out_dir: Path) -> str:
    """
    Описание — ТЕМ ЖЕ description() из youtube.py, с точными главами по
    subs.srt. Не вышло (нет субтитров, глава не нашлась) — берётся блок
    ОПИСАНИЕ из уже собранного youtube.txt, и только потом голое интро.
    """
    import youtube
    srt = out_dir / "subs.srt"
    if srt.exists():
        try:
            chaps = youtube.chapters(job, youtube.read_srt(srt))
            return youtube.description(job, chaps, 0.0)
        except SystemExit as e:
            log(f"  ! главы не собрались ({e}) — беру описание из youtube.txt")
    card = out_dir / "youtube.txt"
    if card.exists():
        m = re.search(r"ОПИСАНИЕ[^\n]*\n(.*?)\n\nТЕГИ", card.read_text(
            encoding="utf-8"), flags=re.S)
        if m:
            return m.group(1).strip()
    return (job["youtube"].get("description_intro") or "").strip()


def tag_list(job) -> list:
    import youtube
    tags, total = [], 0
    for t in youtube.as_list(job["youtube"].get("tags"), "tags"):
        t = clean(t).strip()
        # YouTube считает тег с пробелом за два символа кавычек сверху
        cost = len(t) + (2 if " " in t else 0) + (1 if tags else 0)
        if total + cost > TAGS_MAX:
            break
        tags.append(t)
        total += cost
    return tags


def build_body(job, out_dir: Path, privacy: str = "private") -> dict:
    y = job["youtube"]
    snippet = {
        "title": clean(y["title"]).strip()[:TITLE_MAX],
        "description": clip_bytes(clean(description_text(job, out_dir)), DESC_MAX),
        "tags": tag_list(job),
        "categoryId": str(y.get("categoryId") or y.get("category_id")
                          or CATEGORY_DEFAULT),
        "defaultLanguage": "en",
        "defaultAudioLanguage": "en",
    }
    status = {
        "privacyStatus": privacy,
        "embeddable": True,
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
        "license": "youtube",
    }
    if y.get("publish_at"):
        # отложенная публикация живёт ТОЛЬКО у private
        status["privacyStatus"] = "private"
        status["publishAt"] = str(y["publish_at"])
    return {"snippet": snippet, "status": status}


def pick_cover(out_dir: Path):
    for name in ("cover_1.jpg", "cover_2.jpg", "thumbnail.jpg"):
        if (out_dir / name).exists():
            return out_dir / name
    return None


def thumb_bytes(img: Path):
    """jpeg/png до 2 МБ; больше — пережимаем в 1280x720 jpeg."""
    data = img.read_bytes()
    mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    if len(data) <= THUMB_MAX:
        return data, mime
    from PIL import Image
    with Image.open(img) as im:
        im = im.convert("RGB")
        im.thumbnail((1280, 720))
        for q in (90, 85, 80, 70, 60):
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=q, optimize=True)
            if buf.tell() <= THUMB_MAX:
                break
    log(f"  обложка {img.name}: {len(data) // 1024} КБ > 2 МБ — пережата "
        f"до {buf.tell() // 1024} КБ")
    return buf.getvalue(), "image/jpeg"


# ─────────────────────── команды ───────────────────────

def load_job(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    if "youtube" not in job:
        raise SystemExit("в спецификации нет блока youtube — заливать нечего")
    return job


def upload(job_path, out_dir: Path, privacy: str = "private"):
    c = creds()
    if not c:
        log("YouTube: пропуск: нет YT_* секретов (см. docs/youtube-upload.md)")
        return 0
    if privacy not in ("private", "unlisted"):
        raise SystemExit("--privacy только private или unlisted: публикует автор")
    job = load_job(job_path)
    video = out_dir / "final.mp4"
    if not video.exists():
        raise SystemExit(f"нет {video} — заливать нечего")

    body = build_body(job, out_dir, privacy)
    yt = YouTube(c)
    rec_path = out_dir / RECORD
    rec = json.loads(rec_path.read_text(encoding="utf-8")) \
        if rec_path.exists() else {}

    vid = rec.get("video_id")
    if not vid:
        title = body["snippet"]["title"]
        vid = next((v for v, t in yt.recent_uploads() if t == title), None)
        if vid:
            log(f"YouTube: ролик «{title}» уже на канале — {vid}, второй не "
                f"заливаю")

    cover = pick_cover(out_dir)
    if vid and not yt.update_snippet(vid, body["snippet"]):
        log(f"  ! {vid} из записи на канале не найден (удалён?) — заливаю заново")
        vid, rec = None, {}
    if vid:
        # Уже залит: обновляем тексты и обложку, статус НЕ трогаем — автор
        # мог уже опубликовать, и откатить это в private было бы хуже дубля.
        log(f"  = {vid}: название, описание и теги обновлены")
        thumb_ok = bool(rec.get("thumbnail_ok"))
        if cover and not thumb_ok:
            thumb_ok = yt.thumbnail(vid, cover)
        caps_ok = bool(rec.get("captions_ok"))
        privacy_now = rec.get("privacy") or (
            (yt.status(vid) or {}).get("status") or {}).get("privacyStatus", "?")
    else:
        log(f"YouTube: заливаю «{body['snippet']['title']}» как "
            f"{body['status']['privacyStatus'].upper()}"
            + (f", публикация {body['status']['publishAt']}"
               if body["status"].get("publishAt") else ""))
        res = yt.insert(video, body, notify=False)
        vid = res["id"]
        privacy_now = (res.get("status") or {}).get("privacyStatus", privacy)
        thumb_ok = yt.thumbnail(vid, cover) if cover else False
        if not cover:
            log("  ! WARNING обложки нет (cover_1/cover_2/thumbnail.jpg)")
        srt = out_dir / "subs.srt"
        caps_ok = yt.captions(vid, srt) if srt.exists() else False

    rec = {"video_id": vid, "title": body["snippet"]["title"],
           "privacy": privacy_now, "thumbnail_ok": thumb_ok,
           "captions_ok": caps_ok, "url": f"https://youtu.be/{vid}",
           "studio": studio(vid),
           "uploaded_at": rec.get("uploaded_at")
           or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rec_path.write_text(json.dumps(rec, indent=1, ensure_ascii=False),
                        encoding="utf-8")
    log(f"YouTube: готово — {vid}, статус {privacy_now} (публикует автор в "
        f"Studio; из неверифицированного проекта YouTube и так держит private)")
    log(f"  Studio: {studio(vid)}")
    summary(f"\n**YouTube:** `{vid}` — {privacy_now}, обложка "
            f"{'да' if thumb_ok else 'НЕТ'} — [открыть в Studio]({studio(vid)})\n")
    return 0


def status(job_path, out_dir: Path = None):
    c = creds()
    if not c:
        log("YouTube: пропуск: нет YT_* секретов")
        return 0
    job = load_job(job_path)
    out_dir = out_dir or Path("work") / job["id"] / "out"
    rec_path = out_dir / RECORD
    yt = YouTube(c)
    vid = None
    if rec_path.exists():
        vid = json.loads(rec_path.read_text(encoding="utf-8")).get("video_id")
    if not vid:
        title = clean(job["youtube"]["title"]).strip()[:TITLE_MAX]
        vid = next((v for v, t in yt.recent_uploads() if t == title), None)
    if not vid:
        log("YouTube: ролика с этим названием среди последних загрузок нет")
        return 1
    it = yt.status(vid)
    if not it:
        log(f"YouTube: {vid} не найден (удалён?)")
        return 1
    st, pr = it.get("status") or {}, it.get("processingDetails") or {}
    log(f"YouTube: {vid} — {st.get('privacyStatus')}, загрузка "
        f"{st.get('uploadStatus')}, обработка {pr.get('processingStatus', '?')}"
        + (f", публикация {st['publishAt']}" if st.get("publishAt") else ""))
    log(f"  Studio: {studio(vid)}")
    return 0


def auth_flow():
    """Разово на своём компьютере; без компьютера — OAuth Playground, см. док."""
    import http.server
    import urllib.parse
    import webbrowser
    cid = os.environ.get("YT_CLIENT_ID", "").strip()
    sec = os.environ.get("YT_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        raise SystemExit("задай YT_CLIENT_ID и YT_CLIENT_SECRET")
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got["code"] = (qs.get("code") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Готово, окно можно закрыть.".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = ("https://accounts.google.com/o/oauth2/v2/auth?" +
           urllib.parse.urlencode({
               "client_id": cid, "redirect_uri": redirect,
               "response_type": "code", "scope": SCOPE,
               "access_type": "offline", "prompt": "consent"}))
    print("Открываю браузер (выбери КАНАЛ ANCIENT WHISPERS, не личный "
          "профиль). Если не открылся — ссылка:\n" + url)
    webbrowser.open(url)
    srv.handle_request()
    if not got.get("code"):
        raise SystemExit("Google не вернул код авторизации")
    r = requests.post(TOKEN_URL, timeout=30, data={
        "code": got["code"], "client_id": cid, "client_secret": sec,
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    d = r.json()
    if "refresh_token" not in d:
        raise SystemExit(f"нет refresh_token в ответе: {d.get('error')}")
    print("\nYT_REFRESH_TOKEN =\n" + d["refresh_token"])


def main(argv):
    if argv[:1] == ["auth"]:
        auth_flow()
        return 0
    privacy = "private"
    if "--privacy" in argv:
        i = argv.index("--privacy")
        privacy = argv[i + 1] if i + 1 < len(argv) else ""
        argv = argv[:i] + argv[i + 2:]
    if len(argv) == 3 and argv[0] == "upload":
        return upload(argv[1], Path(argv[2]), privacy)
    if len(argv) in (2, 3) and argv[0] == "status":
        return status(argv[1], Path(argv[2]) if len(argv) == 3 else None)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
