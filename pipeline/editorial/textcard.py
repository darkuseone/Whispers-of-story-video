"""
textcard.py — КИНЕТИЧЕСКАЯ ТИПОГРАФИКА на датах, эпохах и числах.

Зачем
-----
Ролик, собранный только из чужих картинок и чужого видео, не содержит НИ
ОДНОГО собственного изображения — даже когда сценарий свой, монтаж свой и
разбор структуры свой. Плашка, набранная и анимированная самим
конвейером, это единственный элемент кадра, которого нет ни в одном стоке.
Стоит она ноль (шрифт системный, рендер тем же ffmpeg) и добавляет ролику
то, чего в исходном материале не было вовсе.

Второй смысл — редакторский, и на этом канале он важнее первого. У канала
о находках сюжет держался на СУММЕ: сорок три миллиона, тысяча четыреста
двадцать семь монет. Здесь сюжет держится на ВРЕМЕНИ: 2600 год до нашей
эры, третий век, тысяча двести лет спустя. Зритель не удерживает такие
числа на слух — тем более когда слушает перед сном и вполуха. Плашка
ставит дату на экран ровно тогда, когда её произносят, и это решение
монтажёра, а не оформление.

Поэтому здесь распознаются не только суммы, но и даты до нашей эры,
столетия и названия эпох, а стилей стало шесть: два новых спокойные, под
ночной канал, — быстрый удар и печатная машинка бодрят.

Когда ставится
--------------
Только на долях-развязках и только там, где в тексте реально звучит число.
Плотность задаётся осью text_density вектора стиля, стиль анимации — осью
text_style. У части роликов ось выпадает в "none", и плашек нет вовсе:
приём, стоящий в каждой загрузке, перестаёт быть приёмом.

Техника
-------
drawtext с выражениями по времени. Все четыре стиля собраны на альфе и
координатах, без внешних файлов и без второго прохода рендера.
"""

import re
from pathlib import Path

# Системные шрифты. Проверяются по порядку: на раннере GitHub Actions
# стоит DejaVu, на локальной машине может быть что угодно. Если не нашли
# ни одного — плашки молча выключаются, ролик собирается без них.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
]

# Стили анимации. Каждый — своя механика появления, а не своя длительность
# одного и того же выезда.
#
# carved и fade_slow добавлены под этот канал и в нём же основные:
# «высеченная» надпись с разрядкой и медленное проявление. stamp и
# typewriter унаследованы и остались рабочими, но выпадают редко — удар и
# печатная машинка бодрят, а ролик смотрят перед сном.
STYLES = ("carved", "fade_slow", "stamp", "slide_up", "typewriter",
          "underline_wipe")

# Сколько плашка держится на экране. Дольше, чем на канале о находках
# (2.6-4.2): там кадр жил пять секунд и плашка не могла его пережить,
# здесь кадр живёт двадцать, а читает зритель медленнее — он не следит,
# он смотрит.
HOLD = (3.8, 6.0)

# Раскладка. Две позиции, чтобы плашки не выстраивались в столбик у тех
# роликов, где их несколько.
PLACES = {
    "lower_left":  dict(x="W*0.070", y="H*0.760"),
    "lower_right": dict(x="W*0.560", y="H*0.762"),
    "upper_left":  dict(x="W*0.072", y="H*0.135"),
}

# Числа словами — тем же словарём, что и в beats.py, но здесь нужен ПОРЯДОК
# слов, чтобы вытащить фразу целиком: «forty-three million pounds», а не
# три отдельных слова.
NUM_TOKENS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million",
    "billion", "and",
}
UNIT_TOKENS = {
    "dollars", "pounds", "euros", "coins", "years", "days", "months",
    "weeks", "hours", "minutes", "seconds", "percent", "kilograms", "kilos",
    "grams", "objects", "pieces", "items", "people", "miles", "inches",
    "feet", "metres", "meters", "centimetres", "carats", "bags", "cans",
    # своё у этого канала: сюжет держится на времени, а не на цене
    "century", "centuries", "millennium", "millennia", "generations",
    "долларов", "рублей", "монет", "лет", "дней", "часов", "процентов",
    "килограммов", "граммов", "метров", "предметов", "человек", "веков",
    "столетий", "тысячелетий", "поколений",
}

