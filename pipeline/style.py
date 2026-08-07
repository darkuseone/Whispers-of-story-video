"""
style.py — движок антишаблонности.

Что это такое
-------------
Стиль ролика — это ВЕКТОР из двадцати с лишним осей (editorial/variation.py).
Вектор бросается целиком, сравнивается с последними роликами канала и
пересдаётся, пока не разойдётся с ними минимум по четырём значимым осям
(editorial/memory.py). Ритм берётся от структуры сценария
(editorial/beats.py) и от выпавшей формы кривой (editorial/pacing.py), а не
от одной зашитой формулы: у формулы одна форма на все загрузки, и её видно
на графике длительностей, даже когда всё остальное разное.

Воспроизводимость: seed из id ролика. Тот же id даёт тот же ролик — это
нужно для отладки и, главное, для пересборки монтажа из кэша: иначе каждая
пересборка давала бы другой план и обесценивала готовые кадры.

Чем этот канал отличается от канала о находках
----------------------------------------------
Ancient Whispers — сорок-пятьдесят минут под сон. Отсюда всё остальное:

  ЦВЕТ ХОЛОДНЫЙ. Тёплая янтарная семья убрана целиком, см. make_luts.py.
  КАДР ДЕРЖИТСЯ ДОЛГО. База 9-16 секунд против 4-6, потолок кадра 42
    секунды против 24. Одна картинка на полминуты — это норма жанра, а не
    авария, но ТОЛЬКО с движением: статика на тридцать секунд убивает
    ролик надёжнее быстрой нарезки.
  ДВИЖЕНИЯ ДЛИННЫЕ. Добавлены проезды через весь кадр, расширения,
    псевдопараллакс и «плавание» — на кадре в 30 секунд обычный наезд
    заканчивается на десятой и дальше стоит.
  ПЕРЕХОДЫ ДЛИННЕЕ И МЯГЧЕ. Диапазон 0.9-2.6 секунды против 0.55-1.95,
    резких переходов в наборе нет вовсе.
  ДВЕ ФАЗЫ. Первые 3-5 минут — 70-80% видео короткими кусками; дальше
    70-80% изображений. Держит это MaterialMix в build.py по экранному
    ВРЕМЕНИ, а не по числу кадров.
"""

import random

from editorial import variation, memory

# Пул цветокоров по умолчанию: из него движок берёт случайный.
# Конкретный цвет ролика задаётся полем lut в спецификации — тогда жребий
# не бросается вовсе.
#
# Семья ХОЛОДНАЯ. Тёплая (warm_amber, oak_brass, lamp_glow, dust_gold,
# copper_dusk) делалась под канал о находках и здесь убрана целиком: два
# канала одного владельца зритель сравнивает первым делом, и одинаковый
# грейд — самое заметное, что между ними может совпасть.
# Таблицы считает pipeline/make_luts.py, там же описано, из чего состоит
# каждый оттенок.
LUTS = ["moonlit_marble", "nile_indigo", "aegean_teal", "temple_ash",
        "starlit_lapis", "bronze_frost"]

# Архивная семья — под подлинное фото, гравюры и музейную съёмку.
# Отличается от семьи канала не температурой (она тоже холодная), а
# плотностью: насыщенность ниже, контраст выше.
ARCHIVE_LUTS = ["archive_platinum", "archive_cyanotype", "archive_stone"]

