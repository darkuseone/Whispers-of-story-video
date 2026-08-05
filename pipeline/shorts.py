"""
shorts.py — три вертикальных шортса из готового ролика.

    python pipeline/shorts.py jobs/ancient-01.json

Запускается ПОСЛЕ монтажа: нужны final.mp4 (оттуда берётся звук со всем
сведением), shots.json (план кадров с путями ИСХОДНИКОВ) и marks.json.
Ключей и денег не тратит вовсе — всё считается локальным ffmpeg.

ЧТО ТАКОЕ ЭТИ ШОРТСЫ. Не «трейлер» и не случайная вырезка, а три разных
захода на одну и ту же историю, каждый — своя ставка на удержание:

  1 hook        первые фразы ролика. Сценарий уже написан так, что они —
                самое плотное место всей начитки; резать лучший крючок
                заново машиной было бы хуже, чем взять готовый
  2 revelation  доля-развязка с самыми «цифровыми» предложениями: даты и
                суммы — то, ради чего зритель дослушивает
  3 escalation  доля-нагнетание с поворотными фразами («but», «until»...)
                — обрыв на самом интересном, лучший мостик к длинному
                ролику

ОТКУДА УДЕРЖАНИЕ, по пунктам — каждое решение ниже в коде:
  - старт с первого слова, без заставки и без титула: у шортса нет трёх
    секунд на разгон, решение «листать дальше» принимается за одну
  - видеоряд перерезается ЗАНОВО, кадр 1.4-2.6 секунды вместо 6-12 в
    длинном ролике: вертикальная лента приучила к этому темпу
  - вертикаль 9:16 режется из ИСХОДНИКОВ кадров, а не апскейлится из
    готового 16:9: чужие горизонтальные поля в шортсе — первый признак
    ленивой нарезки, по которому его пролистывают
  - пословные белые капшены: слово появляется ровно в момент, когда оно
    звучит. Глазам всегда есть за чем следить, и шортс работает даже без
    звука — а это половина просмотров ленты
  - последние две секунды — стрелка на полный ролик: шортс здесь не
    самоцель, а воронка на канал

Всё детерминировано от id ролика: пересборка даёт те же три шортса.
"""

import json
import random
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

N_SHORTS = 3
# Вилка длины. Меньше 25 секунд история не успевает зацепить, больше 55 —
# хвост досматривают хуже, а лимит площадки в минуту рядом.
TARGET_LEN = 46.0
HARD_MAX = 56.0
MIN_LEN = 12.0            # деградация для тестовых роликов в пару минут

# Кадр шортса. Быстрее длинного ролика в разы — это темп ленты. Кадры из
# одного исходника подряд склеиваются в один подлиннее, см. cut_plan.
CUT_RANGE = (1.4, 2.6)
CUT_MERGED_MAX = 3.6

# Вертикальный холст с запасом на движение: кроп 1080x1920 ездит по нему.
# 1440/1080 — треть запаса, столько же, сколько у горизонтального рендера.
CANVAS_W, CANVAS_H = 1440, 2560

CTA_TEXT = "FULL STORY ON THE CHANNEL"
CTA_SECONDS = 2.2


def log(*a):
    print(*a, flush=True)


