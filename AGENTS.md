# Agent instructions (Ancient Whispers)

This repository builds long Ancient Whispers YouTube videos (voice, stock,
edit, 2 Shorts, 2 covers) from a job JSON in `jobs/`.

## If the user asks to invent a topic, write a script, or prepare a new video

Follow **`ИНСТРУКЦИЯ-ЧАТ.md` in the project root** end to end.

Your deliverables are always:

1. `jobs/<id>.json` — full pipeline job (schema: `jobs/ancient-example.json`).
   Must include `_длительность` targeting **40–50 minutes** of narration
   (~6000–8000 English words across `script_blocks`) for history /
   mysteries / ancient-world topics.
2. `jobs/<id>.youtube.txt` — posting pack (title, description + sources,
   tags, cover prompt with yellow text, community post + image prompts,
   two Shorts titles/questions, **pinned first comment**)

Also read: `docs/протокол-сценария.md`, `docs/протокол-монтажа.md`,
`jobs/ancient-01.json`, `channel/log.json`.

Do **not** start GitHub Actions / full render unless the user explicitly
asks. Script + posting pack first.

## If the user asks to build / render / run the pipeline

Use the existing pipeline (`CLAUDE.md`, `.github/workflows/build.yml`):
push a **new** `jobs/<id>.json` to `main` for automatic `stage: auto`, or
trigger **Build video** manually with `job=<id>` and `stage=auto` (or run
local `mock`/`smoke`/`build` as appropriate).
Never re-run `stage: assets` just to remount — use `auto` so voice/image
cache is reused.
