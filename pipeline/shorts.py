"""
shorts.py — два вертикальных шортса из готового ролика.

    python pipeline/shorts.py jobs/ancient-01.json

Запускается ПОСЛЕ монтажа: нужны final.mp4 (звук со сведением),
shots.json (пути ИСХОДНИКОВ) и marks.json. Ключей не тратит.

ЧТО ЭТО. Не случайная вырезка и не «сплюснутый» 16:9, а два разных
захода на историю под воронку Shorts:

  1 hook        первые фразы ролика — готовый крючок сценария
  2 revelation  развязка / поворот с ответом на вопрос

ОТКУДА УДЕРЖАНИЕ (по практике Shorts 2025–26):
  - вопрос СВЕРХУ на всю длину: VFX-появление, потом висит
  - дальше начитка ОТВЕЧАЕТ на этот вопрос
  - обычные субтитры ЦЕЛЫМИ предложениями ВНИЗУ (не
    пословно / не караоке по 1–3 слова — они плывут относительно
    звука, если тайм-коды слов без пауз между главами)
  - вопрос сверху, перенос строк, поля — не вылезает за край
  - БЕЗ Ken Burns / дрейфа поверх: только нарезка исходников.
    Движение в клипе — то, что уже есть в футаже (zoom/parallax
    длинного ролика); фото — статичный кадр. Лишний zoompan
    на телефоне читается как тряска.
  - клип ВПИСЫВАЕТСЯ, а не обрезается в 9:16: жёсткий кроп горизонтального
    стока под вертикаль оставляет ~32% ширины кадра и втрое усиливает
    любое дрожание камеры в исходнике — это тоже тряска, просто не
    добавленная, а получившаяся из самого кропа. Поля заполняет размытая
    подложка того же кадра, см. render_cut
  - длина 30–90 с, цель ~55–70 с
  - в конце стрелка на полный ролик

Всё детерминировано от id ролика. Правила — docs/протокол-монтажа.md.
"""

import json
import random
import re
import shlex
import shutil
import subprocess
import sys
from bisect import bisect_left
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import render
from editorial import beats as beats_mod

SW, SH, SFPS = 1080, 1920, 30

N_SHORTS = 2

# Длина: 30 с … 1.5 мин. Цель — успеть задать вопрос и дать ответ.
TARGET_LEN = 62.0
HARD_MAX = 90.0
MIN_LEN = 28.0
MIN_LEN_SOFT = 12.0       # тестовые короткие ролики (mock)

# Кадр шортса. Без дополнительного движения поверх — только нарезка.
CUT_RANGE = (2.8, 4.8)
CUT_MERGED_MAX = 7.0

# ─────────────── ФИРМЕННАЯ ТИПОГРАФИКА КАНАЛА ───────────────
#
# ШРИФТЫ ЛЕЖАТ В РЕПОЗИТОРИИ, А НЕ БЕРУТСЯ ИЗ СИСТЕМЫ. Узнаваемость —
# это когда все шортсы канала выглядят ОДИНАКОВО, а системный набор
# шрифтов на раннере не гарантирован ничем: libass молча подставит
# что найдёт, и один выпуск выйдет не похожим на остальные. Раньше
# здесь стоял DejaVu Sans — шрифт, который есть везде и не значит
# ничего.
#
#   Montserrat SemiBold — вопрос наверху. Тот же геометрический гротеск,
#                что на заставке длинного ролика (Regular + разрядка),
#                на шаг жирнее, чтобы жёлтый читался на телефоне.
#   Montserrat ExtraBold — субтитры и призыв. Не спорит с вопросом:
#                вопрос тонкий с трекингом, субтитр плотный без него.
#
# Менять эту пару — значит менять лицо канала. Если когда-нибудь
# понадобится, менять надо СРАЗУ ВЕЗДЕ и осознанно, а не подставлять
# другой шрифт одному выпуску.
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_QUESTION_FILE = FONT_DIR / "Montserrat-SemiBold.ttf"
# Имя ИМЕННО с начертанием. У файла два имени семейства: «Montserrat»
# со стилем SemiBold и «Montserrat SemiBold» со стилем Regular.
# По короткому libass ищет обычный вес, не находит его в папке (там
# Regular — отдельный файл) и молча подставляет системный шрифт.
FONT_QUESTION = "Montserrat SemiBold"
# То же правило, что у вопроса: полное имя семейства, не «Montserrat».
FONT_CAPTION = "Montserrat ExtraBold"

# Размеры (pt в координатах PlayRes 1080x1920).
# КРУПНО — ЭТО ТРЕБОВАНИЕ, А НЕ ВКУС. Шортс смотрят с телефона в руке,
# и кегль, приличный на мониторе, читается там как мелкий шрифт в
# договоре. У вопроса кегль ниже прежних 56: широкая разрядка сама
# занимает ширину, и 56pt с трекингом вылезал за край на двух строках.
QSIZE = 44               # вопрос в постоянном положении наверху
CAPSIZE = 64             # субтитр
CTASIZE = 46
Q_TRACK = 6.0            # добавка \fsp между буквами, px PlayRes
Q_FILL = 0.90            # доля ширины кадра, которую занимает строка