# ─────────────────────────── ДВИЖЕНИЕ КАМЕРЫ ───────────────────────────
#
# w0/w1 — ширина виртуального холста в начале и конце (растёт = наезд,
# падает = отъезд). x/y — положение окна кадра 0..1. ease — форма кривой
# времени, см. EASINGS ниже.
#
# Холст 3000 пикселей (PREP_W в render.py), кадр 1920. Нижняя граница не
# опускается ниже 1920 — там кадр перестал бы помещаться, верхняя не
# поднимается выше 2900: наезд с амплитудой сверх этого упирается в холст
# и идёт на растяжение.
#
# ЧТО ЗДЕСЬ НОВОГО ПРОТИВ КАНАЛА О НАХОДКАХ. Там кадр жил 4-6 секунд, и
# любое движение успевало отработать. Здесь кадр живёт 10-40 секунд, и
# короткий ход заканчивается на первой трети — дальше зритель тридцать
# секунд смотрит стоп-кадр. Поэтому добавлены длинные ходы: проезд через
# весь кадр, расширение из детали в общий план, псевдопараллакс и
# «плавание» — почти неподвижный дрейф, который на полминуты читается как
# дыхание, а не как ошибка.
MOVES = {
    # ── короткие классические: вступление и кадры по 5-12 секунд ──
    "push_in":      dict(w0=2180, w1=2680, x0=0.50, x1=0.50, y0=0.50, y1=0.50),
    "pull_out":     dict(w0=2700, w1=2200, x0=0.50, x1=0.50, y0=0.50, y1=0.50),
    "pan_right":    dict(w0=2520, w1=2560, x0=0.06, x1=0.94, y0=0.50, y1=0.50),
    "pan_left":     dict(w0=2520, w1=2560, x0=0.94, x1=0.06, y0=0.50, y1=0.50),
    "tilt_down":    dict(w0=2560, w1=2600, x0=0.50, x1=0.50, y0=0.06, y1=0.94),
    "tilt_up":      dict(w0=2560, w1=2600, x0=0.50, x1=0.50, y0=0.94, y1=0.06),
    "push_left":    dict(w0=2200, w1=2700, x0=0.72, x1=0.28, y0=0.42, y1=0.58),
    "push_right":   dict(w0=2200, w1=2700, x0=0.28, x1=0.72, y0=0.58, y1=0.42),
    "sweep_in":     dict(w0=2240, w1=2700, x0=0.10, x1=0.62, y0=0.52, y1=0.46),
    "sweep_out":    dict(w0=2700, w1=2230, x0=0.66, x1=0.16, y0=0.44, y1=0.56),
    "drift_out":    dict(w0=2700, w1=2210, x0=0.36, x1=0.64, y0=0.64, y1=0.36),
    "hold_drift":   dict(w0=2300, w1=2440, x0=0.44, x1=0.56, y0=0.53, y1=0.47),

    # ── ДОЛГИЕ ПРОЕЗДЫ: через весь кадр, ход идёт всю длительность ──
    # Панорамы выше проходят 88% ширины на среднем зуме; эти идут по
    # ПОЧТИ полному запасу холста и с лёгким наездом по пути, поэтому на
    # кадре в тридцать секунд движение не кончается.
    "long_pan_right": dict(w0=2620, w1=2760, x0=0.02, x1=0.98, y0=0.46, y1=0.54,
                           ease="linear"),
    "long_pan_left":  dict(w0=2620, w1=2760, x0=0.98, x1=0.02, y0=0.54, y1=0.46,
                           ease="linear"),

    # Расширение: из детали в общий план. w1 не ниже 1920 — иначе crop
    # в render.py не может вырезать кадр (см. max(W,…) в motion_filter).
    "reveal_out":   dict(w0=2860, w1=2200, x0=0.42, x1=0.50, y0=0.44, y1=0.50,
                         ease="out"),
    # И обратный: из общего плана в деталь, медленно и до самого конца.
    "reveal_in":    dict(w0=2000, w1=2820, x0=0.50, x1=0.56, y0=0.50, y1=0.44,
                         ease="in"),

    # ПСЕВДОПАРАЛЛАКС. Настоящий параллакс требует разделения на слои —
    # этого у нас нет и не будет: разделение стоит либо денег (модель
    # глубины), либо времени (ручная маска). Здесь тот же эффект берётся
    # разной скоростью двух движений: холст наезжает, а окно кадра едет
    # ПРОТИВ наезда. Передний план в кадре при этом смещается заметно
    # быстрее заднего — ровно то, что глаз читает как параллакс.
    "parallax_left":  dict(w0=2180, w1=2760, x0=0.68, x1=0.24, y0=0.50, y1=0.50,
                           ease="linear"),
    "parallax_right": dict(w0=2180, w1=2760, x0=0.32, x1=0.76, y0=0.50, y1=0.50,
                           ease="linear"),

    # Диагонали: ход по обеим осям сразу, самый «живой» на статичном фото.
    "diag_down":    dict(w0=2300, w1=2700, x0=0.24, x1=0.72, y0=0.22, y1=0.74,
                         ease="linear"),
    "diag_up":      dict(w0=2700, w1=2320, x0=0.74, x1=0.26, y0=0.76, y1=0.24,
                         ease="linear"),

    # ПЛАВАНИЕ. Почти неподвижный кадр: 120 пикселей хода за всю
    # длительность. Нужен там, где важна сама картинка, а не движение по
    # ней, — но статики быть не должно нигде, и это компромисс.
    "float_left":   dict(w0=2500, w1=2560, x0=0.58, x1=0.42, y0=0.48, y1=0.52,
                         ease="linear"),
    "float_right":  dict(w0=2500, w1=2560, x0=0.42, x1=0.58, y0=0.52, y1=0.48,
                         ease="linear"),
    # Дыхание: наезд на два процента и обратно не сделать одной кривой,
    # поэтому это просто очень медленный наезд с длинным успокоением.
    "breathe_in":   dict(w0=2420, w1=2600, x0=0.50, x1=0.50, y0=0.52, y1=0.48,
                         ease="out"),

    # Оседание: быстрый заход и долгое успокоение. Единственный ход, где
    # движение заметно в начале и почти замирает к концу, — под кадры,
    # которые должны «встать» под важной фразой.
    "settle_in":    dict(w0=2240, w1=2640, x0=0.44, x1=0.52, y0=0.56, y1=0.48,
                         ease="out"),
}

# Формы кривой времени. Раньше была одна на все движения (ease-in-out), и
# это слышно так же, как одна форма арки темпа: любое движение канала
# начиналось и заканчивалось одинаково.
#
#   smooth  3p²−2p³, мягкий старт и мягкая остановка. Умолчание
#   linear  равномерно. Для долгих проездов единственно верный: у smooth
#           середина проезда идёт вдвое быстрее краёв, и на тридцати
#           секундах это видно как рывок посередине
#   out     быстрый заход, долгое успокоение
#   in      медленный заход, разгон к концу
EASINGS = ("smooth", "linear", "out", "in")

# Семьи движений. Нужны для двух вещей: чтобы ось motion_bias могла
# наклонить ролик в сторону проездов или расширений, и чтобы rails.py ловил
# три однотипных движения подряд — push_in и push_left это оба наезда, и
# зритель видит их как одно, как их ни называй.
MOVE_FAMILY = {
    "push": ["push_in", "push_left", "push_right", "settle_in", "breathe_in"],
    "pull": ["pull_out", "drift_out"],
    "pan": ["pan_right", "pan_left"],
    "tilt": ["tilt_down", "tilt_up"],
    "sweep": ["sweep_in", "sweep_out"],
    "travel": ["long_pan_right", "long_pan_left", "diag_down", "diag_up"],
    "reveal": ["reveal_out", "reveal_in"],
    "parallax": ["parallax_left", "parallax_right"],
    "float": ["float_left", "float_right", "hold_drift"],
}

# Наклон набора движений под ось motion_bias. Веса, а не жёсткие списки:
# ролик «с проездами» должен изредка давать наезд, иначе это не наклон,
# а новый шаблон.
MOTION_BIAS = {
    "travel":   dict(push=1.0, pull=0.9, pan=1.4, tilt=0.5, sweep=0.8,
                     travel=4.2, reveal=1.4, parallax=1.6, float=0.9),
    "reveal":   dict(push=1.2, pull=1.4, pan=0.8, tilt=0.6, sweep=0.8,
                     travel=1.4, reveal=4.0, parallax=1.2, float=1.0),
    "parallax": dict(push=1.0, pull=0.9, pan=1.0, tilt=0.5, sweep=1.0,
                     travel=1.6, reveal=1.2, parallax=4.0, float=1.0),
    "push":     dict(push=3.6, pull=1.4, pan=1.0, tilt=0.6, sweep=1.4,
                     travel=1.2, reveal=1.4, parallax=1.0, float=0.8),
    "mixed":    dict(push=1.4, pull=1.2, pan=1.3, tilt=0.8, sweep=1.1,
                     travel=1.8, reveal=1.5, parallax=1.4, float=1.2),
    "floating": dict(push=0.9, pull=0.9, pan=1.0, tilt=0.6, sweep=0.6,
                     travel=1.6, reveal=1.2, parallax=1.2, float=3.4),
}

