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

# ШРИФТ ТИТРОВ — отдельный от плашек, и это не прихоть.
#
# Плашка-число должна ЧИТАТЬСЯ мгновенно поверх любого кадра: жирная
# гротеска с чёрной обводкой. Титр решает обратную задачу — он висит на
# спокойном кадре несколько секунд и не должен спорить с ним. Поэтому
# тонкое геометрическое начертание с широкой разрядкой: так набирают
# заставки документальных фильмов, и именно этот вид заказан.
#
# Montserrat Light лежит В РЕПОЗИТОРИИ, а не берётся из системы. На
# раннере GitHub Actions стоит только DejaVu, и титр, набранный ею,
# выглядел бы обычным жирным текстом — то есть ровно тем, чего здесь
# избегаем. Тот же приём уже применён к шрифтам шортсов.
# Regular, а не Light: заказано «тот же шрифт, но чуть пожирнее».
# Light повторяет референс один в один и на ярком дневном кадре местами
# теряется — Regular держит тот же геометрический рисунок и разрядку, но
# уверенно читается. Light лежит рядом на случай возврата.
TITLE_FONT_CANDIDATES = [
    str(Path(__file__).parent.parent.parent
        / "assets" / "fonts" / "Montserrat-Regular.ttf"),
    str(Path(__file__).parent.parent.parent
        / "assets" / "fonts" / "Montserrat-Light.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
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
    # Титры центруются ПО ШИРИНЕ САМОГО ТЕКСТА (text_w), а не по фиксированной
    # доле кадра: длина названия главы заранее неизвестна и гуляет втрое.
    "center":      dict(x="(w-text_w)/2", y="(h-text_h)/2"),
    "center_high": dict(x="(w-text_w)/2", y="H*0.400"),
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


def title_font_path():
    """Шрифт титров. Нет ни одного — титры молча выключаются."""
    for p in TITLE_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _spaced(text: str) -> str:
    """
    Разрядка ПО СЛОВАМ, а не по всей строке подряд.

    У drawtext нет межбуквенного расстояния вовсе, разрядка делается
    пробелами в самом тексте. Но `" ".join(строка)` на фразе из двух слов
    даёт «Т Е К С Т   И   Е Щ Ё» с одинаковыми промежутками везде — слова
    сливаются в одну ленту и фраза перестаёт читаться. Поэтому буквы
    внутри слова разводятся одним пробелом, а слова между собой — тремя.
    """
    return "   ".join(" ".join(w) for w in (text or "").split())


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


# ─────────────────────── ТИТРЫ ───────────────────────
#
# Три титра одного семейства, все набраны одним шрифтом и одним приёмом
# (см. стиль "title"), различаются только размером и моментом:
#
#   opening_title  — название ролика в первые секунды, самый крупный
#   chapter_titles — название главы В ПАУЗЕ диктора, вдвое мельче
#   the_end        — на чёрном хвосте, вдвое мельче названия ролика
#
# Кадр 1920 шириной; титр не должен подходить к краям ближе чем на 7%.
FRAME_W = 1920
# 0.80, а не 0.86: на замере длинное название почти упиралось в края, а
# титру нужен воздух — он висит несколько секунд, и тесная строка читается
# как ошибка вёрстки.
TITLE_FILL = 0.80

# Название ролика. Появляется не в нулевую секунду, а когда открытие уже
# показало пару кадров: титр поверх первого же кадра читается как заставка
# телеканала, а не как название фильма.
#
# И НЕ ПОСРЕДИ ПЕРВОЙ ФРАЗЫ. Сценарии этого канала открываются холодным
# крючком — «Я пересматривал запись с камеры больше раз, чем могу
# сосчитать. Четыре утра, шестое июля 2022-го». Титр, выехавший на 1.8 с,
# садится ровно на эту фразу и растаскивает внимание надвое: зритель и
# читает, и слушает, и не удерживает ни того ни другого. Поэтому заставка
# ждёт, пока диктор ДОГОВОРИТ первое предложение, и выходит в паузу за
# ним. OPENING_AT остаётся запасным значением на случай, когда тайм-кодов
# нет вовсе (смоук, синтетика).
OPENING_AT = 1.8
OPENING_GAP = 0.35        # столько после последнего слова первой фразы
OPENING_MIN = 1.8         # раньше — титр наезжает на самое начало кадра
OPENING_MAX = 16.0        # позже — зритель уже не свяжет титр с роликом
OPENING_HOLD = 5.6
OPENING_SIZE = 92
OPENING_TEXT_MAX = 46

# Титул главы держится дольше плашки-числа: его читают не как факт, а как
# ориентир, и он не должен исчезнуть раньше, чем зритель поднял глаза.
CHAPTER_HOLD = (4.6, 5.6)
CHAPTER_TEXT_MAX = 40
CHAPTER_SIZE = 52

# НА СКОЛЬКО ТИТУЛ ОПЕРЕЖАЕТ ПЕРВОЕ СЛОВО ГЛАВЫ.
#
# Между главами уже стоит пауза 2.0-3.0 с (assets.build_voice, pause_NN.mp3),
# и титул обязан появиться В НЕЙ, а не поверх начавшейся речи: сначала
# тишина и название на экране, потом диктор начинает говорить, и название
# уходит. Отсюда и число — оно чуть меньше САМОЙ КОРОТКОЙ паузы (2.0),
# иначе на коротком стыке титул наползал бы на конец предыдущей главы.
CHAPTER_LEAD = 1.9

# Финальный титр на чёрном.
THE_END_TEXT = "THE END"
THE_END_AT = 1.2          # через сколько после начала чёрного кадра
THE_END_HOLD = 4.0
THE_END_SIZE = int(OPENING_SIZE * 0.5)


def _fit_size(text: str, want: int, floor: int = 22) -> int:
    """
    Размер, при котором РАЗРЯЖЕННЫЙ текст влезает в кадр по ширине.

    Разрядка растягивает строку вдвое с лишним, и длина названия главы
    гуляет от одного слова до пяти. Фиксированный кегль поэтому не
    годится: «A COMPASS CUT IN STONE» при том же размере, что «THE END»,
    уходит за края кадра — а титр, обрезанный рамкой, выглядит браком
    рендера, и увидеть это можно только на готовом ролике.

    Меряется настоящими метриками шрифта; если PIL недоступен, работает
    грубая оценка по средней ширине знака.
    """
    spaced = _spaced(text)
    if not spaced:
        return want
    limit = FRAME_W * TITLE_FILL
    path = title_font_path()
    width_at_want = None
    if path:
        try:
            from PIL import ImageFont
            f = ImageFont.truetype(path, want)
            box = f.getbbox(spaced)
            width_at_want = box[2] - box[0]
        except Exception:
            width_at_want = None
    if width_at_want is None:
        # Разряженная строка — примерно наполовину пробелы, поэтому
        # средняя ширина знака заметно меньше кегля.
        width_at_want = len(spaced) * want * 0.42
    if width_at_want <= limit:
        return want
    return max(floor, int(want * limit / width_at_want))


def _card(t, text, size, hold, place, fade_in, fade_out):
    """Одна карточка семейства «титр»."""
    return dict(t=round(float(t), 3), text=text, style="title",
                place=place, size=_fit_size(text, size),
                hold=round(float(hold), 2),
                font=title_font_path(),
                fade_in=fade_in, fade_out=fade_out)


def short_title(job) -> str:
    """
    Короткое название ролика для заставки.

    Заголовок YouTube написан под выдачу и поиск — он длинный и с
    уточнением в скобках («…: America's Most Mysterious Monument (And Who
    Blew It Up)»). На экран идёт только та его часть, которая называет
    ТЕМУ: до двоеточия, а если и она длинна — до скобки. Явное поле
    youtube.opening_title перебивает разбор целиком.
    """
    y = (job or {}).get("youtube") or {}
    explicit = str(y.get("opening_title") or "").strip()
    if explicit:
        return explicit.upper()[:OPENING_TEXT_MAX]
    title = str(y.get("title") or (job or {}).get("topic") or "").strip()
    for sep in (":", "—", " - ", "("):
        if sep in title:
            head = title.split(sep)[0].strip()
            if len(head) >= 8:
                title = head
                break
    return title.upper()[:OPENING_TEXT_MAX]


def opening_at(marks) -> float:
    """
    Когда выводить заставку: в паузу ПОСЛЕ первой фразы диктора.

    Фиксированная секунда здесь не работает, потому что первая фраза у
    каждого выпуска своя: где-то она длится две секунды, где-то восемь.
    Титр, выехавший по таймеру, в первом случае опаздывает, во втором —
    садится ровно на крючок сценария.

    Тайм-коды у нас посимвольные от ElevenLabs, конец первой фразы
    известен точно, так что считать тут нечего — надо просто взять его.
    Границы обязательны: сценарий может открыться одним словом («Тишина.»)
    или, наоборот, абзацем без единой точки.
    """
    if not marks:
        return OPENING_AT
    try:
        end = float(marks[0]["end"])
    except (KeyError, TypeError, ValueError):
        return OPENING_AT
    return round(min(OPENING_MAX, max(OPENING_MIN, end + OPENING_GAP)), 3)


def opening_title(job, marks=None):
    """
    Название ролика поверх открывающей нарезки, в паузу после первой фразы.

    Зритель включает ролик и должен увидеть, ПРО ЧТО он, не читая
    описание, — это же и есть первое обещание, ради которого он остаётся.
    Но увидеть он должен ПОСЛЕ крючка, а не поверх него: см. opening_at.
    """
    text = short_title(job)
    if not text or not title_font_path():
        return []
    return [_card(opening_at(marks), text, OPENING_SIZE, OPENING_HOLD,
                  "center_high", fade_in=1.1, fade_out=1.3)]


def chapter_titles(names, edges, rng):
    """
    Титул главы В ПАУЗЕ перед ней.

    Зритель этого канала слушает вполуха и часто с закрытыми глазами;
    тот, кто смотрит, должен видеть смену главы, а не только слышать её.
    Названия уже написаны человеком для описания YouTube — здесь они
    ставятся в кадр, но НЕ поверх речи: между главами стоит пауза
    диктора, и титул занимает её (см. CHAPTER_LEAD).

    names — названия глав из спецификации, edges — [(секунда, номер
    блока)] из плана кадров. Первая глава титула не получает: её место
    занимает название ролика, и два титра подряд спорили бы друг с другом.
    """
    if not names or not edges or not title_font_path():
        return []
    out = []
    for t, block in edges:
        if not 0 < block < len(names):
            continue
        text = str(names[block]).strip().upper()[:CHAPTER_TEXT_MAX]
        if not text:
            continue
        out.append(_card(max(0.0, t - CHAPTER_LEAD), text,
                         CHAPTER_SIZE, rng.uniform(*CHAPTER_HOLD),
                         "center", fade_in=1.0, fade_out=1.2))
    return out


def the_end(tail_start: float):
    """
    THE END на чёрном хвосте.

    Ставится не в конец картинки, а в НАЧАЛО тишины: после последнего
    слова кадр уже погас, и несколько секунд чёрного — часть формата (см.
    build.TAIL_HOLD_SECONDS). Титр занимает первую половину этого
    времени, вторая остаётся настоящей тишиной без единого знака на
    экране — именно она и есть послевкусие, ради которого хвост заведён.

    ВРЕМЯ У ЭТОГО ТИТРА ОСОБОЕ — anchor="tail". Все остальные карточки
    пересчитываются из абсолютной секунды в локальную вычитанием начала
    группы, но чёрные кадры дорисовывает tpad уже ПОСЛЕ склейки, и на
    таймлайне ролика их ещё нет. Вдобавок переходы xfade сжимают группу,
    и её настоящая длина не равна сумме длительностей кадров. Поэтому
    место титра считает join(): от конца своей группы плюс offset. Здесь
    t остаётся только для сортировки слоёв и лога.
    """
    if not title_font_path():
        return []
    card = _card(tail_start + THE_END_AT, THE_END_TEXT,
                 THE_END_SIZE, THE_END_HOLD,
                 "center", fade_in=1.0, fade_out=1.4)
    card["anchor"] = "tail"
    card["offset"] = THE_END_AT
    return [card]


# ─────────────────────── ЧЕМ РИСОВАТЬ ───────────────────────

def _esc(s: str) -> str:
    """
    Экранирование текста для drawtext.

    Порядок важен: слэш первым. ASCII-апостроф и прямые кавычки
    НОРМАЛИЗУЮТСЯ в типографские ДО экранирования: `filter_complex`
    в build.join собирается внутри shell double-quotes, и последовательность
    \\' там либо ломает разбор графа (exit 234, label out не создаётся),
    либо доходит до ffmpeg уже не той. Типографские ’ “ ” в single-quoted
    text= drawtext безопасны и глазу неотличимы. Поймано на ufos-history-01
    титулом «Egypt's Fiery Disks…».
    """
    s = (s.replace("'", "\u2019")
          .replace("`", "\u2019")
          .replace('"', "\u201d"))
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
    # Шрифт проверяется У КАЖДОЙ карточки, а не один раз на всю цепочку:
    # у титров он свой (title_font_path), и раньше отсутствие СИСТЕМНОЙ
    # DejaVu выключало бы заодно и их — хотя лежат они в репозитории и
    # доступны всегда.
    if not items or (not font and not title_font_path()):
        return ""

    parts = []
    for it in items:
        if not (it.get("font") or font):
            continue
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

    # вход и выход по альфе — общие для всех стилей, различается движение.
    # Титру нужны СВОИ, длинные: плашка-число обязана появиться быстро,
    # пока число звучит, а титр обязан проявиться медленно — резкий титр
    # на спокойном кадре читается как врезка рекламы.
    fade_in = float(it.get("fade_in", 0.35))
    fade_out = float(it.get("fade_out", 0.45))
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

    if style == "title":
        # ТИТР: название ролика, название главы, THE END.
        #
        # Три отличия от плашки, и все три намеренные:
        #   - тонкое начертание с широкой разрядкой (см. _spaced) вместо
        #     жирной гротески: заставка документального фильма, а не титр;
        #   - БЕЗ чёрной обводки. Обводка нужна плашке, чтобы читаться
        #     поверх чего угодно; на титре она превращает тонкие штрихи в
        #     жирные и убивает весь смысл шрифта. Вместо неё — мягкая
        #     размытая тень, она отделяет текст от кадра и не утолщает его;
        #   - цвет чуть тёплее чистого белого: 0xFFFFFF на ночном кадре
        #     бьёт по глазам, а ролик смотрят перед сном.
        tfont = it.get("font") or font
        spaced = _esc(_spaced(it["text"]))
        body = (f"fontfile={tfont}:text='{spaced}':fontcolor=0xF2EFE9:"
                f"fontsize={size}:borderw=0:"
                f"shadowx=0:shadowy=3:shadowcolor=black@0.55:"
                f"expansion=none:enable='{en}'")
        return f"drawtext={body}:x={x}:y='{y}':alpha='{alpha}'"

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
