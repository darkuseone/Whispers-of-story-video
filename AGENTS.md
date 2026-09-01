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
2. `jobs/<id>.youtube.txt` — posting pack (title, description + sources,
   tags, **two CTR cover prompts** (font locked to channel yellow
   condensed caps; composition from `docs/протокол-обложки.md`),
   community post + image prompts, two Shorts titles/questions,
   **pinned first comment**)

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
