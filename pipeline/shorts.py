"""
shorts.py — два вертикальных ролика под Shorts из уже готового видео.

    python pipeline/shorts.py jobs/dead-internet-01.json

Запускается ПОСЛЕ монтажа, рядом с youtube.py, и НИЧЕГО НЕ ЗАКАЗЫВАЕТ
заново: ни озвучки, ни генерации, ни поиска материала. Всё, что нужно
шортсу, уже лежит в готовом ролике — картинка, голос, подложка, цветокор.
Отсюда и цена: два шортса стоят полторы минуты процессорного времени и ноль
центов, поэтому пересобирать их можно сколько угодно.

ЧТО ДЕЛАЕТ ШОРТС ШОРТСОМ. Не обрезка по времени, а три вещи разом:

  1. ВОПРОС НА ЭКРАНЕ с первого кадра и до последнего. Зритель мотает ленту
     и решает за полсекунды; вопрос — единственное, что он успевает
     прочитать. Он же держит его до конца: ответ обещан, и пока он не
     прозвучал, уходить некуда.
  2. КУСОК, В КОТОРОМ ЭТОТ ОТВЕТ ЕСТЬ. Не первая минута ролика и не
     случайная середина: ищется предложение, которое отвечает на вопрос, и
     кусок строится ВОКРУГ него — подводка перед, отбивка после.
  3. СУБТИТРЫ. Шортсы смотрят без звука. Реплика без подписи на экране —
     это молчащий кадр.

Вертикаль режется из центра кадра 16:9. Материал канала — общие планы и
фактуры, у них середина кадра и есть кадр; лица и предметы по краям здесь
не живут, поэтому центральный кроп ничего не отрезает.
"""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

W, H = 1080, 1920            # вертикаль под Shorts
SRC_W, SRC_H = 1920, 1080    # из чего режем, см. render.W/H
CROP_W = 608                 # 1080 * 9/16, округлено до чётного

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Длина. YouTube пускает в Shorts до трёх минут, но досматриваемость живёт
# в первой минуте, и вопрос без ответа дольше сорока секунд не держится.
WANT_SECONDS = 40.0
MAX_SECONDS = 58.0
MIN_SECONDS = 18.0
# Бюджет подводки — сколько секунд звучит ДО ответа, долей от заказанной
# длины. Ответ должен прозвучать ближе к концу, но не в последней секунде:
# после него нужен ещё вдох, иначе шортс обрывается на полуслове ровно так
# же, как обрывался большой ролик.
#
# 0.55 подобрано замером на обоих шортсах этого выпуска: ответ
# договаривается к 79% длины, дальше одно предложение отбивки. Меньше —
# шортс начинается почти с развязки и вопрос сверху не успевает сработать.
LEAD_SHARE = 0.55

Q_SIZE = 62                  # вопрос сверху
Q_TOP = 150
CAP_SIZE = 56                # субтитры
CAP_Y = 0.54                 # доля высоты кадра
SIDE = 46                    # поля слева и справа
CAP_CHARS = 62               # сколько символов в одной подписи

FADE_IN, FADE_OUT = 0.4, 0.6


def log(*a):
    print(*a, flush=True)


