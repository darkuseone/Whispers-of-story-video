"""
mock.py — синтетические материалы для локальной отладки монтажа.

    python pipeline/mock.py jobs/auction-01.json

Кладёт в work/<id>/assets/ ровно то же, что кладёт assets.py, но считает
это на месте и бесплатно: вместо озвучки — тишина нужной длины с тайм-кодами,
посчитанными из текста, вместо генерации — цветные карточки, вместо стока —
testsrc2.

Зачем: прогон в GitHub Actions это десять минут даже на коротком ролике, а
ошибки монтажа (перекрытие переходов, обрыв группы склейки, съехавший звук)
ловятся за минуты, если материал под рукой. Плюс код монтажа можно
проверять ДО того, как появились ключи, голос и подложка.

НИЧЕГО НЕ ПЕРЕЗАПИСЫВАЕТ. Если настоящий материал уже лежит на месте,
функция его не трогает: смысл в том, чтобы дополнить недостающее, а не
затереть скачанное. Своё видно по имени — у синтетики в имени mock.

Разбивка по предложениям считается из текста по 15 символов в секунду. Это
близко к темпу диктора и, главное, воспроизводимо: одна и та же
спецификация всегда даёт один и тот же таймлайн.
"""

import json
import math
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

CHARS_PER_SECOND = 15.0     # темп диктора, из задания
PAUSE = 0.35                # пауза между предложениями
IMG_W, IMG_H = 1536, 864


def log(*a):
    print(*a, flush=True)


def run(cmd):
    subprocess.run(cmd, shell=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def split_sentences(text: str):
    """
    Режет блок на предложения по тем же знакам, что и sentence_marks.

    Тем же — значит буквально тем же условием: разрыв только перед пробелом
    или переносом, и не внутри инициального сокращения (R.C., U.S.). Раньше
    здесь резалось по ЛЮБОМУ встреченному знаку без этих условий вовсе, и
    на georgia-guidestones-01 «For thirty-five years, "R.C. Christian»
    распадалось на три обрывка вместо одной реплики — ничего похожего на
    то, что складывает assets.sentence_marks из настоящей озвучки. Такое
    расхождение уже роняло смоук месяц назад: он проверял не тот путь,
    который работает в бою.
    """
    abbrev_end = {m.end() - 1
                  for m in re.finditer(r"\b(?:[A-Z]\.){2,}", text)}
    out, buf = [], ""
    for i, ch in enumerate(text):
        buf += ch
        if ch in ".!?" and i + 1 < len(text) and text[i + 1] in " \n" \
                and i not in abbrev_end:
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s]


def build_marks(job):
    """Тайм-коды предложений из длины текста. Тот же формат, что у assets.py."""
    marks, t = [], 0.0
    for block in job["script_blocks"]:
        for s in split_sentences(block):
            dur = max(1.2, len(s) / CHARS_PER_SECOND)
            marks.append({"text": s, "start": round(t, 3),
                          "end": round(t + dur, 3)})
            t += dur + PAUSE
    return marks, round(t, 3)


def make_voice(out: Path, total: float):
    """
    Тишина нужной длины. Именно тишина, а не тон: сведение прогоняет дорожку
    через loudnorm, и синус на -16 LUFS в наушниках невыносим, а проверять
    всё равно нужно длину, а не содержимое.
    """
    run(f'ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=stereo -t {total:.2f} '
        f'-c:a aac -b:a 192k "{out}"')


