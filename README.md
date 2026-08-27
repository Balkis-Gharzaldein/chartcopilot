# ChartCopilot

An **agentic Excel-visualization agent** that turns a spreadsheet plus plain-English instructions into a finished, interactive dashboard — no chart-building, no code.

```
[upload: data sheets + Instructions sheet/text]
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│  INGESTION                                                  │
│  • Parse Excel/CSV with pandas/openpyxl                     │
│  • Auto-detect header row (does not assume row 0)           │
│  • Profile columns: names, dtypes, samples, null/unique     │
│  → Workbook (profiles + DataFrames)                         │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  GUIDELINE EXTRACTION                                       │
│  • Pull lines from Instructions-like sheet or text box      │
│  • One chart request per line (roughly)                     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  PLANNING                                                   │
│  • Group lines by intent (same topic = one chart)           │
│  • Detect explicit column refs (\Column\ or "use X as")     │
│  • Detect count-distinct, split/comma, top-N, sort          │
│  • Determine chart type (bar/line/pie/scatter/horiz_bar)    │
│  → list[ChartSpec]  (pydantic-validated)                    │
│                                                              │
│  Modes:                                                      │
│  • LLM mode (with API key) → structured output via LLM      │
│  • Deterministic mode (no API key) → heuristic planner      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  AGENT LOOP  (per chart, ReAct-style)                       │
│                                                              │
│  1. OBSERVE   → inspect_data (schema, never raw data)       │
│  2. THINK     → codegen (pandas snippet from ChartSpec)      │
│  3. ACT       → run_code (sandboxed subprocess execution)    │
│  4. BUILD     → create_chart (Plotly figure)                │
│  5. VERIFY    → verify_computed (independent recomputation)  │
│                                                              │
│  Modes:                                                      │
│  • LLM codegen (with API key) → LLM writes pandas snippet   │
│  • Deterministic codegen (no key) → rule-based snippet       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  SANDBOX  (tools/sandbox_worker.py)                         │
│  • Persistent subprocess (reused across charts)             │
│  • Restricted exec: df, pd, safe builtins only              │
│  • No imports, no file I/O, no network, no dunder access    │
│  • AST-level rejection + hard timeout (default 8s)          │
│  • Kill + respawn on timeout or malicious snippet           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  NARRATIVE SYNTHESIS                                        │
│  • Input: computed_summary dicts only (never raw data)      │
│  • Every claim must trace to a number                       │
│  • LLM mode or deterministic template fallback              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  DASHBOARD  (Streamlit UI)                                  │
│  • Charts in guideline order (Plotly)                       │
│  • Grounded narrative summary                               │
│  • Per-chart: data table, computed numbers, verification    │
│  • Skipped/flagged items shown transparently                │
│  • Chat refinement: edit one chart in place                 │
└─────────────────────────────────────────────────────────────┘
```

## Key Features

| Feature | Description |
|---------|-------------|
| **Deterministic mode** | Works without any API key — heuristic planning, codegen, and narrative |
| **Line grouping** | Combines guideline lines about the same topic into one chart |
| **Explicit column detection** | Respects `\Column\` references and "use X as dimension" patterns |
| **Split/explode** | Handles comma-separated values in cells (e.g. multi-label fields) |
| **Count distinct** | Detects "count unique/distinct" and uses `nunique()` |
| **Chart adaptation** | Buckets long tails into "other", substitutes inappropriate chart types |
| **Verification** | Independent recomputation against raw data to validate results |
| **Sandboxed execution** | Persistent subprocess with AST-level security, timeout, kill switch |
| **Grounded narrative** | Summary only cites computed numbers, never raw data |

## Requirements

- Python 3.9+
- Optional: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` or `GEMINI_API_KEY` for LLM mode

Without an API key the app works end-to-end in **deterministic mode** — clearly flagged in the UI.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/Balkis-Gharzaldein/chartcopilot.git
cd chartcopilot

# Create virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# (Optional) Enable LLM mode
# export ANTHROPIC_API_KEY=sk-...
# export OPENAI_API_KEY=sk-...
# export GEMINI_API_KEY=sk-...

# Run the app
streamlit run app.py
```

Open http://localhost:8501, upload a data file, enter your guidelines, and click **Run**.

## Docker

```bash
docker build -t chartcopilot .
docker run -p 8501:8501 chartcopilot
```

## Tests

```bash
python -m pytest tests/ -v
```

## Project Structure

```
chartcopilot/
├── app.py                  # Streamlit entrypoint (upload, run, dashboard, chat)
├── agent.py                # ReAct tool loop + codegen (LLM & deterministic)
├── planning.py             # Guideline → ChartSpec (LLM & deterministic)
├── schemas.py              # Pydantic v2 models
├── ingestion.py            # Excel/CSV parsing, header detection, profiling
├── guideline.py            # Instructions sheet / text area extraction
├── narrative.py            # Grounded summary from computed aggregates
├── llm.py                  # Anthropic/OpenAI/Gemini client abstraction
├── tools/
│   ├── inspect_data.py     # Schema inspection (never raw data)
│   ├── run_code.py         # Sandboxed pandas execution
│   ├── sandbox_worker.py   # Persistent sandbox subprocess
│   └── create_chart.py     # Plotly chart building + adaptation
├── tests/
│   ├── test_pipeline.py    # Full acceptance tests
│   └── test_run_code.py    # Sandbox isolation tests
├── sample_data/            # Example datasets
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container packaging
└── README.md               # This file
```

## Architecture Details

### Planning Stage

The planning stage groups guideline lines by intent and generates `ChartSpec` objects:

1. **Line grouping** — lines about the same topic (same column matches) are merged into one chart
2. **Explicit column detection** — `\Column\` backtick references and "use X as dimension" patterns are detected per-group
3. **Chart type detection** — keywords like "trend", "pie", "scatter" determine the chart type
4. **Aggregation detection** — "count", "sum", "average", "count distinct" are detected
5. **Data notes** — split/comma, top-N, sort order are extracted for the codegen stage

### Codegen Stage

Two modes for generating pandas snippets:

- **LLM mode**: sends the ChartSpec + schema to an LLM, which returns a pandas snippet
- **Deterministic mode**: rule-based codegen that handles:
  - Split/explode for comma-separated values
  - `nunique()` for count-distinct
  - `groupby` + aggregation for bar/pie/horizontal_bar
  - `sort_values` + `head()` for top-N
  - Time-series handling for line charts

### Verification

After each chart is built, `verify_computed` independently recomputes the headline numbers from the raw data and compares them with the chart's `computed_summary`. This catches mismatches between what the codegen produced and what the data actually says.

## Known Limitations

- Each chart performs one sandboxed execution round-trip; latency scales with chart count
- Fuzzy column matching is deliberately conservative — uncertainty is flagged, not guessed
- Uploaded files are held only in memory for the session; nothing is persisted
- No multi-user support, no saved dashboards (v1 scope)

## License

MIT — see [LICENSE](LICENSE) for details.
