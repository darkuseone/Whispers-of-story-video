"""
overlays.py — генерирует зацикленные слои поверх кадра.

Запускается ОДИН раз, результат лежит в репозитории:

    python pipeline/overlays.py

При монтаже робот просто накладывает готовый файл, а не считает частицы
заново — иначе рендер вырос бы вдвое.

Четыре семьи, по три варианта плотности в каждой:

  motes   пылинки в луче света, медленно оседают. Основной слой канала:
          самый незаметный и работает под что угодно
  stars   звёздное поле с медленным горизонтальным дрейфом
  sparks  угли от факела, летят вверх. Единственный ТЁПЛЫЙ слой набора,
          поэтому идёт редко и слабо: в холодном ролике тёплое пятно
          работает как акцент, а не как фон
  mist    холодная дымка, ползёт вбок

Скорости у первых трёх втрое ниже, чем были у канала о находках (16-34
пикселя в секунду против 80-120): угли, летящие вверх со скоростью углей,
уместны над костром, а те же пылинки на той же скорости — это метель.

Яркость печётся здесь на полную, а СИЛА наложения задаётся отдельно при
склейке (overlay_opacity в style.py). Так у плотности и у прозрачности по
одной ручке на каждую, а не одна перемноженная на другую.
"""

import math
import random
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# Частоту берём из render: слой искр обязан идти с той же частотой,
# что и кадр, иначе ffmpeg будет дублировать кадры при наложении
# и ровное движение начнёт подрагивать.
import sys
sys.path.insert(0, str(Path(__file__).parent))
from render import W, H, FPS

# Три варианта плотности на выбор движка стиля
# Считаны с запасом: период оборота больше высоты кадра, поэтому
# часть частиц в каждый момент находится за экраном.
SPARK_COUNTS = (34, 46, 58)
MOTE_COUNTS = (70, 110, 150)
STAR_COUNTS = (120, 190, 260)


