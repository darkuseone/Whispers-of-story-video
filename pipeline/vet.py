"""
vet.py — автоматическая отбраковка материала. Заменяет человека на шаге отбора.

    python pipeline/vet.py jobs/pawn-02.json

Раньше между скачиванием и монтажом стоял человек: смотрел контактные листы и
называл номера негодного. Это работало, но означало ручной шаг в каждом ролике,
а в подборку регулярно попадают бананы по запросу «свет лампы на старом дереве»
и эскалаторы торгового центра по запросу «блошиный рынок».

Здесь тот же отбор делает робот, в два прохода:

  1. ДЕШЁВЫЙ, локальный. Ничего не стоит и ловит брак формы: почти белый кадр
     (каталожная съёмка на белом фоне — на холодном тёмном цветокоре канала
     это дыра в кадре), пустую заливку без деталей, мелкое разрешение, и
     главное — статичное «видео», где за десять секунд не меняется ничего.

  2. ЗРЕНИЕ, через xAI. Модели показывается кадр и рассказывается, о чём ролик.
     Она отвечает, годится ли кадр, ПОЧЕМУ и НАСКОЛЬКО ХОРОШО он подходит
     именно этому ролику — оценкой от единицы до пятёрки. Это единственный
     способ отличить храм от храма: ни теги стока, ни имя файла не знают,
     что на кадре стоит турист с рюкзаком, а само здание построено в
     девятнадцатом веке.

Отбраковка на этом канале СТРОЖЕ, чем на соседнем, и это заказано отдельно.
Три отличия:

  — доверенных источников нет вовсе (TRUSTED_SOURCES пуст): сток отдаёт по
    археологическому запросу чаек и спорткары, и никакой источник от этого
    не страхует;
  — у видео проверяются ДВА кадра, начало и конец, а не один из середины:
    именно на краях живут заставки оцифровщиков и титры;
  — мало сказать «годится»: кадр с оценкой ниже трёх не берётся, потому
    что материала всегда больше, чем нужно ролику.

Но строгость эта — про КАЧЕСТВО, а не про чистоту эпохи. Кадр годится, если
он про предмет выпуска: музейный зал с посетителями, археологи за работой и
съёмка места таким, какое оно сегодня, — это не порча древности, а то, как
она доходит до зрителя. См. DEFAULT_PERIOD_CONTEXT, там же цена прежнего
правила: 9 годных клипов из 315.

Второй проход НЕОБЯЗАТЕЛЕН. Нет ключа, модель недоступна, сеть легла — работает
только первый, в лог уходит внятное предупреждение, сборка не останавливается.
Проверка, которая роняет работающий пайплайн, хуже отсутствующей.

Результат ложится в work/<id>/assets/vetted.json и читается монтажом наравне с
ручным reject из спецификации. Файлы НЕ удаляются: вердикт отменяется правкой
одного поля, без повторного скачивания.
"""

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

XAI = "https://api.x.ai/v1"

# Модель зрения НЕ ЗАШИТА. Первый боевой прогон упёрся в
# «Model not found: grok-2-vision-1212»: имя, взятое из головы, оказалось
# несуществующим, зрение не сработало ни разу, и понятно это стало только
# из лога. Имена моделей живут своей жизнью и переживают любой код, который
# их угадывает.
#
# Поэтому модель ВЫБИРАЕТСЯ НА МЕСТЕ: список берётся у самого сервиса через
# /v1/models, кандидаты сортируются по предпочтению, и первый, который
# реально ответил на пробную картинку, идёт в работу. Поле vet_model в
# спецификации перебивает выбор, если нужно закрепить конкретную.
# Порядок предпочтения. Сначала явно НЕ рассуждающие: для ответа «годится
# или нет» рассуждение бесполезно, а платится оно выходными токенами.
# Дальше по возрастанию цены.
MODEL_PREFERENCE = ("non-reasoning", "nonreasoning", "non_reasoning",
                    "build", "vision", "4.20", "4-20", "4.3", "4-3",
                    "grok-4", "grok-3", "grok-2")
# Заведомо не для зрения: генераторы картинок и эмбеддинги.
MODEL_EXCLUDE = ("image", "imagine", "embed", "tts", "whisper")

PROBE_W = 512          # кадр под проверку: больше модели не нужно
WORKERS = 6            # запросов к зрению одновременно
VET_TIMEOUT = 60

# Цена зрения, долларов за миллион токенов. Сверено с прайс-листом xAI
# (скриншоты консоли, июль 2026). Ключ — кусок имени модели, значение —
# (вход, выход). Берётся первая подошедшая строка, иначе DEFAULT.
#
# Считать надо по ТОЙ модели, которую выбрал choose_model, а не по одной
# зашитой цифре: между самой дешёвой и самой дорогой из тех, что умеют
# зрение, разница втрое по выходу.
PRICE_TABLE = (
    ("build",   (1.00, 2.00)),   # Grok Build 0.1 — самый дешёвый со зрением
    ("4.20",    (1.25, 2.50)),   # Grok 4.20, в том числе Non-Reasoning
    ("4-20",    (1.25, 2.50)),
    ("4.3",     (1.25, 2.50)),   # Grok 4.3
    ("4-3",     (1.25, 2.50)),
    ("4.5",     (2.00, 6.00)),   # Grok 4.5 — дороже всех, выход втрое
    ("4-5",     (2.00, 6.00)),
)
PRICE_DEFAULT = (1.25, 2.50)


def price_of(model: str):
    """(доллары за млн входных, за млн выходных) для выбранной модели."""
    low = (model or "").lower()
    for key, pair in PRICE_TABLE:
        if key in low:
            return pair
    return PRICE_DEFAULT


# ЧЕРЕЗ xAI ВИДЕО НЕ ГЕНЕРИРУЕТСЯ НИКОГДА — так заказано, и цифры это
# подтверждают: Grok Imagine Video 1.5 стоит $0.25 за секунду в 1080p, то
# есть трёхсекундная вставка равна 75 центам при цене картинки в 2-5
# центов и проверки кадра зрением меньше цента.
#
# Короткие вставки, когда футажа по теме не нашлось вовсе, делает Magnific
# по подписке, потолком в 5% и с суточным лимитом — см. pipeline/magnific.py
# и assets.fill_video. Здесь, в отбраковке, они узнаются по источнику
# magnific-gen и зрением не проверяются: промпт наш собственный.

# Источники, которым верим БЕЗ зрения.
#
# СПИСОК ПУСТ, и это осознанное ужесточение. На канале о находках здесь
# стоял Met: музей предметов, по предметному запросу отдаёт предмет, и
# платить за просмотр его выдачи было не за что. Здесь другая задача —
# отсеивается не «не тот предмет», а не та ЭПОХА и не та цивилизация:
# средневековый замок, ренессансная живопись, викинги. Музейный источник
# от этого не спасает совсем, он честно отдаёт то, что нашёл по слову
# «temple», включая храмы XIX века.
#
# Зрение стоит центы за ролик (счёт печатается в конце) и это тот случай,
# когда платить надо: заказано «повысить качество отбраковки».
# Восстановить прежнее поведение можно полем trusted_sources в
# спецификации, не трогая код.
TRUSTED_SOURCES = ()

