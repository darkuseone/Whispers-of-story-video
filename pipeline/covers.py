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

Текст рисует модель (grok-imagine-image); PIL + Anton — запасной путь
без ключа. Кэш: лежащие cover_1.jpg / cover_2.jpg не перерисовываются.
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
# в docs/протокол-обложки.md. Имена совпадают с youtube.cover_patterns.
PATTERNS = {
    "object": (
        "PATTERN object+overlay: extreme close-up of ONE artifact filling "
        "most of the frame, shallow depth of field, dramatic rim light, "
        "dark simple background. The object must be recognizable when the "
        "whole image is only 120 pixels wide."
    ),
    "scene": (
        "PATTERN cinematic scene: movie-poster establishing shot of ONE "
        "place or moment, not a collage. Scale and atmosphere. Leave a "
        "large empty region of the frame for type."
    ),
    "silhouette": (
        "PATTERN mystery silhouette: a back-facing or fully shadowed human "
        "figure with no visible face, plus ONE small piece of evidence in "
        "the light. Identity stays hidden."
    ),
    "split": (
        "PATTERN before/after: a clean 50/50 split of the SAME subject "
        "then versus now. No arrows, no circles. The contrast is the hook."
    ),
    "tension": (
        "PATTERN tension object: one wrong detail lit in darkness — a "
        "broken seal, a redacted line, an empty plinth, a door that should "
        "be open, a missing inscription. Uncanny, not gory, not shock-bait."
    ),
}

# Контраст, поля, бейдж длительности, один герой. Палитра history/mystery:
# тёмный navy/charcoal + один светлый предмет; жёлтый текст — pop-цвет.
SCENE_LOCK = (
    "YouTube thumbnail, 16:9 photoreal cinematic still for a mystery-history "
    "documentary. {pattern} "
    "HERO (the only subject, instantly readable at postage-stamp size): "
    "{scene}. "
    "High contrast two-color mass: dark navy or charcoal base, one lit "
    "subject. Avoid beige/teal AI-default grading. "
    "Leave the largest empty region of the frame (top OR side) as negative "
    "space for the title — do not put the hero under the letters. "
    "Keep the bottom-right 180x72 pixels empty for the YouTube duration "
    "badge. Keep ~8 percent margins; no critical detail on the far edges "
    "(mobile crop). Maximum four visual elements. "
    "No people facing camera. No extra text besides the yellow hook. "
    "No logos, arrows, circles, starbursts, watermarks, YouTube UI."
)

# Типографика — единственное, что копируется со скриншота автора.
TYPE_LOCK = (
    "Typography lock (do not ignore): the ONLY text in the image is "
    "\"{text}\" in extra-condensed ultra-bold ALL-CAPS sans-serif "
    "(Anton / Impact condensed), bright saturated yellow-gold "
    + YELLOW_HEX +
    ", letter height about 20 to 30 percent of the frame so it stays "
    "legible at 120 pixels wide, placed in the empty negative-space "
    "region, one line if two words else two centered lines. Tight "
    "tracking, flat letters, no 3D bevel, no second subtitle, no "
    "channel name."
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


def scene_hints(job):
    """
    Две разных сцены под две обложки.

    Сначала youtube.cover_scenes — это то, что сценарист написал именно
    под превью. Иначе первый кусок image_prompts / archive_queries, как
    раньше: лучше слабая сцена, чем пустой промпт.
    """
    y = job.get("youtube") or {}
    hints = []
    for s in y.get("cover_scenes") or []:
        bit = str(s).strip()
        if bit and bit not in hints:
            hints.append(bit)
    for p in (job.get("image_prompts") or [])[:8]:
        bit = re.split(r",", str(p))[0].strip()
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


def prompt_for(job, index, text=None, scene=None, pattern=None):
    """
    Полный промпт обложки index (0 или 1).

    Приоритет:
      1. youtube.cover_prompts[index] — полный текст от сценариста
      2. youtube.cover_prompt — общий шаблон на обе (с {text}/{scene}/{pattern})
      3. SCENE_LOCK + TYPE_LOCK по паттерну и cover_scenes

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
        if _needs_type_lock(body, text):
            body = body.rstrip() + " " + TYPE_LOCK.format(text=text)
        return body
    return (SCENE_LOCK.format(pattern=pattern_text, scene=scene)
            + " " + TYPE_LOCK.format(text=text))


def xai_cover(prompt: str, dst: Path, model: str, key: str) -> bool:
    """Один запрос к xAI images/generations. True — файл записан."""
    body = {"model": model, "prompt": prompt, "n": 1,
            "aspect_ratio": "16:9"}
    r = requests.post(f"{XAI}/images/generations", timeout=180,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      json=body)
    if r.status_code != 200:
        # часть моделей не принимает aspect_ratio — повторяем без него
        if "aspect" in r.text.lower() or r.status_code == 400:
            body.pop("aspect_ratio", None)
            r = requests.post(f"{XAI}/images/generations", timeout=180,
                              headers={"Authorization": f"Bearer {key}",
                                       "Content-Type": "application/json"},
                              json=body)
    if r.status_code != 200:
        log(f"  ! обложка не вышла: {r.status_code} {r.text[:180]}")
        return False
    try:
        url = r.json()["data"][0]["url"]
    except (KeyError, IndexError, ValueError):
        log("  ! обложка: в ответе нет ссылки")
        return False
    dst.write_bytes(requests.get(url, timeout=120).content)
    return dst.exists() and dst.stat().st_size > 1000


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
            log(f"обложка {n}: рисую через xAI — «{text}»")
            if xai_cover(prompt, dst, model, key):
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
        if hook not in p:
            raise SystemExit(f"обложка: в промпте нет хука {hook!r}")
        if "16:9" not in p:
            raise SystemExit("обложка: промпт без 16:9")
        if YELLOW_HEX.lower() not in low:
            raise SystemExit("обложка: промпт без #FFD400")
        if "anton" not in low:
            raise SystemExit("обложка: промпт потерял гарнитуру Anton")
        if "120" not in low:
            raise SystemExit("обложка: промпт не требует читаемости с 120 px")
        if "negative" not in low:
            raise SystemExit("обложка: нет пустой зоны под текст")
        if "duration" not in low and "bottom-right" not in low:
            raise SystemExit("обложка: нет поля под бейдж длительности")
        if "storm sky" in low or "golden-hour glow on the horizon" in low:
            raise SystemExit(
                "обложка: в дефолтный промпт снова зашили пейзаж со "
                "скриншота — кадр должен идти от паттерна, не от тумана")
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
    if "PAGE 25 FOUND" not in a or YELLOW_HEX not in a:
        raise SystemExit("обложка: сцене без хука не дописали TYPE_LOCK")
    if "THE 1983 CIA DOSSIER" not in b or YELLOW_HEX.lower() not in b.lower():
        raise SystemExit("обложка: готовый промпт потерял свой хук")

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
            if t not in p:
                raise SystemExit(f"обложка {i}: промпт без хука {t!r}")
            if "yellow" not in p.lower() and YELLOW_HEX.lower() not in p.lower():
                raise SystemExit(f"обложка {i}: промпт без жёлтого текста")

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
