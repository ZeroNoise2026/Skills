# skills/

Shared **skill framework** for the QuantAgent project. A skill is a
self-contained capability (generate a research report, compare tickers,
explain a filing, ...) that any consumer in the repo can invoke.

> **For PMs / analysts:** This is the place where reusable analyst
> workflows live. Today there are two: writing a single-ticker research
> report, and comparing multiple tickers side-by-side. New skills can be
> added without changing the chat product or the CLI — they show up
> automatically in both.

> **For engineers:** This is a top-level Python package shared by
> `Summarization/` and `ChatbotUI/`. It owns skill discovery, the LLM
> router that maps user messages to skills, and a small neutral
> Moonshot client used by skills that need an LLM call.

## Why this exists

Before today, analyst-facing logic lived in two places that couldn't
share code: the `Summarization/` CLI (offline reports) and the
`ChatbotUI/` backend (RAG Q&A). Adding a new capability — like
multi-ticker comparison — had no clean home.

`skills/` is that home. One skill, two surfaces:

```
                          skills/<name>/skill.py
                                 │
                ┌────────────────┴────────────────┐
                ▼                                 ▼
        Summarization CLI                ChatbotUI chat endpoint
   `python -m summary.run                router → skill → SSE
    --skill <name> --args …`             (fallback to RAG on miss)
```

## Layout

```
skills/
├── __init__.py            public re-exports (Skill, SkillResult, registry API)
├── base.py                Skill abstract class + SkillResult dataclass
├── registry.py            auto-discovery (skills/*/skill.py) + lookup API
├── router.py              LLM-based message → (skill_name, args) classifier
├── _llm.py                neutral Moonshot client used internally
├── README.md              this file
├── generate_report/
│   ├── SKILL.md           "when to use" guidance (read by the router LLM)
│   └── skill.py           Skill subclass implementing run()
├── compare_tickers/
│   ├── SKILL.md
│   └── skill.py
└── tests/
    ├── test_smoke.py          offline pipeline tests (33 cases, all mocked)
    ├── test_router_prompt.py  inspect what the router sends to Moonshot
    └── test_live_router.py    real-API smoke test (requires MOONSHOT_API_KEY)
```

Each subdirectory of `skills/` whose name doesn't start with `_` or `.`
and that contains a `skill.py` is treated as a skill. Anything else
(this README, helper modules, `tests/`) is ignored by the registry.

## Available skills

| Name | Purpose | Required args | Returns |
|------|---------|---------------|---------|
| `generate_report` | Full investment-analysis report for one ticker | `ticker: str` | Markdown content + artifact at `Summarization/output/{TICKER}_{DATE}.md` |
| `compare_tickers` | Side-by-side comparison of 2+ tickers | `tickers: list[str]` | Markdown content (no artifact) |

Run `python -m summary.run --list-skills` (from `Summarization/`) to see
the same list with descriptions.

## Adding a new skill

1. Create `skills/<name>/` with `__init__.py`, `SKILL.md`, and `skill.py`.
2. In `skill.py`, subclass `skills.base.Skill`, set the three class
   attributes (`name`, `description`, `parameters`), and implement
   `run(**kwargs) -> SkillResult`.
3. Write `SKILL.md` describing when the skill should fire and what each
   parameter means. The router LLM reads this to decide routing.
4. Done — the registry discovers it on next import. No registration
   step, no separate config file.

Names must be unique across the whole tree; duplicates raise at import
time so collisions can't ship.

Minimal example:

```python
# skills/news_digest/skill.py
from skills.base import Skill, SkillResult

class NewsDigestSkill(Skill):
    name = "news_digest"
    description = "Summarize recent news for a single ticker."
    parameters = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "days":   {"type": "integer", "default": 7},
        },
        "required": ["ticker"],
    }

    def run(self, *, ticker: str, days: int = 7) -> SkillResult:
        # ... fetch news, build prompt, call LLM ...
        return SkillResult(content="...", meta={"ticker": ticker, "days": days})
```

## Consuming skills

### From the Summarization CLI

```bash
# From inside Summarization/
python -m summary.run --list-skills
python -m summary.run --skill generate_report --args ticker=AAPL
python -m summary.run --skill compare_tickers \
    --args '{"tickers":["AAPL","MSFT"],"focus":"valuation"}'
```

