# Project Brief — Topic-Oriented Email Summary Engine

You are building a Python repository that summarizes emails per topic with expert-level lens, detects trending themes across sources via local embedding clustering, and produces an HTML digest with deep-links back to the source emails in the Spark mail client. Read this brief fully before any other action.

---

## 0. First Action — Existing Code in This Repo (mandatory)

Before writing a single new line of code:

1. This repo was created by merging the former `unsubscribe` project. The Gmail API backend (`src/unsubscribe/gmail_api_backend.py`), Gmail facade (`src/unsubscribe/gmail_facade.py`), progress timer (`src/unsubscribe/timed_run.py`), and JSON persistence (`src/unsubscribe/keep_list.py`) are already in place and **shared** by both unsubscribe and digest features.
2. Read the existing `src/unsubscribe/` modules and classify each function as: **reusable as-is**, **partially reusable (needs minor adaptation)**, or **unsubscribe-only**.
3. The existing `src/unsubscribe/classifier.py` has `is_unsubscribable_newsletter()` — the digest engine needs the inverse: `is_digestible()`.
4. The existing `src/unsubscribe/keep_list.py` persists the **same JSON keep file for both tools** (default path **`~/.unsubscribe_keep.json`** — see `DEFAULT_KEEP_LIST_PATH` in `src/unsubscribe/cli.py`), **no second store**. Digest uses **inverse semantics**: kept senders are digest sources; unsubscribe uses kept senders as “do not prompt / protect” for the unsubscribe walkthrough. User previews candidates in a dry run before committing.
5. Prefer importing existing `src/unsubscribe/` modules over duplicating code. Do not rewrite working Gmail API code just to match a style guide.

This is the most important instruction in the brief. Skipping it wastes Chaehan's prior work.

---

## 1. What We're Building

A topic-parameterized email summary engine. Inputs:

- Email accounts (Gmail API — already in `src/unsubscribe/gmail_api_backend.py`).
- Per-topic YAML configs that define sender allowlist, persona system prompt, trending parameters.

Outputs:

- One self-contained HTML report per topic per run, with clickable Spark deep-links to source emails.

Two initial topics:

- **`ai`** — founder/researcher lens. Priority: local LLMs, voice AI, investable companies and sectors, model benchmarks, genuine research surprises. Skip vibes-only commentary.
- **`health`** — Ph.D. psychology lens. Skip beginner advice. Integrate cross-source insights. Prefer mechanism-level claims and numbers over self-help framing.

This codebase is **infrastructure**, not a one-off. Two follow-on projects (an invoice handler and a decision questionnaire) will reuse:

- The LLM provider abstraction
- The Gmail API collection path (shared backend)
- The HTML report renderer

Build accordingly. Keep modules narrow, documented, importable.

---

## 2. User Context

The user is Chaehan: 54-year-old Korean-German, Ph.D. in psychology (Yonsei, lecturer 2 years), former professor in design psychology (6.5 years), 4+ years as a published AI researcher, currently founder/CEO of Virtual Friend (Delaware C-corp). He reads 10–20 minutes of email per day on Spark across mobile + desktop, changes countries every 1–3 months.

Implications for output content and tone:

- He **cannot stand** surface-level content. Never write "10 tips for X." Never explain a concept he obviously knows. If unsure whether he knows something, assume he does.
- He wants synthesis, not aggregation. "Three sources this week converge on X, with Y dissenting" beats "Source 1 said X. Source 2 said Y."
- For AI: stock-investable implications, local LLM benchmarks, voice AI advances are explicit interest areas. Mention model names, parameter counts, benchmark numbers, dates.
- For health/psy: mechanism > advice. Effect sizes when available. He'll dismiss output that reads like Psychology Today.

---

## 3. Architecture — Modules

Digest code lives under **`src/email_digest/`** (not a top-level `core/` package). Email collection uses the **existing** `src/unsubscribe/gmail_api_backend.py` — do not add a parallel IMAP collector.

```
src/email_digest/
  llm.py           # Provider abstraction (DeepSeek, Claude, LM Studio via litellm)
  pipeline.py      # Orchestrator
  embed.py         # Local sentence-transformer embeddings + disk cache
  cluster.py       # Trending detection (HDBSCAN or cosine threshold)
  spark_link.py    # Spark deep-link generator from Message-ID
  render.py        # Jinja2 HTML report renderer
  cache.py         # SQLite-backed cache for extractions and embeddings
  config.py        # YAML topic loader
topics/
  ai.yaml
  health.yaml
templates/
  digest.html.j2
src/unsubscribe/cli.py   # add `digest` subcommand: python -m email_digest digest run …
```