# Движения, которые ИМЕЮТ СМЫСЛ на длинном кадре. На кадре в 25+ секунд
# обычный наезд отрабатывает за первую треть и дальше стоит — а стоп-кадр
# на двадцать секунд это ровно то, из-за чего такой ролик выключают.
# Набор ограничивается ПАРАМЕТРОМ pick_move(only=...), а не подменой
# результата снаружи: подмена ходит мимо счётчика семей, и проверка плана
# ловила на этом девять наездов подряд.
LONG_SHOT_MOVES = ["long_pan_right", "long_pan_left", "reveal_out",
                   "reveal_in", "parallax_left", "parallax_right",
                   "diag_down", "diag_up", "float_left", "float_right",
                   "breathe_in"]

# С этой длительности кадр считается долгим.
LONG_SHOT_SECONDS = 16.0

# Движения для фотографий во вступлении: только скольжение и наезд.
# Наклоны там читаются вяло, а статики быть не должно вовсе.
INTRO_MOVES = ["push_in", "pan_right", "pan_left", "sweep_in",
               "push_right", "push_left", "settle_in"]

# ─────────────────────────── ПЕРЕХОДЫ ───────────────────────────
#
# Все мягкие и тянутся во времени — резкой склейки в списке нет вовсе.
# Справа отмечено, какой пункт CapCut этим закрывается.
#
# Набор ШИРЕ, чем у канала о находках, и это осознанно: там переход между
# кадрами по пять секунд занимает 12% кадра и почти не читается, здесь на
# кадре в тридцать секунд переход — самостоятельное событие, которое
# зритель успевает рассмотреть. Значит и вариантов должно быть больше.
#
# fadeblack (CapCut «Black Fade») здесь ЕСТЬ, в отличие от соседнего
# канала. Там уход в чёрное посреди ролика читался как обрыв записи; на
# сорокаминутном ролике под сон это, наоборот, знак препинания — и он
# стоит только на границах глав, см. CHAPTER_TRANSITIONS.
TRANSITIONS = [
    "smoothleft",    # Wispy Wipe
    "smoothright",   # Wispy Wipe
    "smoothup",
    "smoothdown",
    "dissolve",      # Mix
    "hblur",         # Blur
    "circleopen",
    "circleclose",
    "fadegrays",
    "fade",          # Cross Fade
    "fadeslow",      # Slow Fade
    "slideleft",     # Passerby
    "slideright",    # Passerby
    "wipeleft",      # Shadow Sweep
    "radial",        # медленная стрелка часов, читается как «время идёт»
    "horzopen",      # раскрытие по горизонтали
    "vertopen",      # и по вертикали
    "diagtl",        # диагональ из угла
    "diagbr",
    "smoothdown",
]

# Переходы на ГРАНИЦАХ ГЛАВ. Смена главы — единственное место, где склейка
# имеет право быть заметной: это конец мысли, а не середина. Здесь идут
# самые долгие и самые «закрывающие» переходы, и только здесь разрешён
# уход в чёрное.
CHAPTER_TRANSITIONS = ["fadeblack", "fadeslow", "fade", "dissolve",
                       "fadegrays", "circleclose"]
CHAPTER_TRANSITION_DUR = (1.6, 2.8)