`--args` accepts two forms: simple `k=v,k=v` (convenient for one-offs) or
JSON (`{...}`, required for nested values like lists). Boolean and integer
strings are auto-coerced in the `k=v` form.

The original CLI flags (`--ticker`, `--all`, `--dry-run`) continue to
work unchanged.

### From the ChatbotUI chat endpoint

The chat backend (`ChatbotUI/backend/main.py`) already calls the router
inside `/api/chat/stream` — no extra wiring needed for new skills.
Pseudocode of what happens per message:

```python
decision = route(user_message)         # one small Moonshot call
if decision.skill is not None:
    result = get_skill(decision.skill).run(**decision.args)
    yield_as_sse(result.content)
else:
    yield_from(rag_pipeline(user_message))  # original behavior
```

If routing fails for any reason (missing API key, network blip, JSON
parse error) the request falls through to RAG. **No new failure modes
introduced** in chat.

### Programmatic use

```python
from skills import get_skill, list_skills
from skills.router import route

# Direct invocation
result = get_skill("generate_report").run(ticker="AAPL")
print(result.content)
for path in result.artifacts:
    print("wrote", path)

# Or let the router decide
decision = route("Compare AAPL and MSFT on valuation")
if decision.skill:
    result = get_skill(decision.skill).run(**decision.args)
```

## How routing works

The router is a single Moonshot call with a system prompt listing every
registered skill (name, description, JSON-schema for parameters). The
model returns a tiny JSON object:

```json
{"skill": "compare_tickers", "args": {"tickers": ["AAPL", "MSFT"]}}
```

…or `{"skill": null}` when no skill fits. The classifier model is small
and cheap (`moonshot-v1-8k` at temperature 0), so routing adds ~one
fast API call per message. Override via `SKILLS_ROUTER_MODEL` env var.

To see exactly what gets sent to the router:

```bash
python -m skills.tests.test_router_prompt
```

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `MOONSHOT_API_KEY` | required | Without it, routing and LLM-backed skills disable themselves and the caller falls through to its default behavior. |
| `MOONSHOT_BASE_URL` | `https://api.moonshot.ai/v1` | API endpoint. |
| `SKILLS_ROUTER_MODEL` | `moonshot-v1-8k` | Model used for the router classification call. |

## Testing

```bash
# Fast offline suite (no API calls, no DB). 33 cases.
python -m skills.tests.test_smoke

# Inspect the router prompt without sending it.
python -m skills.tests.test_router_prompt

# Real router call against Moonshot — needs MOONSHOT_API_KEY.
python -m skills.tests.test_live_router
```

## Design notes (engineering)

- **Synchronous, not async.** Both consumers (Summarization CLI and the
  ChatbotUI SSE endpoint) are sync today; the bottleneck is the LLM
  call, not concurrency. Skills that need internal parallelism can spin
  up threads or call `asyncio.run()` themselves.
- **One instance per call.** `get_skill()` returns a fresh instance every
  time, so a skill can keep per-invocation state on `self` safely.
- **No persistence in MVP.** Skill results are returned to the caller
  and not written to a `skill_runs` table. `SkillResult.artifacts` is
  the escape hatch for skills (like `generate_report`) that do write
  files.
- **Router degrades gracefully.** If `route()` fails for any reason it
  returns `skill=None`, and the caller falls back to its default
  behavior.
- **`_llm.py` is the survivor.** Two near-identical Moonshot wrappers
  exist elsewhere in the repo (`Summarization/shared/llm.py` and
  `ChatbotUI/backend/summarizer.py`). `skills/_llm.py` is the minimal
  neutral version — when we consolidate the three, this is the one to
  keep.

## Known tech debt

- `registry.invoke()` does not yet validate kwargs against the JSON
  schema. Skills are expected to do their own arg checks until we add
  `jsonschema` as a dep.
- The Moonshot client is duplicated in three places (see above).
- `compare_tickers` lets the LLM build the metrics table — more robust
  would be to assemble the table deterministically from `earnings` /
  `price_snapshot` and let the LLM write only the prose around it.