Each module must be importable standalone (no circular imports, no implicit global state, no module-level network calls).

---

## 4. LLM Provider Layer (critical)

Chaehan explicitly wants provider-swappable LLM with local fallback. Default aliases:

```python
MODEL_ALIASES = {
    "fast":  "deepseek/deepseek-v4-flash",   # per-email extraction
    "smart": "deepseek/deepseek-v4-pro",     # final synthesis
    # local / local_smart: direct MLX (not litellm); mirrors local-chat MODEL_VARIANTS paths
    "local":        "2b",   # Qwen3.5-2B-MLX-4bit
    "local_smart":  "4b",   # Qwen3.5-4B-MLX-4bit
}
MLX_MODEL_VARIANTS = {
    "qwen3": "~/.lmstudio/models/lmstudio-community/Qwen3-4B-Instruct-2507-MLX-4bit",
    "0.8b":  "~/.lmstudio/models/mlx-community/Qwen3.5-0.8B-MLX-4bit",
    "2b":    "~/.lmstudio/models/mlx-community/Qwen3.5-2B-MLX-4bit",
    "4b":    "~/.lmstudio/models/mlx-community/Qwen3.5-4B-MLX-4bit",
}
```

`fast` and `smart` use litellm → DeepSeek API. `local` / `local_smart` bypass litellm entirely and load the Qwen model directly via `mlx_lm` (same lazy-load singleton pattern as `local-chat/src/llm.py`). No env vars needed for local models — paths are hardcoded in the module.

Implementation options, in order of preference:

1. `litellm` — handles DeepSeek with one signature. Used for `fast` / `smart`.
2. Direct `mlx_lm` — used for `local` / `local_smart` (avoids the LM Studio HTTP server entirely).

Single function signature:

```python
def complete(
    messages: list[dict],
    alias: str = "smart",
    *,
    max_tokens: int = 2000,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> str: ...
```

Log every call's token counts to a SQLite table `llm_calls(ts, alias, model, input_tokens, output_tokens, cost_usd)`. Chaehan tracks costs.

---

## 5. Topic Config Schema (YAML)

```yaml
name: ai
display_name: "AI Trends — week of {date}"
senders:
  - "*@thealgorithm.com"
  - "research@*"
  - "specific-newsletter@substack.com"
folders:                       # optional Gmail label names (when wired to the API)
  - "INBOX"
  - "AI Newsletters"
window_days: 7
extract_model: fast            # alias: fast | smart | local (direct MLX: Qwen3.5-2B)
synthesize_model: smart        # or local_smart (direct MLX: Qwen3.5-4B)
persona_prompt: |
  You are summarizing for Chaehan, a working AI founder (Virtual Friend, Delaware
  C-corp) with 4+ years of published AI research. Skip introductory framing.
  Assume he knows transformer architecture, RLHF, agent loops, evaluation methods.
  Prioritize: (1) local LLM developments and benchmarks, (2) voice AI advances,
  (3) companies and business areas with stock-investment implications,
  (4) genuine research surprises. Quote specific numbers, model names, dates.
  Skip "hot takes" without empirical content. Do not pad.
trending:
  min_cluster_size: 2
  similarity_threshold: 0.62
  algorithm: hdbscan           # or "cosine_threshold"
output:
  template: digest_html
  also_email_to: "self"        # optional self-send
```

Adding a new topic later must be a YAML edit, not a code change. No DSLs. No code-as-config.

---

## 6. Pipeline (per topic run)