# Метки эпохи. Ради них вся эта надстройка и написана: «2600 BC» без
# «BC» — это не дата, а число, и на экране оно врёт.
ERA_TOKENS = {"bc": "BC", "bce": "BCE", "ad": "AD", "ce": "CE"}

# Порядковые числительные — для столетий. Дальше двадцать первого не
# идём: канал про древний мир, двадцать второго века там не бывает.
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "twenty-first": 21,
}


def _ordinal(n: int) -> str:
    """3 -> 3RD. Для плашки: «3RD CENTURY BC» читается за полсекунды."""
    if 10 <= n % 100 <= 20:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(n % 10, "TH")
    return f"{n}{suffix}"


def _era_phrase(text: str):
    """
    Дата или эпоха из предложения. Пусто — значит их там нет.

    Три случая, все три встречаются в каждом втором сценарии канала:

      «2600 BC»                 цифра плюс метка эпохи
      «the third century BC»    порядковое числительное плюс столетие
      «twelve hundred BC»       числительное словами плюс метка эпохи

    Порядок проверок от самого однозначного к самому спорному. Метка
    эпохи ищется в пределах трёх слов: между числом и «BC» стандартно
    встают «years» и «or so».
    """
    low = (text or "").lower()
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9\-]+", low)

    # ── столетие ──
    for i, w in enumerate(words):
        if w in ("century", "centuries"):
            for back in range(max(0, i - 2), i):
                n = ORDINALS.get(words[back])
                if n:
                    era = ""
                    for k in range(i + 1, min(i + 4, len(words))):
                        if words[k] in ERA_TOKENS:
                            era = " " + ERA_TOKENS[words[k]]
                            break
                    return f"{_ordinal(n)} CENTURY{era}".strip()

    # ── цифра плюс эпоха ──
    m = re.search(r"(\d[\d,]*)\s*(?:\w+\s+){0,2}?(bc|bce|ad|ce)\b", low)
    if m:
        return f"{m.group(1).rstrip(',')} {ERA_TOKENS[m.group(2)]}"

    # ── числительное словами плюс эпоха ──
    for i, w in enumerate(words):
        if w not in ERA_TOKENS:
            continue
        chain = []
        k = i - 1
        while k >= 0 and (words[k] in NUM_TOKENS or words[k] in ("years",)):
            if words[k] in NUM_TOKENS:
                chain.insert(0, words[k])
            k -= 1
        if chain:
            value = _to_digits(chain)
            if value:
                return f"{value:,} {ERA_TOKENS[w]}"
    return None

# Насколько далеко за числом искать единицу измерения. Два слова, потому
# что между ними стандартно встаёт определение: «одна тысяча четыреста
# двадцать семь ЗОЛОТЫХ монет». На одном слове единица терялась, и на
# экран уходило голое «1,427».
UNIT_LOOKAHEAD = 2


def font_path():
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


# ─────────────────────── ГДЕ СТАВИТЬ ───────────────────────

