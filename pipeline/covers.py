"""
covers.py — две обложки ролика через xAI, с жёлтым текстом на картинке.

    Вызывается из youtube.py после сборки ролика.
    Можно отдельно:  python -c "from covers import main; main('jobs/…')"

Стиль канала (см. скриншот в задании): тёмный кинематографичный фон +
ОЧЕНЬ КРУПНЫЙ жёлтый/золотой текст ЗАГЛАВНЫМИ, 2–5 слов — хук, а не
полный заголовок. Текст рисует модель прямо на изображении
(grok-imagine-image это умеет); PIL-наложение — только запасной путь,
когда ключа нет (смоук, локальная отладка).

Кэш: если cover_1.jpg / cover_2.jpg уже лежат в out/, повторно не
рисуем. Перерисовать — удалить файлы и прогнать youtube.py снова.
Пересборка монтажа обложки не трогает и денег не жжёт.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import requests

XAI = "https://api.x.ai/v1"
W, H = 1280, 720

# Промпт собран под тот вид, что на канале: тёмный кадр, один крупный
# жёлтый хук, без мелкого текста, без людей лицом в камеру (правило
# канала), без логотипов и водяных знаков.
PROMPT = (
    "YouTube thumbnail, 16:9, cinematic dark atmospheric photograph of "
    "{scene}, high contrast, dramatic lighting, deep shadows, mysterious "
    "mood, photoreal. Huge bold bright yellow gold all-caps text "
    "\"{text}\" centered in the upper half of the frame, thick letters, "
    "highly readable on a phone screen, text is part of the image. "
    "No people facing camera, no logos, no watermarks, no small subtitle "
    "lines, no channel name, no extra text besides the yellow hook."
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
    """Короткие описания сцены из первых image_prompts / archive_queries."""
    hints = []
    for p in (job.get("image_prompts") or [])[:4]:
        hints.append(re.split(r",", p)[0].strip())
    for q in (job.get("archive_queries") or [])[:2]:
        hints.append(q)
    while len(hints) < 2:
        hints.append("ancient ruins at night under moonlight")
    return hints[:2]


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


def pil_fallback(video: Path, at: float, text: str, dst: Path):
    """
    Запасной путь без xAI: кадр из ролика + крупный жёлтый текст.
    Нужен смоуку и локальной отладке — не замена боевым обложкам.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
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
    d = ImageDraw.Draw(im)
    size = 78
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    words, lines, cur = text.split(), [], ""
    for w in words:
        probe = (cur + " " + w).strip()
        if d.textlength(probe, font=font) > W - 80 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = probe
    if cur:
        lines.append(cur)
    y = 80
    yellow = (255, 214, 32)
    for ln in lines[:3]:
        # обводка — читаемость на любом кадре
        for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3),
                       (-2, -2), (2, 2), (-2, 2), (2, -2)):
            d.text((40 + dx, y + dy), ln, font=font, fill=(0, 0, 0))
        d.text((40, y), ln, font=font, fill=yellow)
        y += size + 10
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
        prompt = PROMPT.format(scene=scene, text=text)
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


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    out = Path("work") / job["id"] / "out"
    video = out / "final.mp4"
    out.mkdir(parents=True, exist_ok=True)
    build_covers(job, out, video if video.exists() else None)


if __name__ == "__main__":
    import sys
    main(sys.argv[1])
