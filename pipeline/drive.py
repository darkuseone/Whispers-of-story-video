"""
drive.py — выкладка готового выпуска на Google Drive прямо из Actions.

Зачем так, а не через чат. Ролик весит полгигабайта; прогонять его через
MCP-коннектор значит гнать байты через контекст модели — дорого и
бессмысленно. Здесь раннер, на котором ролик только что собрался, сам
отправляет файлы в Drive API кусками по 32 МБ (resumable upload): ни
памяти, ни токенов, и обрыв сети посреди файла докачивается с места
обрыва, а не с нуля.

Что уезжает (что есть на диске, то и уезжает):
    final.mp4, subs.srt, cover_1/2.jpg, thumbnail.jpg, youtube.txt,
    shorts/short_1..4.mp4
в папку канала «Whisper of History» (GDRIVE_FOLDER_ID или имя
GDRIVE_ROOT_NAME), внутри — своя папка на каждый ролик с его НАЗВАНИЕМ
из youtube.title: «<название> [<id>]». Id в скобках — ключ: название
можно поправить, а папка найдётся та же. Файл с тем же именем и размером повторно не льётся —
перезапуск шага ничего не дублирует; другой размер — старый файл
заменяется новой версией (тот же id, та же ссылка).

Доступ — OAuth со scope drive (полный): папку канала автор создаёт сам
(или её создаёт чат через MCP-коннектор Drive), а при drive.file скрипт
видит только созданное им самим и чужую папку не нашёл бы — завёл бы
рядом вторую с тем же именем. Нужны три секрета
Actions: GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN.
Нет секретов — шаг молча пропускается (выход 0), сборка не падает.
Как их получить — docs/google-drive.md.

    python pipeline/drive.py upload jobs/<id>.json <папка out> [--all]
    python pipeline/drive.py auth          # получить refresh token на своём ПК
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive"
ROOT_DEFAULT = "Whisper of History"
FOLDER = "application/vnd.google-apps.folder"
CHUNK = 32 * 1024 * 1024          # кратно 256 КБ, как требует API
TRIES = 6

FILES = ["final.mp4", "subs.srt", "cover_1.jpg", "cover_2.jpg",
         "thumbnail.jpg", "youtube.txt",
         "shorts/short_1.mp4", "shorts/short_2.mp4",
         "shorts/short_3.mp4", "shorts/short_4.mp4"]

MIME = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".srt": "text/plain",
        ".txt": "text/plain"}


def log(*a):
    print(*a, flush=True)


def creds():
    c = {k: (os.environ.get(f"GDRIVE_{k.upper()}") or "").strip()
         for k in ("client_id", "client_secret", "refresh_token")}
    return c if all(c.values()) else None


class Drive:
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
                f"Чаще всего refresh token протух: приложение в режиме "
                f"Testing выдаёт токены на 7 дней. См. docs/google-drive.md.")
        d = r.json()
        self.token = d["access_token"]
        self.expires = time.time() + int(d.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self.token}"}

    def req(self, method, url, **kw):
        """Запрос с повтором на 429/5xx и обрыв сети."""
        # Заголовки вынимаются ОДИН раз, до цикла: pop внутри цикла на
        # повторе отдавал пустой словарь, и повторный запрос открытия
        # сессии уходил без X-Upload-Content-Type/Length.
        base = dict(kw.pop("headers", {}) or {})
        for attempt in range(TRIES):
            try:
                h = {**base, **self.auth()}
                r = requests.request(method, url, headers=h, timeout=120, **kw)
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

    def find(self, name, parent=None, folder=False, contains=False):
        esc = name.replace("\\", "\\\\").replace("'", "\\'")
        q = [f"name {'contains' if contains else '='} '{esc}'",
             "trashed = false"]
        if parent:
            q.append(f"'{parent}' in parents")
        if folder:
            q.append(f"mimeType = '{FOLDER}'")
        r = self.req("GET", f"{API}/files", params={
            "q": " and ".join(q), "fields": "files(id,name,size,webViewLink)",
            "spaces": "drive", "pageSize": 10})
        r.raise_for_status()
        files = r.json().get("files") or []
        return files[0] if files else None

    def folder(self, name, parent=None):
        got = self.find(name, parent, folder=True)
        if got:
            return got
        meta = {"name": name, "mimeType": FOLDER}
        if parent:
            meta["parents"] = [parent]
        r = self.req("POST", f"{API}/files", json=meta,
                     params={"fields": "id,name,webViewLink"})
        r.raise_for_status()
        return r.json()

    def upload(self, path: Path, parent: str):
        size = path.stat().st_size
        old = self.find(path.name, parent)
        if old and int(old.get("size") or -1) == size:
            log(f"  = {path.name}: уже на Диске, тот же размер")
            return old
        meta = {"name": path.name}
        mime = MIME.get(path.suffix.lower(), "application/octet-stream")
        if old:
            # новая версия того же файла: ссылка остаётся прежней
            url = f"{UPLOAD}/{old['id']}?uploadType=resumable"
            r = self.req("PATCH", url, json={}, headers={
                "X-Upload-Content-Type": mime,
                "X-Upload-Content-Length": str(size)})
        else:
            meta["parents"] = [parent]
            r = self.req("POST", f"{UPLOAD}?uploadType=resumable", json=meta,
                         headers={"X-Upload-Content-Type": mime,
                                  "X-Upload-Content-Length": str(size)})
        if r.status_code != 200 or "Location" not in r.headers:
            raise SystemExit(f"{path.name}: сессия загрузки не открылась "
                             f"({r.status_code}): {r.text[:200]}")
        session = r.headers["Location"]
        sent, started = 0, time.time()
        with path.open("rb") as f:
            while sent < size:
                f.seek(sent)
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                try:
                    r = requests.put(session, data=chunk, timeout=300, headers={
                        **self.auth(), "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {sent}-{end}/{size}"})
                except requests.RequestException as e:
                    log(f"    обрыв на {sent // 1048576} МБ: {e}")
                    r = None
                if r is not None and r.status_code in (200, 201):
                    sent = size
                    break
                if r is not None and r.status_code == 308:
                    rng = r.headers.get("Range")
                    sent = int(rng.split("-")[1]) + 1 if rng else 0
                    continue
                # Сбой куска: спрашиваем сервер, сколько он уже принял, и
                # продолжаем с этого места, а не с начала файла.
                time.sleep(3)
                q = requests.put(session, timeout=60, headers={
                    **self.auth(), "Content-Length": "0",
                    "Content-Range": f"bytes */{size}"})
                if q.status_code in (200, 201):
                    sent = size
                    break
                if q.status_code == 308:
                    rng = q.headers.get("Range")
                    sent = int(rng.split("-")[1]) + 1 if rng else 0
                    continue
                raise SystemExit(f"{path.name}: загрузка сорвалась "
                                 f"({q.status_code}): {q.text[:200]}")
        mb = size / 1048576
        log(f"  + {path.name}: {mb:.1f} МБ за {time.time() - started:.0f} с")
        return self.find(path.name, parent)


def video_folder(d, root_id, job_id, title):
    """
    Папка ролика: «<название> [<id>]». Ищется по «[id]», а не по полному
    имени — поправленное название не плодит вторую папку.
    """
    tag = f"[{job_id}]"
    got = d.find(tag, root_id, folder=True, contains=True)
    if got:
        return got
    clean = "".join(ch for ch in (title or "") if ch not in '/\\:*?"<>|')
    name = f"{clean.strip()[:120]} {tag}".strip()
    return d.folder(name, root_id)


def collect(out_dir: Path, everything: bool):
    """
    Что выкладывать. Из сборки — известный комплект (FILES): в папке out
    лежит и служебное (монтажный лист, план). Из релиза (--all) — всё,
    что скачалось: релиз уже и есть комплект выкладки.
    """
    if not everything:
        return [out_dir / rel for rel in FILES if (out_dir / rel).exists()]
    return sorted(p for p in out_dir.rglob("*") if p.is_file())


def upload(job_path: str, out_dir: Path, everything: bool = False):
    c = creds()
    if not c:
        log("Google Drive: секретов GDRIVE_* нет — выкладку пропускаю "
            "(см. docs/google-drive.md)")
        return 0
    jp = Path(job_path)
    job = {}
    if jp.suffix == ".json" and jp.exists():
        job = json.loads(jp.read_text(encoding="utf-8"))
    job_id = job.get("id") or jp.stem
    title = (job.get("youtube") or {}).get("title") or job_id
    files = collect(out_dir, everything)
    if not files:
        log(f"Google Drive: в {out_dir} нечего выкладывать")
        return 0
    d = Drive(c)
    fid = (os.environ.get("GDRIVE_FOLDER_ID") or "").strip()
    if fid:
        r = d.req("GET", f"{API}/files/{fid}", params={"fields": "id,name"})
        if r.status_code != 200:
            raise SystemExit(f"GDRIVE_FOLDER_ID {fid}: папка не найдена "
                             f"({r.status_code}) — нет доступа или удалена")
        root = r.json()
    else:
        root = d.folder(os.environ.get("GDRIVE_ROOT_NAME") or ROOT_DEFAULT)
    sub = video_folder(d, root["id"], job_id, title)
    log(f"Google Drive: {root['name']}/{sub['name']} — {len(files)} файлов")
    for p in files:
        d.upload(p, sub["id"])
    link = sub.get("webViewLink") or f"https://drive.google.com/drive/folders/{sub['id']}"
    log(f"Google Drive: готово — {link}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"\n**Google Drive:** [{root['name']}/{job_id}]({link})\n")
    return 0


def auth_flow():
    """
    Разовое получение refresh token на СВОЁМ компьютере (не в Actions).

    Открывает браузер, ловит ответ Google на локальном порту и печатает
    токен — его кладут в секрет GDRIVE_REFRESH_TOKEN. Нужны
    GDRIVE_CLIENT_ID и GDRIVE_CLIENT_SECRET в окружении (клиент типа
    «Desktop app», см. docs/google-drive.md).
    """
    import http.server
    import urllib.parse
    import webbrowser
    cid = os.environ.get("GDRIVE_CLIENT_ID", "").strip()
    sec = os.environ.get("GDRIVE_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        raise SystemExit("задай GDRIVE_CLIENT_ID и GDRIVE_CLIENT_SECRET")
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
    print("Открываю браузер. Если не открылся — перейди по ссылке:\n" + url)
    webbrowser.open(url)
    srv.handle_request()
    if not got.get("code"):
        raise SystemExit("Google не вернул код авторизации")
    r = requests.post(TOKEN_URL, timeout=30, data={
        "code": got["code"], "client_id": cid, "client_secret": sec,
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    d = r.json()
    if "refresh_token" not in d:
        raise SystemExit(f"нет refresh_token в ответе: {json.dumps(d)[:300]}")
    print("\nGDRIVE_REFRESH_TOKEN =\n" + d["refresh_token"])


def main(argv):
    if argv[:1] == ["auth"]:
        auth_flow()
        return 0
    if len(argv) in (3, 4) and argv[0] == "upload":
        return upload(argv[1], Path(argv[2]), everything="--all" in argv[3:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
