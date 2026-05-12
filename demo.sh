# Examples

## `synthetic_run.py`

End-to-end demo of the full pipeline using a fake recording (two
PIL-generated screenshots + three synthesized events). **No display
required** — runs in CI, on a headless VPS, anywhere Python ≥ 3.10 +
Pillow are available.

```bash
python examples/synthetic_run.py        # default: ./runs/
python examples/synthetic_run.py --keep # retain the demo run
make demo                               # equivalent shortcut
```

What it does:

1. Creates a new run via `dare runs new`.
2. Generates two `800×600` PNG frames and a 3-event JSONL stream.
3. Runs every pipeline stage in order: `normalize → generate-dsl →
   build-intents → clarify --auto-clarify → validate → execute` (dry-run).
4. Prints the per-stage summary JSON and verifies that every expected
   artifact exists on disk.
5. Cleans up the demo run unless `--keep` is passed.

Use this as the **smoke test** for any fresh install of D.A.R.E.
