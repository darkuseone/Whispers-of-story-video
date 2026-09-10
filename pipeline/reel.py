"""
reel.py — вертикальный ролик из СЫРОГО материала, а не из готового final.mp4.

Чем отличается от shorts.py
---------------------------
`shorts.py` режет уже смонтированный ролик: берёт куски final.mp4, звук
оттуда же, и живёт тем, что длинное видео уже собрано. Это правильный
путь, когда ролик есть.

Здесь другой случай и другая задача: собрать вертикальный ролик, который
ОСТАНАВЛИВАЕТ ЛИСТАЮЩИЙ ПАЛЕЦ, из отдельных кадров — архивных фото и
кусков плёнки. Никакой начитки: текст несёт всю нагрузку сам, поэтому он
рублёный, крупный и появляется пословно. Так устроено большинство
работающих Shorts без диктора, и это не компромисс, а формат.

Что делает картинку «дорогой», а не «слайд-шоу»
-----------------------------------------------
Шесть вещей, и ни одна не необязательная — вместе они и дают разницу
между нарезкой и роликом:

1. ДВИЖЕНИЕ У КАЖДОГО КАДРА. Статичная фотография в вертикали читается
   как заставка. Здесь у каждого кадра свой ход (наезд, отъезд, панорама),
   считается он scale+crop с eval=frame — тем же приёмом, что в render.py,
   а не zoompan: zoompan замерен и стоит 31× реального времени.
2. РАЗДЕЛЬНАЯ ТОНИРОВКА. Холодные тени, тёплые средние тона — кривыми
   и colorbalance, БЕЗ архивного LUT. Замерено сравнением шести
   цветокоров на бумаге и на улице: LUT красит всё в один сепийный
   оттенок и выглядит фильтром, раздельная тонировка — кино.
3. ПЛЁНОЧНОЕ ЗЕРНО И ВИНЬЕТКА ОДНИМ СЛОЕМ НА ВЕСЬ РОЛИК, а не покадрово.
   Зерно, наложенное на каждый кадр отдельно, на склейке заметно
   «переключается»; сплошной слой поверх склеенного держит ролик единым.
4. ВОРОТ ПЛЁНКИ (film weave) — микросдвиг кадра по синусоиде, доли
   пикселя в секунду. Глазом не читается как эффект, но убирает
   «цифровую неподвижность» рамки.
5. СВЕТОЛИК НА СКЛЕЙКАХ. Короткая тёплая вспышка на стыке: она и
   маскирует стык, и даёт ощущение проектора.
6. КИНЕТИЧЕСКИЙ ТЕКСТ. Слова появляются по одному, с микро-въездом
   снизу. Строка целиком, возникшая разом, читается как титр; слова по
   одному читаются как речь.

Ритм
----
Шаг склейки 1.1-2.0 с — как у shorts.py и по той же причине (формат
живёт на этом шаге). Первые кадры короче: пока зритель решает,
листать дальше или нет, картинка обязана меняться.

Запуск
------
    python pipeline/reel.py reels/<id>.json

Спецификация ролика — JSON: хук, реплики, кадры, музыка. Формат описан
в reels/README.md и проверяется reel.check() перед рендером.
"""

import hashlib
import json
import math
import random
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
LUT_DIR = ROOT / "assets" / "luts"
MUSIC_DIR = ROOT / "assets" / "music"

W, H, FPS = 1080, 1920, 30

# ─────────────────────────── ЦВЕТ ───────────────────────────
#
# LUT'а здесь НЕТ НАМЕРЕННО, и это результат замера, а не упрощение.
# Архивные цветокоры канала (archive_platinum, moonlit_marble) на этом
# материале тянут ВСЁ в один сепийный кисель: и бумага, и небо, и тень
# приходят к одному коричневому, ролик читается как «фото под фильтром».
# Сравнение трёх цветокоров на конверте и на плёнке: вариант без LUT —
# кривые с подъёмом синего в тенях плюс тёплые средние тона — даёт
# НАСТОЯЩИЙ чёрный (кривая r начинается с нуля), холодную тень и тёплую
# бумагу. Разница видна сразу и именно она отличает кадр из фильма от
# фотографии с фильтром.
GRADE = (
    "curves=r='0/0.00 0.35/0.33 1/1.0':g='0/0.02 0.5/0.5 1/0.98'"
    ":b='0/0.10 0.4/0.45 1/0.93',"
    "colorbalance=rm=0.10:bm=-0.05:rh=0.06:bh=-0.03,"
    "eq=saturation=0.85:contrast=1.16"
)

# Зерно и виньетка — на весь ролик одним слоем (см. шапку, пункт 3).
# VIGNETTE был 4.0 и душил углы: на готовом ролике все кадры сходились
# к одинаковой тёмной мгле по краю, а раздельная тонировка пропадала
# вместе с ней. МЕНЬШЕ число — СИЛЬНЕЕ виньетка (это делитель PI).
MUSIC_LUFS = -14.0               # целевая громкость подложки, как в ленте
GRAIN = 6
VIGNETTE = 5.5