def _phrase_at(text: str):
    """
    Вытаскивает числовую фразу из предложения.

    Возвращает короткую строку для экрана либо None. Фраза обрезается по
    четырём словам: «one thousand four hundred and twenty-seven gold coins»
    на экране нечитаемо, а «1,427 COINS» читается за полсекунды — поэтому
    словесные числительные ещё и сворачиваются в цифры.
    """
    text = text or ""
    # ДАТА ВПЕРЁД ЧИСЛА. «In 2600 BC the river rose four metres» содержит
    # и то, и другое, и на экран должна уйти дата: сюжет канала держится
    # на времени, а «4 METRES» — это подпись к чему-то другому.
    era = _era_phrase(text)
    if era:
        return era

    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9\-]+", text)
    low = [w.lower() for w in words]

    # Готовая цифра в тексте. Ищется ПО ИСХОДНОЙ СТРОКЕ, а не по разбитым
    # словам: разделитель тысяч в класс символов слова не входит, и «43,000»
    # приезжало сюда двумя кусками, из которых брался первый. На экране
    # выходило «43» вместо «43,000» — ошибка в тысячу раз, и молчаливая.
    mnum = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if mnum:
        raw = mnum.group(0).rstrip(".,")
        tail = text[mnum.end():].strip().split()
        unit = tail[0].strip(".,!?").upper() if tail and \
            tail[0].strip(".,!?").lower() in UNIT_TOKENS else ""
        return f"{raw} {unit}".strip().upper()

    # числительные словами: ищем самую длинную непрерывную цепочку
    best, cur = [], []
    for i, w in enumerate(low):
        if w in NUM_TOKENS or re.fullmatch(r"(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)-\w+", w):
            cur.append(i)
        else:
            if len(cur) > len(best):
                best = cur
            cur = []
    if len(cur) > len(best):
        best = cur
    if not best:
        return None

    span = words[best[0]:best[-1] + 1]

    # Единица измерения ищется не вплотную за числом, а в пределах двух
    # слов: между ними стандартно встаёт определение («тысяча четыреста
    # двадцать семь ЗОЛОТЫХ монет»).
    unit = None
    for k in range(best[-1] + 1, min(best[-1] + 1 + UNIT_LOOKAHEAD, len(low))):
        if low[k] in UNIT_TOKENS:
            unit = words[k]
            break

    # Однословное числительное берётся ТОЛЬКО с единицей измерения. Без
    # неё «one» из «one of the three» стало бы плашкой «1» — а таких «one»
    # в любом сценарии десятки, и каждое просилось бы на экран.
    if len(best) < 2 and unit is None:
        return None
    if unit:
        span = span + [unit]

    value = _to_digits([w.lower() for w in span])
    if value is not None:
        unit = span[-1].upper() if span[-1].lower() in UNIT_TOKENS else ""
        return f"{_display(value)} {unit}".strip()
    if len(span) > 5:
        return None
    return " ".join(span).upper()


def _display(v: int) -> str:
    """
    Число на экран. Крупные разряды словом, остальное цифрами.

    «43,000,000» в углу кадра читается как случайный набор нулей, «43
    MILLION» — мгновенно. А вот «1,427» словом («ONE THOUSAND FOUR HUNDRED
    AND TWENTY SEVEN») не читается вовсе, поэтому граница проходит по
    миллиону, а не по тысяче.
    """
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:g} BILLION"
    if v >= 1_000_000:
        return f"{v / 1_000_000:g} MILLION"
    return f"{v:,}"


_ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALE = {"hundred": 100, "thousand": 1000, "million": 1_000_000,
          "billion": 1_000_000_000}


def _to_digits(tokens):
    """
    Словесное числительное -> целое число. Не разобралось — None.

    Обычный разбор английских числительных: разряд УМНОЖАЕТ накопленное и
    сбрасывает накопитель, а не прибавляется к нему.

    Первая версия прибавляла, и «one thousand four hundred and twenty
    seven» превращалось в 428 вместо 1427 — единица от «one thousand»
    складывалась с 427 вместо того, чтобы стать тысячей. Поймано
    прогоном на реальных фразах сценария: ни один тест на отдельных
    словах такую ошибку не показывает, она вылезает только на составных.
    """
    total, current = 0, 0
    seen = False
    for t in tokens:
        for part in t.split("-"):
            if part in _ONES:
                current += _ONES[part]
                seen = True
            elif part in _TENS:
                current += _TENS[part]
                seen = True
            elif part == "hundred":
                current = max(current, 1) * 100
                seen = True
            elif part in _SCALE:                 # thousand / million / billion
                total += max(current, 1) * _SCALE[part]
                current = 0
                seen = True
            elif part in ("and",) or part in UNIT_TOKENS:
                continue
            else:
                return None
    if not seen:
        return None
    total += current
    return total or None