# Пороги дешёвого прохода.
#
# У белизны ДВА порога, и это главное в двухъярусной схеме. Выше HARD —
# кадр отбраковывается на месте, бесплатно. Между SOFT и HARD — случай
# непонятный: каталожное фото предмета на светлом фоне бывает и лучшим, что
# есть по теме. Такие уходят к зрению, а не в мусор.
PALE_HARD = 0.55       # ниже, чем было (0.62): грейд канала холодный и
PALE_SOFT = 0.30       # тёмный, белый прямоугольник на нём заметнее
# ЯРКОСТЬ. Порог опущен с 16 до 9 — и это послабление, а не ужесточение.
# Канал ночной: снятые в сумерках руины, звёздное небо и вода под луной
# честно имеют среднюю яркость 10-14, и прежний порог браковал ровно тот
# материал, ради которого канал существует.
DARK_MEAN = 9
# Зато добавлена проверка на ПУСТОТУ: ровная заливка без деталей.
# Тёмный кадр с фактурой — то, что нужно; тёмный кадр без фактуры —
# просто чернота, и отличаются они разбросом яркости, а не средней.
FLAT_STDEV = 6.0
MIN_PIXELS = 640 * 360
STATIC_DELTA = 1.6     # средняя разница кадров видео, ниже — стоп-кадр


def log(*a):
    print(*a, flush=True)


# ─────────────────────── ДЕШЁВЫЙ ПРОХОД ───────────────────────

def clip_seconds(path: Path) -> float:
    """
    Длительность клипа. Спрашивается ТРЕМЯ способами, а не одним.

    Раньше здесь стоял единственный запрос `format=duration`, и когда
    контейнер её не несёт, дальше срабатывал `if dur <= 0: return []`, а
    отбраковка печатала «файл не открылся». Формулировка вводила в
    заблуждение: файл открывается прекрасно, у него просто нет длительности
    в заголовке. На cahokia-01 так «не открылись» 136 клипов — при том, что
    playable() в assets.py те же файлы честно декодировал, и выметено из
    пула было всего три.

    Порядок: длительность контейнера, длительность видеопотока, и наконец
    пересчёт по пакетам — последнее медленно, поэтому спрашивается, только
    когда первые два промолчали.
    """
    probes = (
        ["-show_entries", "format=duration"],
        ["-select_streams", "v:0", "-show_entries", "stream=duration"],
        ["-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=duration"],
    )
    for args in probes:
        r = subprocess.run(["ffprobe", "-v", "error", *args,
                            "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True)
        try:
            dur = float(r.stdout.strip().rstrip(",").split(",")[0])
        except ValueError:
            continue
        if dur > 0:
            return dur
    return 0.0


def video_frames(path: Path, n=3):
    """n кадров, равномерно по клипу. Первые кадры у стоков часто чёрные."""
    dur = clip_seconds(path)
    if dur <= 0:
        # Длительности нет, но кадр в файле может быть. Берём первый без
        # перемотки: -ss по файлу без длительности не работает, а
        # единственный кадр из начала лучше, чем ложное «не открылся».
        tmp = path.parent / f"_{path.stem}_probe0.png"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                        "-frames:v", "1", "-y", str(tmp)], check=False)
        if tmp.exists():
            try:
                im = Image.open(tmp).convert("RGB").copy()
                tmp.unlink(missing_ok=True)
                return [im]
            except Exception:
                pass
            tmp.unlink(missing_ok=True)
        return []
    out = []
    for k in range(1, n + 1):
        at = dur * k / (n + 1)
        # Имя пробного кадра НЕ ДОЛЖНО начинаться с clip_ или arch_.
        # Раньше он назывался clip_007.probe1.png и попадал под тот же
        # glob, которым набирается материал. При обычной работе файл
        # удаляется и это незаметно, но прогон, снятый по таймауту или
        # отменённый в Actions, оставляет их на диске — и следующая
        # сборка честно берёт png-заглушку за клип, вписывает ей вердикт
        # и отдаёт монтажу.
        tmp = path.parent / f"_{path.stem}_probe{k}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}",
                        "-i", str(path), "-frames:v", "1", "-y", str(tmp)],
                       check=False)
        if tmp.exists():
            try:
                out.append(Image.open(tmp).convert("RGB").copy())
            except Exception:
                pass
            tmp.unlink(missing_ok=True)
    return out


