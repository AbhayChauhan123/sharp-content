# Sharp — Daily Content Engine

Auto-generates Sharp's daily briefing: pulls multi-source RSS, has Claude select the
top ~10 cross-corroborated, conversation-worthy stories **per niche** (finance, tech,
politics, sports, general), each tagged `region: US | Global`, with a grounded quiz.

- **Guards:** facts only from source snippets (no hallucination), prefer ≥2 sources,
  copy rule (informed, never "smarter/IQ").
- **Output:** `output/latest.json` (the app fetches this) + a dated archive file.
- **Cost:** ~$0.30–0.50/day on `claude-sonnet-4-6`; flat regardless of user count.

## Automated (this repo)
The **Daily Sharp Content** GitHub Action runs every morning (11:00 UTC), regenerates
`output/latest.json`, and commits it. The app reads it from the raw URL:

```
https://raw.githubusercontent.com/AbhayChauhan123/sharp-content/main/output/latest.json
```

Needs one repo secret: **`ANTHROPIC_API_KEY`**.

## Run locally
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 generate.py            # full run -> output/latest.json
python3 generate.py --dry-run  # fetch + per-niche counts only (no key)
```

Configurable via env: `SHARP_MODEL` (default `claude-sonnet-4-6`; cheaper `claude-haiku-4-5-20251001`),
`SHARP_PER_VERTICAL` (default 10).