def make_images(out: Path, prompts, tag: str):
    """
    Цветные карточки вместо генерации. Цвет считается из номера, поэтому
    соседние кадры на экране заведомо разные — видно, что картинки меняются
    и что они меняются в правильном порядке.

    Размер взят больше кадра: prepare_image готовит холст 3000 пикселей и
    ВСЕГДА уменьшает до него. На мелком исходнике проверка резкости
    показала бы мыло, которого в настоящем ролике не будет.
    """
    from PIL import Image, ImageDraw, ImageFont
    out.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 96)
    except OSError:
        font = ImageFont.load_default()

    made = 0
    for i, p in enumerate(prompts, 1):
        dst = out / f"{tag}_{i:03d}_mock.jpg"
        if dst.exists() or (out / f"{tag}_{i:03d}.jpg").exists():
            continue
        # тёплая палитра: на холодных карточках цветокор канала не проверить
        h = (i * 37) % 60 / 60.0
        r = int(150 + 90 * math.sin(h * math.tau))
        g = int(105 + 60 * math.sin(h * math.tau + 1.2))
        b = int(60 + 40 * math.sin(h * math.tau + 2.4))
        im = Image.new("RGB", (IMG_W, IMG_H), (max(r, 0), max(g, 0), max(b, 0)))
        d = ImageDraw.Draw(im)
        # диагональ и рамка: по ним на готовом ролике видно и движение
        # камеры, и кадрирование — на ровной заливке они незаметны
        d.line([0, 0, IMG_W, IMG_H], fill=(255, 255, 255), width=3)
        d.rectangle([40, 40, IMG_W - 40, IMG_H - 40], outline=(20, 20, 20),
                    width=6)
        # СВОЯ ГЕОМЕТРИЯ У КАЖДОЙ КАРТОЧКИ. Цвет её не разводит: отбраковка
        # дублей считает перцептивный хэш по ЯРКОСТИ, и полсотни карточек,
        # отличающихся только оттенком, для неё один и тот же кадр — она
        # честно выбрасывала их как копии, и пул архива схлопывался втрое.
        # Крупные пятна по грубой сетке: хэш смотрит сетку 8x8 и пятно
        # мельче её ячейки не замечает вовсе.
        rnd = random.Random(4000 + i)
        for _ in range(3):
            bx, by = rnd.randrange(0, 6) * 256, rnd.randrange(0, 4) * 216
            bw, bh = rnd.choice((200, 300, 420)), rnd.choice((160, 240, 320))
            tone = rnd.choice(((20, 18, 16), (235, 232, 225)))
            d.rectangle([bx, by, bx + bw, by + bh], fill=tone)
        d.text((80, IMG_H // 2 - 60), f"{tag} {i:03d}", font=font,
               fill=(25, 20, 15))
        d.text((80, IMG_H // 2 + 60), p[:38], fill=(40, 32, 25))
        im.save(dst, quality=92)
        made += 1
    return made


def write_manifest(out: Path, prefix: str, queries):
    """
    Манифест для синтетики — ТОТ ЖЕ формат, что пишет assets.gather.

    Без него подбор по смыслу в build.keywords_for падает на имена файлов
    («clip 003 mock»), и смоук показывал 0% попаданий по архиву и стоку —
    то есть проверял не тот путь, который работает в бою. Запросы берутся
    из спецификации и раздаются файлам по кругу: тот же файл всегда
    получает тот же запрос, прогон воспроизводим.
    """
    if not queries:
        return
    man = out / "_manifest.json"
    rows = []
    if man.exists():
        try:
            rows = json.loads(man.read_text())
        except json.JSONDecodeError:
            rows = []
    known = {Path(r.get("file", "")).name for r in rows}
    added = 0
    for p in sorted(out.glob(f"{prefix}_*")):
        if p.name in known:
            continue
        try:
            n = int(p.name.split("_")[1].split(".")[0])
        except (IndexError, ValueError):
            continue
        rows.append({"file": str(p), "q": queries[n % len(queries)],
                     "src": "mock", "kind": "mock"})
        added += 1
    if added:
        man.write_text(json.dumps(rows, indent=1, ensure_ascii=False))


def make_clips(out: Path, count: int, seconds=14.0):
    """
    testsrc2 вместо стока. Длина 14 секунд — как у настоящего стока, чтобы
    проверялся тот же путь: кусок берётся из середины файла, а на кадре
    длиннее исходника включается петля.

    КЛИПЫ ВЫХОДЯТ ОДИНАКОВЫМИ, и развести их дёшево не получается: замер
    на двадцати клипах показал минимум НОЛЬ бит расхождения даже после
    сдвига фазы анимации, отражений и двух крупных заливок по сетке.
    Перцептивный хэш сравнивает СОСЕДНИЕ пиксели, а внутри сплошного
    пятна они равны — заливка его просто не двигает.

    Поэтому синтетика исключена из сведения близнецов на стороне
    отбраковки, по источнику `mock` в манифесте (см. vet.vet_all). Иначе
    отбраковка дублей честно выбрасывала половину пула как «тот же кадр
    под другим именем», доли материала после этого не сходились, и смоук
    падал на синтетике, а не на коде.
    """
    out.mkdir(parents=True, exist_ok=True)
    have = len(list(out.glob("clip_*.mp4")))
    made = 0
    for i in range(have, count):
        dst = out / f"clip_{i:03d}_mock.mp4"
        run(f'ffmpeg -y -f lavfi -i testsrc2=size=1920x1080:rate=30:'
            f'duration={seconds:.1f} -c:v libx264 -crf 24 -preset veryfast '
            f'-pix_fmt yuv420p -an "{dst}"')
        made += 1
    return made


def main(job_path):
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise SystemExit("нет Pillow — поставь зависимости: "
                         "pip install -r requirements.txt")
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    work = Path("work") / job["id"] / "assets"
    work.mkdir(parents=True, exist_ok=True)

    marks, total = build_marks(job)
    if not marks:
        raise SystemExit("в спецификации нет script_blocks — считать нечего")
    (work / "marks.json").write_text(json.dumps(marks, indent=1,
                                                ensure_ascii=False))
    (work / "state.json").write_text(json.dumps(
        {"total_audio": total, "marks": len(marks)}, indent=1))
    log(f"тайм-коды : {len(marks)} предложений, {total/60:.1f} мин "
        f"(по {CHARS_PER_SECOND:.0f} символов в секунду)")

    voice = work / "voice_full.m4a"
    if voice.exists():
        log("голос     : уже есть, не трогаю")
    else:
        make_voice(voice, total)
        log(f"голос     : тишина {total/60:.1f} мин")

    n = make_images(work / "images", job["image_prompts"], "img")
    log(f"картинки  : добавлено {n}, "
        f"всего {len(list((work / 'images').glob('img_*')))}")

    # ПУЛ АРХИВА СЧИТАЕТСЯ ОТ ЧИСЛА АРХИВНЫХ ЗАПРОСОВ, как и пул стока.
    #
    # Раньше он считался от числа image_prompts — то есть от генерации, к
    # архиву отношения не имеющей. Из-за этого нехватку архивных запросов
    # смоук увидеть не мог В ПРИНЦИПЕ: у dead-internet-01 их было пять на
    # сорок минут, мок исправно рисовал девятнадцать файлов «по числу
    # промптов», проверка проходила, а в боевом прогоне архив дал ноль
    # процентов экранного времени и генерация вылезла на 81.7% при
    # заказанных 45%.
    #
    # Как и у стока: в боевом прогоне запрос приносит max(1, round(6 *
    # overshoot)) файлов, из которых отбраковка съедает около двух третей.
    # Подписи карточек — НАСТОЯЩИЕ запросы из спецификации, по кругу: по
    # ним же пишется манифест, и подбор по смыслу проверяется тем же
    # путём, что и в бою, а не по именам файлов.
    over = float(job.get("material_overshoot", 1.4))
    want_arch = max(12, len(job.get("archive_queries", [])) *
                    max(1, round(6 * over) // 3))
    arch_qs = job.get("archive_queries") or []
    n = make_images(work / "archive",
                    [arch_qs[i % len(arch_qs)] if arch_qs
                     else f"archive photo {i+1}" for i in range(want_arch)],
                    "arch")
    write_manifest(work / "archive", "arch", arch_qs)
    log(f"архив     : добавлено {n}, "
        f"всего {len(list((work / 'archive').glob('arch_*')))}")

    # ПУЛ СТОКА РАЗМЕРОМ КАК ПОСЛЕ ОТБРАКОВКИ, а не по одному клипу на
    # запрос. Один на запрос — это шестнадцать клипов на сорокашестиминутный
    # ролик, и доля видео в теле упирается в потолок повторов задолго до
    # заказанных процентов: смоук печатал «видео 5.0% (заказано 24%)» и
    # называл это пройденным прогоном. Проверка доли, которую нечем
    # выполнить, не проверяет ничего.
    #
    # В боевом прогоне запрос приносит max(1, round(5 * overshoot)) файлов,
    # из которых отбраковка съедает примерно две трети. Три на запрос —
    # это и есть реалистичный остаток.
    want_clips = max(24, len(job.get("footage_queries", [])) *
                     max(1, round(5 * over) // 3))
    n = make_clips(work / "footage", want_clips)
    write_manifest(work / "footage", "clip", job.get("footage_queries") or [])
    log(f"футаж     : добавлено {n}, "
        f"всего {len(list((work / 'footage').glob('clip_*')))}")

    log(f"\nготово. Дальше:  python pipeline/build.py {job_path}")


if __name__ == "__main__":
    main(sys.argv[1])