def moments(beats, marks, vector, rng, skip_times=None):
    """
    Где по таймлайну поставить плашки.

    Возвращает список словарей {t, text, style, place, hold}. Пустой список
    — нормальный результат: у ролика может не выпасть стиль либо не найтись
    ни одного числа.

    skip_times — секунды, ВОКРУГ которых плашек не ставить: туда уже
    встали карточки-прерывания (см. interrupts), и та же дата дважды
    подряд — угловой плашкой и полным экраном — читается как заикание.
    """
    style = vector.get("text_style", "none")
    if style == "none" or not font_path():
        return []
    density = float(vector.get("text_density", 0.3))
    skip = list(skip_times or [])

    # кандидаты — только доли-развязки и нагнетания: плашка на завязке
    # оформляет то, что не требует оформления
    want_kinds = {"revelation", "escalation"}
    cands = []
    for b in beats:
        if b.kind not in want_kinds:
            continue
        for m in marks[b.first_mark:b.last_mark + 1]:
            phrase = _phrase_at(m.get("text", ""))
            if phrase and 2 <= len(phrase) <= 22:
                cands.append((m["start"], phrase, b.kind))

    if not cands:
        return []

    # прореживаем: не чаще одной плашки на MIN_GAP и не больше, чем просит
    # плотность. Две плашки подряд превращают ролик в инфографику.
    MIN_GAP = 45.0
    picked, last_t = [], -1e9
    for t, phrase, kind in cands:
        if t - last_t < MIN_GAP:
            continue
        if any(abs(t - s) < 30.0 for s in skip):
            continue
        if rng.random() > density:
            continue
        picked.append(dict(
            t=round(t, 3), text=phrase,
            style=style if style in STYLES else rng.choice(STYLES),
            place=rng.choice(list(PLACES)),
            hold=round(rng.uniform(*HOLD), 2)))
        last_t = t
    return picked


# ─────────────────────── КАРТОЧКИ-ПРЕРЫВАНИЯ ───────────────────────

# Не чаще одной на этот интервал. Прерывание работает, потому что оно
# редкое: три чёрных карточки за десять минут — это уже не приём, а
# слайд-презентация.
INTERRUPT_GAP = 480.0
INTERRUPT_MAX = 4
# Короче этого ролика карточки не ставятся вовсе: на тестовых сборках и
# коротких роликах им негде быть редкими.
INTERRUPT_MIN_TOTAL = 15 * 60.0


def interrupts(beats, marks, rng, total: float):
    """
    Полноэкранные карточки на главных датах ролика.

    Паттерн-прерывание в чистом виде: кадр на несколько секунд уступает
    место чёрному полю с одной цифрой — «2600 BC». Через сорок минут
    ровного видеоряда любое короткое НЕ-видео перезапускает внимание, и
    это единственное место конвейера, где такое позволено. Только на
    развязках, не чаще одной на восемь минут, не больше четырёх на ролик.

    Музыка под карточкой приседает — точки для ям забирает build.py из
    поля t этих же словарей.
    """
    if total < INTERRUPT_MIN_TOTAL or not font_path():
        return []
    cands = []
    for b in beats:
        if b.kind != "revelation":
            continue
        for m in marks[b.first_mark:b.last_mark + 1]:
            phrase = _phrase_at(m.get("text", ""))
            if phrase and 2 <= len(phrase) <= 16:
                # чем «цифровее» доля, тем раньше её кандидат в очереди
                cands.append((-b.features.get("num", 0.0), m["start"], phrase))
    if not cands:
        return []

    cands.sort()
    picked = []
    for _score, t, phrase in cands:
        if len(picked) >= INTERRUPT_MAX:
            break
        # к краям ролика не лепим: в первые минуты зритель ещё решает
        # остаться, в последние карточка спорит с уходом в чёрное
        if t < 240.0 or t > total - 90.0:
            continue
        if any(abs(t - p["t"]) < INTERRUPT_GAP for p in picked):
            continue
        picked.append(dict(t=round(t, 3), text=phrase, style="interrupt",
                           place="lower_left",
                           hold=round(rng.uniform(3.6, 4.6), 2)))
    picked.sort(key=lambda p: p["t"])
    return picked


# ─────────────────────── ТИТУЛЫ ГЛАВ ───────────────────────

# Сколько титул держится. Дольше плашки-числа: его читают не как факт, а
# как ориентир, и он не должен исчезнуть раньше, чем зритель поднял глаза.
CHAPTER_HOLD = (4.6, 5.6)
CHAPTER_TEXT_MAX = 40


