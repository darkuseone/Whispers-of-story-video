"""
magnific.py — второй поставщик картинок, коротких видео и редкой графики.

Зачем он вообще появился
------------------------
На канале о находках генерация была одна — xAI, и её доля держалась на 30%:
там сюжет про конкретный предмет, и рисованная монета выдавала бы себя за
фотографию настоящей. Здесь другой случай. Фивы при жизни, ночь перед
Саламином, Атлантида — этого не сфотографировал никто и никогда. Реального
материала под такие кадры не существует в природе, существуют руины и
музейные витрины, а сорок пять минут ими одними не закрыть.

Поэтому генерации здесь 40-50% экранного времени, и делится она так:

    70%  Magnific — по подписке, генерация не тарифицируется поштучно
    30%  xAI (grok) — как было на соседнем канале

Пропорция не вкусовщина и не осторожность: она держит канал живым, если
один из двух поставщиков ляжет или сменит модели. Ролик, целиком висящий
на одном ключе, — это ролик, который однажды не соберётся.

Три разные вещи через один ключ
-------------------------------
1. ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ. Модели flux2pro, nano-banana2, seedream5pro.
   По подписке безлимитные, поэтому счётчика на них нет — есть только
   чередование моделей: три разных генератора дают три разных почерка, и
   ролик перестаёт выглядеть нарисованным одной рукой. Это тот же принцип,
   по которому разводятся цветокор и открытие.

2. ГЕНЕРАЦИЯ КОРОТКОГО ВИДЕО. Модели minimax-hailuo-2.3, seedance-1.5pro,
   kling-2.5, wan-2.2. Только 2-3 секунды и только тогда, когда футажа по
   теме НЕ НАШЛОСЬ ни на одном стоке. Потолок — 5% от нужного ролику
   видео плюс суточный лимит. Причина жёсткости: сгенерированное видео
   узнаётся мгновенно, и чем его больше, тем быстрее ролик читается как
   машинный. Две секунды на сорок минут — это акцент, тридцать секунд —
   это уже другой канал.

3. БИБЛИОТЕКА. Готовые видео и фото, векторы, графика, иллюстрации.
   ТОЛЬКО когда по запросу не нашлось ничего в открытых архивах и на
   стоках, и не больше 10-15 штук в сутки — так заказано. Суточный счётчик
   ведётся отдельным журналом, см. LEDGER.

Про адрес API
-------------
Базовый адрес и пути к методам вынесены в константы и перекрываются
переменными окружения. Это не перестраховка: у сервисов такого рода пути
живут своей жизнью и переживают любой код, который их угадывает — ровно на
этом однажды сгорел первый прогон отбраковки с несуществующим именем
модели зрения. Если Magnific отвечает по другим путям, чинится это одной
переменной в workflow, а не правкой кода:

    MAGNIFIC_BASE=https://api.magnific.ai/v1
    MAGNIFIC_IMAGE_PATH=/images/generations
    MAGNIFIC_VIDEO_PATH=/videos/generations
    MAGNIFIC_JOB_PATH=/jobs
    MAGNIFIC_SEARCH_PATH=/assets/search

Ответ разбирается НЕ по жёсткой схеме, а поиском первой ссылки на файл в
любом месте JSON (см. _first_url). Схемы у разных сервисов разные, а
ссылка на результат есть у всех, и такой разбор переживает смену формата.
Что именно ответил сервис — печатается в лог целиком при первой же
неудаче, чтобы чинить было по чему.
"""

import json
import os
import random
import re
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent

BASE = (os.environ.get("MAGNIFIC_BASE") or "https://api.magnific.ai/v1").rstrip("/")
IMAGE_PATH = os.environ.get("MAGNIFIC_IMAGE_PATH", "/images/generations")
VIDEO_PATH = os.environ.get("MAGNIFIC_VIDEO_PATH", "/videos/generations")
JOB_PATH = os.environ.get("MAGNIFIC_JOB_PATH", "/jobs")
SEARCH_PATH = os.environ.get("MAGNIFIC_SEARCH_PATH", "/assets/search")

TIMEOUT = 90
POLL_EVERY = 6
POLL_TRIES = 40           # до четырёх минут на одно видео