# ─────────────────────────── ЭФФЕКТЫ КАДРА ───────────────────────────
#
# Названия — из CapCut, цепочки собраны на фильтрах ffmpeg и являются
# ПРИБЛИЖЕНИЕМ, а не портированием: чужие пресеты закрыты, повторяется
# характер, а не точная кривая.
#
# Держатся намеренно слабыми. Поверх кадра дальше ещё лягут LUT, плёночная
# база и зерно; эффект в полную силу тут перебьёт весь цветокор ролика.
#
# Многоветочные цепочки (split/blend) допустимы: эффект всегда идёт
# ПОСЛЕДНИМ звеном фильтра, см. render.with_effect.
EFFECTS = {
    # ── перенесены с канала о находках, оставлены сознательно ──
    # выцветшая плёнка: поднятый чёрный, ушедшая насыщенность, крупная грязь
    "vintage_blemish": ("curves=r='0/0.06 0.5/0.52 1/0.95'"
                        ":g='0/0.05 0.5/0.50 1/0.92'"
                        ":b='0/0.04 0.5/0.47 1/0.88',"
                        "eq=saturation=0.72,noise=alls=14:allf=t"),
    # кассета: расслоение красного и синего плюс тяжёлые тени
    "vhs_dark": ("rgbashift=rh=-3:bh=3,eq=contrast=1.08:brightness=-0.04,"
                 "noise=alls=10:allf=t"),
    # почти монохром, уголь по бумаге
    "charcoal_film": "eq=saturation=0.22:contrast=1.10:gamma=0.94",
    # призматическая кайма по краям предметов
    "prism_1": "chromashift=cbh=-4:crh=4",
    # плотный техниколор: каналы разводятся друг от друга
    "technicolor_flash": ("colorchannelmixer=rr=1.10:rg=-0.06:gg=1.06"
                          ":gb=-0.06:bb=1.04:br=-0.04,"
                          "eq=saturation=1.14:contrast=1.05"),

    # ── НОВЫЕ, под холодный ночной канал ──
    # ЛУННОЕ СВЕЧЕНИЕ. Света расплываются мягким ореолом — так снимают
    # ночь, и так же выглядит подсвеченный камень. Считается через split:
    # одна ветка размывается сильно и накладывается обратно экраном.
    # Самый заметный эффект набора, поэтому вес у него небольшой.
    "moon_bloom": ("split=2[mb0][mb1];[mb1]gblur=sigma=22:steps=2,"
                   "eq=brightness=0.04[mbb];"
                   "[mb0][mbb]blend=all_mode=screen:all_opacity=0.26"),
    # ХОЛОДНАЯ ДЫМКА. Подъём чёрного в синий и падение контраста —
    # утренний туман над руинами, воздух между камерой и предметом.
    "cold_mist": ("curves=r='0/0.05 0.5/0.48 1/0.94'"
                  ":g='0/0.06 0.5/0.49 1/0.95'"
                  ":b='0/0.10 0.5/0.53 1/0.99',"
                  "eq=saturation=0.86:contrast=0.94"),
    # ГЛУБИНА ВОДЫ. Красный уводится, зелёный и синий вперёд — Эгейское
    # море, затопленные города, всё подводное.
    "deep_water": ("colorchannelmixer=rr=0.86:gg=1.02:bb=1.10:gr=0.04:br=0.06,"
                   "eq=saturation=1.08:contrast=1.03"),
    # ЛАЗУРИТ И ЗОЛОТО. Тень в синий, света в бледное золото. Египетская
    # пара цветов, единственный тёплый акцент в холодном наборе.
    "lapis_gold": ("curves=r='0/0.02 0.5/0.50 1/1.00'"
                   ":g='0/0.03 0.5/0.49 1/0.96'"
                   ":b='0/0.09 0.5/0.51 1/0.90',"
                   "eq=saturation=1.10:contrast=1.05"),
    # ВЫЖЖЕННЫЙ КАМЕНЬ. Обесцвеченная середина, жёсткие света — полуденная
    # пустыня, известняк, песчаник. Единственный эффект, ПОДНИМАЮЩИЙ
    # яркость: остальные её опускают, и без него набор был бы односторонним.
    "sun_bleached": ("eq=saturation=0.68:contrast=1.12:brightness=0.05,"
                     "curves=r='0/0.04 0.5/0.54 1/1.00'"
                     ":g='0/0.04 0.5/0.53 1/0.99'"
                     ":b='0/0.05 0.5/0.51 1/0.96'"),
    # ДЫХАНИЕ ОГНЯ. Медленная пульсация яркости — факел, жаровня, лампа.
    # Период втрое длиннее, чем был на канале о находках (0.55 Гц против
    # 1.6): на кадре в тридцать секунд быстрое мерцание раздражает, а
    # медленное укачивает. eval=frame обязателен, иначе выражение
    # посчитается один раз и пульсации не будет вовсе.
    "torchlight": ("colorbalance=rs=0.04:rm=0.05:bm=-0.03,"
                   "eq=brightness='0.028*sin(2*PI*t*0.55)':eval=frame"),
    # ЗВЁЗДНОЕ ДЫХАНИЕ. То же, но в холод и ещё медленнее: ночное небо,
    # вода под луной. Ходит контраст, а не яркость, — это тоньше.
    "night_swell": ("colorbalance=bs=0.05:bm=0.04:rm=-0.03,"
                    "eq=contrast='1+0.04*sin(2*PI*t*0.33)':eval=frame"),
    # МЯГКИЙ СОН. Лёгкая расфокусировка по всему кадру — под самые долгие
    # кадры финала. Собирается тем же split, что и moon_bloom, но
    # накладывается не экраном, а полупрозрачно: резкость падает, свет нет.
    "dream_soft": ("split=2[ds0][ds1];[ds1]gblur=sigma=9[dsb];"
                   "[ds0][dsb]blend=all_mode=average:all_opacity=0.42"),
    # ПАТИНА. Зелень окисленной бронзы в средних тонах, тени плотнее.
    "bronze_patina": ("colorchannelmixer=rr=0.94:gg=1.06:bb=0.98:gb=0.05,"
                      "eq=saturation=0.94:contrast=1.06:gamma=0.97"),
    # ПЫЛЬ ВРЕМЕНИ. Крупное мягкое зерно плюс просевшая насыщенность —
    # оцифровка старого слайда. Единственный эффект с шумом в новом
    # наборе: остальные чистые, зерно и так лежит поверх всего ролика.
    "time_dust": ("eq=saturation=0.80:contrast=1.04,"
                  "noise=alls=11:allf=t+u,gblur=sigma=0.4"),
}

# ВЕСА ЭФФЕКТОВ. Раньше эффект выбирался равновероятно, и самые заметные
# приёмы попадались так же часто, как самые тихие, — набор из двенадцати
# эффектов при равных весах даёт ролик, который каждые три кадра меняет
# характер картинки.
#
# Здесь тихие идут вчетверо чаще заметных. Это же разводит два канала:
# приёмы формально общие, а частота у них разная, и на глаз это разные
# наборы.
EFFECT_WEIGHTS = {
    "cold_mist": 3.4, "night_swell": 3.0, "time_dust": 2.6,
    "bronze_patina": 2.2, "deep_water": 2.0, "charcoal_film": 1.8,
    "lapis_gold": 1.8, "torchlight": 1.6, "sun_bleached": 1.4,
    "vintage_blemish": 1.2, "moon_bloom": 1.0, "dream_soft": 0.9,
    "technicolor_flash": 0.5, "vhs_dark": 0.35, "prism_1": 0.3,
}

# Кадрирование при повторном использовании одного изображения.
# Одна картинка показывается 2-3 раза, но каждый раз это другой кадр.
FRAMINGS = {
    "wide":         dict(scale=1.00, cx=0.50, cy=0.50),
    "tight_left":   dict(scale=1.35, cx=0.30, cy=0.45),
    "tight_right":  dict(scale=1.35, cx=0.70, cy=0.45),
    "detail_low":   dict(scale=1.60, cx=0.50, cy=0.70),
    "detail_high":  dict(scale=1.55, cx=0.45, cy=0.28),
    "medium":       dict(scale=1.18, cx=0.55, cy=0.50),
}

# Наклон кадрирования. Ролик «крупными планами» и ролик «общими» — это
# разный монтаж при одном и том же материале, и ось того стоит.
FRAMING_BIAS = {
    "wide_leaning":  {"wide": 3.0, "medium": 2.2, "tight_left": 1.0,
                      "tight_right": 1.0, "detail_low": 0.6, "detail_high": 0.6},
    "tight_leaning": {"wide": 0.8, "medium": 1.2, "tight_left": 2.4,
                      "tight_right": 2.4, "detail_low": 2.0, "detail_high": 2.0},
    "balanced":      {"wide": 1.6, "medium": 1.6, "tight_left": 1.4,
                      "tight_right": 1.4, "detail_low": 1.2, "detail_high": 1.2},
}