# ─────────────────────────── ДВИЖЕНИЕ ───────────────────────────
#
# Холст задаётся ПО ВЫСОТЕ, а не по ширине, и это не стилистика.
# Материал почти весь альбомный; если тянуть по ширине, при кропе
# 1080×1920 высоты не хватает — ffmpeg падает на «Invalid too big or
# non positive size». Поймано первым же прототипом.
#
# Каждый ход — (высота холста в начале, в конце, x в начале, x в конце,
# y в начале, y в конце). Доли: 0.0 — левый/верхний край, 1.0 — правый/
# нижний. Наезд = холст уменьшается, отъезд = растёт.
MOVES = {
    "push_in":     dict(h0=2560, h1=2180, x0=0.50, x1=0.50, y0=0.45, y1=0.50),
    "pull_out":    dict(h0=2100, h1=2520, x0=0.50, x1=0.50, y0=0.50, y1=0.44),
    "pan_right":   dict(h0=2300, h1=2260, x0=0.28, x1=0.68, y0=0.48, y1=0.48),
    "pan_left":    dict(h0=2300, h1=2260, x0=0.70, x1=0.30, y0=0.48, y1=0.48),
    "drift_down":  dict(h0=2260, h1=2200, x0=0.50, x1=0.50, y0=0.22, y1=0.70),
    "drift_up":    dict(h0=2260, h1=2200, x0=0.50, x1=0.50, y0=0.72, y1=0.26),
    "punch_in":    dict(h0=2700, h1=2050, x0=0.50, x1=0.50, y0=0.46, y1=0.50),
    "diag":        dict(h0=2480, h1=2160, x0=0.32, x1=0.62, y0=0.30, y1=0.58),
}


def log(*a):
    print(*a, flush=True)