# Безлимитные по подписке модели изображений. Порядок значения не имеет:
# они чередуются, а не ранжируются.
IMAGE_MODELS = ["flux2pro", "nano-banana2", "seedream5pro"]

# Модели видео. Тоже по подписке, но используются в час по чайной ложке —
# см. VIDEO_SHARE.
VIDEO_MODELS = ["minimax-hailuo-2.3", "seedance-1.5pro", "kling-2.5", "wan-2.2"]

# Сколько СЕКУНД просить у видеомодели. Только короткая вставка: длинный
# сгенерированный план разваливается сам — у моделей плывут руки, буквы и
# архитектура, и чем дольше кадр, тем виднее.
VIDEO_SECONDS = (2, 3)

# Потолок доли сгенерированного видео от всего видео ролика.
VIDEO_SHARE = 0.05

# ── СУТОЧНЫЕ ЛИМИТЫ ─────────────────────────────────────────────────
# Заказано: не больше 10-15 готовых видео и фото из библиотеки в день.
# Берём середину. Генерация изображений сюда НЕ входит — она безлимитная.
DAILY_LIBRARY_LIMIT = int(os.environ.get("MAGNIFIC_DAILY_LIMIT", "12"))
# Отдельный, свой потолок на сгенерированное видео: доля в 5% на коротком
# ролике даёт единицы, но пересборок у ролика бывает десяток, и без
# суточного счётчика они сложатся.
DAILY_VIDEO_LIMIT = int(os.environ.get("MAGNIFIC_VIDEO_DAILY_LIMIT", "6"))

# Журнал расхода. Лежит РЯДОМ С ЖУРНАЛОМ КАНАЛА, а не в рабочей папке
# ролика: лимит суточный и общий на все ролики, а рабочая папка своя у
# каждого. В GitHub Actions файл приезжает и уезжает отдельным кэшем с
# ключом по дате, см. workflow.
LEDGER = Path(os.environ.get("MAGNIFIC_LEDGER")
              or ROOT / "channel" / "magnific_usage.json")


def log(*a):
    print(*a, flush=True)


def key() -> str:
    return (os.environ.get("MAGNIFIC_API_KEY") or "").strip()


def available() -> bool:
    return bool(key())


def _headers():
    return {"Authorization": f"Bearer {key()}",
            "Content-Type": "application/json"}


# ─────────────────────────── ЖУРНАЛ РАСХОДА ───────────────────────────

def _load_ledger() -> dict:
    if not LEDGER.exists():
        return {}
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def used_today(kind: str) -> int:
    """Сколько единиц вида kind уже взято сегодня. kind: library | video."""
    return int(_load_ledger().get(date.today().isoformat(), {}).get(kind, 0))


def remaining(kind: str) -> int:
    cap = DAILY_LIBRARY_LIMIT if kind == "library" else DAILY_VIDEO_LIMIT
    return max(0, cap - used_today(kind))


def charge(kind: str, n: int = 1):
    """
    Записывает расход. Пишется СРАЗУ после успешного скачивания, а не
    пачкой в конце: прогон в Actions обрывается по таймауту чаще, чем
    хотелось бы, и потерянный счётчик означает перерасход лимита.

    Заодно чистит записи старше недели — файл лежит в репозитории и
    расти ему незачем.
    """
    data = _load_ledger()
    today = date.today().isoformat()
    row = data.setdefault(today, {})
    row[kind] = int(row.get(kind, 0)) + n
    cutoff = sorted(data)[-7:]
    data = {k: v for k, v in data.items() if k in cutoff}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                      encoding="utf-8")


def report():
    """Строка для лога: сколько сегодня потрачено и сколько осталось."""
    return (f"Magnific за сутки: библиотека {used_today('library')}/"
            f"{DAILY_LIBRARY_LIMIT}, сгенерированное видео "
            f"{used_today('video')}/{DAILY_VIDEO_LIMIT}")


# ─────────────────────────── РАЗБОР ОТВЕТА ───────────────────────────

_URL_KEYS = ("url", "image_url", "video_url", "output_url", "result_url",
             "download_url", "signed_url", "src", "output", "asset_url")
