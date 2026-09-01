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


def main(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
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

    # ШОРТСЫ. Рендер дорогой, здесь только то, что ломалось молча:
    # шрифт/плашка вопроса, разрядка, сдвиг капшенов на t0, одни часы
    # у картинки и звука. Без этого смоук пропускал Archivo и input -ss,
    # а расхождение было видно только на телефоне. Стоит до проверки
    # глав: ancient-mini падает на длине главы, а шортсы от этого не зависят.
    print("── шортсы")
    import shorts
    if not shorts.FONT_QUESTION_FILE.exists():
        raise SystemExit(f"нет шрифта вопроса {shorts.FONT_QUESTION_FILE}")
    marks_s = [
        {"text": "When you navigate into the room.", "start": 10.0, "end": 14.0},
        {"text": "Search the archive next.", "start": 14.2, "end": 17.0},
    ]
    q = shorts.question_for(
        {"mark_idx": 0}, marks_s,
        ["Why did the CIA investigate the holographic universe?"], 1)
    if q != q.upper():
        raise SystemExit(f"шортс: вопрос не заглавными: {q!r}")
    if "CIA" not in q.replace("\n", " "):
        raise SystemExit(f"шортс: вопрос потерял смысл при переносе: {q!r}")
    ass_p = Path("/tmp/shorts-smoke.ass")
    n_cap = shorts.write_ass(marks_s, t0=10.0, dur=8.0, out=ass_p, question=q)
    ass = ass_p.read_text(encoding="utf-8")
    if "Archivo" in ass or "QBox" in ass:
        raise SystemExit("шортс: вопрос всё ещё Archivo / QBox")
    if shorts.FONT_QUESTION not in ass:
        raise SystemExit(f"шортс: в ASS нет {shorts.FONT_QUESTION}")
    if r"\fsp" not in ass:
        raise SystemExit("шортс: нет анимации разрядки \\fsp")
    if r"\move" in ass:
        raise SystemExit("шортс: вопрос снова едет через \\move")
    if n_cap < 1:
        raise SystemExit("шортс: write_ass не вывел ни одного субтитра")
    if "Dialogue: 2,0:00:00.00," not in ass:
        raise SystemExit("шортс: капшен при t0=10 начинается не с нуля")
    if "0:00:04.20" not in ass:
        raise SystemExit("шортс: второй капшен не сдвинулся на t0")
    filt = shorts.sync_filters(10.0, 8.0, 7.6, "x.ass", "/fonts")
    if "atrim=start=10.000:duration=8.000" not in filt:
        raise SystemExit(f"шортс: atrim не на t0/dur: {filt}")
    if "trim=duration=8.000" not in filt:
        raise SystemExit(f"шортс: видео не выровнено в dur: {filt}")
    print(f"   вопрос {len(q.splitlines())} строк, капшенов {n_cap}, "
          f"ASS без Archivo/QBox, часы t0+dur")

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

    print("\nСМОУК-ПРОГОН ПРОЙДЕН")


if __name__ == "__main__":
    main(sys.argv[1])