def run(cmd):
    subprocess.run(cmd, shell=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ─────────────────────── ПОСЛОВНЫЕ ТАЙМ-КОДЫ ───────────────────────

def words_from_alignment(job, vdir: Path):
    """
    Слова с временами из посимвольных тайм-кодов ElevenLabs.

    Это НАСТОЯЩИЕ моменты произнесения: капшен вспыхивает ровно на слове,
    а не «где-то в предложении». block_NN.json пишет build_voice, времена
    в нём — от начала блока, поэтому блоки складываются через длительности
    их mp3 — тем же способом, каким assets.py собирал marks.json.
    """
    words, offset = [], 0.0
    for i in range(1, len(job["script_blocks"]) + 1):
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
    return words or None


def words_from_marks(marks):
    """
    Запасной путь без посимвольных тайм-кодов (синтетика): время
    предложения раздаётся словам пропорционально их длине. На тишине
    mock.py этого достаточно — проверяется механика, а не попадание в
    голос.
    """
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


# ─────────────────────── ВЫБОР ТРЁХ ОКОН ───────────────────────

def window_from(marks, idx, total):
    """Окно от начала предложения idx: целые предложения, до HARD_MAX."""
    t0 = marks[idx]["start"]
    j = idx
    while (j + 1 < len(marks)
           and marks[j + 1]["end"] - t0 <= min(HARD_MAX, TARGET_LEN + 8)):
        j += 1
        if marks[j]["end"] - t0 >= TARGET_LEN:
            break
    t1 = min(marks[j]["end"], t0 + HARD_MAX, total - 0.1)
    return round(t0, 3), round(max(t1, t0 + 1.0), 3)


def pick_windows(story, marks, total):
    """
    Три окна: hook, revelation, escalation. Каждое начинается с начала
    предложения — обрезанное первое слово читается как случайная вырезка.
    Окна не пересекаются; на коротком тестовом ролике, где не пересечься
    негде, ограничение снимается — там проверяется механика, не выпуск.
    """
    wins = []

    def overlaps(a0, a1):
        return any(a0 < w["t1"] + 2.0 and a1 > w["t0"] - 2.0 for w in wins)

    def add(idx, role, why):
        t0, t1 = window_from(marks, idx, total)
        if t1 - t0 < MIN_LEN:
            return False
        if overlaps(t0, t1):
            return False
        wins.append(dict(t0=t0, t1=t1, role=role, why=why))
        return True

    # 1. Хук — первое предложение. Всегда есть и всегда первым номером.
    add(0, "hook", "первые фразы ролика — готовый крючок сценария")

    # 2-3. Развязка по «цифровости», нагнетание по поворотным словам.
    # Ранги свои внутри вида, как везде в editorial: вопрос не «много ли
    # здесь цифр», а «больше ли, чем в остальных развязках ЭТОГО ролика».
    for kind, feat, why in (
            ("revelation", "num", "развязка с самыми плотными датами"),
            ("escalation", "turn", "нагнетание с поворотом — обрыв на "
                                   "самом интересном")):
        pool = sorted((b for b in story if b.kind == kind),
                      key=lambda b: b.features.get(feat, 0.0), reverse=True)
        for b in pool:
            if add(b.first_mark, kind, why):
                break

    # Недобор — добираем сильными долями любого вида, потом равными
    # шагами по таймлайну. Три файла на выходе — обещание конвейера.
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
        idx = bisect_left(starts, total * (0.08 + 0.045 * k))
        if idx < len(marks):
            add(idx, "extra", "добор равным шагом по таймлайну")
    while len(wins) < N_SHORTS:
        # совсем короткий ролик: окна совпадают, и это честнее, чем упасть
        w = dict(wins[len(wins) % max(len(wins), 1)])
        w["role"], w["why"] = "extra", "ролик короче трёх окон, окно повторено"
        wins.append(w)

    wins.sort(key=lambda w: {"hook": 0, "revelation": 1,
                             "escalation": 2}.get(w["role"], 3))
    return wins[:N_SHORTS]


# ─────────────────────── ВЕРТИКАЛЬНЫЙ ВИДЕОРЯД ───────────────────────

# Ходы по вертикальному холсту. Амплитуды крупнее, чем в длинном ролике:
# шортс смотрят на телефоне с руки, и медленный дрейф там читается как
# статика. w0/w1 — множители ширины кропа к ширине холста.
V_MOVES = {
    "punch_in":   dict(w0=1.32, w1=1.10, x0=.50, x1=.50, y0=.44, y1=.42),
    "punch_out":  dict(w0=1.10, w1=1.32, x0=.50, x1=.50, y0=.42, y1=.46),
    "sweep_left": dict(w0=1.18, w1=1.18, x0=.82, x1=.18, y0=.45, y1=.45),
    "sweep_right": dict(w0=1.18, w1=1.18, x0=.18, x1=.82, y0=.45, y1=.45),
    "rise":       dict(w0=1.16, w1=1.16, x0=.50, x1=.50, y0=.75, y1=.25),
    "dive":       dict(w0=1.22, w1=1.12, x0=.42, x1=.58, y0=.30, y1=.60),
}


def v_motion(move: str, dur: float) -> str:
    """Тот же приём, что в render.motion_filter: scale eval=frame + crop."""
    m = V_MOVES[move]
    p = render.ease_expr("smooth", dur)
    # холст масштабируется до w-кратной ширины кропа: w=1.18 значит
    # «вокруг окна 18% запаса на проезд». Холст всегда УМЕНЬШАЕТСЯ
    # (1440 -> максимум 1425), картинка остаётся резкой — тот же принцип,
    # что у горизонтального рендера.
    w0 = SW * m["w0"]
    w1 = SW * m["w1"]
    wexpr = f"trunc(({w0:.1f}+({w1 - w0:.1f})*{p})/2)*2"
    xexpr = f"(iw-{SW})*({m['x0']:.3f}+({m['x1'] - m['x0']:.3f})*{p})"
    yexpr = f"(ih-{SH})*({m['y0']:.3f}+({m['y1'] - m['y0']:.3f})*{p})"
    return (f"scale=w='{wexpr}':h=-2:eval=frame,"
            f"crop={SW}:{SH}:x='{xexpr}':y='{yexpr}',setsar=1")


def prepare_vertical(src: Path, dst: Path):
    """
    Вертикальный холст из исходника кадра — окно 9:16 из середины,
    приведённое к CANVAS. То же, что render.prepare_image, но для
    вертикали: резать из исходника, а не из готового 16:9, — это и есть
    разница между «снято под шортс» и «сплюснутый телевизор».
    """
    from PIL import Image
    im = Image.open(src).convert("RGB")
    iw, ih = im.size
    ar = CANVAS_W / CANVAS_H
    cw = min(iw, ih * ar)
    ch = cw / ar
    if ch > ih:
        ch = ih
        cw = ch * ar
    cw, ch = max(2, int(cw)), max(2, int(ch))
    cx = int((iw - cw) * 0.5)
    # окно чуть выше геометрического центра: у фото и генерации главное
    # почти всегда в верхних двух третях кадра
    cy = int((ih - ch) * 0.40)
    im.crop((cx, cy, cx + cw, cy + ch)).resize(
        (CANVAS_W, CANVAS_H), Image.LANCZOS).save(dst, quality=95)


def cut_plan(shots, t0, t1, rng):
    """
    Перерезка окна под темп ленты: 1.4-2.6 секунды на кадр.

    Исходник берётся у того кадра длинного ролика, который в эту секунду
    стоял на таймлайне: видеоряд остаётся рассказом под теми же словами,
    просто нож ходит чаще. Соседние куски из одного исходника склеиваются
    — тот же кадр дважды подряд с разным движением читается как заикание.
    """
    cuts, t = [], t0
    while t < t1 - 0.05:
        dur = min(rng.uniform(*CUT_RANGE), t1 - t)
        mid = t + dur / 2
        sh = next((s for s in shots
                   if s["start"] <= mid < s["start"] + s["duration"]),
                  shots[-1])
        if cuts and cuts[-1]["file"] == sh["file"] \
                and cuts[-1]["dur"] + dur <= CUT_MERGED_MAX:
            cuts[-1]["dur"] = round(cuts[-1]["dur"] + dur, 3)
        else:
            cuts.append(dict(file=sh["file"], kind=sh["kind"],
                             src_start=sh.get("src_start", 0.0)
                             + max(0.0, t - sh["start"]),
                             dur=round(dur, 3)))
        t += dur
    # последний кус дотягивается до конца окна точно
    drift = (t1 - t0) - sum(c["dur"] for c in cuts)
    if cuts:
        cuts[-1]["dur"] = round(cuts[-1]["dur"] + drift, 3)
    return cuts


def render_cut(c, out: Path, rng, canvas_cache, tmp: Path):
    """Один вертикальный кус: картинка едет ходом, видео кропится в 9:16."""
    src = Path(c["file"])
    if c["kind"] == "clip":
        vf = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,"
              f"crop={SW}:{SH},fps={SFPS},setsar=1")
        run(f"ffmpeg -y -stream_loop -1 -ss {float(c['src_start']):.2f} "
            f"-i {shlex.quote(str(src))} -vf {shlex.quote(vf)} "
            f"-t {c['dur']:.3f} -c:v libx264 -crf 19 -preset veryfast "
            f"-pix_fmt yuv420p -an {shlex.quote(str(out))}")
        return
    canvas = canvas_cache.get(src)
    if canvas is None:
        canvas = tmp / f"v_{len(canvas_cache):03d}.jpg"
        prepare_vertical(src, canvas)
        canvas_cache[src] = canvas
    move = rng.choice(sorted(V_MOVES))
    vf = v_motion(move, c["dur"])
    run(f"ffmpeg -y -loop 1 -t {c['dur']:.3f} -r {SFPS} "
        f"-i {shlex.quote(str(canvas))} -vf {shlex.quote(vf)} "
        f"-c:v libx264 -crf 19 -preset veryfast -pix_fmt yuv420p -an "
        f"{shlex.quote(str(out))}")