_FILE_RE = re.compile(r"https?://[^\s\"']+?\.(?:jpg|jpeg|png|webp|mp4|webm|mov)"
                      r"(?:\?[^\s\"']*)?", re.I)


def _first_url(payload) -> str:
    """
    Первая ссылка на файл в ЛЮБОМ месте ответа.

    Разбирать по жёсткой схеме нельзя: у разных сервисов результат лежит
    то в data[0].url, то в output[0], то в result.assets[0].signed_url, и
    схема меняется без предупреждения. Ссылка на файл при этом есть
    всегда и опознаётся однозначно.

    Порядок обхода: сначала известные имена ключей (они точнее), потом
    регулярное выражение по всему тексту ответа.
    """
    seen = []

    def walk(node):
        if isinstance(node, dict):
            for k in _URL_KEYS:
                v = node.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    seen.append(v)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    for u in seen:
        if _FILE_RE.match(u):
            return u
    if seen:
        return seen[0]
    m = _FILE_RE.search(json.dumps(payload, ensure_ascii=False))
    return m.group(0) if m else ""


def _job_id(payload) -> str:
    """Идентификатор задания, если сервис ответил не результатом, а задачей."""
    if not isinstance(payload, dict):
        return ""
    for k in ("id", "job_id", "task_id", "request_id", "generation_id"):
        v = payload.get(k)
        if isinstance(v, (str, int)) and str(v):
            return str(v)
    for k in ("data", "job", "result", "task"):
        inner = payload.get(k)
        if isinstance(inner, dict):
            got = _job_id(inner)
            if got:
                return got
    return ""


def _state(payload) -> str:
    """Состояние задания в нижнем регистре: completed / failed / running."""
    if not isinstance(payload, dict):
        return ""
    for k in ("status", "state"):
        v = payload.get(k)
        if isinstance(v, str):
            return v.lower()
    for k in ("data", "job", "result", "task"):
        inner = payload.get(k)
        if isinstance(inner, dict):
            got = _state(inner)
            if got:
                return got
    return ""


DEAD = ("failed", "error", "cancelled", "canceled", "rejected")


def _wait_for(job: str, what: str) -> str:
    """Опрашивает задание, пока не появится ссылка. Пусто — не дождались."""
    for _ in range(POLL_TRIES):
        time.sleep(POLL_EVERY)
        try:
            r = requests.get(f"{BASE}{JOB_PATH}/{job}", timeout=TIMEOUT,
                             headers=_headers())
        except requests.RequestException as e:
            log(f"  ! magnific: опрос задания {job} не прошёл: {e}")
            return ""
        if r.status_code != 200:
            log(f"  ! magnific: опрос задания {job} ответил {r.status_code} "
                f"{r.text[:160]}")
            return ""
        data = r.json()
        url = _first_url(data)
        if url:
            return url
        st = _state(data)
        if st in DEAD:
            log(f"  ! magnific: {what} не сделано ({st}) — {r.text[:200]}")
            return ""
    log(f"  ! magnific: {what} не дождались за "
        f"{POLL_EVERY * POLL_TRIES // 60} мин")
    return ""