def run(cmd, timeout=600):
    """Команда СТРОКОЙ: запуск идёт через shell, и список сюда передавать
    нельзя — shell возьмёт из него только первый элемент, а остальное
    молча уедет в позиционные параметры оболочки. Выглядит это как
    «ffmpeg напечатал справку по использованию» и ищется долго."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        print(f"  ! ffmpeg {r.returncode}")
        print((r.stderr or "")[-1800:])
        raise subprocess.CalledProcessError(r.returncode, cmd)
    return r


def _ease(dur: float) -> str:
    """
    Доля пройденного хода со сглаженными краями (smoothstep).

    Линейный ход выдаёт себя стартом и остановкой: кадр трогается рывком
    и так же встаёт. smoothstep разгоняет и тормозит движение, и ход
    перестаёт читаться как анимация — читается как съёмка.
    """
    p = f"min(1,max(0,t/{dur:.3f}))"
    return f"({p})*({p})*(3-2*({p}))"


def motion_vf(move: str, dur: float) -> str:
    """Ход камеры по неподвижному кадру: scale+crop с eval=frame."""
    m = MOVES[move]
    p = _ease(dur)
    hexpr = f"max({H},trunc(({m['h0']}+({m['h1'] - m['h0']})*{p})/2)*2)"
    xexpr = f"(iw-{W})*({m['x0']:.3f}+({m['x1'] - m['x0']:.3f})*{p})"
    yexpr = f"(ih-{H})*({m['y0']:.3f}+({m['y1'] - m['y0']:.3f})*{p})"
    return (f"scale=w=-2:h='{hexpr}':eval=frame,"
            f"crop={W}:{H}:x='{xexpr}':y='{yexpr}'")


# ─────────────────────── КАДР ИЗ ПЛЁНКИ ───────────────────────
#
# Архивная плёнка приходит 4:3 и часто с перфорацией по краю кадра
# (сканы с рулона). Два следствия, оба обязательные:
#
#   1. inset — обрезка краёв ДО всего остального. Перфорация в кадре
#      читается как брак, а не как приём: белые прямоугольники по краю
#      забирают внимание с того, ради чего кадр показан.
#   2. вертикаль собирается «подложка + окно»: размытая копия на весь
#      экран, поверх — сам кадр по ширине. Жёсткий кроп 4:3 в 9:16
#      выбрасывает две трети композиции, а пустые поля по краям делают
#      ролик похожим на репост с ютуба.
def film_vf(dur: float, inset: float, drift: float) -> str:
    """
    Фильтр-цепочка для куска плёнки: чистка краёв, подложка, окно.

    МАСШТАБ СЧИТАЕТСЯ «ПО ПОКРЫТИЮ», через max(нужная_ширина/iw,
    нужная_высота/ih), а не по одной стороне. Плёнка приходит 4:3, и
    scale по ширине даёт высоту 810 — меньше кадра, после чего crop
    падает с «Invalid too big or non positive size» уже на середине
    рендера. Через покрытие обе стороны гарантированно не меньше
    нужных, каким бы ни было соотношение исходника.
    """
    keep = 1.0 - 2 * inset
    # 1180 давало мелкое окно в мутной рамке — плёнка читалась как
    # вставленная картинка. 1320 это ~69% высоты кадра: видно, что
    # смотришь плёнку, а не иллюстрацию к тексту.
    vis_h = 1320                       # высота окна с плёнкой
    # Лёгкий ход и на плёнке: она живая, но статичная рамка вокруг —
    # нет, и без движения окно читается как вставленная картинка.
    p = _ease(dur)
    zoom = f"(1+{drift:.3f}*{p})"
    # Запас 1.004 и ОБЕ стороны считаются явно, а не через h=-2. При h=-2
    # высота выводится из округлённой ширины и промахивается на пиксель
    # вниз — 1920 превращается в 1918, и crop падает уже в середине
    # рендера. Поймано на клипе 1280×960: расчётная высота выходила
    # ровно 1920.4 и округлялась не в ту сторону.
    cover_bg = f"max({W}/iw\\,{H}/ih)*1.004"
    cover_fg = f"max({W}/iw\\,{vis_h}/ih)*1.004"
    return (
        f"crop=iw*{keep:.3f}:ih*{keep:.3f}:iw*{inset:.3f}:ih*{inset:.3f},"
        f"split=2[bg][fg];"
        f"[bg]scale=w='trunc(iw*{cover_bg}/2)*2':"
        f"h='trunc(ih*{cover_bg}/2)*2',"
        f"crop={W}:{H}:'(iw-{W})/2':'(ih-{H})/2',"
        f"boxblur=16:1,eq=brightness=-0.16:saturation=0.45[bgo];"
        f"[fg]scale=w='trunc(iw*{cover_fg}*{zoom}/2)*2':"
        f"h='trunc(ih*{cover_fg}*{zoom}/2)*2':eval=frame,"
        f"crop={W}:{vis_h}:'(iw-{W})/2':'(ih-{vis_h})/2'[fgo];"
        f"[bgo][fgo]overlay=0:'({H}-{vis_h})/2'"
    )


# ВЫРАВНИВАНИЕ ЭКСПОЗИЦИИ ПО КАДРАМ.
#
# Замер готового ролика: у фотографий средняя яркость 92-191, у кусков
# плёнки 23-53. Разница ВОСЬМИКРАТНАЯ, и на телефоне она читается не как
# «разный материал», а как «видео погасло»: склейка с документа на плёнку
# роняет кадр в темноту, зритель думает, что что-то сломалось, и уходит.
# Причина понятная — у плёнки полкадра занимает затемнённая размытая
# подложка, и цветокор вдобавок сажает тени.
#
# Лечится гаммой, а не яркостью: гамма тянет полутона и НЕ ТРОГАЕТ ни
# чёрную, ни белую точку, то есть выравнивает уровень, ничего не срезая.
# Сдвиг идёт НЕ ДО КОНЦА, а на EXPOSURE_PULL пути к цели: свести всё в
# одно число — значит выбросить светотень выпуска и получить ровную
# серую ленту. Задача — убрать провал на склейке, а не уравнять кадры.
EXPOSURE_TARGET = 110.0          # к какому уровню тянем (0-255)
EXPOSURE_PULL = 0.7              # какую долю пути проходим
# Нижняя граница низкая НАМЕРЕННО. Скан документа приходит почти белым
# (яркость окна 182 из 255), и потолок 0.60 не давал его придавить: три
# бумажных кадра подряд светили в глаза, а следующий за ними кусок плёнки
# проваливался на 57 пунктов. Документ, приглушённый до ~130, выглядит
# плёнкой, а не сканом, — каналу это и нужно.
EXPOSURE_GAMMA_MIN = 0.45
EXPOSURE_GAMMA_MAX = 2.00


def source_luma(src: Path, at: float) -> float:
    """Средняя яркость кадра ИСХОДНИКА — до цветокора и подложки."""
    import tempfile
    from PIL import Image
    d = Path(tempfile.mkdtemp())
    png = d / "l.png"
    ss = f"-ss {at:.2f} " if at > 0 else ""
    try:
        run(f'ffmpeg -y -v error {ss}-i {shlex.quote(str(src))} '
            f'-frames:v 1 -vf scale=160:-2 {shlex.quote(str(png))}')
        b = Image.open(png).convert("L").tobytes()
        return sum(b) / max(1, len(b))
    except Exception:
        return EXPOSURE_TARGET
    finally:
        for f in d.glob("*"):
            f.unlink(missing_ok=True)
        d.rmdir()


def exposure_gamma(mean: float) -> float:
    """
    Гамма, подтягивающая среднюю яркость mean к цели на EXPOSURE_PULL пути.

    Цель шага считается в логарифме яркости (m^(1-s) * T^s), а не в линейном
    среднем: яркость воспринимается логарифмически, и «полпути» по линейке
    от 23 до 110 глазу серединой не кажется.
    """
    m = min(250.0, max(5.0, mean))
    goal = (m ** (1.0 - EXPOSURE_PULL)) * (EXPOSURE_TARGET ** EXPOSURE_PULL)
    g = math.log(m / 255.0) / math.log(min(0.999, goal / 255.0))
    return max(EXPOSURE_GAMMA_MIN, min(EXPOSURE_GAMMA_MAX, g))


def shot_key(shot: dict) -> str:
    """
    Отпечаток кадра: по нему сохранённый кусок узнаётся своим.

    Кэш кусков нужен — пересборка монтажа не должна каждый раз гонять
    ffmpeg по семнадцати файлам. Но узнавать кусок ПО НОМЕРУ В СПИСКЕ
    нельзя, и это уже стоило одной пересборки: подменили в спецификации
    расфокус и пустой лист, ролик пересобрался, текст обновился — а
    картинка осталась старая, потому что shot_007.mp4 лежал на месте.
    В логе при этом всё правильно, отбраковка на новом материале чистая,
    и расходится только то, что на экране.
    Поэтому в отпечаток входит ВСЁ, что решает вид куска: сама
    спецификация кадра, цветокор, размер кадра и частота, а также размер
    и время файла-исходника — перекачали материал, кусок пересоберётся.
    """
    src = ROOT / shot["file"] if not Path(shot["file"]).is_absolute() \
        else Path(shot["file"])
    try:
        st = src.stat()
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        stamp = "нет"
    payload = json.dumps(shot, sort_keys=True, ensure_ascii=False)
    # В отпечаток идут ВСЕ ручки экспозиции, а не только цель: правка
    # одного лишь ограничителя гаммы уже прошла мимо кэша, и пересборка
    # честно доложила «из кэша 17 из 17», ничего не изменив на экране.
    payload += (f"|{GRADE}|{W}x{H}@{FPS}|{stamp}"
                f"|{EXPOSURE_TARGET}:{EXPOSURE_PULL}"
                f":{EXPOSURE_GAMMA_MIN}:{EXPOSURE_GAMMA_MAX}")
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def build_shot(shot: dict, dst: Path, idx: int) -> Path:
    """Один кадр ролика: 1080×1920, с ходом и цветокором, без зерна."""
    src = Path(shot["file"])
    dur = float(shot["seconds"])
    at = float(shot.get("at", 0))
    g = exposure_gamma(source_luma(src, at + (dur / 2 if shot.get("kind")
                                              == "clip" else 0)))
    # выравнивание идёт ПЕРЕД цветокором: цветокор рассчитан на нормально
    # экспонированный вход, и подставлять ему провал по яркости незачем
    grade = f"eq=gamma={g:.3f}," + GRADE if abs(g - 1.0) > 0.02 else GRADE
    if shot.get("kind") == "clip":
        chain = film_vf(dur, float(shot.get("inset", 0.10)),
                        float(shot.get("drift", 0.10)))
        speed = float(shot.get("speed", 1.0))
        pre = f"setpts={1 / speed:.3f}*PTS," if abs(speed - 1.0) > 0.01 else ""
        fc = (f"[0:v]{pre}fps={FPS},{chain},{grade},setsar=1[v]")
        cmd = (f'ffmpeg -y -ss {float(shot.get("at", 0)):.2f} '
               f'-i {shlex.quote(str(src))} -filter_complex "{fc}" '
               f'-map "[v]" -t {dur:.3f} -c:v libx264 -preset veryfast '
               f'-crf 18 -pix_fmt yuv420p -an {shlex.quote(str(dst))}')
    else:
        vf = f"{motion_vf(shot.get('move', 'push_in'), dur)},{grade},setsar=1"
        cmd = (f'ffmpeg -y -loop 1 -t {dur:.3f} -r {FPS} '
               f'-i {shlex.quote(str(src))} -vf "{vf}" '
               f'-c:v libx264 -preset veryfast -crf 18 '
               f'-pix_fmt yuv420p -an {shlex.quote(str(dst))}')
    run(cmd)
    return dst


# ─────────────────────── ТЕКСТ ───────────────────────

FONT_HOOK = "Archivo Black"
FONT_LINE = "Montserrat ExtraBold"
C_YELLOW = "&H0000D4FF"          # #FFD400 — фирменный жёлтый канала
C_WHITE = "&H00FFFFFF"

# Кегли НЕ фиксированные, а считаются под конкретный текст (_fit_size):
# доля кадра, которую должна занять самая длинная строка. Числа —
# именно доли, а не пиксели, потому что решает заполнение кадра, а не
# абстрактный размер шрифта.
HOOK_FILL = 0.88                 # крупный хук в начале — почти во всю ширину
HOOK_TOP_FILL = 0.72             # он же, осевший наверх
LINE_FILL = 0.86                 # реплика
HOOK_SIZE_MAX = 150
HOOK_TOP_SIZE_MAX = 80
HOOK_TOP_SIZE_MIN = 44           # ниже этого шапка перестаёт читаться в ленте
LINE_SIZE_MAX = 116
LINE_SIZE_MIN = 58
HOOK_HOLD = 1.9                  # сколько держится крупным
HOOK_SHRINK = 0.45
HOOK_BIG_Y = 900
HOOK_TOP_Y = 268
LINE_SIZE = 76                   # опорный кегль межстрочья
LINE_BOTTOM_Y = 1470
WORD_STEP = 0.085                # шаг появления слов внутри реплики
WORD_FADE = 130                  # мс на проявление слова
# Призыв — не сноска: он последнее, что видит зритель, и он должен быть
# НЕ МЕЛЬЧЕ реплик. При 0.70/72 он выходил вдвое мельче субтитров и
# читался как выходные данные, а не как «дальше — на канале».
CTA_FILL = 0.82
CTA_SIZE_MAX = 92
CTA_SIZE_MIN = 40


def _wrap(text: str, per_line: int):
    """
    Грубый перенос по числу знаков. Остался ТОЛЬКО как запасной путь,
    когда PIL недоступен: он считает знаки, а не ширину, и потому режет
    «I FOUND A LETTER IN A BOX OF HIS THINGS» на две полные строки и
    висячее «THINGS». Рабочий перенос — fit_block() ниже.
    """
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > per_line:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


# КАЛИБРОВКА PIL → libass, СВОЯ У КАЖДОЙ ГАРНИТУРЫ.
#
# ASS Fontsize и пиксельный размер PIL — разные величины: libass привязывает
# размер к другой метрике шрифта, и коэффициент между ними зависит от того,
# как у конкретной гарнитуры соотносятся em и высота прописной. Замер прямой
# (assets/fonts, строка «HAMBURGEFONTSIV», Fontsize=80, ширина чернил на
# кадре против PIL.getlength при том же числе):
#
#     Archivo Black         drawn 675  pil 911.9  → 0.740
#     Montserrat ExtraBold  drawn 558  pil 875.3  → 0.637
#
# РАЗНИЦА В 16% — НЕ ОКРУГЛЕНИЕ. Пока обе гарнитуры считались по одному
# числу 0.636, хук Archivo Black мерился на 16% уже, чем рисуется, кегль
# под него выбирался на 16% крупнее нужного — и «HE HID THIS FOR 40 YEARS»
# уезжал за оба края кадра. По логу это невидимо: размер честно посчитан,
# ffmpeg доволен, брак виден только на готовом кадре.
#
# Добавляя гарнитуру, ЗАМЕРЬТЕ ЕЁ, а не берите соседнюю: см. функцию
# calibrate() внизу файла — она печатает готовую строку для этой таблицы.
ASS_PIL_RATIO = {
    "ArchivoBlack-Regular.ttf": 0.740,
    "Montserrat-ExtraBold.ttf": 0.637,
}
ASS_PIL_DEFAULT = 0.68


def _text_w(text: str, size: float, font_file: str) -> float:
    """
    Ширина строки В КООРДИНАТАХ ASS настоящим шрифтом, а не на глаз.

    Нужна ПОСЛОВНОЙ раскладке: каждое слово — отдельное событие ASS со
    своим \\pos, и без замера все слова встают в одну точку по центру и
    громоздятся друг на друге. Именно так и вышло на первом прогоне:
    «LETTER» читалось как «LEТER», «DATED 1961» — как «D1961D».
    Меряем advance (getlength), а не чернильный bbox: у пробела нулевые
    чернила и ненулевая ширина, и по bbox межсловные промежутки
    схлопнулись бы в ноль.
    """
    try:
        from PIL import ImageFont
        f = ImageFont.truetype(str(FONT_DIR / font_file), int(round(size)))
        k = ASS_PIL_RATIO.get(font_file, ASS_PIL_DEFAULT)
        return float(f.getlength(text)) * k
    except Exception:
        k = ASS_PIL_RATIO.get(font_file, ASS_PIL_DEFAULT)
        return len(text) * size * 0.58 * k


def _split_rows(words, n: int, font_file: str, ref: int = 100):
    """
    Разложить слова на n строк так, чтобы САМАЯ ШИРОКАЯ строка была как
    можно уже. Это и есть «выключка» — то, чем набранный текст отличается
    от перенесённого.

    Жадный перенос (набивать строку, пока лезет) на короткой реплике даёт
    висячее слово: «I FOUND A LETTER / IN A BOX OF HIS / THINGS». На
    вертикали это читается как ошибка вёрстки, потому что там и есть
    ошибка вёрстки. Здесь перебираются все разрезы, а не первый попавшийся:
    динамика по (слово, оставшиеся строки), стоимость разреза — ширина
    самой широкой строки. Слов в реплике меньше десятка, строк не больше
    трёх, так что перебор стоит микросекунды.
    """
    w = [_text_w(x, ref, font_file) for x in words]
    sp = _text_w(" ", ref, font_file)
    N = len(words)
    INF = float("inf")
    dp = [[INF] * (n + 1) for _ in range(N + 1)]
    cut = [[0] * (n + 1) for _ in range(N + 1)]
    dp[N][0] = 0.0
    for i in range(N - 1, -1, -1):
        for k in range(1, n + 1):
            width = 0.0
            for j in range(i, N):
                width += w[j] + (sp if j > i else 0.0)
                rest = dp[j + 1][k - 1]
                if rest == INF:
                    continue
                val = width if width > rest else rest
                if val < dp[i][k]:
                    dp[i][k] = val
                    cut[i][k] = j + 1
    if dp[0][n] == INF:
        return [" ".join(words)], _text_w(" ".join(words), ref, font_file)
    rows, i, k = [], 0, n
    while k > 0:
        j = cut[i][k]
        rows.append(" ".join(words[i:j]))
        i, k = j, k - 1
    return rows, dp[0][n]


def fit_block(text: str, font_file: str, want: float, lo: float, hi: float,
              max_rows: int = 3, ref: int = 100):
    """
    Строки И кегль под конкретную фразу: (rows, size).

    Правило выбора числа строк: берём НАИМЕНЬШЕЕ, при котором текст уже
    упирается в потолок кегля. Больше строк крупнее уже не сделают, а
    читаются дольше и съедают низ кадра. Если до потолка не дотягивает
    ни одно — берём то, что даёт самый крупный набор.
    """
    words = text.split()
    if not words:
        return [""], int(lo)
    best = None
    for n in range(1, min(max_rows, len(words)) + 1):
        rows, widest = _split_rows(words, n, font_file, ref)
        size = hi if widest <= 0 else want / widest * ref
        size = max(lo, min(hi, size))
        if best is None or size > best[1] + 0.5:
            best = (rows, size)
        if size >= hi - 0.5:
            break
    return best[0], int(round(best[1]))


def _fit_size(rows, want: float, lo: float, hi: float, font_file: str,
              ref: int = 100) -> int:
    """
    Кегль, при котором САМАЯ ДЛИННАЯ строка блока занимает want пикселей.

    Постоянный кегль на вертикали не работает: реплика из двух слов и
    реплика из шести при одном размере дают то полупустую строку, то
    текст в край кадра. Здесь размер считается под конкретный блок, и
    каждая реплика заполняет кадр одинаково — именно это и читается как
    «оформлено», а не «подписано».
    """
    widest = max((_text_w(r, ref, font_file) for r in rows), default=1.0)
    if widest <= 0:
        return int(hi)
    return int(round(max(lo, min(hi, want / widest * ref))))


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def write_ass(spec: dict, lines: list, total: float, out: Path):
    """
    Слои текста: хук, пословные реплики, призыв.

    ПОСЛОВНО, А НЕ СТРОКОЙ. Строка, возникшая целиком, читается как
    титр — глаз получает её разом и уходит с картинки читать. Слова по
    одному держат тот же ритм, что речь: внимание идёт за словом, а
    картинка остаётся видна. Каждое слово — своё событие ASS со своим
    \\fad и микросдвигом снизу (\\move на 14 px), поэтому строка
    «собирается», а не «появляется».
    """
    hook = spec["hook"].upper()
    hook_lines, hook_size = fit_block(
        hook, "ArchivoBlack-Regular.ttf", W * HOOK_FILL, 60, HOOK_SIZE_MAX,
        max_rows=2)
    # ОСЕВШИЙ ХУК — ОДНОЙ СТРОКОЙ. Наверху он работает как шапка канала, а
    # шапка в две строки съедает кадр и спорит с репликами внизу. На две
    # строки он ломается только тогда, когда одной уже не прочесть.
    hook_top_lines, hook_top_size = fit_block(
        hook, "ArchivoBlack-Regular.ttf", W * HOOK_TOP_FILL,
        HOOK_TOP_SIZE_MIN, HOOK_TOP_SIZE_MAX, max_rows=1)
    if hook_top_size <= HOOK_TOP_SIZE_MIN:
        hook_top_lines, hook_top_size = fit_block(
            hook, "ArchivoBlack-Regular.ttf", W * HOOK_TOP_FILL,
            HOOK_TOP_SIZE_MIN, HOOK_TOP_SIZE_MAX, max_rows=2)
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}",
        f"PlayResY: {H}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
        " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
        " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Hook,{FONT_HOOK},{hook_size},{C_YELLOW},{C_YELLOW},"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,6,3,5,40,40,40,1",
        f"Style: Line,{FONT_LINE},{LINE_SIZE},{C_WHITE},{C_WHITE},"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,5,2,2,60,60,60,1",
        f"Style: Key,{FONT_LINE},{LINE_SIZE},{C_YELLOW},{C_YELLOW},"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,5,2,2,60,60,60,1",
        f"Style: Cta,{FONT_LINE},{CTA_SIZE_MAX},{C_WHITE},{C_WHITE},"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,2,2,60,60,60,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR,"
        " MarginV, Effect, Text",
    ]
    ev = []

    def add(style, t0, t1, text, layer=0):
        ev.append(f"Dialogue: {layer},{_ass_time(t0)},{_ass_time(t1)},"
                  f"{style},,0,0,0,,{text}")

    # ХУК. Крупно в центре, потом уезжает наверх и висит до конца —
    # ОДНИМ событием с \move и \t: двумя титрами со стыком текст дёргается,
    # потому что у крупного и мелкого разная высота блока.
    big = "\\N".join(hook_lines)
    top = "\\N".join(hook_top_lines)
    shrink = int(round(hook_top_size / max(1, hook_size) * 100))
    t_move0, t_move1 = HOOK_HOLD, HOOK_HOLD + HOOK_SHRINK
    # ХУК СТОИТ НА ПЕРВОМ ЖЕ КАДРЕ. Начало в 0.10 с проявлением 220 мс
    # означало, что первые ~0.3 с в ленте показывается картинка БЕЗ
    # хука, — а это ровно тот кадр, который видно в ленте до нажатия и по
    # которому решают, останавливаться или листать дальше.
    add("Hook", 0.0, t_move1,
        f"{{\\an5\\pos({W // 2},{HOOK_BIG_Y})\\fad(90,0)"
        f"\\t({int(t_move0 * 1000)},{int(t_move1 * 1000)},"
        f"\\fscx{shrink}\\fscy{shrink})}}{big}")
    add("Hook", t_move1, total - 0.2,
        f"{{\\an5\\pos({W // 2},{HOOK_TOP_Y})\\fs{hook_top_size}"
        f"\\fad(120,200)}}{top}")

    # РЕПЛИКИ пословно, КАЖДОЕ СЛОВО НА СВОЁМ МЕСТЕ.
    #
    # Раскладка считается здесь, а не отдаётся libass: у каждого слова
    # своё событие со своим \pos, иначе все они центруются в одну точку
    # и наезжают друг на друга (см. _text_w). Порядок: меряем слова и
    # пробел, складываем ширину строки, от неё считаем левый край, дальше
    # идём слева направо, накапливая смещение.
    for ln in lines:
        t0, t1 = float(ln["t0"]), float(ln["t1"])
        # Строки И кегль СВОИ У КАЖДОЙ РЕПЛИКИ: перенос выключен по ширине,
        # кегль подобран под самую длинную строку, чтобы и «DATED 1961», и
        # «ADDRESSED TO A NAME I DID NOT RECOGNIZE» заполняли кадр одинаково.
        rows, size = fit_block(ln["text"].upper(), "Montserrat-ExtraBold.ttf",
                               W * LINE_FILL, LINE_SIZE_MIN, LINE_SIZE_MAX,
                               max_rows=3)
        n_rows = len(rows)
        keys = {w.upper() for w in ln.get("key", [])}
        space = _text_w(" ", size, "Montserrat-ExtraBold.ttf")
        widx = 0
        for ri, row in enumerate(rows):
            words = row.split()
            widths = [_text_w(w, size, "Montserrat-ExtraBold.ttf")
                      for w in words]
            total_w = sum(widths) + space * (len(words) - 1)
            x = (W - total_w) / 2.0
            # Строки блока стоят снизу вверх: нижняя строка на
            # LINE_BOTTOM_Y, каждая следующая выше на межстрочье.
            y = LINE_BOTTOM_Y - (n_rows - 1 - ri) * int(size * 1.22)
            for word, ww in zip(words, widths):
                st = t0 + widx * WORD_STEP
                if st >= t1 - 0.12:
                    st = max(t0, t1 - 0.12)
                style = "Key" if word.strip(".,!?—:").upper() in keys \
                    else "Line"
                cx = int(round(x + ww / 2.0))
                # \move снизу вверх на 14 px за время проявления: слово
                # не возникает, а подаётся.
                add(style, st, t1,
                    f"{{\\an5\\fs{size}\\move({cx},{y + 14},{cx},{y},0,"
                    f"{WORD_FADE})\\fad({WORD_FADE},110)}}{word}", layer=1)
                x += ww + space
                widx += 1

    cta = spec.get("cta", "FULL STORY ON THE CHANNEL").upper()
    cta_rows, cta_size = fit_block(cta, "Montserrat-ExtraBold.ttf",
                                   W * CTA_FILL, CTA_SIZE_MIN, CTA_SIZE_MAX,
                                   max_rows=2)
    add("Cta", total - 2.4, total - 0.15,
        f"{{\\an2\\fs{cta_size}\\pos({W // 2},{LINE_BOTTOM_Y})"
        f"\\fad(200,200)}}" + "\\N".join(cta_rows))

    out.write_text("\n".join(head + ev) + "\n", encoding="utf-8")
    return out


# ─────────────────────── СБОРКА ───────────────────────

def _leak_expr(cuts: list, total: float) -> str:
    """
    Светолик на склейках: короткая тёплая вспышка на стыке.

    Делается яркостью по времени (`eq=brightness=` с выражением), а не
    наложением файла: файл пришлось бы тянуть в граф отдельным входом
    ради полуторакадровой вспышки. Форма — треугольник длиной LEAK: вход
    и выход по одному кадру, поэтому вспышка читается как засветка
    плёнки на склейке, а не как мигание.
    """
    LEAK = 0.16
    parts = []
    for c in cuts:
        if c <= 0.05 or c >= total - 0.05:
            continue
        parts.append(f"max(0,1-abs(t-{c:.3f})/{LEAK})")
    if not parts:
        return ""
    peak = "+".join(parts)
    return (f"eq=brightness='0.10*min(1,{peak})':"
            f"saturation='1+0.18*min(1,{peak})':eval=frame")


def assemble(shots_files: list, cuts: list, total: float, ass: Path,
             music: Path, out: Path, tmp: Path):
    """
    Склейка + слой на весь ролик: ворот плёнки, зерно, виньетка, светолики,
    текст. Всё одним проходом: каждый лишний проход — это ещё одно
    перекодирование, то есть потеря на том же материале.
    """
    lst = tmp / "concat.txt"
    lst.write_text("".join(f"file '{Path(f).resolve()}'\n"
                           for f in shots_files))
    silent = tmp / "silent.mp4"
    run(f'ffmpeg -y -f concat -safe 0 -i {shlex.quote(str(lst))} '
        f'-c copy {shlex.quote(str(silent))}')

    # ВОРОТ ПЛЁНКИ. Кадр гуляет на пару пикселей по синусоиде — столько,
    # сколько гуляет плёнка в проекторе. Читается не как эффект, а как
    # отсутствие цифровой неподвижности; убери — и картинка «встаёт».
    weave = (f"pad={W + 8}:{H + 8}:4:4:color=black,"
             f"crop={W}:{H}:'4+2*sin(t*1.9)':'4+2*cos(t*1.3)'")
    leak = _leak_expr(cuts, total)
    chain = [weave]
    if leak:
        chain.append(leak)
    chain += [
        f"noise=alls={GRAIN}:allf=t+u",
        f"vignette=PI/{VIGNETTE}",
        f"subtitles={shlex.quote(str(ass))}:fontsdir={shlex.quote(str(FONT_DIR))}",
        "setsar=1",
    ]
    vf = ",".join(chain)
    # Музыка: своя громкость, вход из тишины и уход в тишину. Ролик,
    # обрывающийся на полной громкости, слышно как брак.
    # ГРОМКОСТЬ ВЫРАВНИВАЕТСЯ ПО LUFS, А НЕ МНОЖИТЕЛЕМ.
    #
    # Просто volume=0.62 давало среднюю -27 dB при пике -10.7. Ленты
    # Shorts и Reels приводят звук примерно к -14 LUFS, и ролик, пришедший
    # тише, звучит тише СОСЕДНИХ — на пролистывании это читается как
    # «выключено» и стоит того же, что и тёмная картинка. loudnorm
    # приводит к цели по восприятию (LUFS), а не по амплитуде: один и тот
    # же множитель на разных подложках даёт разную громкость.
    #
    # Затухания идут ПОСЛЕ выравнивания: loudnorm считает статистику по
    # всей дорожке, и если сначала увести хвост в тишину, он подтянет
    # остальное, компенсируя ею же созданный провал.
    af = (f"loudnorm=I={MUSIC_LUFS}:TP=-1.5:LRA=11,"
          f"afade=t=in:st=0:d=1.2,"
          f"afade=t=out:st={max(0.1, total - 1.6):.2f}:d=1.5")
    run(f'ffmpeg -y -i {shlex.quote(str(silent))} '
        f'-stream_loop -1 -i {shlex.quote(str(music))} '
        f'-vf "{vf}" -af "{af}" -t {total:.3f} '
        f'-c:v libx264 -preset medium -crf 19 -pix_fmt yuv420p '
        f'-c:a aac -b:a 160k -movflags +faststart '
        f'{shlex.quote(str(out))}', timeout=1200)
    return out


def check(spec: dict):
    """
    Проверки, которые дешевле сделать до рендера, чем увидеть на готовом
    ролике. Каждая — след настоящей ошибки, а не гипотеза.
    """
    problems = []
    if not spec.get("hook"):
        problems.append("нет хука")
    if len(spec.get("hook", "")) > 62:
        problems.append(f"хук {len(spec['hook'])} знаков — не влезет в три "
                        f"строки крупным кеглем")
    shots = spec.get("shots") or []
    if len(shots) < 8:
        problems.append(f"кадров {len(shots)} — на вертикали это слайд-шоу")
    for i, sh in enumerate(shots):
        p = Path(sh["file"])
        if not p.exists():
            problems.append(f"кадр {i}: нет файла {p}")
        d = float(sh.get("seconds", 0))
        if d > 2.6:
            problems.append(f"кадр {i}: {d:.1f} с — длиннее, чем держит "
                            f"формат (потолок 2.6)")
        if sh.get("kind") != "clip" and sh.get("move") not in MOVES:
            problems.append(f"кадр {i}: неизвестный ход {sh.get('move')!r}")
    seen = {}
    for sh in shots:
        seen[sh["file"]] = seen.get(sh["file"], 0) + 1
    for f, n in seen.items():
        if n > 2:
            problems.append(f"{Path(f).name} показан {n} раза — на 30 "
                            f"секундах это заметно")
    return problems


def build(spec_path: str):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    problems = check(spec)
    if problems:
        for p in problems:
            log(f"  ! {p}")
        raise SystemExit("спецификация ролика не прошла проверку")

    rid = spec["id"]
    out_dir = ROOT / "work" / spec.get("work", "reels") / "out"
    tmp = ROOT / "work" / spec.get("work", "reels") / "tmp" / rid
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)

    shots = spec["shots"]
    log(f"── {rid}: {len(shots)} кадров")
    files, cuts, t = [], [], 0.0
    reused = 0
    for i, sh in enumerate(shots):
        dst = tmp / f"shot_{i:03d}_{shot_key(sh)}.mp4"
        if dst.exists():
            reused += 1
        else:
            # старые куски этого номера убираем сразу: иначе tmp пухнет
            # на каждой правке спецификации
            for old in tmp.glob(f"shot_{i:03d}_*.mp4"):
                old.unlink(missing_ok=True)
            build_shot(sh, dst, i)
        files.append(dst)
        t += float(sh["seconds"])
        cuts.append(t)
    total = round(t, 3)
    cuts = cuts[:-1]
    log(f"   длительность {total:.1f} с, склеек {len(cuts)}, "
        f"из кэша {reused} из {len(shots)}")

    ass = tmp / "text.ass"
    write_ass(spec, spec["lines"], total, ass)

    music = MUSIC_DIR / spec.get("music", "bed3.mp3")
    out = out_dir / f"{rid}.mp4"
    assemble(files, cuts, total, ass, music, out, tmp)
    log(f"   готово: {out}  ({out.stat().st_size / 1e6:.1f} МБ)")
    return out


def preview(spec_path: str, out_png: str = ""):
    """
    Собрать кадры ролика и выложить контактным листом — БЕЗ склейки,
    звука и текста.

    Нужно потому, что судить материал по исходнику нельзя: кусок плёнки
    4:3 уезжает в окно 1080×1320 «по покрытию» и теряет по 38% ширины с
    боков. Групповой план, отличный на превью источника, в ролике
    оказывается одним человеком у края кадра — а замечается это уже на
    готовом ролике. Здесь показано ровно то, что попадёт на экран:
    та же цепочка, тот же кроп, тот же цветокор.
    """
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    rid = spec["id"]
    tmp = ROOT / "work" / spec.get("work", "reels") / "tmp" / rid
    tmp.mkdir(parents=True, exist_ok=True)
    shots = spec["shots"]
    frames = []
    for i, sh in enumerate(shots):
        dst = tmp / f"shot_{i:03d}_{shot_key(sh)}.mp4"
        if not dst.exists():
            for old in tmp.glob(f"shot_{i:03d}_*.mp4"):
                old.unlink(missing_ok=True)
            build_shot(sh, dst, i)
        png = tmp / "preview" / f"{i:02d}_{Path(sh['file']).stem}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        run(f'ffmpeg -y -v error -ss {float(sh["seconds"]) / 2:.2f} '
            f'-i {shlex.quote(str(dst))} -frames:v 1 '
            f'-vf scale=300:-2 {shlex.quote(str(png))}')
        frames.append(png)
    log(f"── {rid}: кадры для просмотра в {frames[0].parent}")
    return frames


def calibrate(fonts=None):
    """
    Замерить коэффициент PIL→libass для гарнитуры и напечатать строку для
    таблицы ASS_PIL_RATIO. Рисуем пробную строку настоящим libass, меряем
    чернила на кадре, делим на то, что для того же числа даёт PIL.

    Считать этот коэффициент «примерно одинаковым у похожих шрифтов»
    нельзя: у Archivo Black и Montserrat ExtraBold он расходится на 16%, а
    16% по ширине — это хук, уехавший за край кадра.
    """
    import tempfile
    from PIL import Image, ImageFont
    probe, size = "HAMBURGEFONTSIV", 80
    fonts = fonts or [("Archivo Black", "ArchivoBlack-Regular.ttf"),
                      ("Montserrat ExtraBold", "Montserrat-ExtraBold.ttf")]
    for family, file in fonts:
        d = Path(tempfile.mkdtemp())
        ass = d / "p.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\n"
            "PlayResY: 400\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour,"
            " SecondaryColour, OutlineColour, BackColour, Bold, Italic,"
            " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
            " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR,"
            " MarginV, Encoding\n"
            f"Style: P,{family},{size},&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,"
            "&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR,"
            " MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,0:00:02.00,P,,0,0,0,,"
            f"{{\\an5\\pos(960,200)}}{probe}\n", encoding="utf-8")
        png = d / "p.png"
        run(f'ffmpeg -y -v error -f lavfi '
            f'-i color=c=black:s=1920x400:d=0.1 '
            f'-vf "subtitles={ass}:fontsdir={FONT_DIR}" '
            f'-frames:v 1 {shlex.quote(str(png))}')
        im = Image.open(png).convert("L")
        bb = im.point(lambda v: 255 if v > 60 else 0).getbbox()
        drawn = bb[2] - bb[0]
        pil = ImageFont.truetype(str(FONT_DIR / file), size).getlength(probe)
        log(f'    "{file}": {drawn / pil:.3f},   '
            f"# drawn {drawn}, pil {pil:.1f}")


def main(argv):
    # argv[0] — имя скрипта, поэтому режим ищем со второго
    if len(argv) > 2 and argv[1] == "preview":
        preview(argv[2])
        return 0
    if len(argv) > 1 and argv[1] == "calibrate":
        calibrate()
        return 0
    if len(argv) < 2:
        raise SystemExit("нужен путь к спецификации: "
                         "python pipeline/reel.py reels/<id>.json")
    for p in argv[1:]:
        build(p)


if __name__ == "__main__":
    main(sys.argv)