# ─────────────────────── КАПШЕНЫ ───────────────────────

def _ass_t(sec: float) -> str:
    h, r = divmod(max(sec, 0.0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def write_ass(words, t0, dur, out: Path):
    """
    Капшены: ОДНО слово, белым, по центру, ровно на времени произнесения.

    Почему ASS, а не гора drawtext: полсотни слов = полсотни фильтров и
    команда длиной с главу; субтитровый файл читается одним фильтром и
    даёт анимацию появления (\\t по масштабу — слово «вспыхивает», а не
    возникает). Слово держится до начала следующего: пустой экран между
    словами дёргает сильнее, чем слово-долгожитель.

    Цифры и даты — крупнее: это те самые «2600 BC», ради которых шортс
    вообще нарезан из этого места.
    """
    head = (
        "[Script Info]\n"
        f"PlayResX: {SW}\nPlayResY: {SH}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
        " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
        " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Word,DejaVu Sans,132,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H78000000,-1,0,0,0,100,100,1,0,1,11,4,5,60,60,0,1\n"
        "Style: Cta,DejaVu Sans,64,&H00E6EFF3,&H00FFFFFF,&H00000000,"
        "&H78000000,-1,0,0,0,100,100,2,0,1,8,3,5,60,60,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR,"
        " MarginV, Effect, Text\n")
    rows = []
    inside = [w for w in words if t0 <= w["start"] < t0 + dur - 0.15]
    for k, w in enumerate(inside):
        s = w["start"] - t0
        nxt = (inside[k + 1]["start"] - t0 if k + 1 < len(inside)
               else min(w["end"] - t0 + 0.8, dur))
        e = max(min(nxt, w["end"] - t0 + 0.8, dur), s + 0.12)
        txt = _ass_esc(w["text"].strip().upper())
        if not txt:
            continue
        fs = ""
        if any(ch.isdigit() for ch in txt):
            fs = r"\fs188"          # даты и суммы — главные слова шортса
        elif len(txt) > 12:
            fs = r"\fs104"          # длинное слово не должно упереться в края
        rows.append(
            f"Dialogue: 0,{_ass_t(s)},{_ass_t(e)},Word,,0,0,0,,"
            "{\\pos(540,1160)" + fs +
            "\\fscx78\\fscy78\\t(0,90,\\fscx100\\fscy100)}" + txt)
    # стрелка на полный ролик — последние секунды
    rows.append(
        f"Dialogue: 1,{_ass_t(max(dur - CTA_SECONDS, 0))},{_ass_t(dur)},"
        "Cta,,0,0,0,,{\\pos(540,1560)\\fad(250,0)}" + _ass_esc(CTA_TEXT))
    out.write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    return len(inside)


# ─────────────────────── СБОРКА ОДНОГО ШОРТСА ───────────────────────

def render_short(n, win, shots, words, final: Path, sdir: Path, seed: str):
    rng = random.Random(f"{seed}-short-{n}")
    t0, t1 = win["t0"], win["t1"]
    dur = round(t1 - t0, 3)
    tmp = sdir / f"tmp_{n}"
    tmp.mkdir(parents=True, exist_ok=True)

    cuts = cut_plan(shots, t0, t1, rng)
    canvas_cache = {}
    segs = []
    for ci, c in enumerate(cuts):
        seg = tmp / f"cut_{ci:03d}.mp4"
        render_cut(c, seg, rng, canvas_cache, tmp)
        segs.append(seg)
    body = tmp / "body.mp4"
    lst = tmp / "concat.txt"
    lst.write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
    run(f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(str(lst))} "
        f"-c copy {shlex.quote(str(body))}")

    ass = tmp / "captions.ass"
    n_words = write_ass(words, t0, dur, ass)

    # Звук — из ГОТОВОГО ролика: голос, подложка, дакинг и громкость уже
    # сведены и отслушаны, второй раз сводить то же самое — способ
    # получить другой результат.
    out = sdir / f"short_{n}.mp4"
    fade_st = max(dur - 0.35, 0.0)
    filt = (f"[0:v]ass={ass}:fontsdir=/usr/share/fonts/truetype/dejavu,"
            f"fade=t=in:d=0.15,fade=t=out:st={fade_st:.2f}:d=0.35[v];"
            f"[1:a]afade=t=out:st={fade_st:.2f}:d=0.35[a]")
    run(f"ffmpeg -y -i {shlex.quote(str(body))} "
        f"-ss {t0:.3f} -t {dur:.3f} -i {shlex.quote(str(final))} "
        f"-filter_complex {shlex.quote(filt)} "
        f"-map [v] -map [a] -t {dur:.3f} "
        f"-c:v libx264 -crf 20 -preset veryfast -pix_fmt yuv420p "
        f"-c:a aac -b:a 192k -movflags +faststart {shlex.quote(str(out))}")
    shutil.rmtree(tmp, ignore_errors=True)

    log(f"  short_{n}: {win['role']:<10} {t0/60:5.1f}-{t1/60:5.1f} мин, "
        f"{dur:.0f} с, {len(cuts)} кадров, {n_words} слов — {win['why']}")
    return dict(file=out.name, role=win["role"], t0=t0, t1=t1,
                seconds=dur, cuts=len(cuts), words=n_words, why=win["why"])


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    base = Path("work") / job["id"]
    assets, out = base / "assets", base / "out"
    final = out / "final.mp4"
    shots_p = out / "shots.json"
    for f in (final, shots_p, assets / "marks.json"):
        if not f.exists():
            raise SystemExit(f"нет {f} — сначала собери ролик: "
                             f"python pipeline/build.py {job_path}")

    marks = json.loads((assets / "marks.json").read_text())
    total = json.loads((assets / "state.json").read_text())["total_audio"]
    total = min(total, render.duration_of(final, "v") or total)

    # план кадров: shots.json писан ДО рендера, в поле file — исходники.
    # Все значения там строковые (см. build.py), числа возвращаем на место.
    shots = []
    for s in json.loads(shots_p.read_text()):
        shots.append(dict(file=s["file"], kind=s["kind"],
                          start=float(s["start"]),
                          duration=float(s["duration"]),
                          src_start=float(s.get("src_start") or 0.0)))

    story = beats_mod.analyze(marks, job["script_blocks"], total)
    words = words_from_alignment(job, assets / "voice")
    if words:
        log(f"слова: {len(words)} по посимвольным тайм-кодам ElevenLabs")
    else:
        words = words_from_marks(marks)
        log(f"слова: {len(words)} раскиданы по длине (посимвольных "
            f"тайм-кодов нет — синтетика?)")

    wins = pick_windows(story, marks, total)
    sdir = out / "shorts"
    sdir.mkdir(parents=True, exist_ok=True)

    log(f"── шортсы: {len(wins)} окна")
    meta = []
    for n, win in enumerate(wins, 1):
        meta.append(render_short(n, win, shots, words, final, sdir,
                                 job["id"]))

    y = job.get("youtube") or {}
    (sdir / "shorts.json").write_text(json.dumps(dict(
        source=final.name, title=y.get("title", ""), shorts=meta),
        indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"готово: {sdir}/short_1..{len(meta)}.mp4 + shorts.json")


if __name__ == "__main__":
    main(sys.argv[1])