def _download(url: str, dst: Path, limit=140 * 1024 * 1024) -> bool:
    """
    Качает результат потоком, с потолком по размеру.

    Через .content нельзя: файл целиком уезжает в память, а оборвать
    раздувшуюся загрузку нечем. Это тот же урок, что и в assets.fetch.
    """
    try:
        r = requests.get(url, stream=True, timeout=(15, 60))
        if r.status_code != 200:
            log(f"  ! magnific: ссылка отдала {r.status_code}")
            return False
        n = 0
        with open(dst, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                n += len(chunk)
                if n > limit:
                    raise TimeoutError(f"{n // 1048576} МБ — тяжелее потолка")
                f.write(chunk)
        if n < 12000:                      # заглушка вместо файла
            dst.unlink(missing_ok=True)
            return False
        return True
    except Exception as e:
        dst.unlink(missing_ok=True)
        log(f"  ! magnific: скачать не вышло: {e}")
        return False


def _post(path: str, body: dict, what: str):
    """POST с честным разбором отказа. Возвращает JSON либо None."""
    try:
        r = requests.post(f"{BASE}{path}", timeout=TIMEOUT,
                          headers=_headers(), json=body)
    except requests.RequestException as e:
        log(f"  ! magnific: {what} — сеть недоступна ({e})")
        return None
    if r.status_code not in (200, 201, 202):
        # Тело печатаем ВСЕГДА. Без него код ответа не объясняет ничего:
        # на этом уже горели и ElevenLabs, и xAI.
        log(f"  ! magnific: {what} — ответ {r.status_code} {r.text[:300]}")
        return None
    try:
        return r.json()
    except ValueError:
        log(f"  ! magnific: {what} — ответ не JSON: {r.text[:200]}")
        return None


# ─────────────────────────── ИЗОБРАЖЕНИЯ ───────────────────────────

def pick_image_model(n: int) -> str:
    """
    Модель для n-й по счёту картинки. ЧЕРЕДОВАНИЕ, а не жребий.

    Три модели рисуют по-разному, и если каждую картинку тянуть жребием,
    у части роликов случайно выйдет перекос в одну — а один почерк на весь
    ролик и есть то, из-за чего генерация опознаётся с первого кадра.
    Ровное чередование гарантирует треть на каждую при любом числе кадров.
    """
    return IMAGE_MODELS[n % len(IMAGE_MODELS)]


def generate_image(prompt: str, dst: Path, model: str = None,
                   aspect: str = "16:9") -> bool:
    """
    Одна картинка. Правда, если файл лёг на диск.

    Счётчика на изображениях НЕТ: по подписке они безлимитные, это и было
    причиной завести здесь второго поставщика.
    """
    if dst.exists():
        return True
    model = model or IMAGE_MODELS[0]
    data = _post(IMAGE_PATH, {"model": model, "prompt": prompt, "n": 1,
                              "aspect_ratio": aspect},
                 f"картинка моделью {model}")
    if data is None:
        return False
    url = _first_url(data)
    if not url:
        job = _job_id(data)
        url = _wait_for(job, f"картинка моделью {model}") if job else ""
    if not url:
        log(f"  ! magnific: в ответе нет ссылки — {json.dumps(data)[:240]}")
        return False
    return _download(url, dst)


# ─────────────────────────── ВИДЕО ───────────────────────────

def video_budget(clips_needed: int) -> int:
    """
    Сколько сгенерированных вставок вообще позволено этому ролику.

    Минимум из трёх ограничений: доля от нужного видео (5%), суточный
    остаток и здравый смысл (не больше трёх на ролик). Считается ДО
    первого запроса, чтобы решение было видно в логе одной строкой.
    """
    by_share = int(clips_needed * VIDEO_SHARE)
    return max(0, min(by_share, remaining("video"), 3))


def generate_video(prompt: str, dst: Path, seconds: int = None,
                   model: str = None, rng: random.Random = None) -> bool:
    """
    Одна короткая вставка. Правда, если файл лёг на диск.

    Расход пишется в суточный журнал СРАЗУ после успеха. Модель тянется
    жребием из четырёх: их слишком мало для чередования и слишком много,
    чтобы выбирать руками.
    """
    if dst.exists():
        return True
    if remaining("video") <= 0:
        log(f"  ! magnific: суточный лимит на сгенерированное видео выбран "
            f"({DAILY_VIDEO_LIMIT})")
        return False
    rng = rng or random
    model = model or rng.choice(VIDEO_MODELS)
    seconds = int(seconds or rng.choice(VIDEO_SECONDS))
    data = _post(VIDEO_PATH, {"model": model, "prompt": prompt,
                              "duration": seconds, "aspect_ratio": "16:9"},
                 f"видео моделью {model}")
    if data is None:
        return False
    url = _first_url(data)
    if not url:
        job = _job_id(data)
        url = _wait_for(job, f"видео моделью {model}") if job else ""
    if not url:
        log(f"  ! magnific: в ответе нет ссылки — {json.dumps(data)[:240]}")
        return False
    if not _download(url, dst):
        return False
    charge("video")
    log(f"  magnific: видео {seconds} с моделью {model} -> {dst.name}")
    return True


# ─────────────────────────── БИБЛИОТЕКА ───────────────────────────

def search_library(query: str, n: int, kind: str = "image"):
    """
    Готовые файлы из библиотеки Magnific: видео, фото, векторы, графика,
    иллюстрации.

    Источник ПОСЛЕДНЕЙ ОЧЕРЕДИ. Вызывается только по тем запросам, по
    которым открытые архивы и стоки не дали ничего, — так заказано, и это
    же разумно: у библиотеки суточный лимит, а у Wikimedia нет.

    Отдаёт столько, сколько осталось по суточному лимиту, и ни файлом
    больше. Формат возврата — тот же, что у остальных источников в
    assets.py: сборщик не должен знать, что этот источник особенный.
    """
    left = remaining("library")
    if left <= 0:
        log(f"  magnific: суточный лимит библиотеки выбран "
            f"({DAILY_LIBRARY_LIMIT}), пропускаю «{query}»")
        return []
    want = max(1, min(n, left))
    try:
        r = requests.get(f"{BASE}{SEARCH_PATH}", timeout=TIMEOUT,
                         headers=_headers(),
                         params={"query": query, "type": kind,
                                 "limit": want, "orientation": "landscape"})
    except requests.RequestException as e:
        log(f"  ! magnific: поиск «{query}» — сеть недоступна ({e})")
        return []
    if r.status_code != 200:
        log(f"  ! magnific: поиск «{query}» — ответ {r.status_code} "
            f"{r.text[:200]}")
        return []
    try:
        data = r.json()
    except ValueError:
        log(f"  ! magnific: поиск «{query}» — ответ не JSON")
        return []

    rows = data if isinstance(data, list) else None
    for k in ("data", "results", "items", "assets", "hits"):
        if rows is None and isinstance(data, dict) and isinstance(data.get(k), list):
            rows = data[k]
    rows = rows or []

    out = []
    for row in rows[:want]:
        url = _first_url(row)
        if not url:
            continue
        is_video = bool(re.search(r"\.(mp4|webm|mov)(\?|$)", url, re.I)) \
            or kind == "video"
        out.append({"url": url, "src": "magnific",
                    "kind": "video" if is_video else "image"})
    if out:
        log(f"  magnific: по «{query}» нашлось {len(out)} "
            f"(осталось на сутки {left - len(out)})")
    return out


def note_library(n: int = 1):
    """
    Отметить расход библиотеки. Вызывается ПОСЛЕ фактического скачивания.

    Отдельной функцией, а не внутри search_library: поиск может отдать
    десять ссылок, из которых скачаются три, и списывать лимит за
    несостоявшиеся файлы нечестно.
    """
    charge("library", n)


# ─────────────────────────── ПРОВЕРКА КЛЮЧА ───────────────────────────

def probe():
    """
    Дешёвый запрос, чтобы понять, принят ли ключ. Возвращает
    (True/False/None, текст) — None означает «не разобрались», и это НЕ
    повод останавливать сборку: проверка, которая роняет работающий
    пайплайн, хуже отсутствующей.
    """
    if not available():
        return None, "ключ не задан"
    try:
        r = requests.get(f"{BASE}/models", timeout=30, headers=_headers())
    except requests.RequestException as e:
        return None, f"сеть недоступна ({e})"
    if r.status_code == 200:
        return True, "принят"
    low = r.text.lower()
    if any(h in low for h in ("invalid_api_key", "invalid api key",
                              "incorrect api key", "unauthorized")):
        return False, f"ОТКАЗ {r.status_code} — {r.text[:200]}"
    # 404 на /models значит только, что такого пути нет: адрес методов у
    # сервиса может быть свой, и ронять из-за этого сборку нельзя.
    return None, (f"ответ {r.status_code} на {BASE}/models — разбираться не "
                  f"берусь: {r.text[:160]}")


if __name__ == "__main__":
    ok, why = probe()
    log(f"ключ: {why}")
    log(report())
    log(f"адрес: {BASE}")
    log(f"модели изображений: {', '.join(IMAGE_MODELS)}")
    log(f"модели видео: {', '.join(VIDEO_MODELS)}")
