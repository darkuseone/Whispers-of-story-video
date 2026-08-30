"""
youtube.py — готовит всё, что нужно для загрузки готового ролика.

Запускается ПОСЛЕ монтажа:  python pipeline/youtube.py jobs/lhc-01.json

Что делает:
  1. Считает тайм-коды глав. Не выдумывает их: находит в субтитрах первое
     предложение каждого блока сценария и берёт его время. Значит главы
     всегда совпадают с тем, что реально звучит в ролике.
  2. Собирает описание — вступление, главы, примечание, теги.
  3. Две обложки через xAI с крупным жёлтым текстом на картинке
     (pipeline/covers.py). PIL-кадр из ролика — только запасной путь.

Редакторская часть (заголовок, названия глав, теги, cover_texts,
shorts_questions) живёт в блоке youtube внутри спецификации ролика.
Здесь только механика.

Главы по правилам YouTube: первая обязательно с 00:00, минимум три штуки,
каждая не короче десяти секунд. Всё это проверяется, а не подразумевается.
"""

import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

W_THUMB, H_THUMB = 1280, 720
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_PLAIN = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
TAGS_LIMIT = 500          # столько символов YouTube пускает в поле тегов


def log(*a):
    print(*a, flush=True)


def norm(s: str) -> str:
    """
    Схлопывает текст до букв и цифр — для сравнения субтитра со сценарием.

    Раньше здесь стояло re.sub(r"[^a-z0-9 ]", "", s), то есть выбрасывалось
    ВСЁ, кроме латиницы. На русском сценарии от блока не оставалось ничего,
    кроме пробелов, начало главы не находилось никогда, и youtube.py падал с
    «не нашёл её начало в субтитрах» на любом ролике. Ловилось только на
    последнем шаге, после всего рендера.

    Теперь выбрасывается пунктуация, а буквы любого алфавита остаются.
    Заодно схлопываются пробелы: в сценарии между предложениями бывает два
    пробела или перенос строки, а в субтитрах — один, и подстрока не
    находилась из-за этого тоже.
    """
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^\w\s]", "", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def read_srt(path: Path):
    """[(секунда, нормализованный текст), ...] по порядку."""
    cues = []
    for chunk in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = chunk.strip().split("\n")
        if len(lines) < 3:
            continue
        m = re.match(r"(\d\d):(\d\d):(\d\d),(\d\d\d) -->", lines[1])
        if not m:
            continue
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
        cues.append((t, norm(" ".join(lines[2:]))))
    return cues


def as_list(value, field="tags", sep=","):
    """
    Приводит поле спецификации к списку. Строку разбирает по разделителю.

    Спецификации роликов пишутся в чате и приезжают JSON-ом, и строка
    вместо списка — опечатка, которую глазами не видно: в файле лежит
    "тег один, тег два", выглядит совершенно нормально.

    А дальше `", ".join(строка)` перебирает её ПОСИМВОЛЬНО и склеивает
    буквы через запятую. На ff-ep05 это дало 341 «тег» по одной букве и
    строку в 1021 символ при лимите в 500 — то есть падение на самом
    последнем шаге, уже ПОСЛЕ полного монтажа. Дороже места для отказа в
    этом конвейере нет.

    Поэтому не роняем, а чиним и предупреждаем: намерение автора здесь
    однозначно, разделитель тот же самый, и терять из-за него сорок минут
    рендера незачем.
    """
    if not value:
        return []
    if isinstance(value, str):
        out = [t.strip() for t in value.split(sep) if t.strip()]
        log(f"  ! {field} записаны строкой, а не списком — разобрал на "
            f"{len(out)} шт. Поправь в спецификации: \"{field}\": [...]")
        return out
    return [str(t).strip() for t in value if str(t).strip()]


def stamp(sec: float) -> str:
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def first_sentence(text: str) -> str:
    """
    Первое предложение блока — ТА ЖЕ граница, что в assets.sentence_marks.

    Раньше брали split('.')[0]: на «U.S. Air Force» ключ обрезался до «the U»,
    а на «Chariots of the Gods? was published…» ключ включал «was…», хотя
    субтитры режутся по «? » и реплика заканчивается на Gods?. Отсюда
    «не нашёл её начало в субтитрах» уже ПОСЛЕ двух часов монтажа.

    Проверка «точка перед пробелом» одна такую границу не держит: у
    инициального сокращения из БУКВЫ-ТОЧКИ-БУКВЫ-ТОЧКИ (R.C., U.S.) первая
    точка не перед пробелом и не ловится, а ПОСЛЕДНЯЯ — перед пробелом, и
    выглядит точь-в-точь как конец предложения. На georgia-guidestones-01
    `For thirty-five years, "R.C. Christian" was a locked box…` обрывалось
    на `R.C.`, а не на настоящем конце фразы: короткое «for thirtyfive
    years rc» не находилось ни в одной реплике, и вся глава 9 срывала
    сборку youtube.py на последнем шаге. Точки внутри такого сокращения —
    ВСЕ, включая последнюю, — из кандидатов на границу исключаются.
    """
    text = text.strip()
    abbrev_end = {m.end() - 1
                  for m in re.finditer(r"\b(?:[A-Z]\.){2,}", text)}
    for i, ch in enumerate(text):
        if ch in ".!?" and (i + 1 >= len(text) or text[i + 1] in " \n") \
                and i not in abbrev_end:
            return text[: i + 1].strip()
    return text