1. **Collect** — pull emails matching `senders` and `folders` within `window_days` using the **Gmail API** (ported from `billing-glugglejug/src/googleads_invoice/gmail_api_backend.py`). Store: Message-ID, sender, subject, date, plain-text body, original HTML. No IMAP — reuse the working Gmail API backend.
2. **Extract** — for each new email (cache by Message-ID), run the `extract_model` with a fixed extraction prompt: output JSON with `key_claims` (5–10 bullets), `entities` (companies, papers, models, people), `numbers` (any quantitative figures). Cache results.
3. **Embed** — embed each `key_claim` string using `sentence-transformers/all-MiniLM-L6-v2` (local, ~90 MB). Cache by claim hash.
4. **Cluster** — group claims across emails by embedding similarity. Clusters of size ≥ `min_cluster_size` are "trending themes."
5. **Synthesize** — call `synthesize_model` with the persona prompt + clusters + a list of single-source highlights. Output JSON with `trending` (list of theme objects: title, synthesis, source Message-IDs) and `highlights` (list of per-email standout claims).
6. **Render** — Jinja2 → self-contained HTML in `output/<topic>_<YYYY-MM-DD>.html`. Optionally email to self.

Idempotent: same emails, same day → same report. Cache hits should make a re-run finish in seconds.

---

## 7. HTML Report Spec

Single self-contained HTML file. Inline CSS. No external assets, no CDN. Sections in order:

1. **Header** — topic display_name, date range, source count, generation timestamp.
2. **Trending themes** — each cluster as a card. Card contents: theme title, 2–4 sentence synthesis, list of source emails as clickable Spark deep-links (sender · subject · date).
3. **Per-source highlights** — collapsible `<details>` per email. Subject, sender, date, key claim bullets, Spark deep-link, "open in browser" plain link as fallback.
4. **Footer** — model used, token cost for the run, link to next/previous topic reports if they exist.

Style: minimal, content-dense, dark-mode by default with `prefers-color-scheme` light variant. Readable in Spark's in-app browser and in regular browsers. No hero images, no emoji, no excessive whitespace. System fonts only.

---

## 8. Spark Deep-Link Generation

**Ship** with this scheme (Readdle has changed schemes before — README documents on-device verification; do not block coding on hardware tests):

```
readdle-spark://openmessage?messageId=<URL-encoded RFC822 Message-ID, including angle brackets>
```

Implementation:

- Take `Message-ID` from the email headers (preserve angle brackets if present).
- URL-encode the full string.
- Wrap in the scheme above.
- In the HTML, render as `<a href="readdle-spark://...">` with the visible text being the email subject.
- Always include a fallback plain `mailto:` or a sender link in case the scheme doesn't fire (e.g., when the HTML is viewed in a browser on a device without Spark).

If Readdle’s scheme changes (docs or device test), update `src/email_digest/spark_link.py` and the README.

---

## 9. Repo Structure

```
.
├── README.md
├── environment.yml             # mamba environment (python=3.12, pip deps)
├── .env.example                # Gmail API OAuth, DEEPSEEK_API_KEY
├── .gitignore                  # output/, cache/, .env, __pycache__/
├── docs/
│   ├── INVENTORY.md            # Code inventory for digest engine
│   ├── IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md  # Implementation plan
│   ├── LESSONS_LEARNED.md      # Gmail API batching / perf notes
│   └── PROJECT_BRIEF_EMAIL_SUMMARIES.md  # This brief
├── src/
│   ├── email_digest/           # NEW — digest engine
│   │   ├── __init__.py
│   │   ├── llm.py
│   │   ├── pipeline.py
│   │   ├── embed.py
│   │   ├── cluster.py
│   │   ├── spark_link.py
│   │   ├── render.py
│   │   ├── cache.py
│   │   └── config.py
│   └── unsubscribe/            # EXISTING — Gmail API, facade, utils (shared)
│       ├── gmail_api_backend.py
│       ├── gmail_facade.py
│       ├── timed_run.py
│       ├── keep_list.py
│       └── ... (existing files unchanged)
├── topics/
│   ├── ai.yaml
│   └── health.yaml
├── templates/
│   └── digest.html.j2
├── output/                     # generated reports, gitignored
├── cache/                      # SQLite + embedding caches, gitignored
├── tests/
│   ├── test_spark_link.py      # NEW — digest tests
│   ├── test_cluster.py         # NEW
│   └── ... (existing unsubscribe tests unchanged)
└── src/email_digest/cli.py     # digest commands; pass-through to unsubscribe
```

CLI:

```
python -m email_digest digest run ai
python -m email_digest digest run --all
python -m email_digest digest run ai --dry-run        # collect + extract, skip synth + render
python -m email_digest digest run ai --since 2026-05-01
python -m email_digest digest cost                    # LLM costs last 7 days

# Existing unsubscribe commands:
python -m email_digest unsubscribe list
python -m email_digest unsubscribe dry-run
...
```