def chapter_titles(names, edges, rng):
    """
    Титул главы в момент её начала.

    Зритель этого канала слушает вполуха и часто с закрытыми глазами;
    тот, кто смотрит, должен видеть смену главы, а не только слышать её
    в длинном переходе. Названия уже написаны человеком для описания
    YouTube — здесь они просто ставятся в кадр, через секунду после
    границы, когда переход уже отработал.

    names — названия глав из спецификации, edges — [(секунда, номер
    блока)] из плана кадров. Первая глава титула не получает: ролик
    только что открылся, и подпись поверх открытия спорит с ним.
    """
    if not names or not edges or not font_path():
        return []
    out = []
    for t, block in edges:
        if not 0 < block < len(names):
            continue
        text = str(names[block]).strip().upper()[:CHAPTER_TEXT_MAX]
        if not text:
            continue
        out.append(dict(t=round(t + 1.0, 3), text=text, style="fade_slow",
                        place="lower_left", size=40,
                        hold=round(rng.uniform(*CHAPTER_HOLD), 2)))
    return out


# ─────────────────────── ЧЕМ РИСОВАТЬ ───────────────────────

def _esc(s: str) -> str:
    """Экранирование текста для drawtext. Порядок важен: слэш первым."""
    s = s.replace("\\", r"\\\\")
    for ch in (":", "'", "%", ",", "[", "]", ";"):
        s = s.replace(ch, "\\" + ch)
    return s


def filter_chain(items, font=None, size_scale=1.0):
    """
    Цепочка drawtext для списка плашек.

    items — [{t_local, text, style, place, hold}], где t_local это секунда
    ВНУТРИ той дорожки, на которую цепочка ляжет. Пересчёт из абсолютного
    времени делает вызывающий: только он знает смещения групп склейки.

    Возвращает строку фильтров (может быть пустой).
    """
    font = font or font_path()
    if not font or not items:
        return ""

    parts = []
    for it in items:
        t0 = float(it["t_local"])
        hold = float(it.get("hold", 3.0))
        t1 = t0 + hold
        place = PLACES.get(it.get("place"), PLACES["lower_left"])
        # размер задаётся и на плашку: титул главы мельче цифры-факта
        size = int(float(it.get("size", 58)) * size_scale)
        parts.append(_one(it, t0, t1, place, font, size))
    return ",".join(p for p in parts if p)


