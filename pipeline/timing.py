"""
timing.py — общий модуль тайм-кодов (5.7).

Раньше это жило в трёх(-четырёх) местах, каждое со своей копией одной и
той же логики:

  - assets.sentence_marks    — предложения из посимвольного alignment
  - mock.split_sentences     — та же граница, но на синтетике
  - youtube.first_sentence   — та же граница, для поиска начала главы
  - assets.build_voice / shorts._chapter_pause — одна и та же формула
    длины драматической паузы между главами, вписанная в двух местах

CLAUDE.md прямо требует, чтобы первые три резали текст ОДИНАКОВО: если
разойдутся, смоук снова начнёт проверять не тот путь, который работает
в бою (см. историю georgia-guidestones-01 в docstring ниже). Одна копия
в одном месте — это не рефакторинг ради чистоты, а единственный способ
гарантировать совпадение.

Модуль — ЛИСТ пакета: не импортирует assets/mock/youtube/shorts/build,
только render (для длительности mp3) и стандартную библиотеку. Это
осознанно: assets.py импортирует timing.py, а не наоборот, иначе
words_from_alignment (которому нужен sentence_marks) и sentence_marks
(которым пользуется assets.py) замкнули бы модули друг на друга.
"""

import json
import re
from pathlib import Path

import render

# ───────────────────── ГРАНИЦА ПРЕДЛОЖЕНИЯ ─────────────────────
#
# Точка перед пробелом сама по себе границу не держит: у инициального
# сокращения из БУКВЫ-ТОЧКИ-БУКВЫ-ТОЧКИ (R.C., U.S.) последняя точка
# тоже стоит перед пробелом и неотличима от конца предложения. На
# georgia-guidestones-01 «R.C. Christian» встречается тринадцать раз, и
# без этой проверки почти каждое упоминание рвало реплику пополам —
# "R." отдельной репликой, "C. Christian was…" следующей, а дальше по
# этому же месту не находил себя youtube.chapters. Все точки внутри
# такого сокращения, включая последнюю, из кандидатов на разрыв
# исключены — и исключены ОДНИМ регулярным выражением на все три
# потребителя, а не тремя одинаковыми копиями.
ABBREV_INITIALS_RE = re.compile(r"\b(?:[A-Z]\.){2,}")


def sentence_end_positions(text: str) -> set:
    """Индексы точек/!/?, которые НЕ конец предложения — последняя точка
    инициального сокращения (R.C., U.S.), а не завершение фразы."""
    return {m.end() - 1 for m in ABBREV_INITIALS_RE.finditer(text)}


def sentence_marks(text, align, offset):
    """
    Превращает посимвольные тайм-коды в границы предложений.
    Это и есть точки, где робот будет менять кадр.
    """
    chars, starts, ends = align["chars"], align["starts"], align["ends"]
    if not chars:
        return []
    joined = "".join(chars)
    abbrev_end = sentence_end_positions(joined)
    marks, buf, buf_start = [], [], None
    for i, ch in enumerate(chars):
        if buf_start is None:
            buf_start = starts[i]
        buf.append(ch)
        if ch in ".!?" and i + 1 < len(chars) and chars[i + 1] in " \n" \
                and i not in abbrev_end:
            marks.append({"text": "".join(buf).strip(),
                          "start": round(buf_start + offset, 3),
                          "end": round(ends[i] + offset, 3)})
            buf, buf_start = [], None
    if buf:
        marks.append({"text": "".join(buf).strip(),
                      "start": round((buf_start or 0) + offset, 3),
                      "end": round(ends[-1] + offset, 3)})
    return marks


def split_sentences(text: str):
    """Та же граница, что у sentence_marks, но на голой строке без
    тайм-кодов — для синтетики (mock.py)."""
    abbrev_end = sentence_end_positions(text)
    out, buf = [], ""
    for i, ch in enumerate(text):
        buf += ch
        if ch in ".!?" and i + 1 < len(text) and text[i + 1] in " \n" \
                and i not in abbrev_end:
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s]


def first_sentence(text: str) -> str:
    """Первое предложение блока — та же граница, что у sentence_marks."""
    text = text.strip()
    abbrev_end = sentence_end_positions(text)
    for i, ch in enumerate(text):
        if ch in ".!?" and (i + 1 >= len(text) or text[i + 1] in " \n") \
                and i not in abbrev_end:
            return text[: i + 1].strip()
    return text


# ───────────────────── ПАУЗА МЕЖДУ ГЛАВАМИ ─────────────────────

def chapter_pause_seconds(job, block_i: int) -> float:
    """
    Длина драматической паузы после блока block_i (1-based), 2.0-3.0 с.
    Детерминирована от id ролика, чтобы пересборка не плясала.

    Одна формула на два потребителя: assets.build_voice (реально режет
    тишину такой длины) и words_from_alignment ниже (пословные тайм-коды
    должны сходиться с тем, что build_voice реально положил на диск, а
    не с приближением). Разойдутся — пословные тайм-коды после первой
    же главы начнут уезжать вперёд настоящего звука.
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


# ───────────────────── ПАУЗА ПОД КАРТОЧКУ НАЗВАНИЯ ─────────────────────
#
# 2.1.2 — пауза после первой фразы блока 1, под заставку с названием.
# Живёт здесь же (не в assets.py), потому что нужна и build_voice
# (реально режет тишину такой длины), и words_from_alignment ниже
# (пословные тайм-коды блока 1 после первой фразы сдвинуты на ту же
# величину) — тот же довод, что у chapter_pause_seconds.
HOOK_PAUSE_DEFAULT = 2.4
HOOK_PAUSE_MAX = 4.0


# ───────────────────── ПОСЛОВНЫЕ ТАЙМ-КОДЫ ─────────────────────

def words_from_alignment(job, vdir: Path):
    """Слова с временами из посимвольных тайм-кодов ElevenLabs + паузы."""
    words, offset = [], 0.0
    n_blocks = len(job["script_blocks"])
    hook_pause = max(0.0, min(
        HOOK_PAUSE_MAX, float(job.get("hook_pause", HOOK_PAUSE_DEFAULT))))
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
        cut_at = None
        if i == 1 and hook_pause > 0:
            fm = sentence_marks(job["script_blocks"][0], al, 0.0)
            if len(fm) >= 2:
                cut_at = fm[0]["end"]

        def shifted(t0, t1):
            if cut_at is not None and t0 >= cut_at - 1e-6:
                return t0 + offset + hook_pause, t1 + offset + hook_pause
            return t0 + offset, t1 + offset

        buf, t0, t1 = [], None, None
        for ch, s, e in zip(chars, starts, ends):
            if ch.isspace():
                if buf:
                    ws, we = shifted(t0, t1)
                    words.append(dict(text="".join(buf),
                                      start=round(ws, 3), end=round(we, 3)))
                    buf, t0 = [], None
                continue
            if t0 is None:
                t0 = s
            t1 = e
            buf.append(ch)
        if buf:
            ws, we = shifted(t0, t1)
            words.append(dict(text="".join(buf), start=round(ws, 3),
                              end=round(we, 3)))
        offset += render.duration_of(mp3, "a")
        if cut_at is not None:
            offset += hook_pause
        # Пауза после главы — как в voice_full / marks.json.
        pause_mp3 = vdir / f"pause_{i:02d}.mp3"
        if i < n_blocks:
            if pause_mp3.exists():
                offset += render.duration_of(pause_mp3, "a")
            else:
                offset += chapter_pause_seconds(job, i)
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