# Цвета ASS: &HAABBGGRR, где AA — ПРОЗРАЧНОСТЬ наоборот: 00 значит
# непрозрачно, FF — невидимо. Перепутать концы легко, а видно ошибку
# только на готовом кадре.
C_QUESTION = "&H0000D4FF"        # #FFD400, фирменный жёлтый с обложек
C_CHROMA_RED = "&H000000FF"
C_CHROMA_CYAN = "&H00FFFF00"
C_CAPTION = "&H00FFFFFF"         # чистый белый, без примеси
C_CTA = "&H00FFFFFF"

# У субтитров подложки нет вовсе — белый текст по чёрной обводке.
# Полупрозрачная плашка, стоявшая здесь раньше, делала их блёклыми на
# светлом небе и при этом закрывала кадр.
OUTLINE_CAPTION = "&H00000000"

# ─────────────── РАСКЛАДКА КАДРА ───────────────
#
# Safe zone Shorts: верх ~12% занято UI, низ ~18% кнопками.
#
# ВОПРОС ВСЕГДА НАВЕРХУ, СУБТИТРЫ ВСЕГДА ВНИЗУ. Раньше субтитры стояли
# по центру (CAPTION_Y = 960) и ложились ровно на середину кадра —
# то есть на то, ради чего кадр и показывают. Теперь они прижаты к
# нижней границе безопасной зоны.
#
# У субтитра выравнивание по НИЗУ (\an2), и это принципиально: строк в
# нём от одной до трёх, и при верхней привязке текст рос бы вниз, в
# зону кнопок, каждый раз на разную величину. С нижней привязкой низ
# стоит на месте, а растёт блок вверх, в пустую часть кадра.
QUESTION_TOP_Y = 210             # верх блока вопроса (\an8)
CAPTION_BOTTOM_Y = 1560          # низ блока субтитров
CTA_BOTTOM_Y = 1560              # призыв встаёт на место субтитров

# ПОЯВЛЕНИЕ ВОПРОСА. Он сразу наверху и висит до конца шортса.
# Крупный хук по центру, который потом уезжал наверх, убран: на
# телефоне он спорил с начиткой, а заставка канала — это разрядка
# и спокойная шапка, не плакат во весь кадр.
#
# Анимация — пачка ASS-событий на одних координатах, без \move:
# хроматический сдвиг, разрядка \fsp, резкость \blur, линия-scan.
Q_INTRO = 0.80                   # разрядка + резкость, секунды
Q_CHROMA = 0.36                  # RGB-сдвиг гаснет
Q_SCAN = 0.85                    # линия прочерчивает блок
Q_BLUR = 2.4
Q_CHROMA_DX = 8                  # горизонтальный разъезд хрома, px

CTA_TEXT = "FULL STORY ON THE CHANNEL"
CTA_SECONDS = 2.4

# Вопрос: до 3 строк. Перенос по ширине с трекингом, не по числу
# символов: 26 знаков Archivo и 26 знаков Montserrat с \fsp — разная
# ширина, и лимит в символах снова выталкивал строку за край.
QUESTION_MAX_LINES = 3


def log(*a):
    print(*a, flush=True)