def frame_stats(im: Image.Image):
    """(доля почти белого, средняя яркость, разброс) по одному кадру."""
    small = im.convert("L").resize((64, 36))
    try:
        px = list(small.get_flattened_data())
    except AttributeError:
        px = list(small.getdata())
    pale = sum(1 for p in px if p > 224) / len(px)
    mean = sum(px) / len(px)
    spread = (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5
    return pale, mean, spread


def frame_problems(im: Image.Image):
    """Беды одного кадра, которые видно арифметикой. (список, доля белого)."""
    bad = []
    pale, mean, spread = frame_stats(im)
    if pale > PALE_HARD:
        bad.append(f"почти белый кадр ({pale*100:.0f}% площади)")
    if mean < DARK_MEAN:
        bad.append(f"кадр практически чёрный (яркость {mean:.0f})")
    # ПУСТОЙ КАДР. Разброс яркости ниже порога означает заливку: ровное
    # небо, ровная стена, титр на однотонном фоне, кадр не в фокусе
    # целиком. На канале о находках такой проверки не было и она была не
    # нужна — там кадр всегда предметный. Здесь половина материала это
    # пейзаж, и пустых кадров приходит много.
    if spread < FLAT_STDEV:
        bad.append(f"пустой кадр без деталей (разброс яркости {spread:.1f})")
    return bad, pale


def cheap_problems(path: Path):
    """
    Брак формы, который виден без всякого зрения.

    Возвращает (кадр, список бед, доля белого, КАДРЫ ДЛЯ ЗРЕНИЯ). Кадры
    отдаются наружу, чтобы не декодировать файл второй раз ради модели.

    Для видео кадров ДВА, и это поправка, стоившая отдельного разбора. Со
    стоков и особенно из архивов приходят клипы, у которых середина
    честная, а начало или конец — заставка оцифровщика с адресом сайта,
    титр или полностью чёрный кадр. Проверка одного кадра из середины их
    пропускает, и в ролике на секунду появляется «archive.org». Смотрим
    первую и последнюю треть: если любая из них не годится, клип не
    годится целиком — резать его по кускам мы не умеем, а ClipCutter
    берёт из файла случайное место.

    АРИФМЕТИКА СЧИТАЕТСЯ ПО ВСЕМ ТРЁМ КАДРАМ, а не по одной середине.
    Раньше края уходили к зрению непроверенными: белую заставку архива и
    чёрный кадр на входе клипа отбраковывала модель — то есть ровно тот
    случай, ради которого края и смотрятся, оплачивался дважды (у видео
    спрашивается по два кадра). Вердикт от этого не меняется, меняется
    только цена: зрение и так отвечало «нет» на белый прямоугольник.
    """
    bad = []
    if path.suffix.lower() in (".mp4", ".m4v"):
        frames = video_frames(path)
        if not frames:
            return None, ["файл не открылся"], 0.0, []
        im = frames[len(frames) // 2]
        look = [frames[0], frames[-1]] if len(frames) >= 2 else [im]
        # Статичное «видео». Сток иногда отдаёт кадр, растянутый на десять
        # секунд: формально это видео, на экране — фотография без движения,
        # и вся идея перебивки на нём ломается.
        if len(frames) >= 2:
            import numpy as np
            a = np.asarray(frames[0].resize((160, 90)), float)
            b = np.asarray(frames[-1].resize((160, 90)), float)
            delta = abs(a - b).mean()
            if delta < STATIC_DELTA:
                bad.append(f"видео без движения (разница кадров {delta:.2f})")
    else:
        try:
            im = Image.open(path).convert("RGB")
        except Exception as e:
            return None, [f"файл не открылся: {e}"], 0.0, []
        look = [im]

    w, h = im.size
    if w * h < MIN_PIXELS:
        bad.append(f"мелкое разрешение {w}x{h}")

    mid_bad, pale = frame_problems(im)
    bad += mid_bad
    # Края клипа: те же проверки, но названные краем — иначе по логу не
    # понять, почему забракован клип с приличной серединой.
    for edge, eim in zip(("начало", "конец"), look):
        if eim is im:
            continue
        edge_bad, edge_pale = frame_problems(eim)
        pale = max(pale, edge_pale)
        bad += [f"{edge} клипа: {b}" for b in edge_bad]

    return im, bad, pale, look


# ─────────────────── ПОВТОРЫ И ПАМЯТЬ ВЕРДИКТОВ ───────────────────

def dhash(im: Image.Image, side=8) -> int:
    """
    Перцептивный хэш кадра: сравнение соседних пикселей по горизонтали.

    Нужен, чтобы не спрашивать зрение об одном и том же дважды. Стоки
    отдают сериями с одной съёмки, а один и тот же ролик приходит из
    Pexels и из Pixabay под разными именами — по запросу «server room
    night» таких близнецов приезжает по три-четыре штуки. Модель отвечает
    на них одинаково, а платится каждый раз.

    Считается по яркости и по РАЗНИЦЕ соседей, поэтому не ломается от
    смены экспозиции и сжатия — в отличие от хэша самого файла, который у
    двух копий одного клипа с разных стоков всегда разный.
    """
    px = list(im.convert("L").resize((side + 1, side)).getdata())
    bits = 0
    for row in range(side):
        base = row * (side + 1)
        for col in range(side):
            bits = (bits << 1) | int(px[base + col + 1] > px[base + col])
    return bits


# Насколько похожими считаем кадры. 64 бита всего; расхождение до четырёх
# — это одна и та же съёмка с другой экспозицией или другим сжатием.
# Больше брать нельзя: на восьми в одну корзину сваливаются просто два
# тёмных кадра, и вердикт по одному уезжает на посторонний материал.
DHASH_NEAR = 4

# А вот расхождение до двух бит — это уже не «похоже», а ОДНО И ТО ЖЕ:
# тот же клип, приехавший и с Pexels, и с Pixabay, или он же во второй раз
# по соседнему запросу. Такие не просто делят вердикт, а отбраковываются.
#
# Иначе они обходят потолок повторов: build.MAX_CLIP_REPEATS считает показы
# ОДНОГО ФАЙЛА, и два файла с одинаковой картинкой дают зрителю до шести
# появлений одного кадра за ролик — при том, что весь редакторский слой
# существует ровно затем, чтобы канал не выглядел конвейерным.
DHASH_SAME = 2


def file_key(path: Path) -> str:
    """
    Отпечаток файла для памяти вердиктов: имя, размер и края содержимого.

    Целиком файл не читается сознательно: материала под четыре гигабайта,
    а меняться после скачивания он не может — новый материал приезжает
    под новым именем. Размер вместе с двумя кусками по 64 КБ отличает
    подменённый файл от прежнего надёжнее, чем время правки, которое
    сбрасывается при восстановлении из кэша Actions.
    """
    h = hashlib.sha1()
    h.update(path.name.encode())
    try:
        size = path.stat().st_size
        h.update(str(size).encode())
        with path.open("rb") as f:
            h.update(f.read(65536))
            if size > 131072:
                f.seek(-65536, os.SEEK_END)
                h.update(f.read(65536))
    except OSError as e:
        h.update(str(e).encode())
    return h.hexdigest()


def policy_key(topic: str, desc: str, period_context: str,
               queries, trusted) -> str:
    """
    Отпечаток УСЛОВИЙ отбраковки — всего, от чего зависит вердикт.

    Память вердиктов действует, пока условия те же. Сюда входит ВСЁ, что
    участвует в решении: промпт и порог оценки, тема и эпоха выпуска,
    список запросов спецификации (по нему triage отличает старый материал
    от текущего), доверенные источники и пороги дешёвого яруса. Меняется
    любое из этого — ключ другой, память пуста, материал пересматривается
    целиком.

    Так правило канала «зрение обязано отработать заново, когда
    изменились запросы, промпт отбраковки или состав скачанного»
    перестаёт быть обещанием в документации и становится арифметикой
    ключа. Забыть про него нельзя: не совпал ключ — нет и памяти.
    """
    h = hashlib.sha1()
    thresholds = (PALE_HARD, PALE_SOFT, DARK_MEAN, FLAT_STDEV,
                  MIN_PIXELS, STATIC_DELTA, MIN_QUALITY)
    parts = [PROMPT, topic, desc, period_context,
             "|".join(sorted(queries or ())), "|".join(sorted(trusted or ())),
             ";".join(str(t) for t in thresholds)]
    for part in parts:
        h.update((part or "").encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def load_cache(path: Path, pkey: str):
    """
    Вердикты прошлых прогонов, если условия отбраковки те же.

    Пересборок у ролика пять-десять, а материал между ними тот же самый:
    без памяти каждая заново платит зрению за весь пул целиком (на
    dead-internet-01 это 853 запроса и полтора доллара за прогон). Смена
    условий отбраковки обнуляет память целиком — см. policy_key.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if data.get("policy") != pkey:
        log("  память вердиктов сброшена: условия отбраковки изменились "
            "(тема, vet_context, промпт или порог оценки)")
        return {}
    return data.get("verdicts") or {}


def save_cache(path: Path, pkey: str, verdicts: dict):
    path.write_text(json.dumps({"policy": pkey, "verdicts": verdicts},
                               indent=1, ensure_ascii=False), encoding="utf-8")


def near_groups(keys: dict, near=DHASH_NEAR):
    """
    Раскладывает файлы по корзинам похожести. {файл: хэш} -> [[файл, ...]].

    Перебор попарный и это осознанно: файлов сотни, не миллионы, а любая
    хитрая схема на таком объёме экономит миллисекунды и добавляет способ
    ошибиться.
    """
    groups, heads = [], []
    for f, h in keys.items():
        for gi, head in enumerate(heads):
            if bin(head ^ h).count("1") <= near:
                groups[gi].append(f)
                break
        else:
            heads.append(h)
            groups.append([f])
    return groups


def round_robin(items, key_of):
    """Переставляет список так, чтобы соседи были из разных групп."""
    lanes = {}
    for it in items:
        lanes.setdefault(key_of(it), []).append(it)
    out = []
    for i in range(max((len(v) for v in lanes.values()), default=0)):
        for lane in lanes.values():
            if i < len(lane):
                out.append(lane[i])
    return out


# Во сколько раз годного материала должно быть больше, чем ролик покажет,
# чтобы перестать платить за проверку остального. Порог применяется к
# КАЖДОМУ виду отдельно, поэтому вдвое здесь — это вчетверо суммарно.
#
# Арифметика на dead-internet-01: сорок минут, 145 кадров, из них реальных
# около восьмидесяти на оба вида вместе. Потолок повторов клипа — три
# (build.MAX_CLIP_REPEATS), то есть полсотни клиповых слотов закрываются
# семнадцатью разными файлами. Порог даёт по 118 годных на вид — вшестеро
# больше, чем нужно для разнообразия, и вчетверо больше всей потребности
# ролика в реальном материале.
VET_POOL_FACTOR = 2.0


def pool_budget(job, work: Path):
    """
    Сколько ГОДНОГО материала достаточно ролику. 0 — проверять всё подряд.

    Формула та же, что в assets.fill_gaps и в смоуке: длина звука делится
    на среднюю длину кадра, из этого вычитается доля генерации, и
    остаётся число реальных файлов. Держать её в трёх местах одинаковой
    важно — иначе одна проверка требует материала больше, чем другая
    считает достаточным.
    """
    factor = float(job.get("vet_pool_factor", VET_POOL_FACTOR))
    if factor <= 0:
        return 0
    try:
        total = float(json.loads(
            (work / "state.json").read_text(encoding="utf-8"))["total_audio"])
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return 0          # длины звука ещё нет — проверяем всё, как раньше
    ov = job.get("style_override") or {}
    base = float((ov.get("base_duration_range") or [9.0, 16.0])[0]) or 11.0
    gshare = float(ov.get("generated_share", 0.45))
    need = int(max(8, int(total / base)) * (1 - gshare) / 2.5)
    return max(24, int(need * factor))


# ─────────────────────── ЗРЕНИЕ ───────────────────────

# Канал расширен с августа 2026 за пределы античности: раскопки и артефакты,
# наука и космос, мифология, исторические расследования, «новый мир»
# (современные технологические и цифровые феномены — теория мёртвого
# интернета, ИИ и так далее). До этой правки PROMPT был жёстко зашит под
# один жанр — «канал про древний мир, отклоняй всё современное» — и это
# прямо ломало отбраковку на первом же ролике за пределами античности:
# dead-internet-01 про серверные и смартфоны получил бы вердикт «это не
# античный мир» на каждом собственном кадре. Универсальные правила (текст
# и логотипы, фэнтези-арт, дешёвый реконструкторский костюм, техническое
# качество) остались общими для всех жанров; то, что считается «своей
# эпохой» и что — анахронизмом, у каждого выпуска своё и приходит из
# спецификации через PERIOD_CONTEXT, а не из константы модуля.
# ПРАВИЛА ОТБОРА СМЯГЧЕНЫ ПО ЗАКАЗУ, И СМЯГЧЕНЫ ОСМЫСЛЕННО.
#
# Прежний текст требовал чистоты эпохи: «если в кадре виден турист или
# современный предмет — отбраковывай, даже если само место то самое».
# Замер на cahokia-01 показал, во что это обходится: из 315 клипов годными
# остались 9, из 270 фото — 23, и ролик собрался почти целиком из
# генерации. При этом ни одного отказа по ОЦЕНКЕ не было — все 306 отказов
# модель вынесла словом «нет», то есть виноват был именно этот текст, а не
# порог MIN_QUALITY.
#
# Новое правило простое: кадр годится, если он про предмет выпуска.
# Музейный зал с посетителями, археологи за работой, съёмка места таким,
# какое оно сегодня, — это не порча эпохи, а то, как древность доходит до
# зрителя. Отбраковывается теперь то, что к выпуску отношения не имеет
# вовсе (чайки и спорткары по археологическому запросу) и чужая
# цивилизация, поданная КАК предмет разговора.
DEFAULT_PERIOD_CONTEXT = """ACCEPT if the frame serves the subject of THIS \
episode. That includes:

- The subject itself: ruins, excavation sites, temples, mounds, statues, \
reliefs, inscriptions, pottery, artefacts, coins, manuscripts, maps and \
engravings.

- MUSEUM AND ARCHIVE FOOTAGE, INCLUDING VISITORS IN SHOT. A display case \
with people looking at it, a lit museum hall, a curator handling an object, \
a reading room. This is how the subject survives into the present and it \
belongs in the video.

- PRESENT-DAY WORK AND PLACES. Archaeologists digging, survey and scanning \
equipment, drone and aerial views of the site as it looks today, paths and \
signage at the site. Modern clothing and modern tools in such frames are \
expected, not a defect.

- ATMOSPHERE. Landscape, weather, sky, stars, water, stone, sand, fire, \
forest, river, prairie — anything carrying the mood of the place the \
episode is about.

REJECT only these:

- NOTHING TO DO WITH THE EPISODE. This is by far the commonest failure: \
stock libraries answer an archaeological query with wildlife, flowers, \
sports cars, city traffic, office desks, weddings. If you cannot say in one \
sentence how the frame relates to the subject, reject it.

- WRONG CIVILISATION OR ERA PRESENTED AS THE SUBJECT. An Egyptian pyramid \
in an episode about North America, a medieval castle in an episode about \
antiquity. A foreign monument filling the frame misleads the viewer about \
what is being discussed.

- MODERN LIFE AS THE SUBJECT rather than as context: shop windows, \
advertising, motorway traffic, sports events, people at leisure with no \
link to the topic.

Judge by RELEVANCE, not by period purity. A frame with people in modern \
clothes is fine when the frame is about the subject. A spotless \
period-accurate frame of the wrong civilisation is not."""

PROMPT = """You are the picture editor of a slow, atmospheric documentary \
channel. Videos run 30-40 minutes, are cool-toned, and are meant to be \
watched in the evening. There is no sensationalism: atmosphere, facts, and \
a steady investigative tone carry the video, not shock or drama.

THE VIDEO IS ABOUT: {topic}
{description}

WHAT COUNTS AS PERIOD-CORRECT FOR THIS SPECIFIC EPISODE:
{period_context}

Look at the attached frame and judge it as a working editor would: not "is \
this a nice picture" but "can this shot go into THIS video without a viewer \
noticing that it does not belong".

Beyond the period rules above, REJECT if the frame shows any of these —
they break the illusion regardless of the episode's subject:

1. TEXT, LOGOS, INTERFACES that are NOT the kind of material this specific \
episode is explicitly collecting. A website address, domain, ".com", \
watermark, channel logo, subtitle, chart, diagram with labels, or an \
archive/digitisation credit card of the sort that old public-domain footage \
carries at its start or end always breaks the illusion. A browser, phone \
screen, or old interface screenshot is fine ONLY if the period context \
above says this episode is specifically about old computers, screens, or \
internet history — otherwise reject it like any other modern chrome.

2. FANTASY AND GAME ART. Video game screenshots, CGI fantasy renders, \
concept art of creatures or magic, "ancient aliens" imagery, glowing \
portals, obvious 3D renders with plastic lighting — unless the episode is \
explicitly illustrating a myth or a fictional claim and says so above.

3. RE-ENACTMENT THAT LOOKS CHEAP. Costume parties, festival cosplay, \
plastic armour, tourist stage shows, film-set props. Serious museum or \
documentary reconstruction is fine; a man in a bedsheet is not.

4. STOCK FILLER UNRELATED TO THIS EPISODE. Generic posed business-handshake \
photography, lifestyle laptop-on-a-desk shots, unrelated sports, food, \
cosmetics, fireworks, pets, or wedding photography, generic abstract \
particle-motion backgrounds — material that has nothing to do with what \
this specific episode is actually about, even if it is technically clean.

5. TECHNICALLY UNUSABLE. Very blurry, heavily compressed, tiny, distorted \
by AI artefacts (melted faces, six-fingered hands, nonsense text), \
extremely low contrast, or a flat catalogue photograph of an object on a \
pure white background — this channel grades everything cold and dark, and a \
white rectangle burns a hole in the frame.

Then rate the frame from 1 to 5 on how good it is for THIS video:
  5 — could open the video
  4 — solid, would use without hesitation
  3 — usable but ordinary
  2 — weak, would only use if nothing else existed
  1 — should not be used

Be strict. There is more material than the video needs, so rejecting a \
doubtful frame costs nothing, while one wrong frame is visible to every \
viewer for the whole video.

Answer with STRICT JSON and nothing else:
{{"keep": true or false, "quality": 1-5, "why": "at most 12 words", \
"what": "what you see, at most 8 words"}}"""

# Ниже какой оценки кадр не берём, даже когда модель сказала keep=true.
#
# Заказано «повысить качество отбраковки», и это сделано здесь, а не
# ужесточением слова «нет»: модель охотно отвечает keep=true на «в принципе
# подходит», и на такой формулировке в ролик попадает всё, что не является
# прямым мусором. Оценка задаёт вопрос иначе — «насколько это хорошо
# ИМЕННО ДЛЯ ЭТОГО ролика», и двойка по ней означает «взяли бы, если бы
# больше ничего не было». Материала всегда больше, чем нужно ролику,
# поэтому такое не берём.
MIN_QUALITY = 3


def list_models(key):
    """Что сервис вообще умеет. Пустой список — не беда, будет запасной путь."""
    try:
        r = requests.get(f"{XAI}/models", timeout=30,
                         headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            log(f"  ? список моделей не отдался ({r.status_code})")
            return []
        return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
    except Exception as e:
        log(f"  ? список моделей не отдался ({e})")
        return []


def candidates(key, want=None):
    """Кандидаты в модель зрения, в порядке предпочтения."""
    if want:
        return [want]
    ids = [i for i in list_models(key)
           if not any(x in i.lower() for x in MODEL_EXCLUDE)]
    out = []
    for pat in MODEL_PREFERENCE:
        for i in ids:
            if pat in i.lower() and i not in out:
                out.append(i)
    for i in ids:                      # всё остальное — следом
        if i not in out:
            out.append(i)
    return out


def choose_model(im, topic, desc, key, want=None, period_context=None):
    """
    Подбирает рабочую модель на ОДНОЙ пробной картинке.

    Проверка боем, а не по имени: модель может существовать в списке и не
    принимать изображения. Единственный надёжный признак — она ответила.
    Стоит это один запрос на весь ролик.
    """
    cands = candidates(key, want)
    if not cands:
        return None, "сервис не отдал ни одной модели"
    for name in cands[:6]:
        keep, why, used, _q = ask_vision(im, topic, desc, name, key, tries=1,
                                         period_context=period_context)
        if keep is not None:
            log(f"  зрение: работает модель {name}")
            return name, ""
        log(f"  ? {name} не подошла: {why}")
    return None, f"ни одна из {len(cands[:6])} моделей не приняла картинку"


def to_data_url(im: Image.Image) -> str:
    im = im.copy()
    im.thumbnail((PROBE_W, PROBE_W))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask_vision(im: Image.Image, topic: str, description: str, model: str,
               key: str, tries=2, period_context=None):
    """
    Вердикт модели по одному кадру.

    Возвращает (годится, что видно, (входных токенов, выходных), оценка).

    При любой беде возвращает None в первом поле — «не знаю», и кадр
    остаётся в работе. Отбраковывать по неудавшемуся запросу нельзя: так
    молча пропадёт весь материал при первом же сбое сети.

    Оценка 1-5 — второй, более строгий вопрос поверх «годится или нет»,
    см. MIN_QUALITY. Модель, которая её не вернула, получает 3: не
    наказываем кадр за то, что модель ответила не полностью.

    period_context — что считается своей эпохой для ЭТОГО конкретного
    выпуска (см. DEFAULT_PERIOD_CONTEXT и topic_text). Канал больше не
    только про древний мир, и жёстко зашитого одного ответа на вопрос
    «что здесь анахронизм» с августа 2026 не существует.
    """
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(
                topic=topic, description=description,
                period_context=period_context or DEFAULT_PERIOD_CONTEXT)},
            {"type": "image_url",
             "image_url": {"url": to_data_url(im), "detail": "low"}},
        ]}],
    }
    for attempt in range(tries):
        try:
            r = requests.post(f"{XAI}/chat/completions", timeout=VET_TIMEOUT,
                              headers={"Authorization": f"Bearer {key}",
                                       "Content-Type": "application/json"},
                              json=body)
            if r.status_code != 200:
                if attempt + 1 < tries and r.status_code in (429, 500, 502, 503):
                    time.sleep(2 * (attempt + 1))
                    continue
                return (None, f"зрение ответило {r.status_code}: "
                        f"{r.text[:120]}", (0, 0), 0)
            txt = r.json()["choices"][0]["message"]["content"].strip()
            # модель иногда оборачивает JSON в ```json ... ```
            if txt.startswith("```"):
                txt = txt.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
            data = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
            u = r.json().get("usage") or {}
            used = (int(u.get("prompt_tokens") or 0),
                    int(u.get("completion_tokens") or 0))
            try:
                quality = int(round(float(data.get("quality", 3))))
            except (TypeError, ValueError):
                quality = 3
            quality = max(1, min(5, quality))
            keep = bool(data.get("keep", True))
            why = str(data.get("what") or data.get("why") or "")[:70]
            if keep and quality < MIN_QUALITY:
                keep = False
                why = f"оценка {quality}/5 — {why}"
            return keep, why, used, quality
        except Exception as e:
            if attempt + 1 < tries:
                time.sleep(1.5)
                continue
            return None, f"зрение не ответило: {e}", (0, 0), 0
    return None, "зрение не ответило", (0, 0), 0


# ─────────────────────── ГЛАВНОЕ ───────────────────────

def index_of(path: Path) -> int:
    return int(path.name.split("_")[1])


def topic_text(job):
    t = job.get("topic") or {}
    y = job.get("youtube") or {}
    topic = t.get("slug", "").replace("-", " ") or y.get("title", "")
    kw = ", ".join(t.get("keywords", []))
    desc = y.get("description_intro", "")
    return (topic + (f" ({kw})" if kw else ""),
            f"More context: {desc}" if desc else "")


def period_context_of(job):
    """
    Что для ЭТОГО выпуска считается своей эпохой, а что анахронизмом.

    Поле vet_context — свободный текст в спецификации ролика: только автор
    сценария знает, что здесь материал по теме, а что мусор (для выпуска
    про интернет-архивы старый скриншот форума — то, что нужно, а не
    брак). Поля нет — берётся DEFAULT_PERIOD_CONTEXT, старое поведение
    «канал про древний мир»: все выпуски античной линейки его не задают
    и продолжают вести себя как раньше.
    """
    return job.get("vet_context") or DEFAULT_PERIOD_CONTEXT


def manifest_meta(work: Path):
    """Источник и запрос каждого файла — из манифестов скачивания."""
    meta = {}
    for folder in ("footage", "archive"):
        man = work / folder / "_manifest.json"
        if not man.exists():
            continue
        try:
            rows = json.loads(man.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for row in rows:
            name = Path(row.get("file", "")).name
            if name:
                meta[name] = (row.get("src", ""), row.get("q", ""))
    return meta


def words(text: str):
    import re as _re
    stop = {"the", "a", "an", "of", "and", "or", "in", "on", "at", "to", "for",
            "with", "from", "closeup", "close", "historic", "old"}
    return {w for w in _re.findall(r"[a-zA-Z]+", (text or "").lower())
            if len(w) > 2 and w not in stop}


def triage(path: Path, im, bad, pale, src, query, current_queries, trusted):
    """
    ПЕРВЫЙ ЯРУС. Решает бесплатно всё, что можно решить бесплатно, и
    честно говорит «не знаю» там, где нужен взгляд.

    Возвращает "reject" | "keep" | "ask" и причину.

    Смысл ярусов в деньгах. Зрение стоит за каждый кадр, и платить за
    очевидное незачем: белый прямоугольник, чёрный кадр и стоп-кадр вместо
    видео видно арифметикой. Ровно так же незачем платить за то, что и так
    заведомо по теме: музейный предмет из Met по предметному запросу и
    собственная генерация по собственному промпту.

    Зрению достаётся середина — то, про что локальные признаки молчат.
    Именно там и живут бананы по запросу про лампу.
    """
    if bad:
        return "reject", "; ".join(bad)

    # собственная генерация: она нарисована по нашему же промпту
    if path.name.startswith("img_"):
        return "keep", "сгенерировано по промпту ролика"
    # и сгенерированные видеовставки тоже: промпт наш, тема наша, а
    # тратить зрение на проверку собственного заказа незачем
    if src == "magnific-gen":
        return "keep", "видеовставка сгенерирована по промпту ролика"

    # Запрос, которого в спецификации больше нет, — это материал, оставшийся
    # в кэше от прошлой версии ролика. Он не обязательно плох, но и по теме
    # уже не гарантирован, поэтому идёт к зрению, а НЕ в отказ.
    #
    # Отбраковывать здесь по пересечению слов запроса с темой нельзя, и это
    # проверено: запросы пишутся под ролик и заведомо по теме, но написаны
    # они другими словами. «hands examining antique object» не пересекается
    # с ключевыми словами темы ни одним словом — и правило, которое казалось
    # разумным, забраковало на тесте весь годный материал разом.
    if current_queries and query and query not in current_queries:
        return "ask", f"запрос «{query}» не из текущей спецификации"

    # доверенный источник — смотреть незачем
    if src in trusted:
        return "keep", f"{src}: предметный источник, отдаёт предметы"

    if pale >= PALE_SOFT:
        return "ask", f"светлый фон {pale*100:.0f}% — нужен взгляд"
    return "ask", "локальные признаки молчат"


# ─────────────────── ВТОРОЙ ЯРУС: КРИТИК ───────────────────

# Пороги критика. Все три подобраны с запасом в сторону «пропустить»:
# ошибка критика стоит дороже ошибки зрения, потому что она бесплатная и
# потому молчаливая — забракованный им кадр никто не пересматривает.
SHARP_MIN = 12.0       # разброс лапласиана на кадре 320 px по ширине


def sharpness(im: Image.Image) -> float:
    """
    Насколько кадр в фокусе. Разброс лапласиана — классическая мера, и
    считается она четырьмя сдвигами массива, без scipy и OpenCV.

    Ширина приводится к 320 px: мера зависит от размера, и без нормировки
    один и тот же кадр в 4K и в 640x360 даёт цифры, различающиеся на
    порядок. Сравнивать их между собой было бы нельзя.
    """
    import numpy as np
    g = np.asarray(im.convert("L").resize((320, 180)), float)
    lap = (4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1]
           - g[1:-1, :-2] - g[1:-1, 2:])
    return float(lap.var())


def critic(im, look, kind):
    """
    ВТОРОЙ ЯРУС. Бесплатный, между жёстким отбором и зрением.

    Возвращает "reject" | "ask" и причину. Ничего не одобряет: сказать
    «годится» про содержание кадра арифметика не может, это работа зрения.
    Задача критика ровно обратная — снять со зрения то, за что не стоит
    платить, потому что кадр негоден по ФОРМЕ, а не по смыслу.

    Первый ярус ловит брак грубый: белый прямоугольник, чёрный кадр,
    стоп-кадр вместо видео. Между ним и «нужен взгляд» остаётся широкая
    полоса, которую тоже видно без модели: расфокус, поля от оцифровки на
    полкадра. На cahokia-01 зрению уходило по 89 файлов на проход, и
    заметная часть этого — именно такие кадры.

    У видео критик смотрит ВСЕ кадры и берёт худший, по той же причине,
    что и первый ярус: ClipCutter возьмёт из файла случайное место, и
    честной серединой расфокусированный хвост не искупается.
    """
    frames = [f for f in ([im] + list(look or [])) if f is not None]
    if not frames:
        return "ask", ""

    soft = min(sharpness(f) for f in frames)
    if soft < SHARP_MIN:
        return "reject", f"кадр не в фокусе (резкость {soft:.0f})"

    # ПРОВЕРКИ НА ПОЛЯ ОЦИФРОВКИ ЗДЕСЬ НЕТ, И ЭТО РЕШЕНИЕ, А НЕ ПРОБЕЛ.
    # Она была написана и выброшена: отличить чёрную рамку от ночного неба
    # арифметикой не выходит. После сжатия кадра до сетки, на которой такое
    # считают, редкие звёзды пропадают, и строка неба становится ровной и
    # тёмной — то есть неотличимой от рамки. На замере ночной кадр с
    # силуэтом земли уверенно определялся как «поля на весь кадр».
    #
    # Канал ночной. Критик бесплатный и потому молчаливый: забракованное им
    # никто не пересматривает. Ошибаться так на основном материале канала
    # он права не имеет, а рамку и без него увидит зрение — оно как раз
    # умеет отличать чёрную полосу от тёмного неба.
    return "ask", ""


def vet_all(job, work: Path, use_vision=True):
    topic, desc = topic_text(job)
    period_context = period_context_of(job)
    # Модель не задаётся здесь: её подбирает choose_model ниже, на пробной
    # картинке. Раньше на этом месте стояла зашитая константа с угаданным
    # именем — она и оказалась несуществующей.
    model = None
    # ИМЯ ЗДЕСЬ ЗНАЧАЩЕЕ, НЕ ПЕРЕИМЕНОВЫВАТЬ ОБРАТНО В key. Ниже по циклу
    # у каждого файла есть свой «ключ» — отпечаток содержимого для памяти
    # вердиктов, — и пока обе величины звались key, вторая затирала первую
    # на первом же файле. Проба модели идёт ДО цикла и потому проходила, а
    # все реальные запросы уходили с отпечатком файла вместо ключа и
    # получали 400 «Incorrect API key provided».
    #
    # Стоило это целого ролика: на cahokia-01 зрение не ответило ни разу
    # (97 запросов, ноль токенов), отбраковка весь прогон держалась на
    # одном локальном ярусе, и в ролик ушло всё, что не является прямым
    # мусором. В логе это выглядело как отказ сервиса, а не как своя
    # ошибка, — потому и прожило несколько прогонов.
    api_key = (os.environ.get("XAI_API_KEY") or "").strip()
    trusted = tuple(job.get("trusted_sources", TRUSTED_SOURCES))
    current_queries = set(job.get("footage_queries", []) +
                          job.get("archive_queries", []))

    meta = manifest_meta(work)
    groups = {"clip": sorted((work / "footage").glob("clip_*")),
              "arch": sorted((work / "archive").glob("arch_*"))}

    # Память вердиктов и её ключ. Живёт рядом с материалом, то есть
    # переезжает вместе с ним в кэш Actions и обратно.
    pkey = policy_key(topic, desc, period_context, current_queries, trusted)
    cache_path = work / "vet_cache.json"
    cache = load_cache(cache_path, pkey)
    # ВЕРДИКТЫ СЛОМАННОЙ ПРОВЕРКИ ЗАБЫВАЮТСЯ. «файл не открылся» до правки
    # clip_seconds означало всего лишь отсутствие длительности в заголовке
    # контейнера — файл при этом открывался и декодировался (assets.playable
    # те же самые честно пропускал). Отпечаток файла от правки кода не
    # меняется, поэтому память вернула бы старый отказ, не перепроверяя, и
    # починенная проверка не получила бы ни одного шанса.
    #
    # Выбрасываются ТОЛЬКО эти записи. Вердикты зрения стоят денег и
    # остаются на месте: обнулять их из-за чужой ошибки незачем.
    stale = [k for k, v in cache.items()
             if not v.get("keep") and "не открылся" in (v.get("why") or "")]
    for k in stale:
        cache.pop(k, None)
    if stale:
        log(f"  забыто {len(stale)} отказов «файл не открылся» — их выносила "
            f"сломанная проверка длительности, материал пересмотрится")
    from_cache = twins = 0
    skipped_files = set()
    budget = pool_budget(job, work)
    if budget:
        log(f"  достаточно {budget} годных файлов каждого вида — дальше "
            f"зрение не спрашивается")

    verdicts = {"clip": {}, "arch": {}}
    tok_in = tok_out = asked = 0
    vision_ok = use_vision and bool(api_key)
    if use_vision and not api_key:
        log("  ! нет XAI_API_KEY — зрение выключено, работает только "
            "локальный ярус")

    # Рабочая модель ищется ОДИН раз на ролик, до всех проверок: иначе
    # неверное имя означает полсотни одинаковых отказов подряд и ноль
    # пользы, ровно как на первом прогоне.
    if vision_ok:
        probe = None
        for files in groups.values():
            for f in files:
                im, bad, _pale, _look = cheap_problems(f)
                if im is not None:
                    probe = im
                    break
            if probe is not None:
                break
        if probe is None:
            vision_ok = False
        else:
            model, why = choose_model(probe, topic, desc, api_key,
                                      job.get("vet_model"),
                                      period_context=period_context)
            if not model:
                log(f"  ! зрение недоступно: {why} — работает только "
                    f"локальный ярус, спорное остаётся в работе")
                vision_ok = False

    for kind, files in groups.items():
        if not files:
            continue
        log(f"── проверяю {kind}: {len(files)} шт")

        decided, ask_list, frames, fkeys, mid_hash = {}, [], {}, {}, {}
        cheap_n = cached_n = twin_n = critic_n = 0
        for f in files:
            # ПАМЯТЬ ПРОВЕРЯЕТСЯ ДО РАЗБОРА ФАЙЛА. Отпечаток считается по
            # имени и краям содержимого, без декодирования: у клипа это
            # три вызова ffmpeg, и на четырёх сотнях файлов именно они, а
            # не зрение, занимают почти всё время шага. Пересборка, в
            # которой ничего не менялось, теперь проходит его насквозь.
            fkeys[f] = fkey = file_key(f)
            hit = cache.get(fkey)
            if hit is not None:
                decided[f] = (bool(hit["keep"]), hit["why"])
                cached_n += 1
                continue
            im, bad, pale, look = cheap_problems(f)
            src, query = meta.get(f.name, ("", ""))
            verdict, why = triage(f, im, bad, pale, src, query,
                                  current_queries, trusted)
            # ВТОРОЙ ЯРУС. Спрашивается только о том, что первый отправил
            # к зрению, и только про форму кадра. Забракованное критиком
            # до платного запроса не доходит.
            if verdict == "ask" and im is not None:
                crit, crit_why = critic(im, look, kind)
                if crit == "reject":
                    verdict, why = "reject", crit_why
                    critic_n += 1
            if verdict != "ask" or im is None:
                decided[f] = (verdict == "keep", why)
                cache[fkey] = {"keep": verdict == "keep", "why": why}
                cheap_n += 1
                continue
            # КАДРЫ ДЕРЖИМ ТОЛЬКО ДЛЯ ТЕХ, КОГО СПРОСИМ, а от среднего
            # оставляем один хэш вместо картинки. Материала под четыре
            # сотни файлов, кадр в 1920x1080 занимает шесть мегабайт, и
            # лишний кадр на файл — это гигабайты памяти раннера на ровном
            # месте. Хэш нужен только для корзин близнецов, ему хватает
            # сетки 9x8.
            frames[f] = look or [im]
            mid_hash[f] = dhash(im)
            ask_list.append((f, why))

        # БЛИЗНЕЦЫ. Спрашиваем по одному представителю от корзины, вердикт
        # ставим всей корзине.
        #
        # Корзины складываются ТОЛЬКО ВНУТРИ ОДНОГО ЗАПРОСА, и это не
        # осторожность, а необходимость: перцептивный хэш считается по
        # яркости, и два просто тёмных кадра из разных запросов попадают в
        # одну корзину, ничем друг на друга не походя. Тогда вердикт по
        # серверной уехал бы на архивное фото телефонистки. В пределах же
        # одного запроса близнец — это ровно то, чем он кажется: один и
        # тот же клип, приехавший и с Pexels, и с Pixabay, или соседние
        # кадры одной съёмки.
        rep_of, bucket_of, dups = {}, {}, {}
        if len(ask_list) > 1:
            why_of = dict(ask_list)
            by_query = {}
            for f, _w in ask_list:
                src, q = meta.get(f.name, ("", ""))
                # Файл без записи в манифесте — материал неизвестного
                # происхождения (старый кэш, ручная подкладка). Такие в
                # корзины не сводятся вовсе: раз запрос неизвестен, нет и
                # оснований считать два похожих по яркости кадра одним и
                # тем же. Своя корзина у каждого — ключ по имени файла.
                #
                # Синтетика из mock.py — туда же, и по той же причине с
                # обратным знаком: она СОВПАДАЕТ побитово по построению
                # (testsrc2 один на всех), и сведение близнецов честно
                # схлопывало бы весь мок-пул в один файл. Смоук после
                # этого падал на долях материала — то есть проверка
                # ломалась о собственную заглушку, а не о код.
                lane = f"?{f.name}" if (src == "mock" or not q) else q
                by_query.setdefault(lane, []).append(f)
            reps = []
            for lane in by_query.values():
                hashes = {f: mid_hash[f] for f in lane}
                for b in near_groups(hashes):
                    bucket_of[b[0]] = b
                    reps.append((b[0], why_of[b[0]]))
                    for twin in b[1:]:
                        rep_of[twin] = b[0]
                        twin_n += 1
                        gap = bin(hashes[b[0]] ^ hashes[twin]).count("1")
                        if gap <= DHASH_SAME:
                            dups[twin] = (b[0], gap)
            ask_list = reps
        from_cache += cached_n
        twins += twin_n

        # ПОРЯДОК ОПРОСА — ВПЕРЕМЕЖКУ ПО ЗАПРОСАМ. Файлы пронумерованы в
        # порядке скачивания, то есть подряд идёт вся выдача одного
        # запроса. При остановке по достатку (ниже) такой порядок оставил
        # бы ролик на первых пяти запросах спецификации из двадцати восьми,
        # а остальные темы не попали бы в кадр вовсе.
        ask_list = round_robin(ask_list,
                               lambda it: meta.get(it[0].name, ("", ""))[1])

        log(f"   первый ярус решил {cheap_n} арифметикой" +
            (f", {cached_n} из памяти прошлых прогонов" if cached_n else "") +
            (f", {twin_n} как близнецов уже спрошенного" if twin_n else "") +
            (f", {critic_n} снял критик" if critic_n else "") +
            f", зрению осталось {len(ask_list)}")

        vision_out = {}
        if vision_ok and ask_list:
            def one(item):
                """
                Вердикт по файлу. У видео СПРАШИВАЕМ ПРО ДВА КАДРА и
                берём худший ответ: клип годится целиком или не годится
                вовсе, потому что ClipCutter возьмёт из него случайное
                место. Именно так в ролик и попадает заставка архива —
                середина честная, край нет.
                """
                f, _why = item
                worst = None
                # Расход считается по КАЖДОМУ запросу, а не по вернувшемуся
                # вердикту. У видео их два, наружу уходит один — худший, —
                # и если сложить только его токены, строка стоимости
                # занижает счёт вдвое на всём видео ролика.
                tin = tout = calls = 0
                for im in frames[f][:2]:
                    res = ask_vision(im, topic, desc, model, api_key,
                                     period_context=period_context)
                    tin += res[2][0]
                    tout += res[2][1]
                    calls += 1
                    if worst is None:
                        worst = res
                    elif res[0] is False:
                        worst = res
                        break
                    elif res[0] is not None and worst[0] is None:
                        worst = res
                return f, worst, tin, tout, calls

            # ОСТАНОВКА ПО ДОСТАТКУ. Скачивается материала в разы больше,
            # чем ролик покажет: overshoot, добор по дырам и запас на
            # отбраковку вместе дают под четыре сотни файлов на ролик,
            # которому нужно шесть десятков. Раньше зрение платно смотрело
            # ВСЕ — то есть три четверти счёта уходило на материал, до
            # экрана заведомо не доходящий.
            #
            # Считается по ГОДНЫМ, а не по просмотренным, поэтому на плохом
            # материале остановка просто не наступает и поведение остаётся
            # прежним. Порядок вперемежку по запросам (выше) держит темы
            # ролика представленными поровну.
            kept_now = sum(1 for keep, _ in decided.values() if keep)
            pending, skipped = list(ask_list), []
            chunk_n = max(WORKERS * 3, 15)
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                while pending:
                    if budget and kept_now >= budget:
                        skipped = pending
                        break
                    chunk, pending = pending[:chunk_n], pending[chunk_n:]
                    for f, res, tin, tout, calls in ex.map(one, chunk):
                        vision_out[f] = res
                        tok_in += tin
                        tok_out += tout
                        asked += calls
                        if res and res[0]:
                            # Дубли из корзины в пул не пойдут, значит и в
                            # счёт достатка они не идут.
                            kept_now += sum(1 for m in bucket_of.get(f, [f])
                                            if m not in dups)
            for f, _why in skipped:
                for member in bucket_of.get(f, [f]):
                    decided[member] = (
                        False, f"проверка остановлена: годного уже {kept_now} "
                               f"при достаточных {budget}")
                    skipped_files.add(member)
            if skipped:
                log(f"   остановился на достатке: {kept_now} годных при "
                    f"достаточных {budget}, не смотрел ещё "
                    f"{sum(len(bucket_of.get(f, [f])) for f, _ in skipped)}")
            done = [v for v in vision_out.values() if v]
            if done and all(v[0] is None for v in done):
                log(f"  ! зрение не ответило ни разу ({done[0][1]}) — "
                    f"спорное оставляю в работе")
        elif ask_list:
            log("   зрение недоступно — спорное оставляю в работе")

        kept = 0
        for f in files:
            n = index_of(f)
            if f in decided:
                keep, why = decided[f]
            else:
                # Близнец берёт вердикт своего представителя: его и
                # спрашивали вместо него.
                asked_for = rep_of.get(f, f)
                answer = vision_out.get(asked_for)
                if f in dups:
                    # Дубль: тот же кадр под другим именем. В пул не идёт
                    # даже когда оригинал годный — иначе он появится в
                    # ролике вдвое чаще, чем разрешает потолок повторов.
                    twin_of, gap = dups[f]
                    keep = False
                    why = (f"дубль {twin_of.name} (расхождение {gap} бита "
                           f"из 64) — тот же кадр из другого источника")
                    cache[fkeys[f]] = {"keep": False, "why": why}
                elif answer is None:
                    # Зрения не было вовсе — спорное остаётся в работе.
                    keep, why = True, "зрение не спрашивалось"
                else:
                    keep, why = answer[0], answer[1]
                    if keep is None:
                        keep, why = True, f"неясный ответ зрения: {why}"
                    else:
                        if asked_for != f:
                            why = f"как у близнеца {asked_for.name}: {why}"
                        # В память кладётся только то, что реально решило
                        # зрение. «Не ответило» — это не вердикт, и
                        # запоминать его значило бы закрепить сетевой сбой
                        # на все будущие пересборки. Близнецы кладутся
                        # тоже: иначе следующая пересборка соберёт корзины
                        # заново и снова заплатит за представителя.
                        cache[fkeys[f]] = {"keep": bool(keep), "why": why}
            verdicts[kind][str(n)] = {"keep": bool(keep), "why": why}
            # ПРИЗНАК «НЕ СМОТРЕЛИ». Для монтажа это тот же отказ — файл в
            # ролик не идёт. А вот добору материала (assets.refill_after_vet)
            # разница принципиальна: он меряет долю брака и по ней решает,
            # качать ли вторую волну. Без этого признака остановка по
            # достатку выглядела бы как «отбраковка съела половину пула» и
            # запускала бы лишнее скачивание — за которое ещё и платит
            # суточный лимит библиотеки.
            if f in skipped_files:
                verdicts[kind][str(n)]["skipped"] = True
            kept += bool(keep)

        log(f"   годных {kept}, отбраковано {len(files) - kept}")
        for n, v in sorted(verdicts[kind].items(), key=lambda x: int(x[0])):
            if not v["keep"]:
                log(f"     {kind} {int(n):03d}: {v['why']}")

    save_cache(cache_path, pkey, cache)
    if from_cache or twins:
        log(f"── сэкономлено запросов: {from_cache} из памяти прошлых "
            f"прогонов, {twins} на близнецах")

    if asked:
        p_in, p_out = price_of(model or "")
        cost = tok_in / 1e6 * p_in + tok_out / 1e6 * p_out
        log(f"── зрение ({model}): {asked} запросов, "
            f"{tok_in} входных и {tok_out} выходных токенов")
        log(f"   ${cost:.3f} за ролик, ${cost/asked*1000:.2f} за тысячу запросов "
            f"(тариф ${p_in:.2f}/${p_out:.2f} за млн). "
            f"Токены измерены по полю usage в ответах API.")
    elif not vision_ok:
        log("── зрение НЕ РАБОТАЛО: отбор сделан одним локальным ярусом, "
            "спорный материал остался в ролике")
    else:
        log("── зрение не понадобилось: всё решил первый ярус")

    out = work / "vetted.json"
    out.write_text(json.dumps(verdicts, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    log(f"── вердикты записаны в {out}")
    return verdicts


def skipped_from(work: Path):
    """
    Номера, которых зрение НЕ СМОТРЕЛО из-за остановки по достатку.

    Монтажу они не нужны — для него это обычный отказ. Нужны добору
    материала: он меряет долю брака, а непросмотренное браком не является
    и в эту долю попадать не должно.
    """
    p = work / "vetted.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {kind: {int(n) for n, v in d.items() if v.get("skipped")}
            for kind, d in data.items()}


def rejected_from(work: Path):
    """Номера, забракованные роботом. Читается монтажом."""
    p = work / "vetted.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {kind: sorted(int(n) for n, v in d.items() if not v.get("keep"))
            for kind, d in data.items()}


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    work = Path("work") / job["id"] / "assets"
    if not work.exists():
        raise SystemExit(f"нет {work} — сначала собери материал")
    vet_all(job, work, use_vision=job.get("vet_vision", True))


if __name__ == "__main__":
    main(sys.argv[1])