---

## 10. Milestones

- **M1 (day 1)** — Repos merged (unsubscribe → email-digest). `docs/INVENTORY.md` and `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md` updated. Shared backend confirmed. No new code yet. Surface findings to Chaehan.
- **M2 (day 2)** — LLM abstraction + **Gmail API** collection working end-to-end. `python -m email_digest digest run ai --dry-run` dumps JSON of extracted emails for the AI topic.
- **M3 (day 3–4)** — Embedding cache + clustering. Trending themes identifiable for a real week of emails.
- **M4 (day 5–6)** — Synthesis prompt + HTML render. Iterate the persona prompt with Chaehan on a real run.
- **M5 (day 7)** — Cron/launchd scheduling, README, polish, cost dashboard query.

Total budget: aim for **under 1500 LOC of new code** (not counting templates and configs). If you exceed this, you're over-engineering.

---

## 11. Quality Bar

- **Caching is mandatory.** Same Message-ID → cached extraction. Same claim string → cached embedding. Re-runs are fast.
- **Cost logging.** Every LLM call's token usage hits SQLite. `python -m email_digest digest cost` prints last 7 days.
- **Error tolerance.** One bad email, one network blip, one malformed feed must not crash the pipeline. Log and continue.
- **No silent drops.** If an email fails to parse, write it to `output/_failures/<date>.log` with the reason.
- **Tests for pure logic.** `cluster.py`, `spark_link.py`, `cache.py`, prompt-rendering — unit-tested. Live Gmail API and live LLM calls — not in CI; integration tests opt-in via env var.
- **Type hints everywhere.** `mypy --strict` should pass on `src/email_digest/`.

---

## 12. What NOT to Do

- No web UI. CLI + HTML output is the entire UX surface.
- No user accounts, auth, multi-tenant logic. Single user.
- No frameworks for the sake of frameworks. No FastAPI, no Celery, no Redis. Cron + SQLite is enough.
- No content that explains concepts Chaehan obviously knows. He'll dismiss the output.
- No mocked or hallucinated quotes from source emails. Either quote verbatim or paraphrase and label as paraphrase.
- No "executive summary" filler at the top. The trending section IS the summary.
- No clever DSLs for topic configs. YAML.
- No silent rewrites of prototype code. If you reuse, preserve. If you rewrite, justify in the commit message.

---

## 13. Hand-off Checkpoint (before M4)

After extraction is working (end of M3), show Chaehan:

1. Three sample raw extractions (JSON) from real emails in his AI topic.
2. One sample cluster (which claims grouped together, the similarity scores).

Ask: "Is the extraction granularity right? Are the trending clusters meaningful, or noise?" Adjust the extraction prompt and clustering parameters based on his feedback BEFORE writing the synthesis prompt. Synthesis quality depends entirely on whether the inputs are right.

---

## 14. Open Questions to Resolve at Build Time

1. **Spark URL scheme** — Implement §8 as written; confirm on device when convenient and update `src/email_digest/spark_link.py` + README if Readdle changes the scheme. **Do not block** shipping on hardware verification.
2. **Email backend** — Use **Gmail API only** (no IMAP). Backend already lives in **`src/unsubscribe/gmail_api_backend.py`** in this repo (merged from unsubscribe); OAuth token path via **`GOOGLE_OAUTH_TOKEN`** env var.
3. **Local LLM** — `local` / `local_smart` use **direct MLX** (`mlx_lm`), not LM Studio HTTP. Models loaded from `~/.lmstudio/models/` with the exact paths in `MLX_MODEL_VARIANTS` (mirrored from sibling `local-chat/src/llm.py`). Defaults: `local` → Qwen3.5-2B, `local_smart` → Qwen3.5-4B. No env vars needed.
4. **Sender allowlists for initial topics** — Repo ships **`topics/ai.yaml`** and **`topics/health.yaml`** with multi-sender **`example.com`** patterns (RFC2606); replace locally with real **`From`** addresses (see **`digest candidates`** / **`walkthrough`**). CI rejects **`TODO-`** in **`senders`** lists.

Resolve these before or during M1. Do not block on them — proceed with reasonable defaults and flag in the README.
