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
# Montserrat Light и Montserrat Regular лежат В РЕПОЗИТОРИИ, а не берутся
# из системы. На раннере GitHub Actions стоит только DejaVu, и титр,
# набранный ею, выглядел бы обычным жирным текстом — то есть ровно тем,
# чего здесь избегаем. Тот же приём уже применён к шрифтам шортсов.
#
# ДВА НАЧЕРТАНИЯ НА ДВЕ РОЛИ, и это не оговорка, а различие по замыслу.
# Название ролика — самый заметный титр семейства, и обязано доминировать
# в кадре с первого взгляда; титул главы обязан визуально отличаться от
# него, а не быть тем же блоком помельче (см. CLAUDE.md, «Титры»). Раньше
# оба титра делили один и тот же TITLE_FONT_CANDIDATES, и Regular был
# выбран для ОБОИХ разом — Light повторяет референс один в один, но на
# ярком дневном кадре местами теряется. Компромисс на двоих читался как
# «тот же шрифт, чуть пожирнее» и различия между заставкой и главой не
# давал вовсе.
#
# Теперь у названия ролика (opening_title) — свой список,
# OPENING_FONT_CANDIDATES, Light первым: он крупнее (OPENING_SIZE) и
# держится на экране дольше, запас на лёгкую потерю контраста есть, а
# усиленная тень в _one() (style="title") компенсирует остальное. Титул
# главы и THE END остаются на TITLE_FONT_CANDIDATES (Regular) — тот же
# выбор, что уже проверен на «уверенно читается», трогать незачем.
TITLE_FONT_CANDIDATES = [
    str(Path(__file__).parent.parent.parent
        / "assets" / "fonts" / "Montserrat-Regular.ttf"),
    str(Path(__file__).parent.parent.parent
        / "assets" / "fonts" / "Montserrat-Light.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
OPENING_FONT_CANDIDATES = [
    str(Path(__file__).parent.parent.parent
        / "assets" / "fonts" / "Montserrat-Light.ttf"),
] + TITLE_FONT_CANDIDATES

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

# Ширина кадра. Общая на весь модуль (титры, акценты-столбики 2.4.2) —
# определена здесь, а не в секции титров ниже, ради этого.
FRAME_W = 1920

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
    """Шрифт титров (титул главы, THE END). Нет ни одного — молча выключаются."""
    for p in TITLE_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def opening_font_path():
    """
    Шрифт названия ролика — свой, Light первым (см. OPENING_FONT_CANDIDATES).

    Нет Light на диске — тихо падает на то же, чем набран титул главы
    (Regular), а не остаётся вовсе без заставки: лучше более жирный
    титр, чем никакого.
    """
    for p in OPENING_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _spaced(text: str, dense: bool = False) -> str:
    """
    Разрядка ПО СЛОВАМ, а не по всей строке подряд.

    У drawtext нет межбуквенного расстояния вовсе, разрядка делается
    пробелами в самом тексте. Но `" ".join(строка)` на фразе из двух слов
    даёт «Т Е К С Т   И   Е Щ Ё» с одинаковыми промежутками везде — слова
    сливаются в одну ленту и фраза перестаёт читаться. Поэтому буквы
    внутри слова разводятся одним пробелом, а слова между собой — тремя.

    dense=True (2.2.2, титул главы против названия ролика) — трекинг
    ВДВОЕ УЖЕ. Целого пробела вполовину не бывает (у drawtext нет дробной
    ширины символа), поэтому половинная плотность — это пробел ЧЕРЕЗ
    БУКВУ, а не через каждую: «T H E» при dense становится «TH E» —
    в среднем ровно половина от пробелов в «T H E» на букву. Между
    словами разрядка тоже уже (два пробела вместо трёх), но не исчезает
    совсем: словам всё ещё нужно быть различимо разделёнными.
    """
    if not dense:
        return "   ".join(" ".join(w) for w in (text or "").split())
    def half(w):
        out = []
        for i, ch in enumerate(w):
            out.append(ch)
            if i % 2 == 0 and i < len(w) - 1:
                out.append(" ")
        return "".join(out)
    return "  ".join(half(w) for w in (text or "").split())


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


# ─────────────────────── СКВОЗНЫЕ ТЕКСТОВЫЕ АКЦЕНТЫ (2.4) ───────────────────────
#
# Плашка-число (moments) — факт, полноэкранная карточка (interrupts) —
# событие; акцент — третье, самое тихое: короткая синхронная подпись к
# произнесённому слову, мелко в углу футажа или столбиком на картинке.
# Три источника, по убыванию надёжности:
#
#   1. явная разметка в спецификации (job["accents"]) — сценарист знает,
#      что в его тексте ударное, робот нет; включается без жребия;
#   2. автоподбор — тот же _phrase_at, что у moments(), но по ВСЕМ долям
#      сценария, а не только развязкам/нагнетаниям, и потому прорежен
#      жёстче;
#   3. имена собственные из assets.script_grounded_queries — самый шумный
#      источник, прорежен жёстче второго.
#
# Плотность — обязательное условие: не чаще одного на ACCENT_MIN_GAP,
# не больше ACCENT_MAX_PER_2400S на сорокаминутный ролик, ничего в первые
# ACCENT_LEAD_IN и за ACCENT_TAIL_OUT до конца.
ACCENT_MIN_GAP = 40.0
ACCENT_LEAD_IN = 30.0
ACCENT_TAIL_OUT = 60.0
ACCENT_MAX_PER_2400S = 45
# Автоподбор и имена собственные тоньше не жеребьёвкой moments() (та уже
# ограничена развязками/нагнетаниями) — здесь кандидат может прийти с
# ЛЮБОЙ доли ролика, кандидатов на порядок больше, и множитель на
# text_density обязан быть жёстче, а не тем же самым.
ACCENT_AUTO_FACTOR = 0.35
ACCENT_PROPER_FACTOR = 0.25
# Раскладки 2.4.2: футаж — мелко и в одну строку, картинка — крупнее и
# столбиком (перенос по словам, до трёх строк).
ACCENT_CLIP_SIZE = 34
ACCENT_IMAGE_SIZE = 52
ACCENT_IMAGE_MAX_LINES = 3
ACCENT_IMAGE_MAX_W = int(FRAME_W * 0.34)
ACCENT_TEXT_MAX = 24
# Короче плашки-числа (moments.HOLD = 3.8-6.0): акцент не факт для
# запоминания, а подсветка слова, которое и так уже прозвучало.
ACCENT_HOLD = (2.4, 3.6)
# Запас по обе стороны от уже стоящего титула/карточки/плашки — 2.4.2,
# «акцент не имеет права встать туда, где в эту же секунду стоит титул
# главы, полноэкранная карточка или плашка-число».
ACCENT_COLLIDE_PAD = 1.0

_WORD_CLEAN_RE = re.compile(r"[^a-zA-Zа-яА-ЯёЁ0-9\-]")


def _norm_word(w: str) -> str:
    return _WORD_CLEAN_RE.sub("", (w or "")).lower()


def _find_phrase(words, phrase: str):
    """
    Первое вхождение фразы в словах начитки — секунда начала либо None.

    Сравнение нормализованное (регистр и знаки препинания не в счёт), но
    НЕ по индексу символа script_blocks (2.4.3): единственная опора —
    посимвольный alignment ElevenLabs через слова из timing.py, потому что
    TTS растягивает и сжимает текст неравномерно и «сороковое слово»
    звучит не на сороковой доле блока.
    """
    ptoks = [t for t in (_norm_word(t) for t in (phrase or "").split()) if t]
    if not ptoks or not words:
        return None
    wtoks = [_norm_word(w.get("text", "")) for w in words]
    n = len(ptoks)
    for i in range(len(wtoks) - n + 1):
        if wtoks[i:i + n] == ptoks:
            return float(words[i]["start"])
    return None


def _shot_kind_at(shots, t: float) -> str:
    """
    Кадр, стоящий на экране в секунду t, — выбор раскладки акцента (2.4.2).

    plan_shots уже разложил shots с полем kind, а моменты считаются ПОСЛЕ
    плана (см. build.main) — искать нечего, только пройти список.
    """
    for s in shots or []:
        try:
            s0 = float(s["start"])
            s1 = s0 + float(s["duration"])
        except (KeyError, TypeError, ValueError):
            continue
        if s0 <= t < s1:
            return s.get("kind", "clip")
    return "clip"


def _wrap_lines(text, size, font, max_w, max_lines):
    """
    Перенос по словам под ширину max_w — метрика та же PIL, что у _fit_size.

    \\n внутри одного drawtext не работает (см. _one, style=="accent"):
    каждая строка идёт СВОИМ drawtext, тем же приёмом, что и typewriter.
    """
    words = (text or "").split()
    if not words:
        return [text]
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        width_px, _ = _text_metrics_px(trial, size, font)
        if cur and width_px > max_w:
            lines.append(" ".join(cur))
            cur = [w]
            if len(lines) >= max_lines:
                break
        else:
            cur.append(w)
    if cur and len(lines) < max_lines:
        lines.append(" ".join(cur))
    return lines[:max_lines] or [text]


def _explicit_accents(job, words):
    """Источник 1 — job["accents"]: say ищется в словах начитки, show
    (или say заглавными, если show не задан) идёт на экран."""
    out = []
    for item in job.get("accents") or []:
        say = str((item or {}).get("say") or "").strip()
        if not say:
            continue
        t = _find_phrase(words, say)
        if t is None:
            continue
        show = str((item or {}).get("show") or "").strip() or say.upper()
        out.append((t, show[:ACCENT_TEXT_MAX]))
    return out


def accents(job, words, beats, marks, shots, vector, rng, total,
           existing_moments=None):
    """
    Сквозные текстовые акценты (2.4) — где и что ставить.

    words — из timing.words_from_alignment/words_from_marks (пословные
    тайм-коды). existing_moments — уже собранные карточки-прерывания,
    плашки и титулы: акцент не имеет права встать туда, где в эту же
    секунду стоит любая из них (2.4.2).
    """
    if not words or not title_font_path():
        return []
    density = float(vector.get("text_density", 0.3))
    existing = list(existing_moments or [])

    auto = []
    for b in beats or []:
        for m in marks[b.first_mark:b.last_mark + 1]:
            phrase = _phrase_at(m.get("text", ""))
            if phrase and 2 <= len(phrase) <= ACCENT_TEXT_MAX:
                auto.append((float(m["start"]), phrase))

    proper = []
    try:
        import assets
        names = assets.script_grounded_queries(job, limit=10)
    except Exception:
        names = []
    for name in names:
        t = _find_phrase(words, name)
        if t is not None:
            proper.append((t, name.upper()[:ACCENT_TEXT_MAX]))

    def in_bounds(t):
        return ACCENT_LEAD_IN <= t <= total - ACCENT_TAIL_OUT

    def not_colliding(t):
        return not any(
            e["t"] - ACCENT_COLLIDE_PAD <= t
            <= e["t"] + float(e.get("hold", 3.0)) + ACCENT_COLLIDE_PAD
            for e in existing)

    picked_t, out = [], []

    def try_add(t, text, keep_prob):
        if not in_bounds(t) or not not_colliding(t):
            return
        if any(abs(t - p) < ACCENT_MIN_GAP for p in picked_t):
            return
        if keep_prob < 1.0 and rng.random() > keep_prob:
            return
        kind = _shot_kind_at(shots, t)
        card = dict(t=round(t, 3), text=text, style="accent",
                   place="lower_right" if kind == "clip" else "upper_left",
                   frame_kind=kind,
                   hold=round(rng.uniform(*ACCENT_HOLD), 2),
                   size=ACCENT_CLIP_SIZE if kind == "clip"
                        else ACCENT_IMAGE_SIZE,
                   fade_in=0.55, fade_out=0.7, font=title_font_path())
        if kind != "clip":
            card["lines"] = _wrap_lines(
                text, card["size"], card["font"],
                ACCENT_IMAGE_MAX_W, ACCENT_IMAGE_MAX_LINES)
        out.append(card)
        picked_t.append(t)

    # явные — приоритет, без жребия по плотности
    for t, text in sorted(_explicit_accents(job, words)):
        try_add(t, text, keep_prob=1.0)

    # автоподбор и имена — общий пул, жеребьёвка жёстче, чем у moments()
    for t, text in sorted(auto):
        try_add(t, text, keep_prob=density * ACCENT_AUTO_FACTOR)
    for t, text in sorted(proper):
        try_add(t, text, keep_prob=density * ACCENT_PROPER_FACTOR)

    out.sort(key=lambda c: c["t"])
    max_count = min(ACCENT_MAX_PER_2400S,
                    max(1, round(ACCENT_MAX_PER_2400S * total / 2400.0)))
    if len(out) > max_count:
        # срез вперемежку по таймлайну, а не первые N подряд — иначе
        # акценты соберутся в начале ролика и пропадут к концу
        step = len(out) / max_count
        out = [out[int(i * step)] for i in range(max_count)]
    return out


# ─────────────────────── ТИТРЫ ───────────────────────
#
# Три титра одного семейства, все набраны одним шрифтом и одним приёмом
# (см. стиль "title"), различаются только размером и моментом:
#
#   opening_title  — название ролика в первые секунды, самый крупный
#   chapter_titles — название главы В ПАУЗЕ диктора, вдвое мельче
#   the_end        — на чёрном хвосте, вдвое мельче названия ролика
#
# Кадр 1920 шириной (FRAME_W, см. начало файла); титр не должен подходить
# к краям ближе чем на 7%.
# Было 0.80: на замере длинное название почти упиралось в края, а титру
# нужен воздух — он висит несколько секунд, и тесная строка читается как
# ошибка вёрстки. Поднято до 0.84 вместе с OPENING_SIZE (см. ниже) —
# больший кегль сам по себе не даёт воздуха, если потолок ширины не
# сдвинуть тоже.
TITLE_FILL = 0.84

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
# 0.25, а не по старому 0.35 — карточка садится в НАЧАЛО настоящей паузы
# (assets.build_voice, hook_pause), а не в условный зазор после слова:
# зазор больше нужен на «успеть среагировать» глазом, чем на честную тишину.
OPENING_GAP = 0.25
OPENING_MIN = 1.8         # раньше — титр наезжает на самое начало кадра
OPENING_MAX = 16.0        # позже — зритель уже не свяжет титр с роликом
OPENING_HOLD = 5.6
# Было 92. Название ролика — самый крупный титр семейства, и на замере
# рядом с титулом главы (52) разница читалась слабее, чем должна: то,
# ради чего зритель остаётся, обязано доминировать в кадре с первого
# взгляда, а не быть «просто чуть крупнее».
OPENING_SIZE = 108
OPENING_TEXT_MAX = 46
# Пол ужимания — СВОЙ, крупнее общего (22 у _fit_size по умолчанию).
# При старте с 92 запас на ужимание был не нужен: длинные названия и так
# садились в кадр с приличным кеглем. При 108 длинное название («A
# COMPASS CUT IN STONE») может уйти в ужимание глубже — без своего пола
# оно способно съехать к общему полу в 22, и КРУПНЕЙШИЙ титр ролика
# внезапно окажется МЕЛЬЧЕ титула главы (52). Раньше проверять было не
# на чем — сюда попадёт ffmpeg-рендер реального кадра.
OPENING_SIZE_FLOOR = 56

# Титул главы держится дольше плашки-числа: его читают не как факт, а как
# ориентир, и он не должен исчезнуть раньше, чем зритель поднял глаза.
CHAPTER_HOLD = (4.6, 5.6)
CHAPTER_TEXT_MAX = 40
# 54, а не 52: разница с названием ролика (108) и так десятикратная по
# отношению — не про то, а про читаемость ЗАГЛАВНЫМИ на общем плане; 52 и
# 54 неотличимы глазом, число просто держит связку с 2.2.2 дословно.
CHAPTER_SIZE = 54
# Номер главы («CHAPTER 4 OF 13») — 2.2.3, дешёвая ориентация на длинном
# ролике: данные (номер блока, общее число глав) уже есть у chapter_titles,
# зритель на сороковой минуте не знает, «сколько ещё».
CHAPTER_NUM_SIZE = 26
CHAPTER_NUM_GAP = 14      # px между низом номера и верхом титула

# НА СКОЛЬКО ТИТУЛ ОПЕРЕЖАЕТ ПЕРВОЕ СЛОВО ГЛАВЫ.
#
# Между главами уже стоит пауза 2.0-3.0 с (assets.build_voice, pause_NN.mp3),
# и титул обязан появиться В НЕЙ, а не поверх начавшейся речи: сначала
# тишина и название на экране, потом диктор начинает говорить, и название
# уходит. Отсюда и число — оно чуть меньше САМОЙ КОРОТКОЙ паузы (2.0),
# иначе на коротком стыке титул наползал бы на конец предыдущей главы.
CHAPTER_LEAD = 1.9

# Финальный титр — гибридный финал (2.3): садится на СТЫК заморозки и
# чёрного, а не в начало чистого чёрного. Отсчёт от конца ПОСЛЕДНЕГО
# РЕАЛЬНОГО кадра (build.join, t=0 там же, где заканчивается начитка);
# кадр темнеет build.TAIL_FADE_SECONDS (6.0) секунд — THE_END_AT держит
# титр ровно посередине этого затемнения (6.0 - HOLD/2), чтобы половина
# HOLD пришлась на угасающий кадр, половина на уже чёрный. Оба числа
# обязаны остаться в связке: поменяли TAIL_FADE_SECONDS в build.py —
# пересчитать и здесь.
THE_END_TEXT = "THE END"
THE_END_HOLD = 4.0
THE_END_AT = 6.0 - THE_END_HOLD / 2   # = 4.0 при нынешних числах
THE_END_SIZE = int(OPENING_SIZE * 0.5)

# ПОСЛОВНАЯ СБОРКА ТИТРОВ (2.5 п.1) — «мягкий въезд без scale».
#
# Референс (kinetic-center-build) собирает слова с scale+blur вторым
# alpha-слоем — дорогой приём (~31× realtime, см. ТЗ), и заведён он
# только для заставки названия ролика, отдельным путём. Здесь — дешёвая
# версия для ВСЕХ существующих титров (заставка, титул главы, номер
# главы, THE END): тот же typewriter-приём из _one, только слоями по
# СЛОВАМ, а не по символам, с альфой и лёгким въездом у каждого слова
# своими. 80% ощущения референса за 0% его цены.
KINETIC_STEP = 0.12       # шаг появления слов
KINETIC_WORD_FADE = 0.35  # своё проявление у каждого слова
KINETIC_RISE = 14         # px въезда снизу, середина заявленных 12-18


def _fit_size(text: str, want: int, floor: int = 22, font_path=None,
              dense: bool = False) -> int:
    """
    Размер, при котором РАЗРЯЖЕННЫЙ текст влезает в кадр по ширине.

    Разрядка растягивает строку вдвое с лишним, и длина названия главы
    гуляет от одного слова до пяти. Фиксированный кегль поэтому не
    годится: «A COMPASS CUT IN STONE» при том же размере, что «THE END»,
    уходит за края кадра — а титр, обрезанный рамкой, выглядит браком
    рендера, и увидеть это можно только на готовом ролике.

    Меряется настоящими метриками шрифта; если PIL недоступен, работает
    грубая оценка по средней ширине знака.

    font_path — ЧЕМ ИМЕННО БУДЕТ НАБРАН ЭТОТ титр. По умолчанию
    title_font_path() (Regular), но название ролика набирается другим
    начертанием (см. opening_font_path) — и мерить его ширину шрифтом,
    которым оно не рисуется, значит мерить не то. Разница между Light и
    Regular на одном кегле не огромная, но она есть, и без этого
    параметра длинное название либо ужималось чуть сильнее нужного, либо
    едва цепляло край кадра.

    dense — мерить строку тем же трекингом, каким она реально будет
    набрана (титул главы, 2.2.2): мерить широким _spaced() текст, который
    выйдет узким dense-трекингом, значит ужимать кегль там, где ужимать
    не нужно было вовсе.
    """
    spaced = _spaced(text, dense=dense)
    if not spaced:
        return want
    limit = FRAME_W * TITLE_FILL
    path = font_path or title_font_path()
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


def _text_metrics_px(text: str, size: int, font_path=None):
    """
    Ширина и высота готовой (уже разряженной) строки в пикселях — та же
    PIL-мерка, что у _fit_size. Нужна там, где ffmpeg-переменные text_w/
    text_h недоступны: они существуют только ВНУТРИ своего drawtext,
    соседний drawbox (линия под титулом главы, 2.2.2) их не видит и падал
    бы с "Undefined constant", попроси он их напрямую.
    """
    path = font_path or title_font_path()
    if path:
        try:
            from PIL import ImageFont
            f = ImageFont.truetype(path, size)
            box = f.getbbox(text)
            return box[2] - box[0], box[3] - box[1]
        except Exception:
            pass
    return int(len(text) * size * 0.42), int(size * 0.9)


def _text_advance_px(text: str, size: int, font_path=None) -> float:
    """
    Продвижение курсора, а не чернильный bbox (2.5 п.1, раскладка слов).

    У пробела свой ADVANCE и нулевые чернила — getbbox (см. _text_metrics_px
    выше) его не видит и вернул бы для разрыва между словами ноль. С ним
    межсловный разрыв (три пробела разрядки, "   ") схлопнулся бы до нуля
    и слова легли бы друг на друга.
    """
    path = font_path or title_font_path()
    if path:
        try:
            from PIL import ImageFont
            f = ImageFont.truetype(path, size)
            return float(f.getlength(text))
        except Exception:
            pass
    return len(text) * size * 0.42


def _title_words(raw_text, size, font, dense, x, y, t0, en, alpha, color):
    """
    Пословная сборка титра (2.5 п.1) — каждое слово своим drawtext на
    СВОЕЙ фиксированной позиции (не растущая подстрока, как у typewriter:
    там центровка "едет" при каждом новом символе, здесь уже показанные
    слова обязаны остаться на месте). Позиции считаются в Python той же
    PIL-меркой, что доверена под пиксель в _fit_size, только по ADVANCE
    (_text_advance_px), а не чернильному bbox.

    x — исходная формула места ("(w-text_w)/2" у center/center_high,
    "W*0.070" у прочих). "text_w" в ней — ширина ВСЕГО титра, не одного
    слова, поэтому заменяется на готовое число ДО того, как к ней
    прибавляется собственное смещение слова; формулы без text_w (все
    места, кроме center*) эта замена не трогает.
    """
    words = (raw_text or "").split()
    if not words:
        return ""
    gap = "  " if dense else "   "
    spaced_words = [_spaced(w, dense=dense) for w in words]
    gap_w = _text_advance_px(gap, size, font)
    word_ws = [_text_advance_px(sw, size, font) for sw in spaced_words]
    total_w = sum(word_ws) + gap_w * (len(words) - 1)
    x_base = x.replace("text_w", str(int(round(total_w))))
    layers, offset = [], 0.0
    for k, (sw, ww) in enumerate(zip(spaced_words, word_ws)):
        wt0 = t0 + KINETIC_STEP * k
        entrance = f"min(1\\,max(0\\,(t-{wt0:.3f})/{KINETIC_WORD_FADE}))"
        a = f"min(({alpha})\\,{entrance})"
        rise = (f"({y})+{KINETIC_RISE}*max(0\\,"
                f"1-(t-{wt0:.3f})/{KINETIC_WORD_FADE})")
        wx = f"({x_base})+{int(round(offset))}"
        body = (f"fontfile={font}:text='{_esc(sw)}':fontcolor={color}:"
                f"fontsize={size}:borderw=0:"
                f"shadowx=1:shadowy=4:shadowcolor=black@0.68:"
                f"expansion=none:enable='{en}'")
        layers.append(f"drawtext={body}:x={wx}:y='{rise}':alpha='{a}'")
        offset += ww + gap_w
    return ",".join(layers)


def _card(t, text, size, hold, place, fade_in, fade_out, font=None, floor=22,
         dense=False, underline=False, y_shift=0, dim=False):
    """
    Одна карточка семейства «титр».

    font  — по умолчанию title_font_path() (Regular: титул главы,
            THE END). opening_title передаёт свой, opening_font_path()
            (Light) — см. комментарий там же.
    floor — свой пол ужимания у названия ролика (OPENING_SIZE_FLOOR):
            крупнейший титр ролика не должен ужаться мельче титула главы.
    dense, underline, y_shift, dim — 2.2.2/2.2.3, различие титула главы
            (уже трекинг, линия под текстом) от названия ролика и номера
            главы (сдвиг над титулом, приглушённый цвет).
    """
    font = font or title_font_path()
    return dict(t=round(float(t), 3), text=text, style="title",
                place=place, size=_fit_size(text, size, floor=floor,
                                            font_path=font, dense=dense),
                hold=round(float(hold), 2),
                font=font,
                fade_in=fade_in, fade_out=fade_out,
                dense=dense, underline=underline, y_shift=y_shift, dim=dim)


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


def opening_hold(marks) -> float:
    """
    Сколько держать заставку — весь OPENING_HOLD, если мерить нечем, но не
    дольше настоящей паузы после первой фразы (assets.build_voice, поле
    hook_pause).

    marks[1]["start"] — начало ВТОРОЙ фразы, а промежуток до него —
    настоящая длина паузы (hook_pause, обычно 2.0-2.8 с). Без потолка
    титр держался бы все OPENING_HOLD = 5.6 с и досиживал бы поверх уже
    звучащей второй фразы — то самое «карточка выезжает поверх
    продолжающейся речи», ради чего пауза и заводилась. Разрыв меньше
    секунды — это обычный стык фраз БЕЗ вставленной паузы (hook_pause=0
    в спецификации, или тайм-коды синтетические): тогда держим как раньше,
    старым поведением, а не рвём титр на пустом месте.
    """
    if not marks or len(marks) < 2:
        return OPENING_HOLD
    try:
        gap = float(marks[1]["start"]) - float(marks[0]["end"])
    except (KeyError, TypeError, ValueError):
        return OPENING_HOLD
    if gap < 1.0:
        return OPENING_HOLD
    # Пол 0.3 — чистая защита от вырожденного нуля на границе gap=1.0
    # (fade_in+fade_out=2.4 у opening_title при этом не помещается
    # целиком, но перекрытия со второй фразой всё равно не будет: это
    # честная цена короткого hook_pause, а не повод его нарушить).
    return round(min(OPENING_HOLD, max(0.3, gap - OPENING_GAP - 0.2)), 2)


def opening_title(job, marks=None):
    """
    Название ролика поверх открывающей нарезки, в паузу после первой фразы.

    Зритель включает ролик и должен увидеть, ПРО ЧТО он, не читая
    описание, — это же и есть первое обещание, ради которого он остаётся.
    Но увидеть он должен ПОСЛЕ крючка, а не поверх него: см. opening_at.
    Держится тоже не дольше самой паузы — см. opening_hold.
    """
    text = short_title(job)
    font = opening_font_path()
    if not text or not font:
        return []
    return [_card(opening_at(marks), text, OPENING_SIZE, opening_hold(marks),
                  "center_high", fade_in=1.1, fade_out=1.3,
                  font=font, floor=OPENING_SIZE_FLOOR)]


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

    2.2.2 — титул главы отличается от названия ролика по ТРЁМ осям сразу
    (кегль, вес шрифта уже даёт opening_font_path у названия, разрядка),
    а не только кеглем и положением, как было: dense=True — трекинг
    вдвое уже, underline=True — тонкая линия под текстом.

    2.2.3 — над титулом мелко «CHAPTER N OF M»: дешёвая ориентация на
    длинном ролике, зритель на сороковой минуте не знает «сколько ещё».
    Второй карточкой, а не частью первой строки — у drawtext один кегль
    на вызов, а номер набран мельче названия. y_shift считается от
    ФАКТИЧЕСКОГО кегля главного титула (main["size"] — _fit_size мог его
    ужать под длинное название), а не от номинального CHAPTER_SIZE:
    иначе на длинных названиях номер наезжал бы на титул или улетал
    слишком высоко.
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
        at = max(0.0, t - CHAPTER_LEAD)
        hold = rng.uniform(*CHAPTER_HOLD)
        main = _card(at, text, CHAPTER_SIZE, hold, "center",
                    fade_in=1.0, fade_out=1.2, dense=True, underline=True)
        out.append(main)
        _, main_h = _text_metrics_px(_spaced(text, dense=True),
                                     main["size"], main["font"])
        num_text = f"CHAPTER {block + 1} OF {len(names)}"
        _, num_h = _text_metrics_px(_spaced(num_text, dense=True),
                                    CHAPTER_NUM_SIZE, main["font"])
        y_shift = -(main_h / 2 + num_h / 2 + CHAPTER_NUM_GAP)
        out.append(_card(at, num_text, CHAPTER_NUM_SIZE, hold, "center",
                         fade_in=1.0, fade_out=1.2, dense=True, dim=True,
                         y_shift=y_shift))
    return out


def the_end(tail_start: float):
    """
    THE END на стыке заморозки и чёрного — гибридный финал (2.3).

    Раньше картинка гасла в чёрное ДО конца реального кадра, и THE END
    ставился в начало уже чистой тишины. Теперь последний кадр сначала
    замирает и темнеет САМ (build.TAIL_FADE_SECONDS секунд заморозки,
    join()), и только потом идёт чистый чёрный хвост (build.TAIL_HOLD_
    SECONDS). THE_END_AT садится на середину этого затемнения — половина
    HOLD ложится на угасающий стоп-кадр, половина уже на чёрное: «зритель
    как будто уснул», а не «экран мигнул текстом в пустоте». За чёрным
    остаётся хвост настоящей тишины без единого знака на экране — именно
    она и есть послевкусие, ради которого весь хвост заведён.

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
    # y_shift (2.2.3) — сдвиг по вертикали от места по умолчанию: номер
    # главы садится НАД титулом тем же place="center", просто с поправкой.
    y_shift = it.get("y_shift", 0)
    if y_shift:
        y = f"({y})+({y_shift})"
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
        dense = bool(it.get("dense"))
        # raw_spaced нужен целиком только подчёркиванию (ширина/высота всей
        # строки); сам текст рисуется словами — см. _title_words ниже.
        raw_spaced = _spaced(it["text"], dense=dense)
        # Тень чуть плотнее прежней (0.55 -> 0.68, shadowy 3 -> 4,
        # добавлен shadowx=1): название ролика теперь набирается Light
        # (см. opening_font_path) — на ярком архивном кадре его тонкие
        # штрихи держались хуже, чем у Regular, и прежняя тень их не
        # спасала. Титул главы и THE END остаются на Regular и от более
        # плотной тени только выигрывают — она их не утяжеляет, штрихи
        # там и так толще.
        #
        # dim (2.2.3, номер главы) — тот же титр, но приглушённый: цифра
        # ориентира не должна спорить с названием главы за внимание.
        color = "0xF2EFE9@0.55" if it.get("dim") else "0xF2EFE9"
        # Пословная сборка (2.5 п.1) — слова появляются по одному, а не
        # весь титр разом; дёшево, тот же typewriter-приём слоями по
        # словам. Раскладка слов уже посчитана в Python (_title_words),
        # x/y здесь передаются ИСХОДНЫМИ формулами места — text_w в них
        # подменяется на готовую общую ширину внутри самой функции.
        out = _title_words(it["text"], size, tfont, dense, x, y, t0, en,
                           alpha, color)
        if it.get("underline"):
            # ЛИНИЯ ПОД ТИТУЛОМ ГЛАВЫ (2.2.2) — underline_wipe без анимации,
            # но БЕЗ его x=bx/y=by трюка: тот читает "text_w"/"text_h" из
            # соседнего drawtext, а у place="center" именно они и стоят в
            # x/y titульного текста ((w-text_w)/2, (h-text_h)/2) — drawbox
            # этих переменных не знает вовсе и упал бы с "Undefined
            # constant" ровно там, где titул отцентрован по своей ширине
            # и высоте. Поэтому и ширина, и высота меряются в Python той
            # же PIL-меркой, что и _fit_size, и в drawbox идёт готовое
            # число вместо text_w/text_h.
            #
            # iw/ih, А НЕ w/h. Та же грабля, что уже описана для W/H у
            # underline_wipe, только зеркальная: у drawbox строчные w/h —
            # это ШИРИНА И ВЫСОТА САМОГО БОКСА (самоссылка на w=852:h=3
            # чуть ниже), а не кадра. Проверено рендером: с w/h в x/y
            # линия молча не рисуется вовсе — ffmpeg не падает, кода
            # ошибки нет, просто пиксели не меняются (замерено по кадру:
            # плоские 36.0 на всех строках вместо ожидаемого скачка до
            # 163). С iw/ih — та же проверка даёт видимую линию.
            width_px, height_px = _text_metrics_px(raw_spaced, size, tfont)
            line_y = f"(ih-{height_px})/2+{height_px}+{int(size * 0.22)}"
            under = (f"drawbox=x='(iw-{width_px})/2':y='{line_y}':"
                     f"w={width_px}:h=3:color=0xF2EFE9@0.65:t=fill:"
                     f"enable='{en}'")
            out += f",{under}"
        return out

    if style == "accent":
        # СКВОЗНОЙ ТЕКСТОВЫЙ АКЦЕНТ (2.4.2). Мельче и тише титра: не факт
        # на экран, а лёгкая синхронная подпись к уже произнесённому слову.
        # Раскладка решена ДО сюда, в accents() (по kind кадра под ней):
        # клип — мелко в углу одной строкой, картинка — крупнее и до трёх
        # строк столбиком (it["lines"]). \n внутри одного drawtext не
        # работает — каждая строка рисуется своим drawtext, тем же приёмом,
        # что и typewriter.
        tfont = it.get("font") or font
        lines = it.get("lines") or [it["text"]]
        line_h = int(size * 1.18)
        parts = []
        for li, line in enumerate(lines):
            ltxt = _esc(line)
            base_y = f"({y})" if li == 0 else f"({y})+{li * line_h}"
            # Микро-въезд (2.5 п.1) — та же механика, что у slide_up, но
            # амплитудой втрое меньше: там 52px сделаны под удар, здесь —
            # под подачу, не заметную глазу.
            rise = f"{base_y}+12*max(0\\,1-(t-{t0:.3f})/0.5)"
            body = (f"fontfile={tfont}:text='{ltxt}':fontcolor=0xF2EFE9@0.80:"
                    f"fontsize={size}:borderw=0:"
                    f"shadowx=1:shadowy=2:shadowcolor=black@0.55:"
                    f"expansion=none:enable='{en}'")
            parts.append(f"drawtext={body}:x={x}:y='{rise}':alpha='{alpha}'")
        return ",".join(parts)

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
