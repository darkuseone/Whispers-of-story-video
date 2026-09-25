"""
Бесплатная проверка материала шортса — глазами арифметики, а не модели.

Зачем модуль вообще есть. Шортс собирается из десятков кадров, и на
готовом ролике брак видно сразу, а в списке файлов — нет. На первом
прогоне letter-01 сквозь отбор прошли: расфокусированный кадр плёнки
(розовое пятно на весь экран), пустой лист бумаги без единой детали и
два ПОЧТИ ОДИНАКОВЫХ снимка подряд. Каждый из трёх стоил ролику
секунды, за которую зритель уходит.

Ни один из трёх не требует зрения модели — все три считаются по
пикселям и стоят ноль:

- РЕЗКОСТЬ  — разброс лапласиана. Расфокус даёт низкий разброс.
- НАПОЛНЕННОСТЬ — стандартное отклонение яркости. Пустая заливка,
  чистый лист, засвет: картинка есть, смотреть в ней нечего.
- ПОВТОР — перцептивный хэш (dhash). Соседние кадры, отличающиеся на
  несколько бит, зритель читает как один кадр, показанный дважды.

Проверяется КАДР, КОТОРЫЙ УВИДЯТ, а не файл: у клипа берётся кадр с
той самой секунды `at`, с которой его режет реел. Иначе проверка
хвалит начало плёнки, а в ролик уезжает муть с середины.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ПОРОГИ СТОЯТ В ИЗМЕРЕННОМ ЗАЗОРЕ, А НЕ НАЗНАЧЕНЫ НА ГЛАЗ. Замер по пулу
# letter-01 (85 фото + 27 кусков плёнки), резкость — по 75-му процентилю
# клеток:
#
#   плёнка:  настоящий расфокус 10.0 | портрет 15.9, 16.7 | дом 20.6,
#            трое в кадре 24.1, улица 24.9, человек у станка 26.8
#   фото:    пустой лист 15.2 | реестр имён 39.2, почерк крупно 68.9
#
# Между браком и худшим годным кадром зазор в полтора-два раза, порог
# ставится в его середину. Двигать порог, чтобы «пропустить вот этот
# файл», нельзя: у плёнки зазор всего 10 → 16, и любой сдвиг вверх
# начинает выбрасывать нормальные планы (так и вышло при пороге 18 —
# отбраковались три плана с говорящими людьми, лица в которых резкие).
# У ПЛЁНКИ И У СКАНА РАЗНАЯ НОРМА РЕЗКОСТИ, и одним числом их не накрыть.
# 16-мм плёнка мягкая по своей природе: зерно, оптика, перегон в цифру.
# Первый прогон с общим порогом 28 забраковал ВСЕ шесть кусков плёнки
# подряд, включая те, что на кадре читаются отлично, — а настоящий брак
# там был ровно один (розовый расфокус, 10). Порог для плёнки поэтому
# свой и ниже; поднимать его до фотографического — значит остаться без
# движущегося материала вовсе.
MIN_SHARP_IMAGE = 25.0
MIN_SHARP_CLIP = 13.0
MIN_FILL = 22.0
DUP_BITS = 8          # ближе этого по dhash — тот же кадр для зрителя
NEAR_WINDOW = 4       # в скольких соседних кадрах искать повтор


def _frame(path: Path, at: float, dst: Path) -> bool:
    """Кадр из файла на секунде at. Для картинки at игнорируется."""
    cmd = ["ffmpeg", "-v", "error"]
    if at > 0:
        cmd += ["-ss", f"{at}"]
    cmd += ["-i", str(path), "-frames:v", "1", "-vf", "scale=320:-2",
            str(dst), "-y"]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def measure(png: Path):
    """(резкость, наполненность, dhash) одного кадра."""
    from PIL import Image
    im = Image.open(png).convert("L")
    px = im.tobytes()
    w, h = im.size

    mean = sum(px) / len(px)
    fill = (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5

    # РЕЗКОСТЬ МЕРЯЕТСЯ ПО САМОМУ ЧЁТКОМУ УЧАСТКУ, А НЕ ПО ВСЕМУ КАДРУ.
    #
    # Разброс лапласиана по всей площади смешивает две разные вещи:
    # «кадр мягкий» и «в кадре много ровного». Портрет на однотонной
    # стене — это чёткое лицо и половина кадра пустого фона, и по
    # среднему он проваливается туда же, куда настоящий расфокус. Ровно
    # так и вышло: три плана с говорящими людьми (кадры крупные, лица
    # резкие) получили 11-14 при пороге 14, а рядом стоял действительно
    # мутный кусок плёнки с оценкой 10 — разделить их средним нельзя.
    #
    # Поэтому кадр бьётся на клетки, считается энергия в каждой, и берётся
    # ВЫСОКАЯ клетка (75-й процентиль). У расфокуса резкой клетки нет ни
    # одной, у портрета их несколько — и числа расходятся втрое.
    gx, gy = 4, 4
    cw, ch = w // gx, h // gy
    cells = []
    for ty in range(gy):
        for tx in range(gx):
            acc, n = 0.0, 0
            y0, y1 = max(1, ty * ch), min(h - 1, (ty + 1) * ch)
            x0, x1 = max(1, tx * cw), min(w - 1, (tx + 1) * cw)
            for y in range(y0, y1, 2):
                row = y * w
                for x in range(x0, x1, 2):
                    v = (4 * px[row + x] - px[row + x - 1] - px[row + x + 1]
                         - px[row - w + x] - px[row + w + x])
                    acc += v * v
                    n += 1
            if n:
                cells.append((acc / n) ** 0.5)
    cells.sort()
    sharp = cells[int(len(cells) * 0.75)] if cells else 0.0

    # dhash 8x8: сравнение соседей по строке, 64 бита.
    sm = im.resize((9, 8), Image.BILINEAR)
    sp = sm.tobytes()
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (1 if sp[y * 9 + x] > sp[y * 9 + x + 1] else 0)
    return sharp, fill, bits


def audit(spec_path: str, verbose: bool = True):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    tmp = ROOT / "work" / spec["work"] / "_check"
    tmp.mkdir(parents=True, exist_ok=True)

    rows, problems = [], []
    for i, sh in enumerate(spec["shots"]):
        src = ROOT / sh["file"]
        at = float(sh.get("at", 0.0))
        # у клипа проверяем середину показанного куска, а не первый кадр
        if sh.get("kind") == "clip":
            at += float(sh.get("seconds", 2.0)) / 2.0
        png = tmp / f"s{i:02d}.png"
        if not src.exists():
            problems.append(f"кадр {i}: файла нет — {sh['file']}")
            continue
        if not _frame(src, at, png):
            problems.append(f"кадр {i}: не открылся — {src.name}")
            continue
        sharp, fill, bits = measure(png)
        rows.append({"i": i, "name": src.name, "at": at, "sharp": sharp,
                     "fill": fill, "hash": bits,
                     "min_sharp": MIN_SHARP_CLIP if sh.get("kind") == "clip"
                     else MIN_SHARP_IMAGE})

    for r in rows:
        if r["sharp"] < r["min_sharp"]:
            problems.append(f"кадр {r['i']} ({r['name']}): расфокус, "
                            f"резкость {r['sharp']:.0f} < {r['min_sharp']:.0f}")
        # ПУСТО — ЭТО «БЛЕДНО И ВДОБАВОК БЕЗ ДЕТАЛИ», А НЕ ПРОСТО «БЛЕДНО».
        #
        # Одной наполненностью эти два случая не разделить, и это измерено,
        # а не предположено: пустой лист даёт 14, реестр имён — 16. Два
        # пункта разницы; любой порог между ними — подгонка под конкретный
        # файл. Зато по ПАРЕ величин они расходятся вдвое: у листа резкость
        # 11, у реестра 35. Скан документа низкоконтрастный по своей
        # природе (бумага и чернила рядом по яркости), но деталь в нём
        # есть — потому и читается на кадре.
        #
        # Отсюда правило: бледный кадр считается пустым только если он и по
        # своей резкости не проходит. Прогнать одну величину мимо другой
        # нельзя — вернётся либо выброшенный реестр, либо пустой лист в
        # ролике.
        if r["fill"] < MIN_FILL and r["sharp"] < r["min_sharp"]:
            problems.append(f"кадр {r['i']} ({r['name']}): пустой кадр, "
                            f"наполненность {r['fill']:.0f} < {MIN_FILL:.0f} "
                            f"при резкости {r['sharp']:.0f}")

    for a in range(len(rows)):
        for b in range(a + 1, min(a + 1 + NEAR_WINDOW, len(rows))):
            d = bin(rows[a]["hash"] ^ rows[b]["hash"]).count("1")
            if d <= DUP_BITS:
                problems.append(
                    f"кадры {rows[a]['i']} и {rows[b]['i']} "
                    f"({rows[a]['name']} / {rows[b]['name']}): "
                    f"один и тот же кадр для зрителя, расхождение {d} бит")

    if verbose:
        print(f"\n── проверка материала {spec['id']}: {len(rows)} кадров")
        for r in rows:
            flag = ""
            if r["sharp"] < r["min_sharp"]:
                flag += " ← расфокус"
            if r["fill"] < MIN_FILL and r["sharp"] < r["min_sharp"]:
                flag += " ← пусто"
            print(f"   {r['i']:2d}  резкость {r['sharp']:6.0f}   "
                  f"наполненность {r['fill']:5.1f}   {r['name']}{flag}")
        if problems:
            print(f"\n   ПРОБЛЕМЫ ({len(problems)}):")
            for p in problems:
                print(f"   ✗ {p}")
        else:
            print("\n   материал чистый")
    return problems


def rank(folder: str, top: int = 0):
    """Разложить папку с материалом по качеству — чем брать замену."""
    d = Path(folder)
    tmp = d.parent / "_check"
    tmp.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".mp4"):
            continue
        png = tmp / f"r_{f.stem}.png"
        at = 2.0 if f.suffix.lower() == ".mp4" else 0.0
        if not _frame(f, at, png):
            continue
        sharp, fill, bits = measure(png)
        out.append((sharp, fill, f.name))
    out.sort(reverse=True)
    for sharp, fill, name in (out[:top] if top else out):
        print(f"   резкость {sharp:6.0f}  наполненность {fill:5.1f}  {name}")
    return out





# ─────────────────── ПРОВЕРКА ГОТОВОГО РОЛИКА ───────────────────
#
# Всё, что ниже, — замеры на СОБРАННОМ файле, а не на материале. Нужны
# они потому, что каждый из этих дефектов проходит сборку с нулевым кодом
# возврата и виден только глазами на готовом ролике:
#
#   провал яркости на склейке — картинка «гаснет» на переходе с бумаги на
#     плёнку (замерено: 191 против 23, восьмикратно);
#   стоп-кадр — ход прописан, но не двигается (плёнка со статичной сценой,
#     ошибка в цепочке движения);
#   тихий звук — лента приводит соседние ролики к -14 LUFS, и пришедший
#     тише звучит выключенным (замерено: -27 dB против -14);
#   расхождение длины — mux -shortest молча режет ролик по короткой
#     дорожке, и хвост пропадает без единой ошибки в логе.

MAX_CUT_DROP = 70.0        # допустимый скачок яркости на склейке, пунктов
MIN_SHOT_MOTION = 6.0      # ниже — кадр стоит
TARGET_LUFS = -14.0
LUFS_TOLERANCE = 3.0


def _mean_luma(png: Path, band: tuple = None) -> float:
    from PIL import Image
    im = Image.open(png).convert("L")
    if band:
        im = im.crop(band)
    b = im.tobytes()
    return sum(b) / max(1, len(b))


def verify(spec_path: str, video: str = "", verbose: bool = True):
    """Замерить собранный ролик. Возвращает список проблем."""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    root = ROOT / "work" / spec["work"]
    vid = Path(video) if video else root / "out" / f"{spec['id']}.mp4"
    problems = []
    if not vid.exists():
        return [f"ролика нет: {vid}"]

    tmp = root / "_check"
    tmp.mkdir(parents=True, exist_ok=True)

    want = round(sum(float(s["seconds"]) for s in spec["shots"]), 2)
    got = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(vid)],
        capture_output=True, text=True).stdout.strip() or 0)
    if abs(got - want) > 0.35:
        problems.append(f"длина {got:.2f} с вместо {want:.2f} с "
                        f"— звук обрезал картинку или наоборот")

    # яркость и движение по кадрам: смотрим ОКНО (центральную полосу),
    # потому что тёмная размытая рамка вокруг плёнки поставлена намеренно
    # ПРОВЕРЯЕТСЯ КАДР, КОТОРЫЙ УВИДЯТ, А НЕ ФАЙЛ-ИСХОДНИК.
    #
    # audit() меряет исходник и потому быстрый — им отбирают материал. Но
    # в ролик попадает КРОП исходника с наездом, и пустым может оказаться
    # именно он: снимок реестра целиком полон текста (резкость 39), а
    # кадр ролика показывает его поле — на экране пустая бумага. Такой
    # кадр проходит отбор материала честно и всплывает только здесь.
    band = (0, 300, 1080, 1620)
    t, lums, still, weak = 0.0, [], [], []
    for i, sh in enumerate(spec["shots"]):
        d = float(sh["seconds"])
        # ТРИ ТОЧКИ, А НЕ ОДНА СЕРЕДИНА. Кадр с наездом бывает пустым в
        # НАЧАЛЕ и наполняется к концу: замер на 0.25 давал 27/31 и
        # проходил, а на 0.15 тот же кадр показывал 21/22 — пустую бумагу
        # ровно в момент склейки, то есть там, где её и видно.
        fr = []
        for frac in (0.15, 0.5, 0.85):
            png = tmp / f"v{i:02d}_{frac}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t + d * frac:.2f}",
                            "-i", str(vid), "-frames:v", "1", str(png), "-y"])
            fr.append(png)
        from PIL import Image
        lums.append(_mean_luma(fr[1], band))
        a = Image.open(fr[0]).convert("L").resize((120, 213)).tobytes()
        b = Image.open(fr[2]).convert("L").resize((120, 213)).tobytes()
        move = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        if move < MIN_SHOT_MOTION:
            still.append((i, Path(sh["file"]).name, move))
        lim = MIN_SHARP_CLIP if sh.get("kind") == "clip" else MIN_SHARP_IMAGE
        for k, f in enumerate(fr):
            crop = tmp / f"w{i:02d}_{k}.png"
            Image.open(f).convert("L").crop(band).save(crop)
            c_sharp, c_fill, _ = measure(crop)
            if c_fill < MIN_FILL and c_sharp < lim:
                weak.append((i, Path(sh["file"]).name, c_sharp, c_fill))
                break
        t += d

    for i, name, move in still:
        problems.append(f"кадр {i} ({name}): стоп-кадр, сдвиг {move:.1f}")
    for i, name, sh, fl in weak:
        problems.append(f"кадр {i} ({name}): НА ЭКРАНЕ пусто — "
                        f"резкость {sh:.0f}, наполненность {fl:.0f}")
    for i in range(len(lums) - 1):
        drop = abs(lums[i + 1] - lums[i])
        if drop > MAX_CUT_DROP:
            problems.append(f"склейка {i}→{i + 1}: скачок яркости {drop:.0f} "
                            f"пунктов ({lums[i]:.0f} → {lums[i + 1]:.0f})")

    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(vid),
                          "-af", "volumedetect", "-f", "null", "/dev/null"],
                         capture_output=True, text=True).stderr
    mean = None
    for line in out.splitlines():
        if "mean_volume:" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0])
    if mean is None:
        problems.append("звуковой дорожки нет вовсе")
    elif abs(mean - TARGET_LUFS) > LUFS_TOLERANCE + 3:
        problems.append(f"громкость {mean:.1f} dB далеко от {TARGET_LUFS} "
                        f"— в ленте ролик будет тише соседних")

    if verbose:
        print(f"\n── проверка ролика {spec['id']}")
        print(f"   длина {got:.2f} с (в спецификации {want:.2f})")
        print(f"   яркость окна {min(lums):.0f}-{max(lums):.0f}, "
              f"худший скачок на склейке "
              f"{max(abs(lums[i+1]-lums[i]) for i in range(len(lums)-1)):.0f}")
        print(f"   громкость {mean:.1f} dB")
        print(f"   стоп-кадров {len(still)} из {len(spec['shots'])}, "
              f"пустых на экране {len(weak)}")
        if problems:
            print(f"\n   ПРОБЛЕМЫ ({len(problems)}):")
            for p in problems:
                print(f"   ✗ {p}")
        else:
            print("\n   ролик чистый")
    return problems


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "rank":
        rank(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
    elif len(sys.argv) > 2 and sys.argv[1] == "verify":
        sys.exit(1 if verify(sys.argv[2]) else 0)
    elif len(sys.argv) > 2 and sys.argv[1] == "all":
        bad = audit(sys.argv[2]) + verify(sys.argv[2])
        sys.exit(1 if bad else 0)
    else:
        sys.exit(1 if audit(sys.argv[1]) else 0)