def _one(it, t0, t1, place, font, size):
    style = it.get("style", "stamp")
    txt = _esc(it["text"])
    x, y = place["x"], place["y"]
    # общая обёртка: плашка живёт только в своём окне
    en = f"between(t\\,{t0:.3f}\\,{t1:.3f})"

    # вход и выход по альфе — общие для всех стилей, различается движение
    fade_in, fade_out = 0.35, 0.45
    alpha = (f"if(lt(t\\,{t0 + fade_in:.3f})\\,"
             f"(t-{t0:.3f})/{fade_in}\\,"
             f"if(gt(t\\,{t1 - fade_out:.3f})\\,"
             f"({t1:.3f}-t)/{fade_out}\\,1))")

    base = (f"fontfile={font}:text='{txt}':fontcolor=white:"
            f"fontsize={size}:borderw=4:bordercolor=black@0.85:"
            f"shadowx=2:shadowy=2:shadowcolor=black@0.5:enable='{en}'")

    if style == "interrupt":
        # ПОЛНОЭКРАННАЯ КАРТОЧКА. Кадр уступает место чёрному полю с
        # одной цифрой по центру. Поле не до конца глухое (0.90): сквозь
        # него едва читается движение кадра, и карточка остаётся частью
        # ролика, а не вставленным слайдом. Позиция раскладки place здесь
        # не используется — центр и есть смысл приёма.
        box = (f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.90:t=fill:"
               f"enable='{en}'")
        big = (f"fontfile={font}:text='{txt}':fontcolor=0xF3EFE6:"
               f"fontsize={int(size * 2.3)}:borderw=0:"
               f"shadowx=0:shadowy=4:shadowcolor=black@0.6:"
               f"x=(w-text_w)/2:y=(h-text_h)/2:enable='{en}'")
        return f"{box},drawtext={big}:alpha='{alpha}'"

    if style == "carved":
        # ВЫСЕЧЕНО В КАМНЕ. Разрядка между буквами плюс приглушённый цвет
        # вместо чистого белого: надпись перестаёт быть титром и
        # становится частью кадра.
        #
        # Разрядка делается ПРОБЕЛАМИ В САМОМ ТЕКСТЕ, а не настройкой:
        # у drawtext нет поля межбуквенного расстояния вовсе, и обойти
        # это можно только так. Пробел узкий (обычный), поэтому «2600 BC»
        # превращается в «2 6 0 0  B C» и занимает вдвое больше места —
        # ради этого размер шрифта здесь на четверть меньше.
        spaced = _esc(" ".join(it["text"]))
        carved = (f"fontfile={font}:text='{spaced}':fontcolor=0xE8E2D6:"
                  f"fontsize={int(size * 0.76)}:borderw=3:"
                  f"bordercolor=black@0.80:shadowx=2:shadowy=2:"
                  f"shadowcolor=black@0.5:expansion=none:enable='{en}'")
        return f"drawtext={carved}:x={x}:y='{y}':alpha='{alpha}'"

    if style == "fade_slow":
        # Просто проявление, но втрое медленнее обычного. Самый тихий
        # стиль набора и самый уместный на канале, который смотрят,
        # засыпая.
        slow_in, slow_out = 1.1, 1.3
        a = (f"if(lt(t\\,{t0 + slow_in:.3f})\\,"
             f"(t-{t0:.3f})/{slow_in}\\,"
             f"if(gt(t\\,{t1 - slow_out:.3f})\\,"
             f"({t1:.3f}-t)/{slow_out}\\,1))")
        return f"drawtext={base}:x={x}:y='{y}':alpha='{a}'"

    if style == "stamp":
        # удар: буквы приходят чуть крупнее и садятся на место. Размер в
        # drawtext не анимируется, поэтому «удар» делается альфой с резким
        # фронтом и подскоком по вертикали на несколько пикселей.
        bump = (f"{y}-14*max(0\\,1-(t-{t0:.3f})/0.22)")
        return (f"drawtext={base}:x={x}:y='{bump}':"
                f"alpha='min(1\\,{alpha}*1.6)'")

    if style == "slide_up":
        rise = f"{y}+52*max(0\\,1-(t-{t0:.3f})/0.55)"
        return f"drawtext={base}:x={x}:y='{rise}':alpha='{alpha}'"

    if style == "underline_wipe":
        # Текст стоит, под ним уезжает линия.
        #
        # Координаты для drawbox пишутся ЧЕРЕЗ iw/ih, а не через W/H.
        # Заглавные W и H знает только drawtext; drawbox на них падает с
        # «Undefined constant», причём падает не при разборе строки, а
        # при первом кадре — то есть на середине рендера группы. Поймано
        # прогоном всех четырёх стилей через ffmpeg, глазами такое не
        # видно вовсе.
        bx = x.replace("W", "iw").replace("H", "ih")
        by = y.replace("W", "iw").replace("H", "ih")
        line_w = f"min(1\\,(t-{t0:.3f})/0.6)"
        under = (f"drawbox=x='{bx}':y='{by}+{int(size * 1.15)}':"
                 f"w='{int(size * 0.62)}*{len(it['text'])}*{line_w}':"
                 f"h=5:color=white@0.85:t=fill:enable='{en}'")
        return f"drawtext={base}:x={x}:y='{y}':alpha='{alpha}',{under}"

    if style == "typewriter":
        # посимвольное появление. drawtext не умеет резать строку по
        # времени, поэтому строка печатается слоями: каждый слой — на один
        # символ длиннее и живёт свой отрезок. Для строк до 22 символов это
        # два десятка вызовов, что рендер не замечает.
        step = 0.055
        layers = []
        s = it["text"]
        for k in range(1, len(s) + 1):
            a = t0 + step * (k - 1)
            b = t0 + step * k if k < len(s) else t1
            layers.append(
                f"drawtext=fontfile={font}:text='{_esc(s[:k])}':"
                f"fontcolor=white:fontsize={size}:borderw=4:"
                f"bordercolor=black@0.85:x={x}:y='{y}':"
                f"alpha='{alpha}':"
                f"enable='between(t\\,{a:.3f}\\,{b:.3f})'")
        return ",".join(layers)

    return f"drawtext={base}:x={x}:y='{y}':alpha='{alpha}'"