def _chapter_keys(block: str) -> list[str]:
    """Ключи поиска от короткого к длинному — короткий влезает в одну реплику."""
    words = norm(first_sentence(block)).split()
    keys = []
    for n in (5, 6, 8, 10, 12):
        if len(words) >= n:
            keys.append(" ".join(words[:n]))
    full = norm(first_sentence(block))[:60]
    if full and full not in keys:
        keys.append(full)
    # слишком короткие дают ложные попадания в середину ролика
    return [k for k in keys if len(k) >= 12]


def _cue_windows(cues, span=3):
    """(t, текст) с склейкой соседних реплик — ключ может пересечь границу."""
    out = []
    for i, (t, txt) in enumerate(cues):
        blob = txt
        for j in range(i + 1, min(i + span, len(cues))):
            blob = f"{blob} {cues[j][1]}"
        out.append((t, blob))
    return out


def chapters(job, cues):
    """
    Сопоставляет блоки сценария с субтитрами и возвращает [(секунда, имя)].

    Ищем по первому предложению блока (граница как у sentence_marks). Главы
    идут строго вперёд по времени — иначе короткий общий зачин мог бы
    поймать более раннюю реплику.
    """
    names = job["youtube"]["chapters"]
    blocks = job["script_blocks"]
    if len(names) != len(blocks):
        raise SystemExit(f"глав {len(names)}, а блоков сценария {len(blocks)} — "
                         "их должно быть поровну")

    windows = _cue_windows(cues)
    out = []
    min_t = -1.0
    for i, (block, name) in enumerate(zip(blocks, names)):
        keys = _chapter_keys(block)
        hit = None
        for key in keys:
            hit = next((t for t, txt in windows
                        if t + 0.05 >= min_t and key in txt), None)
            if hit is not None:
                break
        if hit is None:
            preview = " ".join(norm(first_sentence(block)).split()[:10])
            raise SystemExit(
                f"глава {i+1} «{name}»: не нашёл её начало в субтитрах "
                f"(искал: {preview!r})")
        out.append((hit, name))
        min_t = hit

    out[0] = (0.0, out[0][1])          # YouTube требует, чтобы первая шла с нуля
    for (a, _), (b, nm) in zip(out, out[1:]):
        if b - a < 10:
            raise SystemExit(f"глава «{nm}» короче десяти секунд — YouTube их не покажет")
    if len(out) < 3:
        raise SystemExit("глав меньше трёх — YouTube их не покажет")
    return out


# Подписи в описании. Канал англоязычный, поэтому и умолчания английские;
# ролик на другом языке переопределяет их полем description_labels в
# спецификации, не трогая код.
LABELS = {"chapters": "Chapters", "runtime": "Runtime"}


def description(job, chaps, total):
    y = job["youtube"]
    lab = {**LABELS, **(y.get("description_labels") or {})}
    parts = [y["description_intro"].strip(), "", lab["chapters"], ""]
    parts += [f"{stamp(t)}  {name}" for t, name in chaps]
    if y.get("description_notes"):
        parts += ["", y["description_notes"].strip()]
    parts += ["", f"{lab['runtime']}: {stamp(total)}"]
    hashtags = as_list(y.get("hashtags"), "hashtags")
    if hashtags:
        parts += ["", " ".join(hashtags)]
    return "\n".join(parts)