def make_sparks(out: Path, seconds=20, count=30, seed=1,
                size_range=(1.0, 3.0), px_sec=(80.0, 120.0),
                flicker=(4.0, 8.0)):
    """
    Угли, летящие вверх — как над костром. Быстрый подъём, лёгкий боковой
    снос, частое мерцание. Накладывается режимом screen.

    Скорость задаётся В ПИКСЕЛЯХ В СЕКУНДУ и проверяется линейкой: искра
    при 100 px/s пересекает кадр примерно за одиннадцать секунд.

    ПЕТЛЯ БЕСШОВНА ПО ПОСТРОЕНИЮ, это здесь принципиально и легко сломать.
    Каждая искра оборачивается по СВОЕМУ периоду period = v * seconds / k,
    где k целое. Тогда за длину петли она проходит ровно k периодов и
    возвращается в исходную точку. Период всегда берётся не меньше высоты
    кадра с запасом, поэтому сам момент оборота происходит за экраном и
    зритель его не видит. Боковой снос и мерцание — синусы с ЦЕЛЫМ числом
    периодов за петлю, они замыкаются по той же причине.

    Яркость печатается на полную: сила наложения живёт отдельно, в
    overlay_opacity, чтобы ручка была одна, а не две перемноженные.
    """
    rng = random.Random(seed)
    frames = int(round(seconds * FPS))
    margin = 40
    min_period = H + 2 * margin
    tmp = out.parent / f"_sp_{seed}"
    tmp.mkdir(parents=True, exist_ok=True)

    parts = []
    for _ in range(count):
        v = rng.uniform(*px_sec)                 # пикселей в секунду, вверх
        # k — сколько раз искра обернётся за петлю. Берём floor, чтобы период
        # не оказался короче кадра: иначе оборот было бы видно прямо на экране.
        k = max(1, int(v * seconds / min_period))
        period = v * seconds / k
        # мерцание: радианы в секунду -> целое число периодов за петлю
        cycles = max(1, round(rng.uniform(*flicker) * seconds / math.tau))
        parts.append(dict(
            x0=rng.uniform(0, W), y0=rng.uniform(0, period),
            vy=-v / FPS,                         # пикселей за кадр
            period=period,
            amp=rng.uniform(5, 26),              # боковой снос
            cyc=rng.choice([1, 2, 3]),           # периодов сноса за петлю
            fl=cycles,                           # периодов мерцания за петлю
            ph=rng.uniform(0, math.tau),
            r=rng.uniform(*size_range),
            warm=rng.random() < 0.78,            # угли в основном тёплые
        ))

    for f in range(frames):
        u = f / frames                           # 0..1 по петле
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        for p in parts:
            y = (p["y0"] + p["vy"] * f) % p["period"] - margin
            x = p["x0"] + p["amp"] * math.sin(math.tau * p["cyc"] * u + p["ph"])
            a = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(math.tau * p["fl"] * u + p["ph"]))
            v = int(255 * a)
            col = (v, int(v * 0.60), int(v * 0.22)) if p["warm"] else \
                  (int(v * 0.72), int(v * 0.82), v)
            r = p["r"]
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        img.filter(ImageFilter.GaussianBlur(0.9)).save(tmp / f"{f:05d}.png")

    subprocess.run(
        f"ffmpeg -y -framerate {FPS} -i {tmp}/%05d.png -c:v libx264 -crf 26 "
        f"-preset veryfast -pix_fmt yuv420p {out}",
        shell=True, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()


def make_motes(out: Path, seconds=20, count=110, seed=1,
               size_range=(1.0, 2.6), px_sec=(16.0, 34.0),
               flicker=(0.7, 2.2)):
    """
    Пылинки в луче света. Оседают ВНИЗ, медленно, с боковым покачиванием.

    Основной слой канала. Работает он не как украшение, а как воздух:
    кадр без единой движущейся точки на тридцати секундах читается как
    фотография, кадр с оседающей пылью — как снятое пространство. На
    статичном изображении это единственное, что живёт само по себе.

    ГЛУБИНА. Треть пылинок делается крупнее и заметно тусклее — они как
    бы ближе к камере и вне фокуса. Без этого поле читается как
    равномерная сетка точек, то есть как дефект кодирования.

    ПЕТЛЯ БЕСШОВНА ПО ПОСТРОЕНИЮ, как и у искр: каждая частица проходит за
    длину петли ЦЕЛОЕ число своих периодов, покачивание и мерцание —
    целое число синусов. Подгонять на глаз нельзя, рывок раз в двадцать
    секунд виден на любом ролике.
    """
    rng = random.Random(1000 + seed)
    frames = int(round(seconds * FPS))
    margin = 40
    min_period = H + 2 * margin

    # МЕДЛЕННАЯ ПЫЛЬ ТРЕБУЕТ ДЛИННОЙ ПЕТЛИ, и это не настройка, а
    # арифметика. Чтобы частица прошла кадр целиком и петля при этом
    # сомкнулась, за петлю она обязана пройти ЦЕЛОЕ число полос высотой
    # min_period. При петле в двадцать секунд это минимум 58 px/s, а
    # заказаны были 16-34: `int(v * seconds / min_period)` давал ноль,
    # k зажимался в единицу, period выходил 320-680 пикселей вместо 1160,
    # и вся пыль жила в верхней трети кадра, перескакивая на середине.
    # Нижние две трети оставались пустыми.
    #
    # Формула та же, что у искр, — но искры летят 80-120 px/s и проходят
    # 1600-2400 пикселей за петлю, поэтому у них это никогда не всплывало.
    v_min = min_period / seconds
    lo, hi = px_sec
    if hi < v_min:
        raise ValueError(
            f"пыль {lo}-{hi} px/s не укладывается в петлю {seconds} с: "
            f"чтобы пройти кадр целиком, нужно от {v_min:.0f} px/s. "
            f"Либо ускорить пыль, либо удлинить петлю (см. LOOP_SECONDS).")
    lo = max(lo, v_min)

    tmp = out.parent / f"_mo_{seed}"
    tmp.mkdir(parents=True, exist_ok=True)

    parts = []
    for i in range(count):
        v = rng.uniform(lo, hi)
        k = int(v * seconds / min_period)        # >= 1 по построению
        period = v * seconds / k
        assert period >= min_period - 1e-6, (period, min_period)
        cycles = max(1, round(rng.uniform(*flicker) * seconds / math.tau))
        near = i % 3 == 0                      # ближний план, вне фокуса
        parts.append(dict(
            x0=rng.uniform(0, W), y0=rng.uniform(0, period),
            vy=v / FPS,                        # ВНИЗ, в отличие от искр
            period=period,
            amp=rng.uniform(8, 34),
            cyc=rng.choice([1, 2]),
            fl=cycles,
            ph=rng.uniform(0, math.tau),
            r=rng.uniform(*size_range) * (2.2 if near else 1.0),
            dim=0.34 if near else 1.0,
        ))

    for f in range(frames):
        u = f / frames
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        for p in parts:
            y = (p["y0"] + p["vy"] * f) % p["period"] - margin
            x = p["x0"] + p["amp"] * math.sin(math.tau * p["cyc"] * u + p["ph"])
            a = (0.42 + 0.58 * (0.5 + 0.5 * math.sin(
                math.tau * p["fl"] * u + p["ph"]))) * p["dim"]
            v = int(255 * a)
            # холодная пыль: синий впереди, красный приспущен
            col = (int(v * 0.80), int(v * 0.90), v)
            r = p["r"]
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        img.filter(ImageFilter.GaussianBlur(1.1)).save(tmp / f"{f:05d}.png")

    _encode(tmp, out)


def make_stars(out: Path, seconds=20, count=190, seed=1, size_range=(0.7, 2.0)):
    """
    Звёздное поле. Позиции стоят, поле дышит и чуть покачивается.

    ЗВЁЗДЫ НЕ ЕДУТ ЛИНЕЙНО, и это не лень. Чтобы линейный дрейф замкнулся
    на петле в двадцать секунд, поле обязано пройти ровно ширину кадра —
    то есть 96 пикселей в секунду. Это скорость поезда, а не неба.
    Настоящее небо за двадцать секунд не сдвигается вообще, поэтому
    движение здесь только покачивание на замкнутых синусах и мерцание, а
    ощущение хода даёт движение камеры по самому кадру.

    Мерцают звёзды с разной частотой и в разной фазе — иначе поле
    пульсирует целиком и читается как мигающая гирлянда.
    """
    rng = random.Random(2000 + seed)
    frames = int(round(seconds * FPS))
    tmp = out.parent / f"_st_{seed}"
    tmp.mkdir(parents=True, exist_ok=True)

    parts = []
    for i in range(count):
        parts.append(dict(
            x=rng.uniform(0, W), y=rng.uniform(0, H),
            amp=rng.uniform(2.0, 9.0),
            cyc=rng.choice([1, 1, 2]),
            fl=rng.choice([1, 2, 3, 4, 5]),
            ph=rng.uniform(0, math.tau),
            r=rng.uniform(*size_range),
            base=rng.uniform(0.25, 0.85),
            warm=rng.random() < 0.12,          # редкая тёплая звезда
        ))

    for f in range(frames):
        u = f / frames
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        for p in parts:
            x = p["x"] + p["amp"] * math.sin(math.tau * p["cyc"] * u + p["ph"])
            y = p["y"] + p["amp"] * 0.4 * math.cos(math.tau * p["cyc"] * u + p["ph"])
            a = p["base"] * (0.62 + 0.38 * (0.5 + 0.5 * math.sin(
                math.tau * p["fl"] * u + p["ph"])))
            v = int(255 * min(1.0, a))
            col = (v, int(v * 0.88), int(v * 0.70)) if p["warm"] else \
                  (int(v * 0.78), int(v * 0.86), v)
            r = p["r"]
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        img.filter(ImageFilter.GaussianBlur(0.8)).save(tmp / f"{f:05d}.png")

    _encode(tmp, out)


def _encode(tmp: Path, out: Path, crf=26):
    """Кадры -> mp4 и уборка. Общая часть всех генераторов."""
    subprocess.run(
        f"ffmpeg -y -framerate {FPS} -i {tmp}/%05d.png -c:v libx264 -crf {crf} "
        f"-preset veryfast -pix_fmt yuv420p {out}",
        shell=True, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()


# Дымка — размытое пятно без единой резкой детали, поэтому считается в
# уменьшенном разрешении, а до 1920x1080 её растягивает уже ffmpeg при
# склейке. На глаз отличий нет, а генерация быстрее примерно в девять раз:
# в полном разрешении она одна съедала половину времени сборки ролика.
HAZE_W, HAZE_H = 640, 360


def make_mist(out: Path, seconds=20, seed=1, opacity=0.5):
    """
    Холодная дымка — одна из четырёх семей слоёв канала.

    На канале о находках дымку убрали: там она мылила тёплую предметную
    картинку, где важна фактура вещи. Здесь наоборот — воздух между
    камерой и руинами это и есть содержание кадра, и слой возвращён, но
    холодным и вдвое слабее прежнего.
    """
    return make_haze(out, seconds=seconds, seed=seed, opacity=opacity)


def make_haze(out: Path, seconds=20, seed=1, opacity=0.6):
    """
    Ползущая дымка. Плавный шум, растянутый и размытый.

    Считается в уменьшенном разрешении: до 1920x1080 её растягивает уже
    ffmpeg при склейке. На глаз отличий нет, а генерация быстрее примерно
    в девять раз — в полном разрешении она одна съедала половину времени
    сборки ролика.
    """
    rng = np.random.default_rng(seed)
    frames = seconds * FPS
    w, h = HAZE_W, HAZE_H
    tmp = out.parent / f"_hz_{seed}"
    tmp.mkdir(parents=True, exist_ok=True)

    # три слоя шума разной крупности, чтобы не читался как рябь
    base = [rng.random((h // 14 + 2, w // 14 + 2)) for _ in range(3)]

    for f in range(frames):
        t = f / frames
        acc = np.zeros((h, w), np.float32)
        for i, b in enumerate(base):
            # циклический сдвиг: на стыке петли картинка совпадает сама с собой
            sh = int(t * b.shape[1])
            rolled = np.roll(b, sh + i * 3, axis=1)
            up = np.asarray(Image.fromarray((rolled * 255).astype(np.uint8))
                            .resize((w, h), Image.BICUBIC), np.float32) / 255
            acc += up * (0.5 ** i)
        acc /= acc.max()
        # дымка гуще внизу кадра
        grad = np.linspace(0.55, 1.35, h, dtype=np.float32)[:, None]
        acc = np.clip(acc * grad, 0, 1)
        px = (acc * 255 * opacity).astype(np.uint8)
        rgb = np.dstack([(px * 0.80).astype(np.uint8),
                         (px * 0.90).astype(np.uint8),
                         px])                      # синеватая дымка
        Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(3)) \
             .save(tmp / f"{f:05d}.png")

    subprocess.run(
        f"ffmpeg -y -framerate {FPS} -i {tmp}/%05d.png -c:v libx264 -crf 28 "
        f"-preset veryfast -pix_fmt yuv420p {out}",
        shell=True, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()


# Кто чем считается. Отсюда же берёт build.ensure_overlays, когда файла
# нет на диске: генерация трёх вариантов занимает минуты, и платить их
# каждым прогоном не за что — но и падать из-за отсутствующего слоя
# нельзя.
MAKERS = {
    "motes": (make_motes, MOTE_COUNTS),
    "stars": (make_stars, STAR_COUNTS),
    "sparks": (make_sparks, SPARK_COUNTS),
    "mist": (None, (None, None, None)),      # у дымки плотности нет
}


# Длина петли своя у каждой семьи, и у пыли она втрое длиннее прочих.
# Причина арифметическая, не вкусовая: пыль оседает медленно, а петля
# смыкается только если частица проходит за неё целое число высот кадра.
# Двадцать секунд требуют от пыли 58 px/s — это уже не оседание, а
# падение. Шестьдесят секунд опускают порог до 20 px/s, то есть до
# заказанной скорости. Подробности — в make_motes.
#
# Побочная выгода: на сорокапятиминутном ролике двадцатисекундная петля
# повторяется полторы сотни раз, шестидесятисекундная — сорок пять.
LOOP_SECONDS = {"motes": 60, "stars": 20, "sparks": 20, "mist": 20}


def make(kind: str, variant: int, out: Path, seconds=None):
    """Один слой по имени семьи и номеру варианта."""
    seconds = seconds or LOOP_SECONDS.get(kind, 20)
    if kind == "mist":
        return make_mist(out, seconds=seconds, seed=variant,
                         opacity=(0.38, 0.50, 0.62)[max(0, min(2, variant - 1))])
    fn, counts = MAKERS[kind]
    return fn(out, seconds=seconds, seed=variant,
              count=counts[max(0, min(len(counts) - 1, variant - 1))])


if __name__ == "__main__":
    out = Path(__file__).parent.parent / "assets" / "overlays"
    out.mkdir(parents=True, exist_ok=True)
    # варианты плотности — робот выбирает один на ролик
    for kind in ("motes", "stars", "sparks", "mist"):
        for variant in (1, 2, 3):
            dst = out / f"{kind}_{variant}.mp4"
            if dst.exists():
                print(f"{dst.name}: уже есть")
                continue
            make(kind, variant, dst)
            print(f"{dst.name}: {dst.stat().st_size // 1024} КБ")