def run(cmd):
    subprocess.run(cmd, shell=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ─────────────────────── ВЫБОР КУСКА ───────────────────────

STOP = {"the", "a", "an", "of", "and", "or", "in", "on", "at", "to", "for",
        "with", "from", "is", "are", "was", "were", "that", "this", "it",
        "its", "as", "by", "be", "been", "has", "have", "had", "what",
        "when", "how", "why", "who", "does", "do", "did", "already",
        "most", "your", "you", "we", "they", "not", "but", "if", "then"}


def words(text):
    return [w for w in re.findall(r"[a-zA-Z']+", (text or "").lower())
            if len(w) > 2 and w not in STOP]


def score(sentence, keys):
    """Сколько ключевых слов вопроса встретилось в предложении."""
    have = set(words(sentence))
    return sum(1 for k in keys if k in have)


def find_payoff(marks, question, anchor):
    """
    Номер предложения, которое отвечает на вопрос.

    anchor — точная фраза из сценария, если автор её назвал: это всегда
    точнее любого совпадения по словам, и именно им задаётся, какой из
    десятка похожих абзацев считать ответом.
    """
    if anchor:
        need = " ".join(words(anchor))
        for i, m in enumerate(marks):
            if need and need in " ".join(words(m["text"])):
                return i, f"по якорю «{anchor}»"
        # Не откатываемся на поиск по словам. Якорь — это прямое указание
        # автора, где резать; молчаливый откат дал бы шортс, собранный не
        # про то, и заметить это можно только на просмотре. Опечатка в
        # якоре ловится смоуком бесплатно и до сборки.
        raise SystemExit(
            f"якорь «{anchor}» не найден ни в одном предложении сценария. "
            f"Он ищется по словам без знаков препинания, то есть тире и "
            f"кавычки внутри не мешают, а вот перефразировка — мешает. "
            f"Скопируй кусок фразы прямо из script_blocks.")

    keys = set(words(question))
    best, best_sc = None, 0
    for i, m in enumerate(marks):
        # Пара соседних предложений: ответ редко умещается в одно, а
        # ключевые слова вопроса расходятся по двум.
        sc = score(m["text"], keys)
        if i + 1 < len(marks):
            sc += score(marks[i + 1]["text"], keys) * 0.5
        if sc > best_sc:
            best, best_sc = i, sc
    if best is None:
        raise SystemExit(
            f"не нашёл в сценарии ни одного предложения по вопросу "
            f"«{question}» — задай anchor в спецификации шортса")
    return best, f"по словам вопроса (совпадений {best_sc:.1f})"


def window(marks, payoff, want, hard_max):
    """
    Границы куска по предложениям: подводка, ответ, отбивка.

    Режем ТОЛЬКО по границам предложений. Кусок, начатый с середины фразы,
    звучит как обрывок чужого разговора, и никакой вопрос на экране этого
    не спасает.

    Каждое предложение добавляется, ТОЛЬКО ЕСЛИ ПОСЛЕ НЕГО КУСОК ЕЩЁ
    УКЛАДЫВАЕТСЯ в заказанную длину. Прямолинейное «добавляй, пока
    коротко» кажется тем же самым и им не является: в сценарии попадаются
    предложения по двадцать секунд, и одно такое, добавленное последним,
    растягивало сорокасекундный шортс до пятидесяти пяти. Ответ при этом
    уезжал в первую треть, а дальше полминуты шло послесловие — то есть
    ровно то, из-за чего шортсы не досматривают.
    """
    span = lambda a, b: marks[b]["end"] - marks[a]["start"]
    lead = lambda a: marks[payoff]["start"] - marks[a]["start"]
    lo = hi = payoff

    # Подводка перед ответом — своим бюджетом, иначе длинный ответ съедает
    # его целиком и шортс начинается прямо с развязки.
    while lo > 0 and lead(lo - 1) <= want * LEAD_SHARE:
        lo -= 1
    while hi + 1 < len(marks) and span(lo, hi + 1) <= want:
        hi += 1
    # Совсем короткий кусок добираем до приличного, уже по потолку.
    while (span(lo, hi) < MIN_SECONDS and hi + 1 < len(marks)
           and span(lo, hi + 1) <= hard_max):
        hi += 1
    while (span(lo, hi) < MIN_SECONDS and lo > 0
           and span(lo - 1, hi) <= hard_max):
        lo -= 1
    return lo, hi


# ─────────────────────── ТЕКСТ НА ЭКРАНЕ ───────────────────────

def wrap(text, font, max_w):
    """Разбивка на строки по ШИРИНЕ В ПИКСЕЛЯХ, а не по числу символов."""
    lines, cur = [], ""
    for word in text.split():
        probe = (cur + " " + word).strip()
        if font.getlength(probe) > max_w and cur:
            lines.append(cur)
            cur = word
        else:
            cur = probe
    if cur:
        lines.append(cur)
    return lines


def caption_chunks(marks, lo, hi, t0):
    """
    Подписи с тайм-кодами: [(начало, конец, текст)].

    Длинное предложение делится на куски по словам, а время внутри него
    раздаётся кускам ПО ДЛИНЕ ТЕКСТА. Диктор говорит ровно, поэтому доля
    символов — честная оценка доли времени, и подпись не убегает от
    голоса. Это тот же приём, которым mock.py считает тайм-коды из текста.
    """
    out = []
    for m in marks[lo:hi + 1]:
        text = " ".join(m["text"].split())
        span = max(0.4, m["end"] - m["start"])
        pieces, cur = [], ""
        for word in text.split():
            probe = (cur + " " + word).strip()
            if len(probe) > CAP_CHARS and cur:
                pieces.append(cur)
                cur = word
            else:
                cur = probe
        if cur:
            pieces.append(cur)

        total_chars = sum(len(p) for p in pieces) or 1
        at = m["start"] - t0
        for p in pieces:
            d = span * len(p) / total_chars
            out.append((at, at + d, p))
            at += d
    return out


def draw_lines(lines, font, size, y0, box, tmp: Path, tag, enable=None):
    """
    drawtext на каждую строку отдельно.

    Текст уходит в ФАЙЛ, а не в параметр фильтра: в сценарии есть
    двоеточия, кавычки, запятые и апострофы, и каждый из этих знаков
    внутри filter_complex значит своё. Экранирование здесь ловится не
    глазами, а падением ffmpeg на середине сборки.
    """
    step = int(size * 1.28)
    parts = []
    for i, line in enumerate(lines):
        f = tmp / f"{tag}_{i:02d}.txt"
        f.write_text(line, encoding="utf-8")
        opts = [f"fontfile={FONT}", f"textfile={f}", f"fontsize={size}",
                "fontcolor=white", "x=(w-text_w)/2", f"y={y0 + i * step}"]
        if box:
            # Плашка на КАЖДОЙ строке отдельно, а не на всём блоке: так
            # чёрное идёт по краю текста, а не прямоугольником до полей.
            opts += ["box=1", "boxcolor=black@0.92", "boxborderw=16"]
        else:
            opts += ["borderw=6", "bordercolor=black@0.95"]
        if enable:
            opts.append(f"enable='{enable}'")
        parts.append("drawtext=" + ":".join(opts))
    return parts


# ─────────────────────── СБОРКА ───────────────────────

def build_one(video: Path, marks, spec, out: Path, tmp: Path, n: int):
    from PIL import ImageFont

    question = spec.get("question") or spec.get("title") or ""
    want = float(spec.get("seconds", WANT_SECONDS))
    payoff, how = find_payoff(marks, question, spec.get("anchor"))
    lo, hi = window(marks, payoff, want, MAX_SECONDS)
    t0 = marks[lo]["start"]
    dur = marks[hi]["end"] - t0
    if dur < MIN_SECONDS:
        raise SystemExit(f"шортс {n}: вышло {dur:.1f} с — это не шортс. "
                         f"Проверь anchor и seconds в спецификации")

    log(f"── шортс {n}: «{question}»")
    log(f"   ответ — предложение {payoff + 1} {how}")
    log(f"   кусок {t0/60:.1f}-{(t0+dur)/60:.1f} мин ролика, {dur:.1f} с, "
        f"{hi - lo + 1} предложений")
    log(f"   ответ договорён к {(marks[payoff]['end']-t0)/dur*100:.0f}% "
        f"длины шортса")

    tmp.mkdir(parents=True, exist_ok=True)
    qfont = ImageFont.truetype(FONT, Q_SIZE)
    cfont = ImageFont.truetype(FONT, CAP_SIZE)
    max_w = W - SIDE * 2

    # Вертикаль из центра кадра. lanczos, потому что кроп 608 пикселей
    # растягивается до 1080 — на билинейном это видно как мыло.
    chain = [f"crop={CROP_W}:{SRC_H}:(iw-{CROP_W})/2:0",
             f"scale={W}:{H}:flags=lanczos", "setsar=1"]

    # Вопрос — на весь шортс, без enable.
    chain += draw_lines(wrap(question, qfont, max_w), qfont, Q_SIZE,
                        Q_TOP, True, tmp, f"q{n}")

    # Субтитры: по одному drawtext на подпись, каждый в своё время.
    caps = caption_chunks(marks, lo, hi, t0)
    for i, (a, b, text) in enumerate(caps):
        lines = wrap(text, cfont, max_w)
        y = int(H * CAP_Y) - (len(lines) - 1) * int(CAP_SIZE * 1.28) // 2
        chain += draw_lines(lines, cfont, CAP_SIZE, y, False, tmp,
                            f"c{n}_{i:03d}",
                            enable=f"between(t,{a:.2f},{b:.2f})")
    log(f"   субтитров {len(caps)}")

    chain.append(f"fade=t=in:st=0:d={FADE_IN}")
    chain.append(f"fade=t=out:st={dur - FADE_OUT:.2f}:d={FADE_OUT}")

    afilt = (f"afade=t=in:d={FADE_IN},"
             f"afade=t=out:st={dur - FADE_OUT:.2f}:d={FADE_OUT}")

    # Цепочка длинная (на сорока секундах это под сотню drawtext), и в
    # командную строку она не влезает — ffmpeg читает её из файла.
    script = tmp / f"filter_{n}.txt"
    script.write_text(",".join(chain), encoding="utf-8")

    run(f"ffmpeg -y -ss {t0:.3f} -t {dur:.3f} -i {shlex.quote(str(video))} "
        f"-filter_script:v {shlex.quote(str(script))} "
        f"-af {shlex.quote(afilt)} "
        f"-c:v libx264 -crf 20 -preset medium -pix_fmt yuv420p "
        f"-c:a aac -b:a 192k -movflags +faststart {shlex.quote(str(out))}")
    return {"question": question, "title": spec.get("title") or question,
            "start": t0, "duration": dur, "file": out,
            "text": " ".join(m["text"] for m in marks[lo:hi + 1])}


def specs_from(job):
    """
    Шортсы из спецификации. Полная форма — список объектов:

        "shorts": [{"title": ..., "question": ..., "anchor": ...,
                    "seconds": 40}]

    Короткая, которая уже есть у выпусков, — список вопросов строками:

        "shorts_questions": ["Is most of the internet already fake?", ...]

    Якоря в короткой форме нет, и кусок ищется по словам вопроса. Это
    работает, но угадывает: если шортс уехал не туда, дело почти всегда в
    этом, и лечится дописыванием anchor.
    """
    y = job.get("youtube") or {}
    full = y.get("shorts") or job.get("shorts")
    if full:
        return [s if isinstance(s, dict) else {"question": str(s)}
                for s in full]
    return [{"question": q} for q in (y.get("shorts_questions") or [])]


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    base = Path("work") / job["id"]
    video = base / "out" / "final.mp4"
    marks_file = base / "assets" / "marks.json"
    if not video.exists():
        raise SystemExit(f"нет {video} — сначала собери ролик")
    if not marks_file.exists():
        raise SystemExit(f"нет {marks_file} — без тайм-кодов не режется")

    specs = specs_from(job)
    if not specs:
        log("в спецификации нет ни shorts, ни youtube.shorts_questions — "
            "шортсы не заказаны")
        return

    marks = json.loads(marks_file.read_text(encoding="utf-8"))
    out_dir, tmp = base / "out", base / "tmp" / "shorts"
    made = []
    for n, spec in enumerate(specs, 1):
        made.append(build_one(video, marks, spec, out_dir / f"short_{n}.mp4",
                              tmp, n))

    y = job.get("youtube") or {}
    tags = " ".join(y.get("hashtags") or [])
    card = ["ШОРТСЫ", ""]
    for n, m in enumerate(made, 1):
        size = m["file"].stat().st_size / 2**20
        card += [f"── short_{n}.mp4  ({m['duration']:.0f} с, {size:.0f} МБ, "
                 f"с {int(m['start'])//60:02d}:{int(m['start'])%60:02d} ролика)",
                 f"ЗАГОЛОВОК: {m['title']}",
                 f"ОПИСАНИЕ: {m['question']} Full video on the channel. {tags}",
                 ""]
        log(f"   готов {m['file']} ({m['duration']:.0f} с, {size:.0f} МБ)")
    (out_dir / "shorts.txt").write_text("\n".join(card), encoding="utf-8")
    log(f"── подписи к шортсам: {out_dir / 'shorts.txt'}")


if __name__ == "__main__":
    main(sys.argv[1])
