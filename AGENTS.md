# Agent instructions (Ancient Whispers)

This repository builds long Ancient Whispers YouTube videos (voice, stock,
edit, 2 Shorts, 2 covers) from a job JSON in `jobs/`.

## If the user asks to invent a topic, write a script, or prepare a new video

Follow **`ИНСТРУКЦИЯ-ЧАТ.md` in the project root** end to end.

Your deliverables are always:

1. `jobs/<id>.json` — full pipeline job (schema: `jobs/ancient-example.json`).
   Must include `_длительность` targeting **30–40 minutes** of narration
   (~5000–6500 English words across `script_blocks`).

   Since August 2026 the channel is **mixed-topic**: antiquity and
   archaeology, science and space, mythology, historical investigations,
   and the "new world" (modern technological and digital phenomena —
   dead internet theory, AI, and so on).

   **An episode outside antiquity MUST set `vet_context`.** Material
   vetting asks a vision model whether a frame belongs to this episode's
   period, and without that field it falls back to "this is an
   ancient-world channel" — i.e. it rejects the episode's own correct
   footage as anachronistic. This is not hypothetical: it happened on
   `dead-internet-01`, which is the worked example to copy.
2. `jobs/<id>.youtube.txt` — posting pack (title, **one copy-paste
   description block**: intro → `Chapters` with timecodes (first `00:00`,
   ≥ 3, ascending, each ≥ 10 s; estimated before the build, exact ones
   come from `pipeline/youtube.py` after render) → disclaimer →
   `Sources` (≥ 5 real, verifiable sources actually used — never
   invented) → hashtags; **tags as a separate section**, not inside the
   description, ≤ 500 characters; **two CTR cover prompts** (Anton
   #FFD400 locked to the upper third; detailed 16:9 scene below; grid in
   `docs/протокол-обложки.md`), community post + image prompts, two
   Shorts titles/questions, **pinned first comment**). The same data
   goes into the job's `youtube` block (`description_intro`,
   `chapters` — names only, `description_notes`, `sources`, `hashtags`,
   `tags`). Details: `ИНСТРУКЦИЯ-ЧАТ.md`, sections "Обязательно к
   каждому длинному ролику" and "Файл 2".

Also read: `docs/протокол-сценария.md`, `docs/протокол-монтажа.md`,
`docs/протокол-обложки.md`, `jobs/ancient-01.json`, `channel/log.json`.

Do **not** start GitHub Actions / full render unless the user explicitly
asks. Script + posting pack first.

## If the user asks to build / render / run the pipeline

Use the existing pipeline (`CLAUDE.md`, `.github/workflows/build.yml`):
push a **new** `jobs/<id>.json` to `main` for automatic `stage: auto`. To
**re-run auto** on an existing job (setup failed before cache, etc.), push
`.build/<id>.retry`. If **only montage failed**, use `stage: render` or
push `.render/<id>.retry` (cache — no voice/images/vet). Manual: **Build
video** with `job` and `stage`. Local: `mock`/`smoke`/`build` as
appropriate. Never re-run `stage: assets` just to remount — use `auto` so
voice/image cache is reused.

After a successful build the pipeline delivers by itself: GitHub release
`final-<id>` → Google Drive folder of the channel (`pipeline/drive.py`) →
YouTube upload of the **long video only, always PRIVATE**
(`pipeline/youtube_upload.py`, secrets `YT_*`; setup in
`docs/youtube-upload.md`). The author presses Publish in Studio. Shorts are
not uploaded — the author posts them by hand or via Buffer. Never try to
make a video public from the API, and never route video files through the
chat/MCP.

## If the user asks for analytics / feedback on a published video

Follow **`docs/протокол-аналитика.md`**: retention by segment, CTR,
viewer comments, and — mandatory — a title comparison (YouTube Studio
A/B test "Title only" with the thumbnail fixed, or sequential title swaps
over fixed windows; conclusions on thin data are marked as hypotheses).
The feedback brief goes to the scriptwriter (copy to the director); the
scriptwriter must take its "Заголовки" section into account when writing
the three titles. Read-only: no changes on YouTube, in `jobs/` or in code.