# Типы открытия. Первые пять секунд решают, останется зритель или нет.
#
#   cold_open       без разгона, сразу середина действия, самые короткие куски
#   long_establish  один длинный установочный кадр, потом перебивка
#   quick_cuts      предельно частая нарезка первые секунды
#   black_card      проявление из чёрного
#   slow_reveal     первый кадр медленный и длинный, дальше разгон
#   hard_in         открывает стоковое видео на полном темпе
#   starlit         из чёрного проявляется ОДИН долгий кадр с проездом, и
#                   только потом начинается перебивка — вариант, который
#                   есть только у этого канала
OPENINGS = ("cold_open", "long_establish", "quick_cuts", "black_card",
            "slow_reveal", "hard_in", "starlit")
OPENING_ALIASES = {"long_footage": "long_establish"}

# ─────────────────────────── СЛОИ ПОВЕРХ КАДРА ───────────────────────────
#
# Раньше слой был один — искры, и назывался он в коде sparks. Здесь их
# четыре семьи, потому что подпись канала не может стоять на каждом ролике
# подряд: приём, который виден всегда, перестаёт быть приёмом.
#
#   motes    пылинки в луче света, медленно оседают. Самый частый: он же
#            самый незаметный и работает под что угодно
#   stars    звёздное поле с медленным дрейфом. Под ночь и небо
#   sparks   угли от факела, летят вверх. Тёплые — единственное тёплое
#            пятно в холодном ролике, поэтому редко и слабо
#   mist     холодная дымка, ползёт вбок
#   none     слоя нет вовсе
OVERLAY_KINDS = ("none", "motes", "stars", "sparks")
OVERLAY_VARIANTS = (1, 2, 3)

# Предел множителя амплитуды камеры. Выше — рабочий холст 3000 пикселей
# перестаёт вмещать наезд, и картинка идёт на растяжение.
SPEED_CLAMP = (0.55, 1.45)


def seed_from(video_id: str) -> int:
    return variation.seed_from(video_id)


