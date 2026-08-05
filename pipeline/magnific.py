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
Freepik в апреле 2026 переименовался в Magnific; рабочий адрес API —
`api.freepik.com/v1`, старый `api.magnific.ai` никогда не существовал —
это была ошибочная догадка первого прогона, и именно она уронила все
запросы 404-й на `ancient-mini`. Подтверждено: базовый адрес
`https://api.freepik.com/v1` (у `api.magnific.com` тот же ключ, но
`freepik.com` — исходный и точно рабочий), заголовок авторизации —
`x-freepik-api-key` (НЕ `Authorization: Bearer`), путь у каждой модели
свой: `/ai/text-to-image/{slug}` и `/ai/image-to-video/{slug}`, ответ —
асинхронная задача (`task_id` + `status`, опрос по тому же пути плюс
`/{task_id}`), aspect_ratio — не "16:9", а именованный enum
(`widescreen_16_9`).

Что здесь ДОГАДКА, а не факт: официальная документация
(docs.freepik.com) отдаёт боту 403 на прямой заход, и точные slug'и
только четырёх нужных этому каналу моделей (flux2pro, nano-banana2,
seedream5pro и все четыре видео) в поисковой выдаче не встретились —
встретились только соседние версии (seedream-v4-5, seedream-v5-lite,
kling-v2, minimax-hailuo-02, wan v2.2 без версии в имени пути). Слуги
ниже — лучшее приближение по образцу этих соседей. Неверный слуг НЕ
рушит сборку: путь ответит 404, generate_image/generate_video вернут
False, и assets.build_images откатится на xAI — ровно так это и
сработало в первом прогоне. Поправить, когда найдётся точное имя, можно
одной переменной окружения, без правки кода:

    MAGNIFIC_BASE=https://api.freepik.com/v1
    MAGNIFIC_IMAGE_SLUGS={"seedream5pro": "seedream-v5"}
    MAGNIFIC_VIDEO_SLUGS={"kling-2.5": "kling-v2-5-pro"}

(JSON — только те ключи, что нужно поправить, остальные останутся по
умолчанию).

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

BASE = (os.environ.get("MAGNIFIC_BASE") or "https://api.freepik.com/v1").rstrip("/")
IMAGE_CATEGORY = "text-to-image"
VIDEO_CATEGORY = "image-to-video"
SEARCH_PATH = os.environ.get("MAGNIFIC_SEARCH_PATH", "/resources")

TIMEOUT = 90
POLL_EVERY = 6
POLL_TRIES = 40           # до четырёх минут на одно видео

# Безлимитные по подписке модели изображений. Порядок значения не имеет:
# они чередуются, а не ранжируются. Ключи — внутренние имена (используются
# в логах и job-спеке), значения — реальные slug'и в пути REST API.
_DEFAULT_IMAGE_SLUGS = {
    "flux2pro": "flux-2-pro",
    "nano-banana2": "nano-banana-pro",
    "seedream5pro": "seedream-v5-pro",
}

# Модели видео. Тоже по подписке, но используются в час по чайной ложке —
# см. VIDEO_SHARE.
_DEFAULT_VIDEO_SLUGS = {
    "minimax-hailuo-2.3": "minimax-hailuo-2-3-1080p",
    "seedance-1.5pro": "seedance-pro-1-5-1080p",
    "kling-2.5": "kling-v2-5-pro",
    "wan-2.2": "wan-v2-2-720p",
}


def _load_slug_overrides(env_name: str, defaults: dict) -> dict:
    """Слуги по умолчанию + то, что переопределено JSON'ом в переменной
    окружения. Битый JSON не роняет сборку — просто игнорируется с
    предупреждением в лог."""
    merged = dict(defaults)
    raw = os.environ.get(env_name)
    if not raw:
        return merged
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError:
        log(f"  ! magnific: {env_name} — не разобрать как JSON, игнорирую")
        return merged
    if isinstance(overrides, dict):
        merged.update({k: v for k, v in overrides.items() if isinstance(v, str)})
    return merged


IMAGE_SLUGS = _load_slug_overrides("MAGNIFIC_IMAGE_SLUGS", _DEFAULT_IMAGE_SLUGS)
VIDEO_SLUGS = _load_slug_overrides("MAGNIFIC_VIDEO_SLUGS", _DEFAULT_VIDEO_SLUGS)

IMAGE_MODELS = list(_DEFAULT_IMAGE_SLUGS)
VIDEO_MODELS = list(_DEFAULT_VIDEO_SLUGS)