def run(cmd):
    subprocess.run(cmd, shell=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ─────────────────────── ПОСЛОВНЫЕ ТАЙМ-КОДЫ ───────────────────────

def _chapter_pause(job, block_i: int) -> float:
    """
    Та же пауза между главами, что в assets.build_voice. Без неё
    пословные тайм-коды после первой главы уезжают вперёд звука.
    """
    n_blocks = len(job["script_blocks"])
    if block_i >= n_blocks:
        return 0.0
    seed = abs(hash(job.get("id", "x"))) % 1000
    pause = 2.0 + ((seed + block_i * 7) % 11) / 10.0
    block = job["script_blocks"][block_i - 1]
    tail = block.rstrip()[-120:].lower()
    if ("?" in tail or tail.endswith("...")
            or re.search(r"\b(we don.?t know|nobody knows|"
                         r"still looking|hang on|listen)\b", tail)):
        pause = min(3.0, pause + 0.4)
    return pause


def words_from_alignment(job, vdir: Path):
    """Слова с временами из посимвольных тайм-кодов ElevenLabs + паузы."""
    words, offset = [], 0.0
    n_blocks = len(job["script_blocks"])
    for i in range(1, n_blocks + 1):
        mp3 = vdir / f"block_{i:02d}.mp3"
        aljson = vdir / f"block_{i:02d}.json"
        if not (mp3.exists() and aljson.exists()):
            return None
        al = json.loads(aljson.read_text())
        chars = al.get("chars") or []
        starts = al.get("starts") or []
        ends = al.get("ends") or []
        if not chars:
            return None
        buf, t0, t1 = [], None, None
        for ch, s, e in zip(chars, starts, ends):
            if ch.isspace():
                if buf:
                    words.append(dict(text="".join(buf),
                                      start=round(t0 + offset, 3),
                                      end=round(t1 + offset, 3)))
                    buf, t0 = [], None
                continue
            if t0 is None:
                t0 = s
            t1 = e
            buf.append(ch)
        if buf:
            words.append(dict(text="".join(buf), start=round(t0 + offset, 3),
                              end=round(t1 + offset, 3)))
        offset += render.duration_of(mp3, "a")
        # Пауза после главы — как в voice_full / marks.json.
        pause_mp3 = vdir / f"pause_{i:02d}.mp3"
        if i < n_blocks:
            if pause_mp3.exists():
                offset += render.duration_of(pause_mp3, "a")
            else:
                offset += _chapter_pause(job, i)
    return words or None


def words_from_marks(marks):
    """Запасной путь без посимвольных тайм-кодов (синтетика / mock)."""
    words = []
    for m in marks:
        toks = [t for t in m["text"].split() if t]
        if not toks:
            continue
        span = m["end"] - m["start"]
        weights = [len(t) + 1 for t in toks]
        wsum = sum(weights)
        t = m["start"]
        for tok, w in zip(toks, weights):
            d = span * w / wsum
            words.append(dict(text=tok, start=round(t, 3),
                              end=round(t + d, 3)))
            t += d
    return words


# ─────────────────────── ВЫБОР ДВУХ ОКОН ───────────────────────

def window_from(marks, idx, total, target=None, hard=None):
    """Окно от начала предложения idx: целые предложения, до hard max."""
    target = TARGET_LEN if target is None else target
    hard = HARD_MAX if hard is None else hard
    t0 = marks[idx]["start"]
    j = idx
    while (j + 1 < len(marks)
           and marks[j + 1]["end"] - t0 <= min(hard, target + 12)):
        j += 1
        if marks[j]["end"] - t0 >= target:
            break
    t1 = min(marks[j]["end"], t0 + hard, total - 0.1)
    return round(t0, 3), round(max(t1, t0 + 1.0), 3)


def pick_windows(story, marks, total):
    """
    Два окна: hook и revelation (или escalation). Каждое начинается с
    начала предложения. Окна не пересекаются.
    """
    soft = total < 180
    min_len = MIN_LEN_SOFT if soft else MIN_LEN
    target = min(TARGET_LEN, max(35.0, total * 0.35)) if soft else TARGET_LEN
    hard = min(HARD_MAX, max(45.0, total * 0.55)) if soft else HARD_MAX

    wins = []

    def overlaps(a0, a1):
        return any(a0 < w["t1"] + 2.0 and a1 > w["t0"] - 2.0 for w in wins)

    def add(idx, role, why):
        t0, t1 = window_from(marks, idx, total, target=target, hard=hard)
        if t1 - t0 < min_len:
            return False
        if overlaps(t0, t1):
            return False
        wins.append(dict(t0=t0, t1=t1, role=role, why=why, mark_idx=idx))
        return True

    add(0, "hook", "первые фразы ролика — готовый крючок сценария")

    for kind, feat, why in (
            ("revelation", "num", "развязка с ответом на вопрос"),
            ("escalation", "turn", "нагнетание с поворотом — ответ рядом")):
        if len(wins) >= N_SHORTS:
            break
        pool = sorted((b for b in story if b.kind == kind),
                      key=lambda b: b.features.get(feat, 0.0), reverse=True)
        for b in pool:
            if add(b.first_mark, kind, why):
                break

    if len(wins) < N_SHORTS:
        rest = sorted((b for b in story
                       if b.kind in ("revelation", "escalation", "setup")),
                      key=lambda b: b.start)
        for b in rest:
            if len(wins) >= N_SHORTS:
                break
            add(b.first_mark, b.kind, "добор: сильных долей не хватило")
    starts = [m["start"] for m in marks]
    for k in range(20):
        if len(wins) >= N_SHORTS:
            break
        idx = bisect_left(starts, total * (0.18 + 0.08 * k))
        if idx < len(marks):
            add(idx, "extra", "добор равным шагом по таймлайну")
    while len(wins) < N_SHORTS:
        w = dict(wins[len(wins) % max(len(wins), 1)])
        w["role"], w["why"] = "extra", "ролик короче двух окон, окно повторено"
        wins.append(w)

    wins.sort(key=lambda w: {"hook": 0, "revelation": 1,
                             "escalation": 2}.get(w["role"], 3))
    return wins[:N_SHORTS]


def wrap_question(text: str) -> str:
    """
    Перенос вопроса на 2–3 строки по РЕАЛЬНОЙ ширине с трекингом.

    Заглавные — как на заставке длинного ролика. Лимит в символах здесь
    не годится: разрядка растягивает строку почти вдвое относительно
    плотного Archivo, и 26 знаков уже не влезают.
    """
    raw = " ".join((text or "").split()).upper()
    if not raw:
        raw = "WHAT REALLY HAPPENED?"
    words = raw.split()
    limit = SW * Q_FILL

    def fits(s: str) -> bool:
        return text_width(s, QSIZE, Q_TRACK) <= limit

    lines, cur, i = [], words[0], 1
    while i < len(words):
        trial = f"{cur} {words[i]}"
        if fits(trial):
            cur = trial
            i += 1
            continue
        lines.append(cur)
        cur = words[i]
        i += 1
        if len(lines) >= QUESTION_MAX_LINES - 1:
            last = " ".join([cur] + words[i:])
            full_last = last
            while last and not fits(last) and " " in last:
                last = last.rsplit(" ", 1)[0]
            if last != full_last:
                if raw.endswith("?") and not last.endswith("?"):
                    trial_q = last.rstrip(".!") + "?"
                    last = trial_q if fits(trial_q) else last.rstrip(".!") + "…"
                elif not last.endswith(("?", "!", "…")):
                    last += "…"
            lines.append(last)
            return "\n".join(lines[:QUESTION_MAX_LINES])
    lines.append(cur)
    return "\n".join(lines)


def question_for(win, marks, job_questions, n):
    """
    Вопрос-хук на всю длину шортса. Сначала — youtube.shorts_questions,
    иначе первое предложение окна, ужатое в вопрос.
    """
    if n - 1 < len(job_questions) and str(job_questions[n - 1]).strip():
        raw = str(job_questions[n - 1]).strip()
    else:
        idx = win.get("mark_idx", 0)
        raw = (marks[idx]["text"] if 0 <= idx < len(marks) else "").strip()
        raw = " ".join(raw.split())
        if not raw:
            raw = "What really happened?"
        elif not raw.endswith("?"):
            cut = raw
            for sep in (", ", " — ", " - ", "; "):
                if sep in cut and len(cut.split(sep)[0]) >= 18:
                    cut = cut.split(sep)[0]
                    break
            cut = cut.rstrip(".!")
            raw = (cut + "?") if cut else "What really happened?"
    return wrap_question(raw)


# ─────────────────────── ВЕРТИКАЛЬНЫЙ ВИДЕОРЯД ───────────────────────

def prepare_vertical(src: Path, dst: Path):
    """Статичный вертикальный кадр 9:16 из исходника (без зума/дрейфа)."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    iw, ih = im.size
    ar = SW / SH
    cw = min(iw, ih * ar)
    ch = cw / ar
    if ch > ih:
        ch = ih
        cw = ch * ar
    cw, ch = max(2, int(cw)), max(2, int(ch))
    cx = int((iw - cw) * 0.5)
    cy = int((ih - ch) * 0.38)
    im.crop((cx, cy, cx + cw, cy + ch)).resize(
        (SW, SH), Image.LANCZOS).save(dst, quality=95)


_STOP = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at",
    "for", "is", "are", "was", "were", "be", "been", "with", "as", "by",
    "from", "that", "this", "it", "its", "we", "you", "they", "he", "she",
    "i", "not", "no", "so", "if", "into", "than", "then", "there", "their",
}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower())
            if len(w) > 2 and w not in _STOP}


def _shot_blob(sh: dict) -> str:
    return " ".join(filter(None, [
        Path(sh["file"]).stem.replace("_", " ").replace("-", " "),
        str(sh.get("tag") or ""),
        str(sh.get("why") or ""),
        str(sh.get("beat_kind") or ""),
    ]))


def cut_plan(shots, t0, t1, rng, words=None):
    """
    Перерезка окна: 2.8–4.8 с на кадр + семантический выбор исходника.

    Без Ken Burns / sweep поверх: движение только то, что уже есть
    в исходном клипе. Фото остаётся статичным кадром.
    """
    cuts, t = [], t0

    while t < t1 - 0.05:
        dur = min(rng.uniform(*CUT_RANGE), t1 - t)
        mid = t + dur / 2
        said = ""
        if words:
            said = " ".join(w["text"] for w in words
                            if t <= w["start"] < t + dur)
        said_tok = _tokens(said)

        on_tl = next((s for s in shots
                      if s["start"] <= mid < s["start"] + s["duration"]),
                     shots[-1])
        cand = [s for s in shots
                if abs((s["start"] + s["duration"] / 2) - mid) <= 45.0]
        if not cand:
            cand = [on_tl]

        def score(s):
            overlap = len(said_tok & _tokens(_shot_blob(s)))
            bonus = 0.35 if s is on_tl else 0.0
            kind_b = 0.15 if s.get("kind") == "clip" and t - t0 < 12 else 0.0
            return overlap + bonus + kind_b

        sh = max(cand, key=score)

        if cuts and cuts[-1]["file"] == sh["file"] \
                and cuts[-1]["dur"] + dur <= CUT_MERGED_MAX:
            cuts[-1]["dur"] = round(cuts[-1]["dur"] + dur, 3)
        else:
            src_off = float(sh.get("src_start", 0.0)) + max(
                0.0, t - sh["start"])
            cuts.append(dict(file=sh["file"], kind=sh["kind"],
                             src_start=src_off, dur=round(dur, 3)))
        t += dur

    drift = (t1 - t0) - sum(c["dur"] for c in cuts)
    if cuts:
        cuts[-1]["dur"] = round(cuts[-1]["dur"] + drift, 3)
    return cuts


def render_cut(c, out: Path, canvas_cache, tmp: Path):
    """
    Один вертикальный кус без движения поверх исходника.

    Фото: статичный кадр на всю длительность куска.

    Клип: БЛЮР-ПОДЛОЖКА ПЛЮС ВПИСАННЫЙ ПЕРЕДНИЙ ПЛАН, а не жёсткий кроп
    в 9:16. Жёсткий кроп казался решением («никакого зума сверху — только
    то, что уже снято»), но у канала весь сток горизонтальный (1920x1080,
    см. render.W/H), и scale=increase + crop=SW:SH для такого источника
    оставляет ровно 31.6% ширины кадра — это математика формата, а не
    промах кодирования: 1080/1920 * 1080/1080 упирается в множитель
    1080/1920 недостаточно, а 1920/1080 достаточно, поэтому берётся он, и
    после увеличения кадр обрезается до трети своей ширины. Любое
    движение камеры в исходнике — ручное дрожание, лёгкий панорамный увод
    — при таком кропе увеличивается почти втрое. Это и есть тряска: не
    добавленная, а УСИЛЕННАЯ operatorским кропом.

    Передний план здесь вписывается ЦЕЛИКОМ по ширине (fit, не crop) —
    движение исходника остаётся тем же самым, что было снято, один в
    один. Пустые поля сверху и снизу заполняет размытая и затемнённая
    копия того же кадра, растянутая на весь холст: подложке лишний зум
    не вредит, потому что смотреть на неё резко никто не будет.
    """
    src = Path(c["file"])
    if c["kind"] == "clip":
        vf = (f"split=2[bg][fg];"
              f"[bg]scale={SW}:{SH}:force_original_aspect_ratio=increase,"
              f"crop={SW}:{SH},gblur=sigma=42,eq=brightness=-0.12[bgb];"
              f"[fg]scale={SW}:-2:force_original_aspect_ratio=decrease[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={SFPS}")
        run(f"ffmpeg -y -stream_loop -1 -ss {float(c['src_start']):.2f} "
            f"-i {shlex.quote(str(src))} -filter_complex {shlex.quote(vf)} "
            f"-t {c['dur']:.3f} -c:v libx264 -crf 19 -preset veryfast "
            f"-pix_fmt yuv420p -an {shlex.quote(str(out))}")
        return

    canvas = canvas_cache.get(src)
    if canvas is None:
        canvas = tmp / f"v_{len(canvas_cache):03d}.jpg"
        prepare_vertical(src, canvas)
        canvas_cache[src] = canvas
    run(f"ffmpeg -y -loop 1 -t {c['dur']:.3f} -r {SFPS} "
        f"-i {shlex.quote(str(canvas))} "
        f"-c:v libx264 -crf 19 -preset veryfast -pix_fmt yuv420p -an "
        f"{shlex.quote(str(out))}")


# ─────────────────────── КАПШЕНЫ ───────────────────────

CAPTION_LINE = 26          # символов в строке обычного субтитра
CAPTION_MAX_LINES = 4


def _ass_t(sec: float) -> str:
    h, r = divmod(max(sec, 0.0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_esc(s: str) -> str:
    return (s.replace("\\", "")
             .replace("{", "(")
             .replace("}", ")")
             .replace("\n", r"\N"))


def wrap_caption(text: str, line: int = CAPTION_LINE,
                 max_lines: int = CAPTION_MAX_LINES) -> str:
    """Перенос обычного субтитра на 1–3 строки по словам."""
    words = text.strip().split()
    if not words:
        return ""
    lines, cur, i = [], words[0], 1
    while i < len(words):
        trial = f"{cur} {words[i]}"
        if len(trial) <= line:
            cur = trial
            i += 1
            continue
        lines.append(cur)
        cur = words[i]
        i += 1
        if len(lines) >= max_lines - 1:
            rest = " ".join([cur] + words[i:])
            if len(rest) > line:
                rest = rest[: line - 1].rsplit(" ", 1)[0]
                if not rest.endswith((".", "!", "?", "…")):
                    rest += "…"
            lines.append(rest)
            return "\n".join(lines[:max_lines])
    lines.append(cur)
    return "\n".join(lines)


def captions_from_marks(marks, t0, dur):
    """
    Обычные субтитры: целые предложения из marks.json, время относительно
    начала шортса (= тот же таймлайн, с которого atrim режет звук).

    Раньше резали по 2–3 слова из пословных тайм-кодов — на телефоне это
    выглядело как караоке, и после пауз между главами слова уезжали от
    начитки. Предложения из marks совпадают с SRT длинного ролика.

    Окно, начавшееся в середине фразы: start обрезается в 0, текст
    остаётся целиком. Не ждём отрицательного ms-t0 — иначе реплика
    появилась бы «с начала предложения», которого в этом звуке уже нет,
    и выглядело бы как рассинхрон.
    """
    t1 = t0 + dur
    caps = []
    for m in marks:
        ms, me = float(m["start"]), float(m["end"])
        if me <= t0 + 0.05 or ms >= t1 - 0.05:
            continue
        text = wrap_caption(" ".join(str(m.get("text") or "").split()))
        if not text:
            continue
        start = max(0.0, ms - t0)
        end = min(dur, me - t0)
        if end - start < 0.20:
            continue
        caps.append(dict(text=text, start=round(start, 3), end=round(end, 3)))
    caps.sort(key=lambda p: p["start"])
    for i, p in enumerate(caps):
        if i + 1 < len(caps) and caps[i + 1]["start"] > p["start"] + 0.20:
            p["end"] = min(p["end"], caps[i + 1]["start"] - 0.04)
        p["end"] = max(p["end"], p["start"] + 0.20)
    return caps


def text_width(text: str, size: int, tracking: float = 0.0,
               font_file: Path | None = None) -> float:
    """
    Ширина самой длинной строки НАСТОЯЩИМ шрифтом из assets/fonts.

    tracking — те же пиксели, что ASS \\fsp: Pillow их не считает,
    добавляем по числу промежутков между знаками.

    РАЗБИРАЕМ ОБА ВИДА ПЕРЕНОСА. В ASS строки разделяет «\\N», а не
    «\\n», и на этом уже один раз обожглись: замер получал весь вопрос
    одной строкой в две тысячи пикселей.
    """
    text = text.replace("\\N", "\n")
    lines = [l for l in text.split("\n") if l.strip()] or [""]
    font_file = font_file or FONT_QUESTION_FILE
    try:
        from PIL import ImageFont
        f = ImageFont.truetype(str(font_file), size)
        widths = []
        for line in lines:
            box = f.getbbox(line)
            w = box[2] - box[0]
            w += tracking * max(len(line) - 1, 0)
            widths.append(w)
        return max(widths)
    except Exception:
        return max(len(l) * (size * 0.52 + tracking) for l in lines)


def question_anchor(question: str) -> tuple[int, float, float]:
    """Число строк, ширина самой длинной, высота блока."""
    q = question.replace("\\N", "\n").strip()
    lines = [l for l in q.split("\n") if l.strip()] or [""]
    widest = text_width(q, QSIZE, Q_TRACK)
    height = len(lines) * QSIZE * 1.22
    return len(lines), widest, height


def sync_filters(t0: float, dur: float, fade_st: float,
                 ass_esc: str, fdir: str) -> str:
    """
    Картинка и звук на одних часах длиной dur.

    concat -c copy набирает погрешность timebase; input -ss по MP4
    прыгает на ключевой кадр. Здесь и то и другое снимается: видео
    trim+tpad в ровно dur, звук atrim от t0 на ту же dur.
    """
    video = (
        f"[0:v]fps={SFPS},setpts=PTS-STARTPTS,"
        f"trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop=-1,"
        f"ass={ass_esc}:fontsdir={fdir},"
        f"fade=t=in:d=0.12,fade=t=out:st={fade_st:.2f}:d=0.40[v]"
    )
    audio = (
        f"[1:a]atrim=start={t0:.3f}:duration={dur:.3f},"
        f"asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={fade_st:.2f}:d=0.40[a]"
    )
    return video + ";" + audio


def write_ass(marks, t0, dur, out: Path, question: str):
    """
    Три слоя, и место у каждого своё на всю длину шортса.

      Question  наверху с VFX-появлением, дальше висит до конца.
      Scan      тонкая жёлтая линия под блоком, только на входе.
      Caption   ВНИЗУ, прижат к нижней границе безопасной зоны.
      Cta       в самом конце, на месте субтитров.

    Картинку не перекрывает ничто: середина кадра свободна всегда.
    """
    head = (
        "[Script Info]\n"
        f"PlayResX: {SW}\nPlayResY: {SH}\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
        " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
        " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Вопрос: без плашки и без обводки. Обводка утолщает штрих и
        # убивает геометрию Montserrat — как у титров длинного ролика,
        # держит мягкая тень. Alignment 8 = верх по центру: верх блока
        # стоит на QUESTION_TOP_Y при любом числе строк.
        f"Style: Question,{FONT_QUESTION},{QSIZE},{C_QUESTION},{C_QUESTION},"
        f"&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,3,8,40,40,0,1\n"
        f"Style: Scan,{FONT_QUESTION},20,{C_QUESTION},{C_QUESTION},"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        # Субтитр. BorderStyle 1 = обводка вместо подложки, Alignment 2 =
        # низ по центру, блок растёт вверх.
        f"Style: Caption,{FONT_CAPTION},{CAPSIZE},{C_CAPTION},{C_CAPTION},"
        f"{OUTLINE_CAPTION},&HA0000000,0,0,0,0,100,100,0,0,1,6,3,2,70,70,0,1\n"
        f"Style: Cta,{FONT_CAPTION},{CTASIZE},{C_CTA},{C_CTA},"
        f"{OUTLINE_CAPTION},&HA0000000,0,0,0,0,100,100,1,0,1,6,3,2,70,70,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR,"
        " MarginV, Effect, Text\n")

    rows = []
    q = _ass_esc(question.strip())
    if q:
        _n, widest, block_h = question_anchor(question.strip())
        intro_ms = int(Q_INTRO * 1000)

        def q_tags(x: float) -> str:
            # Привязка по ВЕРХУ (\an8): вопрос сразу в шапке и не едет.
            return (
                f"{{\\an8\\pos({x:.0f},{QUESTION_TOP_Y})\\q2"
                f"\\fsp0\\blur{Q_BLUR}"
                f"\\t(0,{intro_ms},\\fsp{Q_TRACK:.0f}\\blur0)"
            )

        # Хрома: те же буквы, чуть в стороны, гаснут к концу сдвига.
        # Тот же \\fsp, иначе слои разъедутся, пока разрядка ещё идёт.
        for dx, color in (
                (Q_CHROMA_DX, C_CHROMA_RED),
                (-Q_CHROMA_DX, C_CHROMA_CYAN)):
            rows.append(
                f"Dialogue: 0,{_ass_t(0)},{_ass_t(Q_CHROMA)},Question,,0,0,0,,"
                f"{q_tags(540 + dx)}\\1c{color}&\\alpha&H64&\\fad(40,200)}}{q}")
        rows.append(
            f"Dialogue: 1,{_ass_t(0)},{_ass_t(dur)},Question,,0,0,0,,"
            f"{q_tags(540)}\\fad(120,0)}}{q}")
        # Линия-scan: растёт слева направо под блоком, потом гаснет.
        # \\an7 и фигура от нуля — с \\an5 и отрицательными координатами
        # libass увозит прямоугольник в угол кадра.
        scan_w = max(80.0, widest)
        scan_x = 540 - scan_w / 2
        scan_y = QUESTION_TOP_Y + block_h + 10
        grow_ms = min(450, int(Q_SCAN * 1000) - 80)
        box = (f"m 0 0 l {scan_w:.0f} 0 "
               f"l {scan_w:.0f} 3 l 0 3")
        rows.append(
            f"Dialogue: 0,{_ass_t(0)},{_ass_t(Q_SCAN)},Scan,,0,0,0,,"
            f"{{\\an7\\pos({scan_x:.0f},{scan_y:.0f})"
            f"\\fscx0\\t(0,{grow_ms},\\fscx100)\\fad(40,380)\\p1}}"
            f"{box}{{\\p0}}")

    # Субтитры гаснут раньше призыва: место у них одно, и наложить их
    # друг на друга значило бы получить кашу в последние две секунды.
    cta_from = max(dur - CTA_SECONDS, 0.0)
    phrases = captions_from_marks(marks, t0, dur)
    shown = 0
    for p in phrases:
        txt = _ass_esc(p["text"].strip())
        if not txt:
            continue
        end_t = min(p["end"], cta_from - 0.10)
        if end_t - p["start"] < 0.20:
            continue
        shown += 1
        rows.append(
            f"Dialogue: 2,{_ass_t(p['start'])},{_ass_t(end_t)},Caption,,0,0,0,,"
            f"{{\\an2\\pos(540,{CAPTION_BOTTOM_Y})\\q2\\fad(80,60)}}{txt}")

    rows.append(
        f"Dialogue: 3,{_ass_t(cta_from)},{_ass_t(dur)},Cta,,0,0,0,,"
        f"{{\\an2\\pos(540,{CTA_BOTTOM_Y})\\fad(250,0)}}"
        + _ass_esc(CTA_TEXT))
    out.write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    return shown


# ─────────────────────── СБОРКА ОДНОГО ШОРТСА ───────────────────────

def render_short(n, win, shots, words, marks, final: Path, sdir: Path,
                 seed: str, question: str):
    rng = random.Random(f"{seed}-short-{n}")
    t0, t1 = win["t0"], win["t1"]
    dur = round(t1 - t0, 3)
    tmp = sdir / f"tmp_{n}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    cuts = cut_plan(shots, t0, t1, rng, words=words)
    canvas_cache = {}
    segs = []
    for ci, c in enumerate(cuts):
        seg = tmp / f"cut_{ci:03d}.mp4"
        render_cut(c, seg, canvas_cache, tmp)
        segs.append(seg)
    body = tmp / "body.mp4"
    lst = tmp / "concat.txt"
    lst.write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
    run(f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(str(lst))} "
        f"-c copy {shlex.quote(str(body))}")

    ass = tmp / "captions.ass"
    n_phrases = write_ass(marks, t0, dur, ass, question)

    out = sdir / f"short_{n}.mp4"
    fade_st = max(dur - 0.40, 0.0)
    ass_esc = str(ass.resolve()).replace("\\", "/").replace(":", "\\:")
    # Шрифты канала берутся из репозитория, а не из системы —
    # см. FONT_DIR. Без этого libass подставит что найдёт.
    fdir = str(FONT_DIR.resolve()).replace("\\", "/").replace(":", "\\:")
    filt = sync_filters(t0, dur, fade_st, ass_esc, fdir)
    # Звук — вторым входом ЦЕЛИКОМ. Не -ss перед -i: в MP4 это прыжок
    # на ключевой кадр видео, и atrim тогда сидит уже не на t0.
    run(f"ffmpeg -y -i {shlex.quote(str(body))} "
        f"-i {shlex.quote(str(final))} "
        f"-filter_complex {shlex.quote(filt)} "
        f"-map [v] -map [a] -t {dur:.3f} "
        f"-c:v libx264 -crf 20 -preset veryfast -pix_fmt yuv420p "
        f"-c:a aac -b:a 192k -movflags +faststart {shlex.quote(str(out))}")
    shutil.rmtree(tmp, ignore_errors=True)

    log(f"  short_{n}: {win['role']:<10} {t0/60:5.1f}-{t1/60:5.1f} мин, "
        f"{dur:.0f} с, {len(cuts)} кадров, {n_phrases} субтитров")
    log(f"           вопрос:\n             "
        + question.replace("\n", "\n             "))
    log(f"           {win['why']}")
    return dict(file=out.name, role=win["role"], t0=t0, t1=t1,
                seconds=dur, cuts=len(cuts), phrases=n_phrases,
                question=question, why=win["why"])


def ensure_shots(job, assets: Path, out: Path):
    """
    Если shots.json нет (пересборка только шортсов) — строим план заново
    из marks + материала в кэше. Монтаж длинного ролика не трогаем.
    """
    shots_p = out / "shots.json"
    if shots_p.exists():
        return
    import build
    import style as style_mod
    import channel
    log("  shots.json нет — собираю план кадров из кэша материалов")
    marks = json.loads((assets / "marks.json").read_text())
    total = json.loads((assets / "state.json").read_text())["total_audio"]
    av = channel.avoid()
    st = style_mod.StyleEngine(
        job["id"], recent_luts=av["lut"], recent_openings=av["opening"],
        recent_transitions=av["main_transition"],
        recent_overlays=av["overlay"], recent_beds=av["bed"])
    for k in ("lut", "archive_lut"):
        if job.get(k):
            setattr(st, k, job[k])
    build.apply_style_override(st, job)
    shots = build.plan_shots(marks, st, assets, total, job.get("reject"), job)
    out.mkdir(parents=True, exist_ok=True)
    # тот же формат, что пишет build.py
    serial = []
    for s in shots:
        row = {}
        for k, v in s.items():
            if isinstance(v, float):
                row[k] = f"{v:.3f}"
            elif isinstance(v, Path):
                row[k] = str(v)
            else:
                row[k] = v
        serial.append(row)
    shots_p.write_text(json.dumps(serial, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    log(f"  shots.json: {len(serial)} кадров")


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    base = Path("work") / job["id"]
    assets, out = base / "assets", base / "out"
    final = out / "final.mp4"
    for f in (final, assets / "marks.json"):
        if not f.exists():
            raise SystemExit(f"нет {f} — сначала собери ролик: "
                             f"python pipeline/build.py {job_path}")

    ensure_shots(job, assets, out)
    shots_p = out / "shots.json"

    marks = json.loads((assets / "marks.json").read_text())
    media_dur = render.duration_of(final, "v") or render.duration_of(final, "a")
    total = json.loads((assets / "state.json").read_text())["total_audio"]
    # Окна и субтитры только внутри реального final.mp4: marks/total_audio
    # бывают длиннее из‑за среза mux -shortest.
    if media_dur > 0:
        total = min(total, media_dur)
        marks = [m for m in marks if float(m["start"]) < total - 0.2]
        if marks and float(marks[-1]["end"]) > total:
            marks[-1] = dict(marks[-1], end=round(total, 3))
    log(f"таймлайн шортсов: {total:.1f} с "
        f"(final.mp4 {media_dur:.1f} с), предложений {len(marks)}")

    shots = []
    for s in json.loads(shots_p.read_text()):
        shots.append(dict(file=s["file"], kind=s["kind"],
                          start=float(s["start"]),
                          duration=float(s["duration"]),
                          src_start=float(s.get("src_start") or 0.0),
                          tag=s.get("tag"), why=s.get("why"),
                          beat_kind=s.get("beat_kind")))

    story = beats_mod.analyze(marks, job["script_blocks"], total)
    # Слова — только для семантического подбора кадров. Субтитры идут
    # из marks (целые предложения), иначе снова получится караоке.
    words = words_from_alignment(job, assets / "voice")
    if words:
        log(f"слова: {len(words)} по посимвольным тайм-кодам ElevenLabs "
            f"(для подбора кадров)")
    else:
        words = words_from_marks(marks)
        log(f"слова: {len(words)} раскиданы по длине (посимвольных "
            f"тайм-кодов нет — синтетика?)")

    wins = pick_windows(story, marks, total)
    y = job.get("youtube") or {}
    job_qs = list(y.get("shorts_questions") or [])
    sdir = out / "shorts"
    sdir.mkdir(parents=True, exist_ok=True)
    for stale in sdir.glob("short_*.mp4"):
        try:
            n = int(stale.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        if n > N_SHORTS:
            stale.unlink(missing_ok=True)

    log(f"── шортсы: {len(wins)} окна")
    meta = []
    for n, win in enumerate(wins, 1):
        q = question_for(win, marks, job_qs, n)
        meta.append(render_short(n, win, shots, words, marks, final, sdir,
                                 job["id"], q))

    (sdir / "shorts.json").write_text(json.dumps(dict(
        source=final.name, title=y.get("title", ""), shorts=meta),
        indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"готово: {sdir}/short_1..{len(meta)}.mp4 + shorts.json")


if __name__ == "__main__":
    main(sys.argv[1])