class StyleEngine:
    """
    Визуальный стиль ролика и параметры каждого кадра.

    Создаётся один раз на сборку. Всё, что он решил, лежит в .vector и
    уходит в монтажный лист: доказательство осмысленности решений строится
    из тех же чисел, по которым собран ролик.
    """

    def __init__(self, video_id: str, recent_luts=None, recent_openings=None,
                 recent_transitions=None, recent_overlays=None,
                 recent_beds=None, diverge=True, log=print):
        """
        recent_* — что использовалось в последних роликах канала. Списки
        приходят из channel.py и работают как жёсткий запрет: без них
        случайность регулярно выдаёт три похожих ролика подряд, а YouTube
        показывает соседние загрузки рядом.

        diverge=False выключает пересдачу вектора. Нужно ровно в одном
        месте — в тестах, где журнал канала трогать нельзя.
        """
        self.video_id = video_id
        self.log = log

        # ── ВЕКТОР СТИЛЯ ──────────────────────────────────────────────
        # Бросается целиком и разводится с историей канала: минимум четыре
        # значимо разошедшихся оси с каждым из последних роликов, иначе
        # пересдача. Подробности — editorial/memory.py.
        if diverge:
            self.vector, self.divergence = memory.draw_diverged(video_id,
                                                                log=log)
        else:
            self.vector = variation.draw(video_id, 0)
            self.divergence = dict(tries=1, history=0,
                                   note="разведение выключено")
        v = self.vector

        self.rng = random.Random(seed_from(video_id) + 17)
        r = self.rng

        recent_luts = list(recent_luts or [])[-3:]
        recent_openings = [OPENING_ALIASES.get(o, o)
                           for o in list(recent_openings or [])[-2:]]
        recent_transitions = list(recent_transitions or [])[-2:]
        recent_overlays = list(recent_overlays or [])[-2:]
        recent_beds = list(recent_beds or [])[-2:]

        lut_pool = [l for l in LUTS if l not in recent_luts] or LUTS
        self.lut = r.choice(lut_pool)
        # Архивный цветокор задаётся спецификацией; здесь только умолчание,
        # чтобы движок был работоспособен сам по себе.
        self.archive_lut = ARCHIVE_LUTS[0]

        # ДОЛЯ ГЕНЕРАЦИИ. Сколько экранного ВРЕМЕНИ (не кадров) отдаётся
        # сгенерированным изображениям; остальное — стоковое видео и
        # подлинные фото из архивов.
        #
        # 45% против 30% у канала о находках, и разница принципиальная.
        # Там сюжет был про КОНКРЕТНЫЙ предмет: вот эта монета, вот этот
        # сервиз, и рисунок выдавал бы себя за фотографию реальной вещи.
        # Здесь речь про мир, которого не сфотографировал никто: Атлантида,
        # Фивы при жизни, ночь перед битвой. Настоящего материала под это
        # не существует в природе — существуют руины и музейные витрины,
        # и одними ими сорок минут не закрыть. Реального при этом всё равно
        # больше половины: под каждый названный предмет и каждое место идёт
        # подлинное изображение, генерация закрывает атмосферу и то, чего
        # не сохранилось.
        self.generated_share = 0.45

        # ДОЛЯ ВИДЕО В ТЕЛЕ РОЛИКА, тоже по экранному времени. Во
        # вступлении своя, см. intro_clip_share.
        self.body_clip_share = round(v["body_clip_share"], 3)

        # Сжатие финального видео. Упирается в жёсткий лимит: файл в GitHub
        # Releases не может быть больше 2 ГБ, а сорокапятиминутный ролик в
        # 1080p/30 подходит к нему вплотную. Зерно плёнки — шум, и оно бьёт
        # по сжатию сильнее всего остального.
        self.crf = 22
        self.preset = "veryfast"

        # ── фактура: из вектора, а не отдельным жребием ───────────────
        self.grain = int(round(v["grain"]))
        self.vignette = round(v["vignette"], 2)

        # ── слой поверх кадра ─────────────────────────────────────────
        kind = v["overlay_kind"]
        if kind != "none" and kind in recent_overlays:
            pool = [k for k in OVERLAY_KINDS if k not in recent_overlays] \
                or list(OVERLAY_KINDS)
            kind = r.choice(pool)
        self.overlay_kind = kind
        self.overlay_enabled = kind != "none"
        self.overlay_variant = r.choice(OVERLAY_VARIANTS)
        self.overlay_opacity = round(v["overlay_opacity"], 3)
        self.overlay_flip = r.random() < 0.5

        # Скорость задаётся В ПИКСЕЛЯХ В СЕКУНДУ и печётся прямо в петлю.
        # Так её видно и можно проверить линейкой, а не через множитель
        # setpts, у которого физического смысла нет. Числа втрое ниже, чем
        # у канала о находках (там угли летели на 80-120 px/s): пылинки и
        # звёзды, идущие с той же скоростью, читаются как метель.
        self.overlay_speed_px_sec = (16.0, 34.0)
        self.overlay_flicker = (0.7, 2.2)
        self.overlay_size = (1.0, 2.6)

        # ── ритм: из вектора ──────────────────────────────────────────
        self.arc = v["arc"]
        self.base_dur = round(v["base_duration"], 3)
        self.shot_spread = round(v["shot_spread"], 3)
        self.breath_rate = round(v["breath_rate"], 3)
        self.motion_amp = round(v["motion_amp"], 3)
        self.motion_bias = v["motion_bias"]
        self.framing_bias = v["framing_bias"]
        # jitter и decel остаются как поля: на них ссылается старая
        # спецификация через REDRAW в build.py и запасной путь st.clip()
        self.jitter = self.shot_spread
        self.decel = 1.0 + (self.base_dur - 9.0) / 16.0

        # Потолок длительности кадра. У канала о находках он был 24
        # секунды; здесь одна картинка держится до сорока двух — это
        # заказано прямо: «одно изображение на 10-40 секунд можешь
        # задерживать с плавными эффектами». Держится только на движениях
        # из LONG_SHOT_MOVES, см. shot_for.
        self.max_shot_seconds = 42.0
        self.min_shot_seconds = 2.6

        # ── склейка ───────────────────────────────────────────────────
        self.transitions = list(dict.fromkeys(TRANSITIONS))
        tr_pool = [t for t in self.transitions
                   if t not in recent_transitions] or self.transitions
        self.main_tr = (v["main_transition"] if v["main_transition"] in tr_pool
                        else r.choice(tr_pool))
        # Насколько основной переход доминирует.
        self.transition_focus = round(v["transition_focus"], 3)
        self.tr_dur_range = tuple(v["transition_dur"])
        self.tr_dur = round(r.uniform(*self.tr_dur_range), 2)
        self.hard_cut_probability = 0.0
        # ЖЁСТКАЯ СКЛЕЙКА В НАГНЕТАНИИ. По умолчанию резких склеек в ролике
        # нет вовсе (hard_cut_probability выше) — на канале под сон это
        # правильно. Но сорок минут одних растворений сами становятся
        # шаблоном, и у монтажёра под нагнетанием нож работает иначе.
        # Поэтому в долях escalation и в микроритме tighten часть склеек
        # идёт встык: там резкость — это замысел, а не сбой. Вероятность
        # своя у каждого ролика, чтобы и этот признак не был константой.
        self.escalation_cut_probability = round(r.uniform(0.10, 0.22), 3)
        # Переходы на границах глав — самое заметное место склейки.
        self.chapter_transitions = list(CHAPTER_TRANSITIONS)
        self.chapter_transition_dur = CHAPTER_TRANSITION_DUR

        # ── вступление ────────────────────────────────────────────────
        self.opening = OPENING_ALIASES.get(v["opening"], v["opening"])
        if self.opening in recent_openings:
            pool = [o for o in OPENINGS if o not in recent_openings] or list(OPENINGS)
            self.opening = r.choice(pool)
        self.intro_footage_seconds = round(v["opening_seconds"], 1)
        # Куски вступления: видео по 3-8 секунд, фотографии по 7-15.
        # Заказано прямо, и это ровно вдвое длиннее, чем у канала о
        # находках: там перебивка была на 2-4 секунды и работала как
        # разгон, здесь она задаёт темп спокойного рассказа.
        self.intro_clip_duration_range = (3.0, 8.0)
        self.intro_photo_duration_range = (7.0, 15.0)
        self.intro_clip_share = round(v["intro_clip_share"], 3)
        self.intro_transition_duration_range = (0.6, 1.1)
        # Минимум ЕДИНИЦА, а не двойка: при шаге в два кадра и клипе
        # вдвое короче изображения доля видео по времени упирается в
        # 21%, то есть в нижний край заказанного. Пусть у части роликов
        # вставки идут через кадр — это по-прежнему 25-30% времени, а не
        # половина ролика.
        self.body_clip_every_n_shots = max(1, int(round(v["clip_rhythm"])))

        # ── эффекты ───────────────────────────────────────────────────
        self.effects_enabled = True
        self.effects = list(EFFECTS)
        self.effect_probability = round(v["effect_p"], 3)
        self.easings = list(EASINGS)

        # ── типографика и звук ────────────────────────────────────────
        self.text_style = v["text_style"]
        self.text_density = round(v["text_density"], 3)
        self.duck_depth = round(v["duck_depth"], 3)
        self.duck_style = v["duck_style"]

        # ПОДЛОЖКА. Пять разных, bed1..bed5. Выбор — как у цветокора:
        # две последние не повторяются. На сорока минутах одна и та же
        # петля утомляет сама по себе, поэтому подложек в длинном ролике
        # ТРИ: смены заходят на границах глав (см. build.beds_for), на
        # коротком — две. bed_switch_at остаётся ручкой для двухтрековой
        # схемы и для спецификаций, которые задают музыку сами.
        self.bed_pool = [f"bed{i}" for i in range(1, 6)]
        pool = [b for b in self.bed_pool if b not in recent_beds] or self.bed_pool
        self.bed = r.choice(pool)
        self.bed_second = r.choice([b for b in pool if b != self.bed] or [self.bed])
        third_pool = [b for b in self.bed_pool
                      if b not in (self.bed, self.bed_second)] or [self.bed]
        self.bed_third = r.choice(third_pool)
        self.bed_switch_at = round(r.uniform(0.48, 0.62), 3)
        self.bed_gain_db = round(r.uniform(-29.0, -25.0), 1)

        # Титулы глав плашкой. Ориентир для зрителя, который слушает
        # вполуха: смена главы видна, а не только слышна. Выключается
        # через style_override, если под конкретный ролик они не нужны.
        self.chapter_titles = True

        # Раскладка превью. YouTube показывает превью соседних загрузок в
        # одном ряду, поэтому одинаковая вёрстка подписи опознаётся как
        # серия быстрее, чем любой признак внутри самого ролика.
        self.thumb_style = r.choice(["lower_left", "lower_band", "upper_left",
                                     "centre_band"])

        self._last_move = None
        self._last_family = None
        self._family_run = 0
        self._last_effect = None
        self._used_frames = {}

    # ---------- движение камеры ----------

    def pick_move(self, motion_scale: float = 1.0, allow_hold=True,
                  only=None, duration=None):
        """
        Движение, его скорость и форму кривой для очередного кадра.

        Возвращает (имя, скорость, ease).

        Три правила поверх жребия.

        Первое: одно и то же движение не идёт два раза подряд.

        Второе: не идут подряд три движения ОДНОЙ СЕМЬИ. push_in и
        push_left это оба наезда, зритель видит их как одно, и «разные
        движения» из трёх наездов подряд — ровно тот случай, когда
        разнообразие есть в логе и нет на экране.

        Третье, СВОЁ У ЭТОГО КАНАЛА: на кадре длиннее LONG_SHOT_SECONDS
        набор сам сужается до движений, которые идут всю длительность.
        Обычный наезд на кадре в тридцать секунд отрабатывает за первые
        десять и дальше стоит — а стоп-кадр на двадцать секунд это ровно
        то, из-за чего такой ролик выключают.

        only — ограничить набор именами движений. Это ПАРАМЕТР, а не
        подмена результата снаружи: раньше вступление брало движение
        отсюда и, если оно не подходило, перевыбирало своим жребием — мимо
        счётчика семей. Проверка плана нашла на этом девять наездов подряд
        в первых трёх минутах.
        """
        weights = MOTION_BIAS.get(self.motion_bias, MOTION_BIAS["mixed"])
        allowed = set(only) if only else None
        if duration is not None and duration >= LONG_SHOT_SECONDS:
            long_set = set(LONG_SHOT_MOVES)
            allowed = (allowed & long_set) if allowed else long_set
            if not allowed:
                allowed = long_set

        fams = [f for f in MOVE_FAMILY
                if (allow_hold or f != "float")
                and (allowed is None or any(m in allowed for m in MOVE_FAMILY[f]))]
        if not fams:
            fams = [f for f in MOVE_FAMILY if allow_hold or f != "float"]
            allowed = None
        # семья не третий раз подряд
        if self._family_run >= 2 and self._last_family in fams and len(fams) > 1:
            fams = [f for f in fams if f != self._last_family]

        fam = self.rng.choices(fams, weights=[weights.get(f, 1.0) for f in fams])[0]
        family_moves = [m for m in MOVE_FAMILY[fam]
                        if allowed is None or m in allowed] or MOVE_FAMILY[fam]
        pool = [m for m in family_moves if m != self._last_move] or family_moves
        move = self.rng.choice(pool)

        self._family_run = self._family_run + 1 if fam == self._last_family else 1
        self._last_family = fam
        self._last_move = move

        # Амплитуда: базовый жребий × ось ролика × замысел доли. Зажата,
        # чтобы наезд не выехал за рабочий холст — см. SPEED_CLAMP.
        speed = self.rng.uniform(0.82, 1.30) * self.motion_amp * motion_scale
        speed = max(SPEED_CLAMP[0], min(SPEED_CLAMP[1], speed))

        # Форма кривой. У движения есть своя по умолчанию (долгий проезд
        # обязан идти равномерно, иначе середина его летит вдвое быстрее
        # краёв), и она перебивается жребием только там, где движение само
        # не настояло.
        ease = MOVES[move].get("ease")
        if ease is None:
            ease = self.rng.choices(("smooth", "linear", "out", "in"),
                                    weights=(4.0, 1.6, 1.4, 0.8))[0]
        return move, round(speed, 3), ease

    # ---------- переходы ----------

    def pick_transition(self, short=False, chapter=False, hard_p=None):
        """
        Переход и его длительность.

        short    — для быстрой перебивки вступления
        chapter  — граница главы: самый долгий и самый заметный переход
                   ролика. Единственное место, где разрешён уход в чёрное:
                   это конец мысли, а не середина.
        hard_p   — вероятность склейки встык ДЛЯ ЭТОГО кадра. Задаёт
                   замысел доли: под нагнетанием нож работает резче, чем
                   ролик в среднем. None — общий hard_cut_probability.
        """
        if chapter:
            tr = self.rng.choice(self.chapter_transitions)
            d = round(self.rng.uniform(*self.chapter_transition_dur), 2)
            return tr, d
        p_cut = self.hard_cut_probability if hard_p is None else hard_p
        if self.rng.random() < p_cut:
            return "cut", 0.0
        tr = (self.main_tr if self.rng.random() < self.transition_focus
              else self.rng.choice(self.transitions))
        if short:
            d = round(self.rng.uniform(*self.intro_transition_duration_range), 2)
        else:
            d = round(self.rng.uniform(*self.tr_dur_range), 2)
        return tr, d

    # ---------- параметры одного кадра ----------

    def shot_for(self, beat, pacing, beat_index: int, t: float,
                 chapter_edge=False):
        """
        Настройки кадра, начинающегося в секунду t внутри доли beat.

        Это основной путь. Старый st.clip() оставлен ниже для совместимости
        со спецификациями и тестами, но план ролика строится через этот
        метод: он единственный знает про замысел доли.
        """
        want, why = pacing.want(beat_index, beat, t, spread=self.shot_spread)
        want = round(max(self.min_shot_seconds,
                         min(want, self.max_shot_seconds)), 2)
        move, speed, ease = self.pick_move(pacing.motion_scale(beat),
                                           duration=want)
        # Под нагнетанием и в «сжимающемся» микроритме часть склеек идёт
        # встык: резкость там — замысел, а не сбой. Везде ещё действует
        # общий hard_cut_probability (по умолчанию ноль).
        tight = (beat.kind == "escalation"
                 or pacing.micro_of.get(beat_index) == "tighten")
        tr, trd = self.pick_transition(
            chapter=chapter_edge,
            hard_p=self.escalation_cut_probability if tight else None)
        if tr == "cut":
            why += " · склейка встык (нагнетание)"
        if chapter_edge:
            why += " · граница главы"
        return dict(duration=want, move=move, speed=speed, ease=ease,
                    transition=tr, transition_dur=trd,
                    effect=self.effect(), why=why, beat_kind=beat.kind)

    def clip(self, index: int, total: int, is_anchor: bool = False):
        """
        ЗАПАСНОЙ путь: настройки кадра по позиции на таймлайне.

        Оставлен работающим намеренно. Во-первых, на него опираются
        спецификации с base_duration_range и deceleration_range. Во-вторых,
        если разбор сценария почему-либо не дал ни одной доли (пустые
        тайм-коды, странный сценарий), план обязан собраться хоть как-то —
        и собирается по этой формуле.
        """
        r = self.rng
        pos = min(1.0, index / max(total - 1, 1))
        dur = self.base_dur * (1 + (self.decel - 1) * pos)
        dur *= 1 + r.uniform(-self.jitter, self.jitter)
        if is_anchor:
            dur = r.choice([dur * 2.4, dur * 0.42])
        dur = round(max(self.min_shot_seconds,
                        min(dur, self.max_shot_seconds)), 2)
        move, speed, ease = self.pick_move(duration=dur)
        tr, trd = self.pick_transition()
        return dict(duration=dur, move=move, speed=speed, ease=ease,
                    transition=tr, transition_dur=trd, effect=self.effect(),
                    why="запасной путь: кривая по таймлайну", beat_kind=None)

    def effect(self):
        """
        Имя эффекта на этот кадр или None.

        Выбор ВЗВЕШЕННЫЙ (EFFECT_WEIGHTS): тихие приёмы идут вчетверо чаще
        заметных. При равных весах набор из пятнадцати эффектов даёт
        ролик, который каждые три кадра меняет характер картинки.

        Один и тот же эффект не идёт два кадра подряд: два одинаковых
        подряд читаются как один длинный, и приём пропадает зря.
        """
        if not self.effects_enabled or not self.effects:
            return None
        if self.rng.random() >= self.effect_probability:
            self._last_effect = None
            return None
        pool = [e for e in self.effects if e != self._last_effect] or self.effects
        pick = self.rng.choices(
            pool, weights=[EFFECT_WEIGHTS.get(e, 1.0) for e in pool])[0]
        self._last_effect = pick
        return pick

    def framing(self, image_id: str):
        """
        Кадрирование при повторном показе одной и той же картинки.

        Первый показ тянется из наклона ролика (общими планами или
        крупными), последующие — из неиспользованных, чтобы одна картинка
        не выходила дважды одним и тем же кадром.
        """
        used = self._used_frames.setdefault(image_id, [])
        weights = FRAMING_BIAS.get(self.framing_bias, FRAMING_BIAS["balanced"])
        pool = [f for f in FRAMINGS if f not in used] or list(FRAMINGS)
        pick = self.rng.choices(pool, weights=[weights.get(f, 1.0) for f in pool])[0]
        used.append(pick)
        return pick, FRAMINGS[pick]

    def anchor_positions(self, total: int):
        """
        1-2 позиции, где ритм сознательно ломается.

        При разборе по долям почти не нужны: выдохи из pacing.py делают ту
        же работу осмысленнее. Метод остаётся ради запасного пути.
        """
        n = self.rng.choice([1, 2])
        lo, hi = int(total * 0.25), int(total * 0.85)
        if hi <= lo:
            return []
        return sorted(self.rng.sample(range(lo, hi), min(n, hi - lo)))

    def summary(self):
        return {
            "video_id": self.video_id,
            "lut": self.lut,
            "archive_lut": self.archive_lut,
            "generated_share": self.generated_share,
            "body_clip_share": self.body_clip_share,
            "grain": self.grain,
            "vignette": self.vignette,
            "overlay": self.overlay_kind if self.overlay_enabled else None,
            "overlay_variant": self.overlay_variant,
            "overlay_opacity": self.overlay_opacity,
            "overlay_px_sec": list(self.overlay_speed_px_sec),
            "transitions": len(self.transitions),
            "effects": len(self.effects) if self.effects_enabled else 0,
            "effect_p": self.effect_probability,
            "transition_dur": list(self.tr_dur_range),
            "transition_focus": self.transition_focus,
            "hard_cut_p": self.hard_cut_probability,
            "escalation_cut_p": self.escalation_cut_probability,
            "intro_footage_s": self.intro_footage_seconds,
            "intro_clip_s": list(self.intro_clip_duration_range),
            "intro_photo_s": list(self.intro_photo_duration_range),
            "intro_clip_share": self.intro_clip_share,
            "body_clip_every": self.body_clip_every_n_shots,
            "base_duration": round(self.base_dur, 2),
            "max_shot_seconds": self.max_shot_seconds,
            "deceleration": round(self.decel, 2),
            "arc": self.arc,
            "breath_rate": self.breath_rate,
            "shot_spread": self.shot_spread,
            "motion_amp": self.motion_amp,
            "motion_bias": self.motion_bias,
            "framing_bias": self.framing_bias,
            "main_transition": self.main_tr,
            "transition_duration": self.tr_dur,
            "opening": self.opening,
            "thumb_style": self.thumb_style,
            "text_style": self.text_style,
            "text_density": self.text_density,
            "duck_style": self.duck_style,
            "duck_depth": self.duck_depth,
            "bed": self.bed,
            "bed_second": self.bed_second,
            "bed_third": self.bed_third,
            "bed_switch_at": self.bed_switch_at,
            "bed_gain_db": self.bed_gain_db,
        }


if __name__ == "__main__":
    import json
    for vid in ["ancient-01-atlantis", "ancient-02-thebes", "ancient-03-oracle"]:
        s = StyleEngine(vid, diverge=False)
        print(json.dumps(s.summary(), ensure_ascii=False, indent=2))
