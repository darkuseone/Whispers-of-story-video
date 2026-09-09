"""
covers.py — две обложки ролика через xAI, с жёлтым текстом на картинке.

    Вызывается из youtube.py после сборки ролика.
    Можно отдельно:  python -c "from covers import main; main('jobs/…')"

ШРИФТ — со скриншота автора, и только он: extra-condensed ALL CAPS
Anton / Impact, жёлтый #FFD400, 2–5 слов, огромный кегль. Композицию
со скриншота не копировать (это был образец букв, не шаблон кадра).

КАДР — из docs/протокол-обложки.md: исследования CTR 2026 для безликих
history / mystery. Паттерны object / scene / silhouette / split /
tension. Две обложки = два разных паттерна.

ПРОМПТ переписан в сентябре 2026 под то, как диффузионная модель на
самом деле читает текст: запреты в утверждениях, слов качества нет
вовсе, свет задан планом, композиция — долями кадра. Разбор и источники
— в docs/протокол-обложки.md и в комментарии над SCENE_LOCK.

Текст рисует модель (grok-imagine-image); PIL + Anton — запасной путь
без ключа. Кто именно набирает жёлтый хук, решает TEXT_BY_MODEL ниже.
Кэш: лежащие cover_1.jpg / cover_2.jpg не перерисовываются.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import requests

XAI = "https://api.x.ai/v1"
W, H = 1280, 720

ROOT = Path(__file__).resolve().parent.parent
FONT_FILE = ROOT / "assets" / "fonts" / "Anton-Regular.ttf"
YELLOW = (255, 212, 0)          # #FFD400 — тот же жёлтый, что на шортсах
YELLOW_HEX = "#FFD400"

# Кликбейт, который YouTube читает как Unsatisfying, и хуки, которые
# на превью не работают: слишком общие, чтобы выделить ролик в ленте.
BANNED_HOOKS = (
    "WATCH THIS", "YOU WON'T BELIEVE", "YOU WONT BELIEVE",
    "MUST SEE", "MUST WATCH", "CLICK HERE", "GONE WRONG",
    "SHOCKING TRUTH", "WAIT FOR IT", "THE SECRET THEY",
)

# Паттерны кадра — не «ещё один туманный пейзаж». Сводка исследований
# в docs/протокол-обложки.md, формулировки — из разбора промптинга
# диффузионных моделей (сентябрь 2026, см. там же раздел «Что в промпте»).
#
# У каждого паттерна теперь названы РАЗМЕР ПЛАНА, ВЫСОТА КАМЕРЫ, ФОРМА
# СВЕТА и то, чем кадр читается на 120 пикселях. Размер плана —
# единственный «камерный» параметр, который модель исполняет надёжно;
# фокусное и диафрагма работают как стилевой сигнал, а не как расчёт.
# Имена совпадают с youtube.cover_patterns.
PATTERNS = {
    "object": (
        "PATTERN object: extreme close-up. One single artifact fills 60 to 70 "
        "percent of the frame and is cropped by the bottom edge; camera just "
        "below its centre line, looking slightly up. A hard key skims the "
        "surface from the upper left so every crack, chip and tool mark "
        "throws its own shadow, and a thin cold rim traces the far contour "
        "against a near-black void. Nothing else is in focus. The silhouette "
        "alone must be identifiable at 120 pixels."
    ),
    "scene": (
        "PATTERN scene: one wide establishing shot of a single place, never a "
        "collage. Camera low, roughly knee height, horizon in the lower "
        "third. Exactly one practical light source is visible in frame — a "
        "lamp, a fire, a torch, a headlight, a lit doorway — and it accounts "
        "for every highlight in the picture. Real atmosphere: haze with "
        "visible light shafts, not a fog filter. The frame resolves into "
        "three masses: dark foreground, one lit middle ground, quiet sky. One "
        "small human-scale element far away carries the sense of size."
    ),
    "silhouette": (
        "PATTERN silhouette: a single figure seen from behind or reduced to a "
        "solid black shape against the only light in the frame, so there is "
        "no face, no skin detail and no expression to read. In its hands or "
        "at its feet, one small object is the brightest thing in the picture "
        "and carries the whole story. Hard backlight with a halo of haze "
        "around it; the outline of the figure stays clean and unbroken. "
        "Identity remains hidden — that is the point of the shot."
    ),
    "split": (
        "PATTERN split: one hard vertical seam down the exact middle of the "
        "frame. The SAME subject on both sides, from the SAME camera "
        "position and the same lens — intact and warmly lit on the left, "
        "ruined, empty or cold on the right. The seam is a straight cut with "
        "no glow, no gradient, no frame and no device around it. The contrast "
        "between the halves is the only hook; no arrows, no labels, no "
        "before-and-after captions."
    ),
    "tension": (
        "PATTERN tension: a medium-wide shot of an ordinary place in darkness "
        "with exactly one thing wrong, and only that thing is lit — a broken "
        "seal, a torn-out page, a redacted line on a stamped document, an "
        "empty plinth with the dust outline still on it, a door standing open "
        "into blackness. The wrong detail is the brightest five percent of "
        "the picture; everything around it collapses into one dark shape. "
        "Uncanny and quiet, never gory, never a fake catastrophe."
    ),
}

# ─────────────────── КАДР: ЧТО ГОВОРИТЬ МОДЕЛИ ───────────────────
#
# Четыре правила промптинга, из-за которых этот текст выглядит именно так.
# Все четыре измерены не нами, источники — в docs/протокол-обложки.md.
#
# 1. ОТРИЦАНИЯ НЕ РАБОТАЮТ. «No logos, no arrows» модель читает как
#    перечисление того, что надо нарисовать: кросс-внимание остаётся на
#    токенах самого понятия, а инструкция запрета почти не влияет на
#    эмбеддинг. Поэтому запреты переписаны в УТВЕРЖДЕНИЯ («каждая
#    поверхность, способная нести надпись, пуста или отвёрнута»), а
#    короткий хвост «Avoid, if any of it creeps in» оставлен только как
#    слабая подстраховка — и намеренно стоит последним.
#
# 2. СЛОВА КАЧЕСТВА ПОРТЯТ КАДР. photorealistic / 8k / ultra-detailed /
#    masterpiece, а в 2026-м и cinematic включают «эстетический режим»:
#    глянец, ровный свет, симметрия. Признак AI-картинки сегодня — не
#    низкое разрешение, а то, что она слишком чистая. Вместо них здесь
#    описан ЗАХВАТ: зерно, халация, виньетка, промах фокуса, хроматика.
#
# 3. СВЕТ ЗАДАЁТСЯ ПЛАНОМ, А НЕ НАСТРОЕНИЕМ. Один мотивированный ключ,
#    его направление, жёсткость, сколько заполнения и как ведут себя
#    тени. Букет из пяти световых терминов делает результат
#    непредсказуемым — схема должна быть ОДНА на промпт. Поэтому свет
#    живёт В ПАТТЕРНЕ, а не здесь: у объекта в упор и у общего плана с
#    костром он разный, и общая схема поверх частной их бы столкнула.
#
# 4. КОМПОЗИЦИЯ — ДОЛЯМИ КАДРА, А НЕ АЛЬТЕРНАТИВОЙ. Прежнее «оставь
#    пустоту сверху ИЛИ сбоку» модель не разбирает, и зона под текст
#    плавала от выпуска к выпуску. Теперь она прибита к верхней трети —
#    туда же, куда paint_hook кладёт жёлтый Anton в запасном пути.
#
# Порядок частей внутри строки тоже не случаен: xAI переписывает промпт
# перед генерацией (revised_prompt), и хвост он «украшает» охотнее, чем
# начало. Формат и паттерн поэтому стоят первыми.
SCENE_LOCK = (
    "16:9 single photographic frame, one exposure, no collage, no panels, "
    "no border. A night-time historical investigation, documentary "
    "photography, not an illustration and not a movie poster. "
    "{pattern} "
    "SUBJECT, the only thing the frame is about: {scene}. "
    "PALETTE, two colours and no third: an ink-blue near-black background "
    "(#0B1220 to #161C28) and a warm bone-white, aged-ivory or old-gold "
    "subject. The warm/cool split lives between key and rim light, not in a "
    "global filter over the whole frame. "
    "CAPTURE: real optics and a real sensor — fine film grain, gentle "
    "vignetting toward the corners, faint halation where the brightest "
    "highlight blows out, barely visible chromatic aberration on the "
    "high-contrast edge, one plane of dust in the light. Surfaces are "
    "physical and used: worn, chipped, oxidised, pitted, dusty, unevenly "
    "polished, fingerprinted. "
    "READS SMALL: the picture resolves into three or four large value shapes "
    "and still reads in grayscale at 120 pixels wide. The subject's "
    "silhouette stays unbroken against the background. Four visual elements "
    "at most, and the eye lands on one of them first. "
    "LAYOUT: the subject sits in the lower two thirds and may be cropped by "
    "the bottom edge; the top third stays quiet, dark negative space. Keep 8 "
    "percent margins, no critical detail on the far edges because mobile "
    "crops them, and keep the bottom-right 180x72 pixels dark and empty for "
    "the YouTube duration badge. "
    "Everything visible is a physical object photographed by a camera. Any "
    "person present is turned away or is a shape in shadow, so the frame "
    "carries no eyes and no expression. Composition is deliberately "
    "off-centre, exposure protects the highlights, the grade is a hard "
    "two-colour contrast. "
    "Avoid, if any of it creeps in: beige-and-teal AI grading, flat even "
    "overexposed lighting, mirror symmetry, busy cluttered background, "
    "airbrushed plastic surfaces, HDR glow, watermark, logo, arrow, circle, "
    "starburst, app interface."
)

# ────────────────── КТО НАБИРАЕТ ЖЁЛТЫЙ ХУК ──────────────────
#
# True  — буквы рисует модель вместе с кадром (TYPE_LOCK). Так канал
#         работает с самого начала, и так оно остаётся по умолчанию.
# False — модель отдаёт ЧИСТЫЙ кадр с тёмной полосой сверху (PLATE_LOCK),
#         а жёлтый Anton кладёт поверх paint_hook — тот самый код, что
#         уже работает в запасном пути без ключа.
#
# ПЕРЕКЛЮЧАТЕЛЬ ЗАВЕДЁН НЕ РАДИ СИММЕТРИИ. Замеры точности рендера
# текста диффузионными моделями (MARIO-Eval: 14.4% у сырого Flux-стека,
# на котором стоит grok-imagine) означают, что опечатка в хуке —
# вопрос статистики, а не невезения. А проверить её здесь НЕКОМУ:
# build_covers пишет файл, логирует килобайты и КЭШИРУЕТ его, так что
# «THE 1983 CIA DOSSSIER» уедет на YouTube и останется там, пока
# человек не удалит jpg руками. Плюс у модели каждый раз свои буквы —
# другой вес, другой трекинг, другое место, — а протокол обложек
# требует ровно обратного: «менять кадр и хук; не менять буквы».
#
# Менять значение осознанно и сразу на обе обложки: половина выпуска
# с одними буквами, половина с другими — хуже любого из двух вариантов.
TEXT_BY_MODEL = True

# Типографика, когда буквы рисует МОДЕЛЬ.
#
# Названия гарнитур модель не понимает — она понимает ХАРАКТЕР
# начертания. «Anton» здесь оставлено как ключ для self_check и как
# слабая подсказка, а работу делает описание: extra-condensed
# ultra-bold all-caps grotesque. Цвет назван и словом, и кодом: голый
# hex понимает не всякая модель, голое название понимают все, пара
# надёжнее любой половины.
TYPE_LOCK = (
    "TYPOGRAPHY, follow literally: the picture contains exactly one piece of "
    "text and no other writing anywhere. The headline reads exactly "
    "\"{text}\" — those words, that spelling, that capitalisation, nothing "
    "added, nothing abbreviated, no punctuation invented. "
    "It sits inside the empty negative-space band across the top third, "
    "horizontally centred, one line if it is two words and two centred lines "
    "otherwise. "
    "Letterforms: an extra-condensed ultra-bold all-caps grotesque of the "
    "Anton / Impact kind — tall narrow capitals, thick even strokes, flat "
    "solid fill, very tight tracking, baseline dead level. "
    "Colour: one saturated school-bus yellow " + YELLOW_HEX + " on every "
    "letter, identical across the whole headline. "
    "Cap height about a quarter of the frame so the words survive at 120 "
    "pixels wide. "
    "The letters are clean flat shapes carrying a soft drop shadow for "
    "separation, and they stay flat: no bevel, no gradient, no outline, no "
    "glow, no distress texture, no perspective. "
    "Avoid, if any of it creeps in: a second line of copy, a subtitle, a "
    "channel name, a date, a page number, a signature, a logotype."
)

# Типографика, когда буквы кладёт PIL (TEXT_BY_MODEL = False).
#
# Модель просят не «оставить место», а нарисовать тёмную малодетальную
# полосу, ПРИНАДЛЕЖАЩУЮ сцене: небо, дымка, стена в тени, уход в чёрное.
# Разница принципиальная — наложенная плашка читается как «наклейка», а
# полоса, встроенная в кадр, отделяет жёлтый сама, без рамки.
# Яркость под 25%: именно на таком фоне #FFD400 держит контраст на 120 px.
PLATE_LOCK = (
    "TYPE PLATE: this image contains no writing at all. Every surface that "
    "could carry lettering — signs, plaques, book spines, paper, screens, "
    "engravings, labels — is blank, worn smooth, turned away from camera or "
    "far out of focus, so no glyph, letter, digit, rune or caption appears "
    "anywhere in the frame. "
    "The top third of the frame is a clean, dark, low-detail band that "
    "belongs to the scene: a smooth gradient of sky, haze, a wall in shadow "
    "or open blackness, with no small objects, no branches, no skyline "
    "clutter and no bright specular hits inside it. Its luminance stays "
    "under 25 percent so that a very large all-caps headline set in bright "
    "yellow can be composited over it afterwards and separate instantly at "
    "120 pixels wide. That band is negative space, not a drawn rectangle and "
    "not a banner: nothing outlines it, nothing frames it. "
    "Keep the bottom-right 180x72 pixels equally dark and empty."
)

def log(*a):
    print(*a, flush=True)


def cover_texts(job):
    """
    Две короткие строки для обложек.

    Сначала youtube.cover_texts из спецификации — человек знает, какой
    хук кликает на его канале. Иначе — title_alternatives, иначе ужатый
    title. Длиннее пяти слов режется: на превью канала длинный заголовок
    не читается, это видно на любом скрине ленты.
    """
    y = job.get("youtube") or {}
    raw = list(y.get("cover_texts") or [])
    if len(raw) < 2:
        raw += list(y.get("title_alternatives") or [])
    if len(raw) < 2:
        title = y.get("title") or job.get("id", "STORY")
        raw.append(title)
        raw.append(title)
    out = []
    for t in raw[:2]:
        words = re.sub(r"[^\w\s'\-]", "", str(t), flags=re.UNICODE).split()
        # 2-5 слов ЗАГЛАВНЫМИ — как на канале
        hook = " ".join(words[:5]).upper() if words else "WATCH THIS"
        out.append(hook)
    return out


# Слова качества и служебные приставки промптов ролика. Из cover_scenes
# они вырезаются, и это не косметика.
#
# cover_scenes задаёт сценарист, но когда их нет, сцена берётся из
# image_prompts — а те написаны под ИЛЛЮСТРАЦИИ ролика и начинаются с
# «Cinematic photoreal wide shot of…». Дальше эта приставка уезжает в
# слот SUBJECT и включает у модели ровно тот «эстетический режим»
# (глянец, ровный свет, симметрия), который весь остальной промпт
# старательно выключает. Замерено на gateway-process-01: без вырезания
# в промпт обложки уходило «Cinematic photoreal wide shot of a weathered
# vintage 1983 declassified military document…».
_SCENE_JUNK = re.compile(
    r"^\W*(?:"
    r"cinematic|photoreal(?:istic)?|hyperrealistic|ultra[- ]detailed|"
    r"highly detailed|masterpiece|8k|4k|high resolution|"
    r"(?:extreme |wide |medium |close[- ]up |establishing |aerial )?shot of|"
    r"an? |the |image of|photo(?:graph)? of|render of"
    r")\W*", re.IGNORECASE)


# Те же слова качества, но ВНУТРИ подсказки: «Dark cinematic wide shot
# of a wall of monitors» приставкой не снимается — впереди стоит «Dark».
# Здесь вырезаются только чистые прилагательные качества, без «shot of»
# и артиклей: их удаление в середине строки ломало бы грамматику.
_SCENE_JUNK_ANY = re.compile(
    r"\b(?:cinematic|photoreal(?:istic)?|hyperrealistic|ultra[- ]detailed|"
    r"highly detailed|masterpiece|8k|4k|award[- ]winning|breathtaking)\b",
    re.IGNORECASE)


def _clean_scene(bit: str) -> str:
    """Срезает слова качества и «shot of» с начала подсказки сцены."""
    bit = " ".join(str(bit or "").split())
    for _ in range(6):          # приставки идут цепочкой, снимаем по одной
        new = _SCENE_JUNK.sub("", bit, count=1)
        if new == bit:
            break
        bit = new
    bit = " ".join(_SCENE_JUNK_ANY.sub(" ", bit).split())
    return bit.strip(" ,.;:—-")


def scene_hints(job):
    """
    Две разных сцены под две обложки.

    Сначала youtube.cover_scenes — это то, что сценарист написал именно
    под превью. Иначе первый кусок image_prompts / archive_queries, как
    раньше: лучше слабая сцена, чем пустой промпт. Слова качества с
    начала подсказки срезаются, см. _SCENE_JUNK.
    """
    y = job.get("youtube") or {}
    hints = []
    for s in y.get("cover_scenes") or []:
        bit = _clean_scene(s)
        if bit and bit not in hints:
            hints.append(bit)
    for p in (job.get("image_prompts") or [])[:8]:
        bit = _clean_scene(re.split(r",", str(p))[0])
        if bit and bit not in hints:
            hints.append(bit)
        if len(hints) >= 2:
            break
    for q in (job.get("archive_queries") or [])[:4]:
        bit = str(q).strip()
        if bit and bit not in hints:
            hints.append(bit)
        if len(hints) >= 2:
            break
    while len(hints) < 2:
        hints.append("a single ancient artifact against a dark navy void")
    return hints[:2]


def cover_patterns(job):
    """
    Два имени паттерна из PATTERNS. По умолчанию object + scene:
    крупный артефакт и киношный план — не два тумана.
    """
    y = job.get("youtube") or {}
    out = []
    for raw in y.get("cover_patterns") or []:
        name = str(raw).strip().lower()
        if name in PATTERNS and name not in out:
            out.append(name)
    for fallback in ("object", "scene", "tension", "silhouette", "split"):
        if len(out) >= 2:
            break
        if fallback not in out:
            out.append(fallback)
    return out[:2]


def _format_custom(raw, text, scene, pattern=""):
    try:
        return str(raw).format(text=text, scene=scene, pattern=pattern)
    except (KeyError, IndexError, ValueError):
        return str(raw)


def _needs_type_lock(body, text):
    """Свой промпт без жёлтого хука — модель нарисует красивый кадр без CTR."""
    low = body.lower()
    has_color = YELLOW_HEX.lower() in low or "yellow" in low
    has_hook = text.upper() in body.upper()
    return not (has_color and has_hook)


def _needs_plate_lock(body):
    """
    Свой промпт без слов о пустой тёмной полосе сверху.

    В режиме TEXT_BY_MODEL = False буквы кладёт paint_hook, и ему нужна
    зона под них. Промпт сценариста об этом не знает — дописываем.
    """
    low = body.lower()
    return not ("negative space" in low or "empty" in low and "top" in low)


def prompt_for(job, index, text=None, scene=None, pattern=None):
    """
    Полный промпт обложки index (0 или 1).

    Приоритет:
      1. youtube.cover_prompts[index] — полный текст от сценариста
      2. youtube.cover_prompt — общий шаблон на обе (с {text}/{scene}/{pattern})
      3. SCENE_LOCK + типографский блок по паттерну и cover_scenes

    Типографский блок зависит от TEXT_BY_MODEL: буквы рисует модель
    (TYPE_LOCK) или их кладёт paint_hook, а модель готовит под них
    тёмную полосу (PLATE_LOCK).

    Свой промпт без жёлтого хука дополняется TYPE_LOCK: иначе модель
    рисует красивый кадр без бренда канала.
    """
    texts = cover_texts(job) if text is None else None
    scenes = scene_hints(job) if scene is None else None
    patterns = cover_patterns(job) if pattern is None else None
    text = text if text is not None else texts[index]
    scene = scene if scene is not None else scenes[index]
    name = pattern if pattern is not None else patterns[index]
    pattern_text = PATTERNS.get(name, PATTERNS["object"])
    y = job.get("youtube") or {}
    customs = list(y.get("cover_prompts") or [])
    raw = ""
    if index < len(customs) and str(customs[index] or "").strip():
        raw = customs[index]
    elif str(y.get("cover_prompt") or "").strip():
        raw = y["cover_prompt"]
    if raw:
        body = _format_custom(raw, text, scene, pattern_text)
        if TEXT_BY_MODEL:
            if _needs_type_lock(body, text):
                body = body.rstrip() + " " + TYPE_LOCK.format(text=text)
        elif _needs_plate_lock(body):
            body = body.rstrip() + " " + PLATE_LOCK
        return body
    tail = TYPE_LOCK.format(text=text) if TEXT_BY_MODEL else PLATE_LOCK
    return SCENE_LOCK.format(pattern=pattern_text, scene=scene) + " " + tail


# Запрос уходит с самыми полными параметрами, а на отказ ОТКАЗЫВАЕТСЯ ОТ
# НИХ ПО ОДНОМУ, начиная с наименее важного. Порядок здесь — не вкус:
# раньше на любые 400 повтор шёл сразу без aspect_ratio, а умолчание
# эндпойнта — квадрат. То есть «спасательный» заход молча отдавал
# обложку 1:1, которую никто не ресайзил и никто не проверял, — и она
# уезжала на YouTube. Разрешение уступает первым, пропорция последней.
XAI_OPTIONAL = ("resolution", "aspect_ratio")


def xai_cover(prompt: str, dst: Path, model: str, key: str) -> bool:
    """Один запрос к xAI images/generations. True — файл записан."""
    # 2k, а не умолчательный 1k: микрофактура кадра (зерно, халация,
    # края букв) — это ровно то, чему на 1k негде жить, а весит обложка
    # мегабайты против полугигабайта у ролика.
    body = {"model": model, "prompt": prompt, "n": 1,
            "aspect_ratio": "16:9", "resolution": "2k"}
    r = None
    for drop in (None,) + XAI_OPTIONAL:
        if drop is not None:
            if drop not in body:
                continue
            body.pop(drop, None)
            log(f"  обложка: повтор без {drop}")
        r = requests.post(f"{XAI}/images/generations", timeout=180,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json=body)
        if r.status_code == 200:
            break
        if r.status_code != 400 and "aspect" not in r.text.lower() \
                and "resolution" not in r.text.lower():
            break
    if r is None or r.status_code != 200:
        log(f"  ! обложка не вышла: {r.status_code if r else '—'} "
            f"{r.text[:180] if r else ''}")
        return False
    try:
        data = r.json()["data"][0]
        url = data["url"]
    except (KeyError, IndexError, ValueError):
        log("  ! обложка: в ответе нет ссылки")
        return False
    # xAI переписывает промпт ПЕРЕД генерацией. Это единственный способ
    # увидеть, что на самом деле ушло в диффузию: правки шаблона иначе
    # проверяются только глазами по готовой картинке.
    revised = str(data.get("revised_prompt") or "").strip()
    if revised:
        log(f"    промпт после xAI: {revised[:300]}")
    dst.write_bytes(requests.get(url, timeout=120).content)
    if not (dst.exists() and dst.stat().st_size > 1000):
        return False
    # Пропорция проверяется ЗАМЕРОМ, а не доверием к параметру: если
    # aspect_ratio пришлось выбросить, кадр приехал квадратным, и на
    # YouTube он выйдет с полями. Дешевле обрезать здесь.
    return _fix_aspect(dst)


def _fix_aspect(dst: Path) -> bool:
    """Приводит скачанный кадр к 16:9 обрезкой по центру. True — годен."""
    try:
        from PIL import Image
        im = Image.open(dst).convert("RGB")
    except Exception as e:
        log(f"  ! обложка: не открылась ({e})")
        return False
    iw, ih = im.size
    if abs(iw / ih - W / H) < 0.02:
        return True
    log(f"  обложка: пришла {iw}x{ih}, режу под 16:9")
    cw = min(iw, int(ih * W / H))
    ch = int(cw * H / W)
    cx, cy = (iw - cw) // 2, int((ih - ch) * 0.42)
    im.crop((cx, cy, cx + cw, cy + ch)).resize(
        (W, H), Image.LANCZOS).save(dst, quality=92)
    return True


def wrap_hook(text):
    """2 слова — одна строка; 3–5 — две, чтобы кегль остался огромным."""
    words = str(text).split()
    if len(words) <= 2:
        return [" ".join(words)] if words else ["WATCH THIS"]
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def paint_hook(im, text):
    """
    Жёлтый Anton — шрифт со скриншота автора. Запасной путь и смоук:
    без него fallback уходил в DejaVu.
    """
    from PIL import ImageDraw, ImageFont
    if not FONT_FILE.exists():
        raise SystemExit(f"нет шрифта обложки {FONT_FILE}")
    d = ImageDraw.Draw(im)
    lines = wrap_hook(text)
    max_w = int(W * 0.92)
    max_h = int(H * 0.42)
    lo, hi, best = 56, 200, 72
    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(str(FONT_FILE), mid)
        widths = [d.textlength(ln, font=font) for ln in lines]
        gap = int(mid * 0.06)
        height = mid * len(lines) + gap * (len(lines) - 1)
        if max(widths) <= max_w and height <= max_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    font = ImageFont.truetype(str(FONT_FILE), best)
    gap = int(best * 0.06)
    y = int(H * 0.07)
    for ln in lines:
        w = d.textlength(ln, font=font)
        x = (W - w) / 2
        # мягкая тень, не чёрная обводка: на эталонах канала обводки нет
        d.text((x + 2, y + 3), ln, font=font, fill=(0, 0, 0))
        d.text((x, y), ln, font=font, fill=YELLOW)
        y += best + gap
    return im


def pil_fallback(video: Path, at: float, text: str, dst: Path):
    """
    Запасной путь без xAI: кадр из ролика + крупный жёлтый Anton.
    Нужен смоуку и локальной отладке — не замена боевым обложкам.
    """
    from PIL import Image, ImageDraw, ImageFilter
    raw = dst.parent / f"_cover_raw_{dst.stem}.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
         "-frames:v", "1", "-y", str(raw)], check=True)
    im = Image.open(raw).convert("RGB").resize((W, H), Image.LANCZOS)
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for i in range(H // 2):
        sd.line([(0, i), (W, i)], fill=int(200 * (1 - i / (H / 2)) ** 1.2))
    im = Image.composite(Image.new("RGB", im.size, (0, 0, 0)), im,
                         shade.filter(ImageFilter.GaussianBlur(6)))
    paint_hook(im, text)
    im.save(dst, quality=92)
    raw.unlink(missing_ok=True)
    return dst


def stamp_hook(dst: Path, text: str):
    """
    Кладёт жёлтый Anton поверх готового кадра от модели.

    Тот же paint_hook, что и в запасном пути, — то есть буквы на всех
    выпусках канала побайтово одни и те же. Работает только при
    TEXT_BY_MODEL = False; при True кадр приезжает от модели уже с
    текстом, и второй слой поверх был бы кашей.
    """
    from PIL import Image
    im = Image.open(dst).convert("RGB")
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    paint_hook(im, text)
    im.save(dst, quality=92)
    return dst


def build_covers(job, out: Path, video: Path = None):
    """
    Две обложки в out/cover_1.jpg и out/cover_2.jpg.
    Возвращает список путей. Уже лежащие файлы не перерисовываются.
    """
    texts = cover_texts(job)
    scenes = scene_hints(job)
    key = (os.environ.get("XAI_API_KEY") or "").strip()
    model = job.get("image_model", "grok-imagine-image")
    y = job.get("youtube") or {}
    total_hint = 600.0
    if video and video.exists():
        try:
            total_hint = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(video)],
                capture_output=True, text=True).stdout)
        except Exception:
            pass
    ats = [float(y.get("thumbnail_at", total_hint * 0.35)),
           total_hint * 0.62]

    paths = []
    for n, (text, scene) in enumerate(zip(texts, scenes), 1):
        dst = out / f"cover_{n}.jpg"
        paths.append(dst)
        if dst.exists() and dst.stat().st_size > 1000:
            log(f"обложка {n}: уже есть, не трогаю ({text})")
            continue
        prompt = prompt_for(job, n - 1, text=text, scene=scene)
        if key:
            log(f"обложка {n}: рисую через xAI — «{text}»"
                + ("" if TEXT_BY_MODEL else ", буквы кладу сам"))
            if xai_cover(prompt, dst, model, key):
                if not TEXT_BY_MODEL:
                    stamp_hook(dst, text)
                log(f"обложка {n}: {dst.name} ({dst.stat().st_size // 1024} КБ)")
                continue
            log(f"обложка {n}: xAI не отдал — запасной путь с кадра ролика")
        else:
            log(f"обложка {n}: нет XAI_API_KEY — кадр ролика + жёлтый текст")
        if not video or not video.exists():
            raise SystemExit(f"нет ролика для запасной обложки: {video}")
        pil_fallback(video, min(ats[n - 1], max(total_hint - 1, 0)),
                     text, dst)
        log(f"обложка {n}: {dst.name} (fallback, {dst.stat().st_size // 1024} КБ)")
    # thumbnail.jpg = cover_1: привычный путь для выкладки и старых скриптов
    thumb = out / "thumbnail.jpg"
    if paths and paths[0].exists():
        thumb.write_bytes(paths[0].read_bytes())
    return paths


def self_check(job=None):
    """
    Бесплатная проверка шрифта и CTR-каркаса. Смоук зовёт до рендера.
    Со скриншота автора проверяем Anton/#FFD400, не «туманное небо».
    """
    if not FONT_FILE.exists():
        raise SystemExit(f"нет шрифта обложки {FONT_FILE}")
    gold = {
        "id": "cover-self-check",
        "youtube": {
            "cover_texts": ["NEVER FOUND", "PAGE 25 FOUND"],
            "cover_patterns": ["object", "tension"],
            "cover_scenes": [
                "a cracked Egyptian death mask filling the frame, gold rim light, dark navy void",
                "a single redacted line on a stamped 1983 intelligence page, one word still visible",
            ],
        },
    }
    p1 = prompt_for(gold, 0)
    p2 = prompt_for(gold, 1)
    pats = cover_patterns(gold)
    if pats != ["object", "tension"]:
        raise SystemExit(f"обложка: паттерны сбились: {pats}")
    if "close-up" not in p1.lower() and "object" not in p1.lower():
        raise SystemExit("обложка: первый промпт не object-паттерн")
    if "redacted" not in p2.lower() and "tension" not in p2.lower():
        raise SystemExit("обложка: второй промпт не tension-паттерн")
    for p, hook in ((p1, "NEVER FOUND"), (p2, "PAGE 25 FOUND")):
        low = p.lower()
        if "16:9" not in p:
            raise SystemExit("обложка: промпт без 16:9")
        if "120" not in low:
            raise SystemExit("обложка: промпт не требует читаемости с 120 px")
        if "negative" not in low:
            raise SystemExit("обложка: нет пустой зоны под текст")
        if "duration" not in low and "bottom-right" not in low:
            raise SystemExit("обложка: нет поля под бейдж длительности")
        # Кадр идёт от ПАТТЕРНА, а не от одного удачного пейзажа со
        # скриншота: это уже один раз зашивали в дефолтный промпт.
        if "storm sky" in low or "golden-hour glow on the horizon" in low:
            raise SystemExit(
                "обложка: в дефолтный промпт снова зашили пейзаж со "
                "скриншота — кадр должен идти от паттерна, не от тумана")
        # СЛОВА КАЧЕСТВА. Они включают «эстетический режим» модели —
        # глянец, ровный свет, симметрия, — и именно так выглядит AI-slop
        # в ленте. Проверка стоит здесь потому, что дописать
        # «photorealistic 8k» в промпт кажется улучшением.
        for junk in ("8k", "ultra-detailed", "masterpiece", "hyperrealistic",
                     "highly detailed", "photorealistic"):
            if junk in low:
                raise SystemExit(
                    f"обложка: в промпте слово качества {junk!r} — оно даёт "
                    "усреднённую глянцевую картинку. Описывай ЗАХВАТ "
                    "(зерно, халация, промах фокуса), а не качество")
        if TEXT_BY_MODEL:
            if hook not in p:
                raise SystemExit(f"обложка: в промпте нет хука {hook!r}")
            if YELLOW_HEX.lower() not in low:
                raise SystemExit("обложка: промпт без #FFD400")
            if "anton" not in low:
                raise SystemExit("обложка: промпт потерял гарнитуру Anton")
        else:
            # Буквы кладёт paint_hook, и модель обязана отдать кадр БЕЗ
            # текста и с тёмной полосой сверху под него.
            if hook in p:
                raise SystemExit(
                    f"обложка: TEXT_BY_MODEL=False, а хук {hook!r} всё ещё "
                    "в промпте — модель нарисует свои буквы под нашими")
            if "no writing" not in low:
                raise SystemExit("обложка: PLATE_LOCK не запретил надписи")
    if p1 == p2:
        raise SystemExit("обложка: два промпта совпали — нет A/B кадра")

    tagged = {
        "id": "t",
        "youtube": {
            "cover_texts": ["PAGE 25 FOUND", "THE 1983 CIA DOSSIER"],
            "cover_prompts": [
                "dark archive vault, one giant CIA dossier in a cyan laser slit",
                "YouTube thumbnail 16:9 of a stamped 1983 CIA folder, yellow "
                "#FFD400 ALL-CAPS \"THE 1983 CIA DOSSIER\" in empty negative space",
            ],
        },
    }
    a = prompt_for(tagged, 0)
    b = prompt_for(tagged, 1)
    if TEXT_BY_MODEL:
        if "PAGE 25 FOUND" not in a or YELLOW_HEX not in a:
            raise SystemExit("обложка: сцене без хука не дописали TYPE_LOCK")
        if "THE 1983 CIA DOSSIER" not in b or YELLOW_HEX.lower() not in b.lower():
            raise SystemExit("обложка: готовый промпт потерял свой хук")
    else:
        if "no writing" not in a.lower():
            raise SystemExit("обложка: сцене без полосы не дописали PLATE_LOCK")

    if job is not None:
        y = job.get("youtube") or {}
        raw = list(y.get("cover_texts") or [])
        for t in raw[:2]:
            n = len(re.sub(r"[^\w\s'\-]", "", str(t),
                           flags=re.UNICODE).split())
            if n > 5:
                log(f"   ! cover_text длиннее 5 слов, на превью обрежется: {t!r}")
        texts = cover_texts(job)
        for t in texts:
            if t in BANNED_HOOKS:
                raise SystemExit(
                    f"обложка: хук {t!r} — кликбейт без содержания. "
                    "См. ИНСТРУКЦИЯ-ЧАТ.md, раздел про обложки.")
        prompts = [prompt_for(job, i) for i in range(2)]
        if prompts[0] == prompts[1] and texts[0] == texts[1]:
            raise SystemExit(
                "обложка: оба варианта совпали и текстом, и кадром — "
                "нечего сравнивать в Test & Compare")
        for i, (t, p) in enumerate(zip(texts, prompts), 1):
            if TEXT_BY_MODEL:
                if t not in p:
                    raise SystemExit(f"обложка {i}: промпт без хука {t!r}")
                if "yellow" not in p.lower() \
                        and YELLOW_HEX.lower() not in p.lower():
                    raise SystemExit(f"обложка {i}: промпт без жёлтого текста")
            elif t in p:
                # Буквы кладёт PIL, но сценарист вписал хук в свой
                # cover_prompt — модель нарисует свои поверх наших.
                log(f"   ! обложка {i}: TEXT_BY_MODEL=False, а хук {t!r} "
                    f"остался в cover_prompts — убери его из промпта")

    from PIL import Image
    im = Image.new("RGB", (W, H), (18, 18, 22))
    paint_hook(im, "STILL UNSOLVED")

    def has_yellow(box):
        crop = im.crop(box)
        w, h = crop.size
        for x in range(0, w, 8):
            for y in range(0, h, 8):
                r, g, b = crop.getpixel((x, y))[:3]
                if r > 200 and g > 160 and b < 80:
                    return True
        return False

    if not has_yellow((0, 0, W, H // 3)):
        raise SystemExit("обложка: Anton не попал в верхнюю треть кадра")
    if has_yellow((0, 2 * H // 3, W, H)):
        raise SystemExit("обложка: жёлтый текст съехал в нижнюю треть")


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    out = Path("work") / job["id"] / "out"
    video = out / "final.mp4"
    out.mkdir(parents=True, exist_ok=True)
    build_covers(job, out, video if video.exists() else None)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        self_check()
        print("covers self-check ok")
    else:
        main(sys.argv[1])
