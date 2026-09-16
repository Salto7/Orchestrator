# watchdog tick reference

## Paths
- Tick state / raw samples → `/workspace/cache/` (or session workspace)
- Alerts / curated hits → `/workspace/findings/`

## In-script
- Prefer **script** mode (no LLM per tick)
- Keep each tick short and idempotent
- Write structured output to files under `/workspace/cache/`

```python
from datetime import datetime, timezone
from pathlib import Path

cache = Path("/workspace/cache")
cache.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).isoformat()
(cache / "last_tick.txt").write_text(stamp + "\n", encoding="utf-8")
print(f"tick ok {stamp}")
```

## Schedule via capability
```
run_periodic(
  command="python3 /workspace/cache/tick.py",
  interval_seconds=30,
  duration_seconds=120,
)
```
