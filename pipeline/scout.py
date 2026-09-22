"""
scout.py — отбор футажа ГЛАЗАМИ ЧАТА, до сборки и без платного зрения.

С 22 сентября 2026 зрение отбраковки живёт в чате (Claude Code или другая
нейросеть, которой автор даёт тему), а не в xAI: ключ xAI остаётся только
для генерации картинок. Чат пишет сценарий, сам ищет материал и сам
смотрит кадры. Этот файл — его инструмент, три шага:

  1. поиск и листы
       python pipeline/scout.py search jobs/<id>.json [--per 6] [--q "..."]

     Ищет по footage_queries (видео) и archive_queries (фото) тем же
     кодом источников, что и конвейер (assets.ALL_SOURCES), качает
     кандидатов в work/<id>/scout/files и собирает листы
     work/<id>/scout/sheets/q_NNN.jpg: у видео ТРИ кадра в ряд (начало,
     середина, конец — заставка оцифровщика и чужой кадр в конце клипа
     живут по краям), у фото один. На каждом ряду номер кандидата.
     Рядом ложится review.json — пустая анкета на каждого кандидата.

  2. просмотр
     Чат открывает листы (Read по картинке) и заполняет review.json:

       "c012": {"keep": true, "quality": 4,
                "what": "mangrove roots at low tide",
                "subjects": ["mangrove", "tidal flat", "roots"]}

     subjects — 3-8 предметов, которые ВИДНЫ на кадре. Это не
     формальность: монтаж ставит кадр под фразу диктора по этим словам
     (build.keywords_for), и «мангры» в начитке найдут мангры, а не
     третий клип с того же запроса. quality — 1-5, как у зрения прежде:
     ниже трёх не берётся.

  3. перенос в спецификацию
       python pipeline/scout.py apply jobs/<id>.json

     Одобренное дописывается в pinned_clips / pinned_archive с
     src "pinned" и увиденным, а "pinned" — в trusted_sources: файлы
     посмотрены, платить за них ещё раз незачем. Бесплатные ярусы
     отбраковки (белый скан, чёрный кадр, расфокус, битый файл) на них
     всё равно работают.

Ключи Pexels и Pixabay берутся из окружения сессии чата. Без них остаются
открытые источники (Wikimedia, Openverse, Met, archive.org и другие).
Листы и файлы — рабочая папка, в репозиторий не коммитятся.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import assets
import vet

FRAME_W, FRAME_H = 426, 240
LABEL_H = 28
ROW_W = FRAME_W * 3
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MIN_QUALITY = vet.MIN_QUALITY
SCOUT_FILE_LIMIT = 60 * 1024 * 1024

VIDEO_DEFAULT = ["pexels", "pixabay", "wikimedia_video", "archive_org"]
PHOTO_DEFAULT = ["wikimedia", "openverse", "met", "loc"]


def log(*a):
    print(*a, flush=True)


def _font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def _norm_url(url: str) -> str:
    """Тот же вид адреса, что пишет assets.gather_pinned: без query."""
    return (url or "").split("?")[0].strip()


def _paths(job):
    root = Path("work") / job["id"] / "scout"
    return root, root / "files", root / "sheets", root / "candidates.json", \
        root / "review.json"


def _load(p: Path, default):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def _save(p: Path, data):
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False),
                 encoding="utf-8")


def _frames(path: Path, kind: str):
    if kind == "video":
        return vet.video_frames(path, n=3)
    try:
        return [Image.open(path).convert("RGB")]
    except Exception:
        return []


def _row(cid: str, cand: dict, frames):
    """Один ряд листа: номер, источник, длина, затем кадры."""
    row = Image.new("RGB", (ROW_W, FRAME_H + LABEL_H), (18, 18, 18))
    for k, im in enumerate(frames[:3]):
        im = im.copy()
        im.thumbnail((FRAME_W, FRAME_H))
        x = k * FRAME_W + (FRAME_W - im.width) // 2
        row.paste(im, (x, LABEL_H + (FRAME_H - im.height) // 2))
    d = ImageDraw.Draw(row)
    size = ""
    if frames:
        size = f"{frames[0].width}x{frames[0].height}"
    dur = f"{cand.get('dur') or 0:.0f}s " if cand["kind"] == "video" else ""
    d.text((8, 4), f"{cid}  {cand['kind']}  {cand['src']}  {dur}{size}",
           fill=(255, 212, 0), font=_font(18))
    return row


def _sheet(rows, out: Path, title: str):
    if not rows:
        return
    h = sum(r.height for r in rows) + 36
    im = Image.new("RGB", (ROW_W, h), (0, 0, 0))
    ImageDraw.Draw(im).text((8, 6), title[:110], fill=(255, 255, 255),
                            font=_font(20))
    y = 36
    for r in rows:
        im.paste(r, (0, y))
        y += r.height
    im.save(out, quality=82)


def _sources(job, key, default):
    names = [assets.SOURCE_ALIASES.get(n, n)
             for n in (job.get(key) or default)]
    return [(n, assets.ALL_SOURCES[n]) for n in names
            if n in assets.ALL_SOURCES]


def search(job, per: int, extra, only):
    root, files, sheets, cand_p, rev_p = _paths(job)
    for d in (files, sheets):
        d.mkdir(parents=True, exist_ok=True)
    cands = _load(cand_p, {})
    review = _load(rev_p, {})
    known = {_norm_url(c["url"]) for c in cands.values()}
    pinned = {_norm_url(it["url"] if isinstance(it, dict) else it)
              for key in ("pinned_clips", "pinned_archive")
              for it in (job.get(key) or [])}
    n_next = 1 + max([int(k[1:]) for k in cands] or [0])

    plan = []
    if only != "photo":
        vq = list(extra) or list(job.get("footage_queries") or [])
        plan += [("video", q, _sources(job, "video_sources", VIDEO_DEFAULT))
                 for q in vq]
    if only != "video":
        aq = list(extra) or list(job.get("archive_queries") or [])
        plan += [("image", q, _sources(job, "photo_sources", PHOTO_DEFAULT))
                 for q in aq]

    sheet_n = 1 + len(list(sheets.glob("q_*.jpg")))
    for kind, q, srcs in plan:
        rows, added = [], 0
        for name, fn in srcs:
            try:
                found = fn(q, per) or []
                # ПУСТО — ЕЩЁ НЕ ОТВЕТ. Узкий запрос («Miami River
                # Florida») сток отбрасывает целиком фильтром по теме;
                # тот же запрос без хвоста («miami river») находит кадры.
                # Повтор короче — только когда первый дал ноль.
                if not found:
                    sq = assets.short_query(q, keep=2)
                    if sq and sq.lower() != q.lower():
                        found = fn(sq, per) or []
                        if found:
                            log(f"    {name}: «{q}» пусто, взято по «{sq}»")
            except Exception as e:
                log(f"  ! {name} «{q}»: {e}")
                continue
            for it in found[:per]:
                url = it.get("url") or ""
                if not url or _norm_url(url) in known | pinned:
                    continue
                known.add(_norm_url(url))
                cid = f"c{n_next:03d}"
                ext = ".mp4" if kind == "video" else ".jpg"
                low = _norm_url(url).lower()
                if low.endswith(".webm"):
                    ext = ".webm"
                elif low.endswith(".png"):
                    ext = ".png"
                dst = files / f"{cid}{ext}"
                if not assets.fetch(url, dst, limit=SCOUT_FILE_LIMIT):
                    continue
                ok, why = assets.playable(dst)
                if not ok:
                    dst.unlink(missing_ok=True)
                    continue
                frames = _frames(dst, kind)
                if not frames:
                    dst.unlink(missing_ok=True)
                    continue
                cand = {"url": url, "kind": kind, "src": it.get("src", name),
                        "q": q, "file": str(dst),
                        "dur": it.get("dur") or 0,
                        "tags": (it.get("tags") or "")[:200]}
                cands[cid] = cand
                review.setdefault(cid, {"keep": None, "quality": None,
                                        "what": "", "subjects": []})
                rows.append(_row(cid, cand, frames))
                n_next += 1
                added += 1
        if rows:
            out = sheets / f"q_{sheet_n:03d}.jpg"
            _sheet(rows, out, f"{kind}: {q}")
            for r_id, c in cands.items():
                if (c["q"] == q and c["kind"] == kind
                        and "sheet" not in c):
                    c["sheet"] = str(out)
            sheet_n += 1
        log(f"  {kind:<5} «{q}»: {added} кандидатов")
        _save(cand_p, cands)
        _save(rev_p, review)
    todo = sum(1 for v in review.values() if v.get("keep") is None)
    log(f"── кандидатов всего {len(cands)}, не просмотрено {todo}. "
        f"Листы: {sheets}. Анкета: {rev_p}")


def apply(job_path: Path, job):
    _root, _files, _sheets, cand_p, rev_p = _paths(job)
    cands, review = _load(cand_p, {}), _load(rev_p, {})
    have = {_norm_url(it["url"] if isinstance(it, dict) else it)
            for key in ("pinned_clips", "pinned_archive")
            for it in (job.get(key) or [])}
    added = {"video": 0, "image": 0}
    weak = empty = 0
    for cid, v in sorted(review.items()):
        c = cands.get(cid)
        if not c or v.get("keep") is not True:
            continue
        q = int(v.get("quality") or 0)
        if q < MIN_QUALITY:
            weak += 1
            continue
        subj = [str(s).strip().lower() for s in (v.get("subjects") or [])
                if str(s).strip()]
        if not subj:
            # Без предметов кадр встанет куда попало — это и есть та
            # ошибка, ради которой отбор переехал в чат.
            empty += 1
            continue
        if _norm_url(c["url"]) in have:
            continue
        have.add(_norm_url(c["url"]))
        key = "pinned_clips" if c["kind"] == "video" else "pinned_archive"
        job.setdefault(key, []).append({
            "url": c["url"], "q": c["q"], "src": "pinned", "kind": c["kind"],
            "from": c["src"], "what": (v.get("what") or "")[:90],
            "subjects": subj[:10], "quality": q})
        added[c["kind"]] += 1
    trusted = list(job.get("trusted_sources") or [])
    if "pinned" not in trusted:
        trusted.append("pinned")
    job["trusted_sources"] = trusted
    job_path.write_text(json.dumps(job, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    log(f"── в спецификацию: {added['video']} клипов, {added['image']} фото")
    if weak:
        log(f"   {weak} одобренных с оценкой ниже {MIN_QUALITY} — не взяты")
    if empty:
        log(f"   ! {empty} одобренных без subjects — не взяты: без предметов "
            f"монтаж не знает, под какую фразу их ставить")
    todo = sum(1 for v in review.values() if v.get("keep") is None)
    if todo:
        log(f"   {todo} кандидатов ещё не просмотрено")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["search", "apply"])
    ap.add_argument("job")
    ap.add_argument("--per", type=int, default=6,
                    help="кандидатов на запрос с каждого источника")
    ap.add_argument("--q", action="append", default=[],
                    help="свой запрос вместо списков спецификации")
    ap.add_argument("--only", choices=["video", "photo"])
    a = ap.parse_args(argv)
    job_path = Path(a.job)
    job = json.loads(job_path.read_text(encoding="utf-8"))
    if a.cmd == "search":
        search(job, a.per, a.q, a.only)
    else:
        apply(job_path, job)


if __name__ == "__main__":
    main(sys.argv[1:])