def thumbnail(video: Path, out: Path, at: float, title: str, style="lower_left"):
    """
    Кадр из самого ролика плюс заголовок. Ничего дорисованного.

    РАСКЛАДКА МЕНЯЕТСЯ ОТ РОЛИКА К РОЛИКУ. YouTube показывает превью соседних
    загрузок канала в одном ряду, и одинаковая вёрстка подписи опознаётся как
    поточная серия быстрее, чем любой признак внутри самого ролика. Вариант
    выбирает движок стиля по seed, то есть он свой у каждого ролика и при
    этом воспроизводимый.

      lower_left   подпись внизу слева, затемнение снизу  — крупно, спокойно
      lower_band   подпись в плашке во всю ширину внизу   — плотно, «газета»
      upper_left   подпись сверху слева, затемнение сверху — под кадры,
                   где главное в нижней половине
      centre_band  подпись в плашке по центру кадра — под ночные кадры,
                   где и верх, и низ тёмные и градиент по краю не читается
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    raw = out.parent / "_thumb_raw.png"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
                    "-frames:v", "1", "-y", str(raw)], check=True)

    im = Image.open(raw).convert("RGB").resize((W_THUMB, H_THUMB), Image.LANCZOS)
    top = style == "upper_left"

    if style in ("lower_band", "centre_band"):
        # плашка: сплошная полоса, текст на ней всегда читается независимо
        # от того, что попало в кадр. centre_band нужен именно этому
        # каналу: кадры ночные, у них и верх, и низ тёмные, и градиент от
        # края на них не виден вовсе — подпись висит в пустоте.
        band = Image.new("RGB", im.size, (0, 0, 0))
        mask = Image.new("L", im.size, 0)
        box = ([0, H_THUMB - 210, W_THUMB, H_THUMB] if style == "lower_band"
               else [0, H_THUMB // 2 - 105, W_THUMB, H_THUMB // 2 + 105])
        ImageDraw.Draw(mask).rectangle(box, fill=205)
        im = Image.composite(band, im, mask.filter(ImageFilter.GaussianBlur(2)))
    else:
        # градиент от края: мягче, но зависит от содержимого кадра
        shade = Image.new("L", (W_THUMB, H_THUMB), 0)
        sd = ImageDraw.Draw(shade)
        half = H_THUMB // 2
        for i in range(half):
            k = i / half
            y = half - 1 - i if top else half + i
            sd.line([(0, y), (W_THUMB, y)], fill=int(215 * k ** 1.4))
        im = Image.composite(Image.new("RGB", im.size, (0, 0, 0)), im,
                             shade.filter(ImageFilter.GaussianBlur(8)))

    d = ImageDraw.Draw(im)
    size = 54 if style in ("lower_band", "centre_band") else 62
    font = ImageFont.truetype(FONT_BOLD, size)
    step = size + 12
    margin = 46 if style in ("lower_band", "centre_band") else 60

    lines, cur = [], ""
    for w in title.split():
        probe = (cur + " " + w).strip()
        if d.textlength(probe, font=font) > W_THUMB - margin * 2 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = probe
    lines.append(cur)

    if top:
        y = 52
    elif style == "centre_band":
        y = H_THUMB // 2 - len(lines) * step // 2
    elif style == "lower_band":
        y = H_THUMB - 40 - len(lines) * step
    else:
        y = H_THUMB - 56 - len(lines) * step
    for ln in lines:
        d.text((margin, y), ln, font=font, fill=(240, 238, 232))
        y += step

    im.save(out, quality=92)
    raw.unlink(missing_ok=True)
    return out


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    if "youtube" not in job:
        raise SystemExit("в спецификации нет блока youtube — заполнять нечего")

    out = Path("work") / job["id"] / "out"
    srt, video = out / "subs.srt", out / "final.mp4"
    for f in (srt, video):
        if not f.exists():
            raise SystemExit(f"нет {f} — сначала собери ролик")

    total = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout)

    chaps = chapters(job, read_srt(srt))
    y = job["youtube"]

    tags = ", ".join(as_list(y.get("tags"), "tags"))
    if len(tags) > TAGS_LIMIT:
        raise SystemExit(f"теги занимают {len(tags)} символов при лимите {TAGS_LIMIT}")

    card = out / "youtube.txt"
    card.write_text(
        "ЗАГОЛОВОК\n" + y["title"] +
        "\n\nЗАПАСНЫЕ ЗАГОЛОВКИ\n" +
        "\n".join(f"- {t}" for t in y.get("title_alternatives", [])) +
        "\n\nОПИСАНИЕ\n" + description(job, chaps, total) +
        f"\n\nТЕГИ ({len(tags)} из {TAGS_LIMIT} символов)\n" + tags + "\n",
        encoding="utf-8")

    # ДВЕ ОБЛОЖКИ ЧЕРЕЗ xAI. Крупный жёлтый текст рисует модель прямо на
    # картинке — как на канале. PIL-кадр из ролика остался внутри
    # covers.pil_fallback на случай отсутствия ключа. Уже лежащие
    # cover_*.jpg не перерисовываются: пересборка монтажа обложки не жжёт.
    import covers
    cover_paths = covers.build_covers(job, out, video)
    thumb = out / "thumbnail.jpg"

    log(f"главы  : {len(chaps)}, первая с 00:00, последняя с {stamp(chaps[-1][0])}")
    log(f"теги   : {len(tags)} символов из {TAGS_LIMIT}")
    log(f"описание и заголовок: {card}")
    log(f"обложки: " + ", ".join(p.name for p in cover_paths))
    if thumb.exists():
        log(f"превью : {thumb.name} (= cover_1, {thumb.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main(sys.argv[1])
