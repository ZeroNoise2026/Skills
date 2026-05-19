# skills/

Shared "skill" framework for the QuantAgent project.

A **skill** is a self-contained capability (generate a research report,
compare tickers, explain a filing, ...) that can be invoked from any
consumer — currently the Summarization CLI and the ChatbotUI chat endpoint.
Putting them in a top-level `skills/` package keeps both consumers reading
from the same source of truth, so a fix or new feature lands in one place.

## Layout

```
skills/
├── __init__.py       public re-exports
├── base.py           Skill abstract class + SkillResult dataclass
├── registry.py       auto-discovery (skills/*/skill.py) + lookup API
├── router.py         LLM-based message → (skill_name, args) classifier
├── README.md         this file
└── <skill_name>/
    ├── __init__.py
    ├── SKILL.md      "when to use" guidance (read by the router LLM)
    └── skill.py      Skill subclass implementing run()
```

Each subdirectory of `skills/` whose name doesn't start with `_` or `.`
and that contains a `skill.py` is treated as a skill. Anything else
(this README, helper modules, tests) is ignored by the registry.

## Adding a new skill

1. Create `skills/<name>/` with `__init__.py`, `SKILL.md`, and `skill.py`.
2. In `skill.py`, subclass `skills.base.Skill`, set `name`, `description`,
   and `parameters` (JSON schema), and implement `run(**kwargs)` returning
   a `SkillResult`.
3. That's it — the registry discovers it on next import.

Names must be unique across the whole `skills/` tree; duplicates raise at
import time.

## Consuming skills

### From the Summarization CLI

```python
from skills import get_skill

result = get_skill("generate_report").run(ticker="AAPL")
print(result.content)
for path in result.artifacts:
    print("wrote", path)
```

### From the ChatbotUI chat endpoint

```python
from skills import get_skill
from skills.router import route

decision = route(user_message)
if decision.skill is not None:
    result = get_skill(decision.skill).run(**decision.args)
    return result.content
# else: fall through to existing RAG flow
```

## Design notes

- **Sync, not async.** Both consumers are sync today; the LLM call (not
  concurrency) is the bottleneck. Skills that need internal parallelism
  can spin up threads or `asyncio.run()` themselves.
- **One instance per call.** `get_skill()` returns a fresh instance every
  time, so skills can safely keep per-invocation state on `self`.
- **No persistence in MVP.** Skill results are returned to the caller and
  not written to a `skill_runs` table. `SkillResult.artifacts` is the
  escape hatch for skills (like `generate_report`) that do write files.
- **Router degrades gracefully.** If `route()` fails for any reason
  (bad JSON, network blip, missing API key) it returns `skill=None`, and
  the caller falls back to its default behavior.

## Status

Skeleton only — `Skill`, `registry`, `router` exist; concrete skill
implementations (`generate_report`, `compare_tickers`) are stubs to be
filled in once the framework is approved.
