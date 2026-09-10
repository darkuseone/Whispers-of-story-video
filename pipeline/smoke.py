"""
smoke.py — прогон конвейера на настоящих файлах без ключей и без денег.

    python pipeline/smoke.py jobs/pawn-02.json

Зачем отдельный файл: py_compile ловит только синтаксис. Две ошибки подряд
уехали в боевой прогон именно потому, что модуль компилировался, но никогда
не запускался — сначала неверное имя модели зрения, потом ссылка на
удалённую константу (NameError). Обе видны за секунду, если просто вызвать
функции.

Здесь вызываются все шаги, которые можно вызвать бесплатно:
  vet.vet_all      — отбраковка (зрение выключается снятием ключа)
  build.plan_shots — раскладка кадров по таймлайну
  channel.check    — проверка темы на повтор
  youtube.norm     — поиск глав в сценарии

Рендера здесь нет: он долгий, а ломается не он. Прогонять ПЕРЕД каждым
пушем в main.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ВЕРХНИЙ УРОВЕНЬ jobs/<id>.json — ЗАКРЫТЫЙ СПИСОК.
#
# style_override уже проверяется так (см. build.OVERRIDABLE): опечатка в
# имени поля роняет сборку сразу, а не превращается в тихо неработающую
# настройку. Верхний уровень спецификации жил без этой защиты, и это тот
# же класс инцидентов, что уже стоил роликов:
#   - "similarity_boost" вместо "similarity" в voice_settings молча терял
#     0.85 — озвучка звучала иначе, и заметно это было только на слух;
#   - без "vet_context" отбраковка забраковала материал собственного
#     видео как «не тот период» (dead-internet-01, до того как поле
#     завели).
#
# Список составлен по факту: что читает код (job.get(...) / job[...] по
# всем модулям pipeline/), а не что кто-то когда-то написал в спецификации
# — иначе поле, которое перестали читать, никогда бы не всплыло, а
# опечатка в новом поле проходила бы молча ровно так же, как раньше.
TOP_LEVEL_KEYS = {
    "id", "script_blocks",
    # озвучка
    "voice_id", "voice_model", "voice_settings", "hook_pause",
    # генерация изображений/видео
    "image_model", "image_prompts", "video_prompts",
    # поисковые запросы материала и источники
    "footage_queries", "archive_queries", "photo_sources", "video_sources",
    # отбраковка (vet.py)
    "vet_context", "vet_vision", "vet_model", "vet_pool_factor",
    "trusted_sources", "material_overshoot",
    # добор после отбраковки (assets.refill_after_vet / fill_gaps)
    "fill_limit", "fill_prompts",
    # Magnific (доля генерации, пока не выключен переменной среды)
    "magnific_share",
    # ручная правка отбора: reject.clip / reject.arch по номерам с листов
    "reject",
    # монтаж
    "style_override", "lut", "archive_lut",
    "music", "bed_gain_db", "tail_hold",
    # разное
    "topic", "youtube", "batch",
    # ручное переопределение памяти канала (CLAUDE.md, channel.py)
    "recent_luts", "recent_openings",
}


def check_top_level(job):
    """
    Опечатка в имени поля верхнего уровня — не ошибка, а тишина: код
    просто не находит ключ и берёт умолчание. Здесь это ловится сразу,
    до того как деньги ушли на озвучку и генерацию под пустое умолчание.

    Ключи с подчёркивания — комментарии протокола сценария (`_`,
    `_проверить`, `_структура`...) и произвольные заметки автора
    (`_техправки`, `_музыка_пример` и т.п.) — так заведено в самих
    спецификациях, они не тронуты.
    """
    unknown = sorted(k for k in job
                     if not k.startswith("_") and k not in TOP_LEVEL_KEYS)
    if unknown:
        raise SystemExit(
            "верхний уровень спецификации: неизвестные поля "
            + ", ".join(unknown) + "\nЕсли это заметка для человека — "
            "добавь подчёркивание в начало имени (как _проверить). Если "
            "это должно на что-то влиять — сверься со списком допустимых:\n"
            + ", ".join(sorted(TOP_LEVEL_KEYS)))


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    print("── верхний уровень спецификации")
    check_top_level(job)
    print(f"   {len(job)} полей, опечаток в именах нет")

    work = Path("work") / job["id"] / "assets"
    if not work.exists():
        raise SystemExit(
            f"нет {work} — сначала синтетика: python pipeline/mock.py {job_path}")

    os.environ.pop("XAI_API_KEY", None)      # зрение не трогаем, оно платное
    import vet, build, channel, youtube, style as style_mod

    print("── vet")
    vet.vet_all(job, work, use_vision=True)
    rej = vet.rejected_from(work)
    print(f"   отбраковано: {({k: len(v) for k, v in rej.items()})}")

    print("── темы")
    print(f"   {channel.check(job) or 'не повтор'}")

    # ПРОТОКОЛ СЦЕНАРИЯ 2026. YouTube снимает монетизацию с канала за
    # шаблонный/обезличенный контент. Поля _ / _проверить / _структура —
    # доказательство процесса и чек-лист автора; код их не читает в
    # монтаже, но без них ролик нельзя выпускать. Технические *-mini /
    # *-test пропускаются: там проверяется конвейер, а не текст.
    print("── протокол сценария")
    jid = str(job.get("id", ""))
    is_tech = bool(re.search(r"(^|-)(mini|test)($|-)", jid)) or \
        str((job.get("topic") or {}).get("slug", "")).startswith("test-")
    if is_tech:
        print("   технический ролик — поля протокола не требую")
    else:
        missing = [f for f in ("_", "_проверить", "_структура", "_длительность")
                   if not str(job.get(f) or "").strip()]
        if missing:
            raise SystemExit(
                "в спецификации нет полей протокола сценария: "
                + ", ".join(missing)
                + "\nСм. docs/протокол-сценария.md и ИНСТРУКЦИЯ-ЧАТ.md — "
                "без них канал рискует демонетизацией / коротким роликом "
                "вместо формата 40–50 минут.")
        print("   _, _проверить, _структура, _длительность на месте")

    print("── главы")
    seen = set()
    import assets as assets_mod
    long_blocks = []
    for i, b in enumerate(job["script_blocks"], 1):
        k = youtube.norm(b.strip().split(".")[0])[:45]
        if not k:
            raise SystemExit(f"глава {i}: пустой ключ поиска")
        if k in seen:
            raise SystemExit(f"глава {i}: начало блока не уникально")
        seen.add(k)
        if len(b) > assets_mod.BLOCK_CHARS_WARN:
            long_blocks.append((i, len(b)))
    # ДЛИНА БЛОКА. ElevenLabs режет длинные запросы МОЛЧА, с кодом 200:
    # звук приходит на первые несколько тысяч символов, хвост главы
    # просто отсутствует, а тайм-коды честные — на озвученную часть.
    # Обнаруживается это на готовом ролике, где глава обрывается на
    # полуслове, то есть после всех сорока минут рендера. На канале с
    # роликами по сорок пять минут блоки длинные, и проверка нужна.
    if long_blocks:
        raise SystemExit(
            "блоки длиннее " + str(assets_mod.BLOCK_CHARS_WARN) +
            " символов: " + ", ".join(f"{i} ({n})" for i, n in long_blocks) +
            "\nElevenLabs обрежет их молча. Разбей на главы поменьше — "
            "глав в описании станет больше, и это не беда.")
    n_ch = len(job.get("youtube", {}).get("chapters", []))
    if n_ch != len(job["script_blocks"]):
        raise SystemExit(f"глав {n_ch}, блоков {len(job['script_blocks'])}")
    if n_ch < 3:
        raise SystemExit(f"глав {n_ch} — YouTube показывает их от трёх")
    print(f"   {n_ch} глав, начала уникальны")

    # ТИПЫ СПИСОЧНЫХ ПОЛЕЙ. Спецификации пишутся в чате, и строка вместо
    # списка глазами не видна: в файле лежит "тег один, тег два" и выглядит
    # совершенно нормально. Дальше join разбирает её ПОСИМВОЛЬНО.
    # На ff-ep05 это дало 341 «тег» по одной букве и падение youtube.py на
    # лимите тегов — на самом последнем шаге, уже после полного монтажа.
    print("── типы полей")
    for path, val in (("youtube.tags", job.get("youtube", {}).get("tags")),
                      ("youtube.hashtags", job.get("youtube", {}).get("hashtags")),
                      ("youtube.chapters", job.get("youtube", {}).get("chapters")),
                      ("script_blocks", job.get("script_blocks")),
                      ("image_prompts", job.get("image_prompts")),
                      ("footage_queries", job.get("footage_queries")),
                      ("archive_queries", job.get("archive_queries"))):
        if val is not None and not isinstance(val, list):
            raise SystemExit(
                f"{path} записано как {type(val).__name__}, а должно быть "
                f"списком. Строка здесь разберётся по буквам, а не по "
                f"запятым: \"{path.split('.')[-1]}\": [...]")
    tags = ", ".join(youtube.as_list(job.get("youtube", {}).get("tags"), "tags"))
    if len(tags) > youtube.TAGS_LIMIT:
        raise SystemExit(f"теги занимают {len(tags)} символов при лимите "
                         f"{youtube.TAGS_LIMIT} — youtube.py упадёт на них "
                         f"ПОСЛЕ всего рендера")
    print(f"   списки на месте, теги {len(tags)}/{youtube.TAGS_LIMIT} символов")

    # ОБЛОЖКИ. Промпт и хук проверяются здесь, до xAI: без пустого верхнего
    # неба и без жёлтого Anton модель рисует красивый кадр, который в ленте
    # не кликает. Шрифт лежит в репозитории — без него fallback уходит в
    # системный DejaVu.
    print("── обложки")
    import covers
    for path in ("youtube.cover_texts", "youtube.cover_scenes",
                 "youtube.cover_prompts", "youtube.cover_patterns"):
        cur = job.get("youtube") or {}
        for key in path.split(".")[1:]:
            cur = (cur or {}).get(key) if isinstance(cur, dict) else None
        if cur is not None and not isinstance(cur, list):
            raise SystemExit(
                f"{path} записано как {type(cur).__name__}, а должно быть "
                "списком из двух строк")
    covers.self_check(job)
    texts = covers.cover_texts(job)
    pats = covers.cover_patterns(job)
    print(f"   хуки «{texts[0]}» / «{texts[1]}», паттерны {pats[0]}+{pats[1]}, "
          "Anton #FFD400, CTR-каркас на месте")

    # Главы ↔ субтитры. Та же ловушка, что уронила ufos-history-01 на
    # этапе 3 после двух часов монтажа: split('.') резал «U.S.» / не
    # учитывал «Gods?». Проверяем по marks.json бесплатно, до рендера.
    print("── главы в субтитрах")
    marks = json.loads((work / "marks.json").read_text())
    cues = [(float(m["start"]), youtube.norm(m["text"])) for m in marks]
    try:
        chaps = youtube.chapters(job, cues)
    except SystemExit as e:
        raise SystemExit(f"youtube.chapters не сойдётся после монтажа: {e}") from e
    print(f"   {len(chaps)} глав находятся в marks "
          f"(последняя с {youtube.stamp(chaps[-1][0])})")

    print("── план кадров")
    total = json.loads((work / "state.json").read_text())["total_audio"]
    av = channel.avoid()
    st = style_mod.StyleEngine(
        job["id"], recent_luts=av["lut"], recent_openings=av["opening"],
        recent_transitions=av["main_transition"],
        recent_overlays=av["overlay"], recent_beds=av["bed"])
    for k in ("lut", "archive_lut"):
        if job.get(k):
            setattr(st, k, job[k])
    build.apply_style_override(st, job)
    build.check_luts(st)
    shots = build.plan_shots(marks, st, assets := work, total,
                             job.get("reject"), job)
    build.set_render_durations(shots)
    sec, cnt, mt = build.material_report(shots)
    end = shots[-1]["start"] + shots[-1]["duration"]
    print(f"   {len(shots)} кадров, {total/60:.1f} мин, "
          f"генерация {sec['gen']/mt*100:.1f}%")

    # ХВАТАЕТ ЛИ ЗАПРОСОВ НА МАТЕРИАЛ. Считается по той же формуле, что и
    # в assets.fill_gaps, и проверяется ЗДЕСЬ — до озвучки и генерации.
    #
    # Иначе нехватка обнаруживается только в боевом прогоне, причём в
    # самом конце: конвейер честно докладывает «реального материала 48,
    # под ролик нужно около 63», молча затыкает дырку генерацией и выдаёт
    # ролик с 81.7% генерации при заказанных 45%. Деньги на озвучку и
    # картинки к этому моменту уже потрачены, а починка — дописать
    # запросов в спецификацию, то есть ровно то, что можно было сделать
    # до первого потраченного цента.
    ov = job.get("style_override") or {}
    base = float(ov.get("base_duration_range", [9.0, 16.0])[0]) or 11.0
    gshare = float(ov.get("generated_share", 0.45))
    need_real = int(max(8, int(total / base)) * (1 - gshare) / 2.5)
    have_real = (len(list((work / "footage").glob("clip_*"))) +
                 len(list((work / "archive").glob("arch_*"))))
    nq = len(job.get("footage_queries", [])) + len(job.get("archive_queries", []))
    print(f"   реального материала {have_real} при нужных {need_real} "
          f"({nq} запросов на {total/60:.0f} мин)")
    if have_real < need_real:
        raise SystemExit(
            f"материала не хватит: {have_real} при нужных {need_real}.\n"
            f"Запросов в спецификации {nq} "
            f"(footage_queries + archive_queries) на {total/60:.0f} минут — "
            f"мало. Дописать запросов и прогнать смоук заново.\n"
            f"Ориентир: примерно один запрос на минуту ролика. Иначе дырку "
            f"молча закроет генерация, и её доля уедет много выше "
            f"заказанных {gshare*100:.0f}%.")
    if abs(end - total) > 0.5:
        raise SystemExit(f"таймлайн разошёлся со звуком на {abs(end-total):.2f} с")
    print(f"   таймлайн сходится со звуком ({abs(end-total):.3f} с)")

    # ДОЛИ ВИДЕО ПО ФАЗАМ. Главное требование к монтажу этого канала, и
    # проверять его надо здесь, а не глазами на готовом ролике: между
    # заказанной долей и вышедшей стоит материал, и когда стока мало,
    # вступление молча превращается в фотоальбом.
    print("── доли материала")
    intro_end = getattr(st, "intro_end", 0.0)

    # Потолок стока: пул × сколько раз клип можно показать × самый длинный
    # кусок. Если заказанная доля выше него, дело не в раскладке, а в том,
    # что материала физически нет, — и валить прогон за это нельзя.
    pool = len({s["file"] for s in shots if s["kind"] == "clip"})
    ceiling = pool * build.MAX_CLIP_REPEATS * build.CLIP_MAX_SECONDS

    drift = []
    for ru, lo, hi, target in (("вступление", 0.0, intro_end, st.intro_clip_share),
                               ("тело", intro_end, total, st.body_clip_share)):
        inside = [s for s in shots if lo <= s["start"] < hi]
        span = sum(s["duration"] for s in inside)
        if span <= 0:
            continue
        clip_s = sum(s["duration"] for s in inside if s["kind"] == "clip")
        got = clip_s / span
        note = ""
        # ДОПУСК 8 ПУНКТОВ. Заказаны полосы (70-80% и 20-30%), цель — их
        # середина, и попадание в полосу с запасом это примерно столько.
        if abs(got - target) > 0.08:
            if got < target and ceiling < target * span:
                note = (f"  ← стока не хватает физически: пул {pool} клипов "
                        f"даёт максимум {ceiling/60:.1f} мин видео")
            else:
                note = "  ← РАЗОШЛОСЬ"
                drift.append(f"{ru}: {got*100:.1f}% против заказанных "
                             f"{target*100:.0f}%")
        print(f"   {ru:<12} видео {got*100:5.1f}% "
              f"(заказано {target*100:.0f}%), {span/60:.1f} мин{note}")

    # Раньше эти проценты только ПЕЧАТАЛИСЬ. Смоук сообщал «видео 5.0%
    # против заказанных 24%» и тут же объявлял прогон пройденным — то
    # есть главное правило канала не проверялось вовсе, хотя и README, и
    # CLAUDE.md обещали обратное.
    if drift:
        raise SystemExit(
            "доли материала разошлись с заказанными:\n  " +
            "\n  ".join(drift) +
            "\nСтока при этом хватает, значит дело в раскладке — смотри "
            "MaterialMix в build.py и оси clip_rhythm/body_clip_every_n_shots "
            "в style.py. Правила, считающие КАДРЫ, дают по времени вдвое "
            "меньше заказанного.")

    # ДОЛГИЕ КАДРЫ БЕЗ ДОЛГОГО ХОДА. Отдельной строкой, потому что это
    # самая дорогая ошибка канала и по логу сборки она не видна: движение
    # у кадра есть, оно записано, оно даже разное у соседей.
    long_bad = [s for s in shots
                if s["kind"] != "clip" and s.get("move")
                and s["duration"] >= style_mod.LONG_SHOT_SECONDS
                and s["move"] not in style_mod.LONG_SHOT_MOVES]
    if long_bad:
        raise SystemExit(
            f"{len(long_bad)} кадров длиннее "
            f"{style_mod.LONG_SHOT_SECONDS:.0f} с идут коротким движением — "
            f"ход закончится на первой трети, дальше стоп-кадр. "
            f"Смотри пересмотр движения в build.plan_shots")
    longest = max(s["duration"] for s in shots)
    print(f"   самый долгий кадр {longest:.1f} с, все долгие идут "
          f"долгим ходом")

    # ПОДБОР ПО СМЫСЛУ — ПОРОГОМ, а не глазами по логу. Ноль процентов
    # попаданий значит, что подбор упал на имена файлов: манифест не
    # написан или запросы из другого словаря. Ролик при этом собирается
    # без единой ошибки — просто кадры не имеют отношения к словам.
    # Именно так смоук месяц «проверял» не тот путь, что работает в бою.
    #
    # У СТОКА ПОРОГ НИЖЕ, И ЭТО НЕ ПОБЛАЖКА. Клип ставится не только по
    # словам: пока доля видео в фазе отстаёт от заказанной, монтаж берёт
    # лучший доступный кадр даже без совпадения — иначе требование «70-80%
    # видео во вступлении» не выполняется вовсе (замер: 10.8% против 78%).
    # См. picky() в build.plan_shots. Такие показы честно считаются
    # промахами, и на ролике, где запросы к стоку написаны зрительными
    # образами, а начитка идёт про абстракции, доля попаданий у клипов
    # закономерно однозначная. Ловить надо не это, а обвал ВСЕХ видов
    # сразу — он и означает, что подбор упал на имена файлов.
    print("── подбор по смыслу")
    floor = {"clip": 4.0}
    for src, (hits, calls) in (getattr(st, "match_report", {}) or {}).items():
        if not calls:
            continue
        rate = hits / calls * 100
        print(f"   {src:<5} {hits}/{calls} ({rate:.0f}%)")
        if calls >= 12 and rate < floor.get(src, 12.0):
            raise SystemExit(
                f"подбор «{src}»: {rate:.0f}% попаданий по смыслу — материал "
                f"раздаётся вслепую. Проверь _manifest.json рядом с файлами "
                f"и совпадение запросов со спецификацией")

    # НОВЫЕ СЛОИ КАДРА. Считаются бесплатно, поэтому проверяются здесь же:
    # карточки-прерывания не должны налезать друг на друга, титулы глав —
    # выходить за число глав.
    print("── карточки и титулы")
    from editorial import textcard
    cards = textcard.interrupts(getattr(st, "beats", []), marks, st.rng, total)
    for a, b in zip(cards, cards[1:]):
        if b["t"] - a["t"] < textcard.INTERRUPT_GAP:
            raise SystemExit("карточки-прерывания ближе "
                             f"{textcard.INTERRUPT_GAP:.0f} с друг к другу")
    titles = textcard.chapter_titles(
        (job.get("youtube") or {}).get("chapters") or [],
        getattr(st, "chapter_edges", []), st.rng)
    print(f"   карточек {len(cards)}, титулов глав {len(titles)}")

    print("── подложки")
    beds = build.beds_for(st, job, total)
    if not beds:
        print("   ! ни одной подложки не найдено — ролик соберётся, но "
              "с одним голосом")
    switches = build.bed_switch_points(st, total, len(beds))
    if len(beds) > 1:
        if len(switches) != len(beds) - 1:
            raise SystemExit(f"подложек {len(beds)}, а смен {len(switches)} — "
                             f"смотри bed_switch_points")
        print(f"   треков {len(beds)}, смены на "
              + ", ".join(f"{p/60:.1f} мин" for p in switches))

    # 3.3.8 — шортсы раньше не проверял никто, а дефектов оформления там
    # больше всего: они не в коде, а в вопросе, шрифте, замере ширины.
    # Всё здесь считается по marks.json и тексту, без рендера.
    print("── шортсы")
    import random
    import tempfile
    import shorts as shorts_mod
    words = (shorts_mod.words_from_alignment(job, work / "voice")
             or shorts_mod.words_from_marks(marks))
    story = getattr(st, "beats", None) or []
    wins = shorts_mod.pick_windows(story, marks, total)
    if not wins:
        raise SystemExit("шортсы: pick_windows не дал ни одного окна")
    min_len = shorts_mod.MIN_LEN_SOFT if total < 180 else shorts_mod.MIN_LEN
    for w in wins:
        if w["t1"] - w["t0"] < min_len - 0.5:
            raise SystemExit(
                f"шортсы: окно «{w['role']}» короче {min_len:.0f} с")
    for a, b in zip(wins, wins[1:]):
        if a["t1"] > b["t0"] + 0.01:
            raise SystemExit(
                f"шортсы: окна «{a['role']}» и «{b['role']}» пересекаются")

    job_qs = list((job.get("youtube") or {}).get("shorts_questions") or [])
    fonts_needed = ("ArchivoBlack-Regular.ttf", "Montserrat-ExtraBold.ttf")
    for fname in fonts_needed:
        if not (shorts_mod.FONT_DIR / fname).exists():
            raise SystemExit(
                f"шортсы: нет шрифта {fname} в {shorts_mod.FONT_DIR}")

    for n, w in enumerate(wins, 1):
        # question_for сам роняет смоук, если shorts_questions не задан
        # или не кончается на «?» — see 3.3.5, не выдумывать вопрос.
        q = shorts_mod.question_for(job_qs, n)
        wrapped = shorts_mod.wrap_question(q)
        n_lines = wrapped.count("\n") + 1
        if n_lines > shorts_mod.QUESTION_MAX_LINES:
            raise SystemExit(
                f"шортс {n}: вопрос «{q}» не влезает в "
                f"{shorts_mod.QUESTION_MAX_LINES} строки")
        scale = shorts_mod.hook_scale(q)
        if not (100 <= scale <= shorts_mod.HOOK_SCALE_MAX):
            raise SystemExit(
                f"шортс {n}: hook_scale {scale} вне 100-"
                f"{shorts_mod.HOOK_SCALE_MAX} — замер ширины хука сломан "
                f"(та самая грабля с \\N в question_box)")

        t0, t1 = w["t0"], w["t1"]
        dur = round(t1 - t0, 3)
        # СУБТИТРЫ НЕ НАЕЗЖАЮТ НА CTA — проверяется по ГОТОВОМУ .ass, а не
        # по сырому captions_from_words: тот отдаёт естественный конец
        # фразы, а write_ass сам обрезает его до cta_from - 0.10 при
        # рендере. Проверка по сырым концам ловила фантомные пересечения
        # ровно там, где реальный рендер уже подрезан и ничего не
        # накладывается — так и поймано при первом прогоне этой проверки.
        ass_tmp = Path(tempfile.mkstemp(suffix=".ass")[1])
        try:
            shorts_mod.write_ass(words, t0, dur, ass_tmp, q)
            ass_text = ass_tmp.read_text(encoding="utf-8")
        finally:
            ass_tmp.unlink(missing_ok=True)
        cta_start = None
        cap_ends = []
        for line in ass_text.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            parts = line.split(",", 9)
            style, end_s = parts[3], parts[2]
            end_sec = sum(float(x) * m for x, m in
                         zip(reversed(end_s.split(":")), (1, 60, 3600)))
            if style == "Cta":
                cta_start_s = parts[1]
                cta_start = sum(float(x) * m for x, m in
                               zip(reversed(cta_start_s.split(":")), (1, 60, 3600)))
            elif style == "Caption":
                cap_ends.append(end_sec)
        if cta_start is not None and any(e > cta_start + 0.01 for e in cap_ends):
            raise SystemExit(
                f"шортс {n}: в готовом .ass есть субтитр, кончающийся "
                f"позже начала призыва ({cta_start:.2f} с) — write_ass "
                f"больше не обрезает Caption под cta_from")

        rng = random.Random(f"{job['id']}-short-{n}")
        cuts = shorts_mod.cut_plan(shots, t0, t1, rng, words=words,
                                   cutter=build.ClipCutter())
        cut_sum = round(sum(c["dur"] for c in cuts), 3)
        if abs(cut_sum - dur) > 0.05:
            raise SystemExit(
                f"шортс {n}: сумма кусков {cut_sum:.2f} с при окне "
                f"{dur:.2f} с — разошлось")
    print(f"   {len(wins)} окна, вопросы/шрифты/замер ширины на месте, "
          f"субтитры не наезжают на CTA, куски сходятся с длиной окна")

    print("\nСМОУК-ПРОГОН ПРОЙДЕН")


if __name__ == "__main__":
    main(sys.argv[1])