# Freepik принимает не "16:9", а именованный enum. Соответствие —
# подтверждённое (imagen3/seedream в документации), полный список шире,
# но каналу нужны только эти два кадра.
_ASPECT_RATIOS = {
    "16:9": "widescreen_16_9",
    "9:16": "social_story_9_16",
    "1:1": "square_1_1",
}


def _aspect(a: str) -> str:
    return _ASPECT_RATIOS.get(a, "widescreen_16_9")


# Сколько СЕКУНД просить у видеомодели. Только короткая вставка: длинный
# сгенерированный план разваливается сам — у моделей плывут руки, буквы и
# архитектура, и чем дольше кадр, тем виднее. НО ни одна из четырёх видео
# моделей не принимает 2-3 секунды буквально (у всех есть минимальная
# длительность в самом API, 4-6 с) — заказывается минимально возможная, а
# до нужных 2-3 с на экране кадр обрезает та же нарезка, что и обычный
# скачанный клип: она не смотрит, откуда файл, и обрезает любой источник
# под план кадра.
VIDEO_SECONDS = (2, 3)
_MIN_API_DURATION = {
    "minimax-hailuo-2.3": 6,
    "seedance-1.5pro": 4,
    "kling-2.5": 5,
    "wan-2.2": 5,
}

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
    return {"x-freepik-api-key": key(),
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


def _wait_for(create_path: str, job: str, what: str) -> str:
    """
    Опрашивает задание, пока не появится ссылка. Пусто — не дождались.

    create_path — тот же путь, которым задание создавалось
    (/ai/{category}/{slug}); у Freepik статус задания живёт по тому же
    пути с /{task_id} на конце, отдельного /jobs/{id} у сервиса нет.
    """
    for _ in range(POLL_TRIES):
        time.sleep(POLL_EVERY)
        try:
            r = requests.get(f"{BASE}{create_path}/{job}", timeout=TIMEOUT,
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
    slug = IMAGE_SLUGS.get(model, model)
    path = f"/ai/{IMAGE_CATEGORY}/{slug}"
    data = _post(path, {"prompt": prompt, "aspect_ratio": _aspect(aspect)},
                 f"картинка моделью {model} ({slug})")
    if data is None:
        return False
    url = _first_url(data)
    if not url:
        job = _job_id(data)
        url = _wait_for(path, job, f"картинка моделью {model}") if job else ""
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


def generate_video(prompt: str, dst: Path,
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
    slug = VIDEO_SLUGS.get(model, model)
    # Заказанные 2-3 с — это то, сколько кадр держится НА ЭКРАНЕ, а не то,
    # что примет API: у моделей есть свой минимум (см. _MIN_API_DURATION),
    # обрезка до нужной длины — забота обычной нарезки клипа, не этой
    # функции.
    api_seconds = _MIN_API_DURATION.get(model, 5)
    path = f"/ai/{VIDEO_CATEGORY}/{slug}"
    data = _post(path, {"prompt": prompt, "duration": api_seconds,
                        "aspect_ratio": _aspect("16:9")},
                 f"видео моделью {model} ({slug})")
    if data is None:
        return False
    url = _first_url(data)
    if not url:
        job = _job_id(data)
        url = _wait_for(path, job, f"видео моделью {model}") if job else ""
    if not url:
        log(f"  ! magnific: в ответе нет ссылки — {json.dumps(data)[:240]}")
        return False
    if not _download(url, dst):
        return False
    charge("video")
    log(f"  magnific: видео {api_seconds} с моделью {model} -> {dst.name}")
    return True


# ─────────────────────────── БИБЛИОТЕКА ───────────────────────────

def _resolve_download(resource_id) -> str:
    """
    Поиск в /resources отдаёт только превью — лицензированная ссылка на
    файл выдаётся отдельным вызовом на id найденного ресурса. Без этого
    шага в манифест уезжала бы водяная ссылка на превью вместо самого
    файла.
    """
    try:
        r = requests.get(f"{BASE}/resources/{resource_id}/download",
                         timeout=TIMEOUT, headers=_headers())
    except requests.RequestException as e:
        log(f"  ! magnific: скачивание ресурса {resource_id} — сеть "
            f"недоступна ({e})")
        return ""
    if r.status_code != 200:
        log(f"  ! magnific: скачивание ресурса {resource_id} — ответ "
            f"{r.status_code} {r.text[:200]}")
        return ""
    try:
        return _first_url(r.json())
    except ValueError:
        return ""


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
    # Ресурсный поиск Freepik: "term" (не "query"), а фильтры — ВЛОЖЕННЫЕ
    # объекты, а не скалярные значения: `filters[orientation]=landscape`
    # сервис отвергает с 400 «The filters.orientation must be an array»
    # (проверено на живом прогоне), правильная форма —
    # `filters[orientation][landscape]=1`.
    #
    # Для kind="image" тип содержимого НЕ ограничивается: фото, вектор,
    # графика — любое сгодится, это и называется в спецификации «видео,
    # фото, векторы, графика».
    #
    # Просим с запасом (× 2): не у каждого найденного ресурса резолвится
    # скачивание с первого раза, а лимит списывается по факту, не по
    # найденному.
    base_params = {"term": query, "limit": want * 2, "order": "relevance"}
    params = dict(base_params, **{"filters[orientation][landscape]": "1"})
    if kind == "video":
        params["filters[content_type][video]"] = "1"

    def ask(p):
        try:
            return requests.get(f"{BASE}{SEARCH_PATH}", timeout=TIMEOUT,
                                headers=_headers(), params=p)
        except requests.RequestException as e:
            log(f"  ! magnific: поиск «{query}» — сеть недоступна ({e})")
            return None

    r = ask(params)
    if r is None:
        return []
    # 400 — это придирка к ФОРМЕ фильтра, а не отказ в поиске. Библиотека
    # тут источник последней очереди: по этому запросу уже не нашлось
    # ничего нигде, и вернуть пусто из-за спорного имени параметра — хуже,
    # чем вернуть неотфильтрованное. Пробуем ещё раз голым запросом.
    if r.status_code == 400:
        log(f"  ! magnific: поиск «{query}» — фильтры не приняты "
            f"({r.text[:160]}), повторяю без них")
        r = ask(base_params)
        if r is None:
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
    for row in rows:
        if len(out) >= want:
            break
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        url = _resolve_download(rid) if rid else _first_url(row)
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
    # Ресурсный поиск с limit=1 — единственный подтверждённый GET-путь без
    # побочных эффектов (генерация — POST и тратит квоту, а /models,
    # которым проверяли раньше, у этого API не существует вовсе — это и
    # било 404-й на каждом прогоне).
    try:
        r = requests.get(f"{BASE}{SEARCH_PATH}", timeout=30,
                         headers=_headers(),
                         params={"term": "test", "limit": 1})
    except requests.RequestException as e:
        return None, f"сеть недоступна ({e})"
    if r.status_code == 200:
        return True, "принят"
    low = r.text.lower()
    if any(h in low for h in ("invalid_api_key", "invalid api key",
                              "incorrect api key", "unauthorized",
                              "forbidden")):
        return False, f"ОТКАЗ {r.status_code} — {r.text[:200]}"
    return None, (f"ответ {r.status_code} на {BASE}{SEARCH_PATH} — "
                  f"разбираться не берусь: {r.text[:160]}")


# Запасные написания slug'ов. Документация отдаёт боту 403, имена
# подбираются по образцу соседних моделей, и с первого раза угадались не
# все. Проверка путей бесплатная (см. check_slug), поэтому кандидатов
# можно перечислять сколько угодно: selftest переберёт их и скажет,
# какое написание настоящее.
SLUG_CANDIDATES = {
    # Пять путей ниже ПОДТВЕРЖДЕНЫ прогоном: сервис ответил на них 400
    # «негодное тело», то есть путь отозвался. Запасные им не нужны.
    "flux2pro": ["flux-2-pro"],
    "seedream5pro": ["seedream-v5-pro"],
    "nano-banana2": ["nano-banana-pro"],
    "minimax-hailuo-2.3": ["minimax-hailuo-2-3-1080p"],
    "kling-2.5": ["kling-v2-5-pro"],
    "wan-2.2": ["wan-v2-2-720p"],
    # Единственный ненайденный. Тринадцать написаний в image-to-video дали
    # 404; возможно, эта модель живёт в другой категории (text-to-video) —
    # проверяется последними кандидатами с явным префиксом категории.
    "seedance-1.5pro": ["seedance-pro-1-5", "seedance-lite-1080p",
                        "seedance-2-0-pro", "seedance-1-5",
                        "bytedance-seedance-pro-1-5"],
}


def check_slug(category: str, slug: str):
    """
    Существует ли путь модели. Спрашиваем POST с заведомо НЕГОДНЫМ телом.

    Почему именно так, а не GET: GET по пути модели есть НЕ У ВСЕХ — из
    семи наших моделей на GET отзывается одна kling, а остальные шесть
    отвечают 404 при живом и рабочем POST. Проверка через GET была
    поставлена и тут же снята: она объявила несуществующими пять моделей,
    которые на деле есть.

    Тело подобрано так, чтобы до генерации дело не дошло: пустой prompt и
    нулевая длительность не проходят проверку параметров у любой модели.
    Гарантии всё же нет — kling однажды принял ПУСТОЙ объект `{}` и завёл
    настоящее задание, — поэтому 200 здесь считается «путь есть, и, судя
    по всему, задание создано», и об этом печатается предупреждение.

    Возвращает (True/False/None, текст).
    """
    path = f"/ai/{category}/{slug}"
    body = {"prompt": ""}
    if category == VIDEO_CATEGORY:
        body["duration"] = 0
    try:
        r = requests.post(f"{BASE}{path}", timeout=30, headers=_headers(),
                          json=body)
    except requests.RequestException as e:
        return None, f"сеть недоступна ({e})"
    if r.status_code == 404:
        return False, f"404 — такого пути нет ({path})"
    if r.status_code in (400, 422):
        return True, f"путь есть (ответ {r.status_code} на негодное тело)"
    if r.status_code == 405:
        return True, "путь есть (метод не тот, но путь отозвался)"
    if r.status_code in (200, 201, 202):
        return True, ("путь есть, НО СОЗДАЛОСЬ ЗАДАНИЕ — модель приняла "
                      "негодное тело")
    if r.status_code in (401, 403):
        return None, f"{r.status_code} — ключ не пустили сюда: {r.text[:120]}"
    return None, f"ответ {r.status_code}: {r.text[:120]}"


def find_slug(category: str, name: str, current: str):
    """
    Перебирает кандидатов, пока не найдётся существующий путь. Отдаёт
    (рабочий slug или "", строки для лога).
    """
    tried = []
    for slug in dict.fromkeys([current] + SLUG_CANDIDATES.get(name, [])):
        good, note = check_slug(category, slug)
        tried.append(f"      {slug:34} {note}")
        if good:
            return slug, tried
    return "", tried


def selftest():
    """
    Проверка Magnific без сборки ролика: принят ли ключ, отвечает ли
    библиотека и СУЩЕСТВУЮТ ЛИ пути всех восьми моделей.

    Ничего не генерирует и не тратит суточный лимит — это диагностика,
    которую можно гонять сколько угодно. Запускается этапом `magnific`
    в Actions.
    """
    ok, why = probe()
    log(f"ключ   : {why}")
    log(f"адрес  : {BASE}")
    log(report())
    if ok is False:
        return 1

    bad, fixed = [], {}

    for title, models, slugs, category in (
            ("изображений", IMAGE_MODELS, IMAGE_SLUGS, IMAGE_CATEGORY),
            ("видео", VIDEO_MODELS, VIDEO_SLUGS, VIDEO_CATEGORY)):
        log(f"\n── пути моделей {title}")
        for name in models:
            current = slugs[name]
            good, note = check_slug(category, current)
            if good:
                log(f"  + {name:20} -> {current}")
                continue
            # Основное написание не отозвалось — перебираем запасные.
            found, tried = find_slug(category, name, current)
            if found:
                log(f"  ~ {name:20} -> {current} НЕ существует, "
                    f"но подошло: {found}")
                fixed[name] = found
            else:
                log(f"  ! {name:20} -> рабочего написания не нашлось")
                bad.append(name)
            for line in tried:
                log(line)

    log("\n── библиотека")
    rows = search_library("ancient greek temple ruins", 2, "image")
    log(f"  найдено {len(rows)} (лимит НЕ списан: считается по скачанному)")

    if fixed:
        log("\nНАШЛОСЬ рабочее написание — вписать в magnific.py "
            "(или переменной окружения):")
        for name, slug in fixed.items():
            log(f"  {name}: {slug}")
    if bad:
        log("\nНИ ОДИН кандидат не подошёл: " + ", ".join(bad))
        log("Дописать кандидатов в SLUG_CANDIDATES и прогнать этап заново.")
    if not fixed and not bad:
        log("\nвсе пути на месте")
    log("\nНеверный путь сборку НЕ роняет: доля уходит на xAI. Но "
        "заказанные 70/30 при этом не соблюдаются.")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    ok, why = probe()
    log(f"ключ: {why}")
    log(report())
    log(f"адрес: {BASE}")
    log(f"модели изображений: {', '.join(IMAGE_MODELS)}")
    log(f"модели видео: {', '.join(VIDEO_MODELS)}")
