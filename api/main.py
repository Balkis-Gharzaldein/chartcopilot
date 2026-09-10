"""FastAPI boundary around the existing ChartCopilot core pipeline.

Wraps: ingestion.ingest_file, guideline.extract_guideline,
       planning.plan_charts, agent.execute_plan / reexecute_spec,
       narrative.synthesize_narrative

Guarantees preserved:
- LLMs only receive SheetProfile / computed_summary, never raw rows
- Sandboxed execution via tools/run_code
- Independent verification + semantic validation
- Deterministic fallback when no LLM key

Storage: in-memory dict[workbook_id, Workbook] + specs/results.
No DB, no auth, no persistence — same lifetime as original Streamlit session,
but keyed by workbook_id so React frontend is stateless.

Run:  uvicorn api.main:app --reload --port 8000   (from chartcopilot/ dir)
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import execute_plan, reexecute_spec, resolve_edit
from guideline import extract_guideline
from ingestion import Workbook, ingest_file
from llm import available_provider
from narrative import synthesize_narrative
from planning import plan_charts
from schemas import ChartResult, ChartSpec, SheetProfile

app = FastAPI(
    title="ChartCopilot API",
    version="1.0.0",
    description="Clean API boundary for the ChartCopilot visualization pipeline.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- in-memory store -------------------------------------------------------

class WorkbookStore:
    """Holds Workbook + last plan/results per workbook_id."""
    def __init__(self, workbook: Workbook, filename: str):
        self.workbook = workbook
        self.filename = filename
        self.specs: list[ChartSpec] = []
        self.results: list[ChartResult] = []
        self.narrative: str = ""


MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_WORKBOOKS = 20
STORE: OrderedDict[str, WorkbookStore] = OrderedDict()


def _get_store(workbook_id: str) -> WorkbookStore:
    entry = STORE.get(workbook_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"workbook_id '{workbook_id}' not found. Upload a file first.")
    STORE.move_to_end(workbook_id)
    return entry


def _has_llm() -> bool:
    return available_provider() is not None


# --- request / response models --------------------------------------------

class GuidelineRequest(BaseModel):
    text_area: str = Field(default="", description="Optional extra guideline lines (one per line)")


class GuidelineResponse(BaseModel):
    lines: list[str]
    source: str
    instructions_sheet: str | None = None


class PlanRequest(BaseModel):
    lines: list[str] = Field(description="Guideline lines — one chart intent per line")


class PlanResponse(BaseModel):
    specs: list[ChartSpec]
    llm_available: bool = False
    clarifications: list[str] = Field(default_factory=list)


class ExecuteRequest(BaseModel):
    spec_ids: list[str] | None = Field(default=None, description="Subset of spec ids to execute; None = all")
    use_llm: bool | None = Field(default=None, description="Override LLM codegen; defaults to key presence")


class ExecuteResponse(BaseModel):
    results: list[ChartResult]
    narrative: str = ""
    llm_available: bool = False


class NarrativeRequest(BaseModel):
    # if omitted, uses stored results
    results: list[ChartResult] | None = None


class RefineRequest(BaseModel):
    message: str = Field(description="Natural-language edit, e.g. 'make chart 2 a pie chart'")
    target_index: int | None = Field(default=None, description="Explicit chart index to edit; if None, auto-resolve")
    use_llm: bool | None = None


class RefineResponse(BaseModel):
    results: list[ChartResult]
    narrative: str = ""
    reply: str
    target_index: int | None = None


class WorkbookResponse(BaseModel):
    workbook_id: str
    filename: str
    profiles: list[SheetProfile]
    sheet_names: list[str]
    llm_available: bool = False


# --- helpers ---------------------------------------------------------------

def _profile_payload(workbook: Workbook) -> list[dict[str, Any]]:
    # Serialize via Pydantic for consistent JSON
    return [p.model_dump() for p in workbook.profiles]


# --- routes ----------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "llm_available": _has_llm(), "provider": available_provider()}


@app.post("/api/workbooks", response_model=WorkbookResponse, summary="Upload Excel/CSV and create a workbook")
async def create_workbook(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large. Maximum upload size is 200 MB.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large. Maximum upload size is 200 MB.")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file.")
    try:
        workbook = ingest_file(data, file.filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc

    workbook_id = uuid.uuid4().hex[:12]
    STORE[workbook_id] = WorkbookStore(workbook, file.filename)
    STORE.move_to_end(workbook_id)
    while len(STORE) > MAX_WORKBOOKS:
        STORE.popitem(last=False)
    return WorkbookResponse(
        workbook_id=workbook_id,
        filename=file.filename,
        profiles=workbook.profiles,
        sheet_names=[p.sheet_name for p in workbook.profiles],
        llm_available=_has_llm(),
    )


@app.get("/api/workbooks/{workbook_id}", response_model=WorkbookResponse)
def get_workbook(workbook_id: str):
    entry = _get_store(workbook_id)
    return WorkbookResponse(
        workbook_id=workbook_id,
        filename=entry.filename,
        profiles=entry.workbook.profiles,
        sheet_names=[p.sheet_name for p in entry.workbook.profiles],
        llm_available=_has_llm(),
    )


@app.get("/api/workbooks/{workbook_id}/data-profile")
def get_data_profile(workbook_id: str):
    """Expanded Data Profile: uses viz.profiler with frames if available.
    Returns sheet_name -> DataProfile (per-column + dataset-level stats).
    Lazy-loaded by DatasetPage; sampled for >10k rows is handled inside profiler (still exact for small files).
    """
    entry = _get_store(workbook_id)
    from viz.profiler import profile_workbook

    try:
        profiles = profile_workbook(entry.workbook.profiles, entry.workbook.frames)
        # Serialize DataProfile dataclasses to JSON-safe dict
        import dataclasses

        out: dict[str, Any] = {}
        for sheet, dp in profiles.items():
            d = dataclasses.asdict(dp)
            # Ensure tuples (top_correlations) are JSON serializable
            d["top_correlations"] = [list(t) for t in d.get("top_correlations", [])]
            out[sheet] = d
        return out
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to compute data profile: {exc}") from exc


@app.post("/api/workbooks/{workbook_id}/guideline", response_model=GuidelineResponse)
def get_guideline(workbook_id: str, body: GuidelineRequest):
    entry = _get_store(workbook_id)
    data = extract_guideline(entry.workbook, body.text_area or "")
    return GuidelineResponse(
        lines=data.lines,
        source=data.source,
        instructions_sheet=data.instructions_sheet,
    )


@app.post("/api/workbooks/{workbook_id}/plan", response_model=PlanResponse)
def plan(workbook_id: str, body: PlanRequest):
    entry = _get_store(workbook_id)
    if not body.lines:
        raise HTTPException(status_code=400, detail="No guideline lines provided.")
    specs = plan_charts(entry.workbook.profiles, body.lines, frames=entry.workbook.frames)
    entry.specs = specs
    entry.results = []
    entry.narrative = ""
    clarifications = [s.clarification for s in specs if s.clarification]
    return PlanResponse(specs=specs, llm_available=_has_llm(), clarifications=clarifications)


@app.post("/api/workbooks/{workbook_id}/execute", response_model=ExecuteResponse)
def execute(workbook_id: str, body: ExecuteRequest):
    entry = _get_store(workbook_id)
    if not entry.specs:
        raise HTTPException(status_code=400, detail="No plan found. Call /plan first.")

    specs_to_run = entry.specs
    if body.spec_ids is not None:
        wanted = set(body.spec_ids)
        specs_to_run = [s for s in entry.specs if s.id in wanted]
        if not specs_to_run:
            raise HTTPException(status_code=400, detail="No matching spec_ids found.")

    attempt_llm = body.use_llm if body.use_llm is not None else _has_llm()
    results = execute_plan(entry.workbook, specs_to_run, attempt_llm=attempt_llm)
    # store results aligned with full specs list: if subset, merge back
    if body.spec_ids is None:
        entry.results = results
    else:
        # map results back into full list
        id_to_result = {r.spec.id: r for r in results}
        merged: list[ChartResult] = []
        for s in entry.specs:
            if s.id in id_to_result:
                merged.append(id_to_result[s.id])
            else:
                # keep previous result if existed, else skipped placeholder
                prev = next((r for r in entry.results if r.spec.id == s.id), None)
                merged.append(prev if prev else ChartResult(spec=s))
        entry.results = merged
        results = merged

    narrative = synthesize_narrative(entry.results)
    entry.narrative = narrative
    return ExecuteResponse(results=entry.results, narrative=narrative, llm_available=_has_llm())


@app.post("/api/workbooks/{workbook_id}/narrative")
def narrative(workbook_id: str, body: NarrativeRequest):
    entry = _get_store(workbook_id)
    target = body.results if body.results is not None else entry.results
    if not target:
        raise HTTPException(status_code=400, detail="No results to synthesize narrative from. Execute first or provide results.")
    text = synthesize_narrative(target)
    entry.narrative = text
    return {"narrative": text}


@app.get("/api/workbooks/{workbook_id}/results", response_model=ExecuteResponse)
def get_results(workbook_id: str):
    entry = _get_store(workbook_id)
    return ExecuteResponse(results=entry.results, narrative=entry.narrative, llm_available=_has_llm())


@app.post("/api/workbooks/{workbook_id}/refine", response_model=RefineResponse)
def refine(workbook_id: str, body: RefineRequest):
    entry = _get_store(workbook_id)
    if not entry.results:
        raise HTTPException(status_code=400, detail="No results to refine. Execute first.")
    if not body.message or not body.message.strip():
        raise HTTPException(status_code=400, detail="Message is required.")

    attempt_llm = body.use_llm if body.use_llm is not None else _has_llm()

    if body.target_index is not None:
        idx = body.target_index
        if not (0 <= idx < len(entry.results)):
            raise HTTPException(status_code=400, detail="target_index out of range.")
        target = entry.results[idx]
        # still resolve reply wording via _apply_edit path
        results, change_msg = reexecute_spec(entry.workbook, idx, entry.results, body.message, attempt_llm=attempt_llm)
        entry.results = results
        entry.narrative = synthesize_narrative(entry.results)
        return RefineResponse(results=entry.results, narrative=entry.narrative, reply=change_msg, target_index=idx)
    else:
        target, note = resolve_edit(body.message, entry.results)
        if target is None:
            return RefineResponse(results=entry.results, narrative=entry.narrative, reply=note, target_index=None)
        idx = entry.results.index(target)
        results, change_msg = reexecute_spec(entry.workbook, idx, entry.results, body.message, attempt_llm=attempt_llm)
        entry.results = results
        entry.narrative = synthesize_narrative(entry.results)
        return RefineResponse(results=entry.results, narrative=entry.narrative, reply=f"{note}\n{change_msg}", target_index=idx)


class ChartsGenerateRequest(BaseModel):
    workbookId: str = Field(description="Workbook ID from upload")
    userQuestion: str = Field(description="Natural language question, e.g. 'Show sales by region'")
    sheetName: str | None = Field(default=None, description="Optional sheet name")

class ChartsGenerateResponse(BaseModel):
    success: bool
    charts: list[ChartResult] = Field(default_factory=list)
    error: str | None = None

@app.post("/api/charts/generate", response_model=ChartsGenerateResponse, summary="Generate charts from natural language question")
def charts_generate(body: ChartsGenerateRequest):
    store = STORE.get(body.workbookId)
    if not store:
        return ChartsGenerateResponse(success=False, charts=[], error=f"workbookId '{body.workbookId}' not found")
    if not body.userQuestion or not body.userQuestion.strip():
        return ChartsGenerateResponse(success=False, charts=[], error="userQuestion is required")
    try:
        # Call existing orchestrator with user question as intent
        specs = plan_charts(store.workbook.profiles, [body.userQuestion.strip()], frames=store.workbook.frames)
        # Filter to sheet if requested
        if body.sheetName:
            specs = [s for s in specs if s.sheet == body.sheetName]
            if not specs:
                # Fallback to all if sheet filter yields none
                specs = plan_charts(store.workbook.profiles, [body.userQuestion.strip()], frames=store.workbook.frames)
        results = execute_plan(store.workbook, specs, attempt_llm=_has_llm())
        # Update store for refine to work
        store.specs = specs
        store.results = results
        store.narrative = synthesize_narrative(results)
        return ChartsGenerateResponse(success=True, charts=results)
    except Exception as exc:  # noqa: BLE001
        return ChartsGenerateResponse(success=False, charts=[], error=str(exc))


@app.delete("/api/workbooks/{workbook_id}")
def delete_workbook(workbook_id: str):
    if workbook_id not in STORE:
        raise HTTPException(status_code=404, detail="workbook_id not found.")
    del STORE[workbook_id]
    return {"deleted": workbook_id}


# --- root ------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "name": "ChartCopilot API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/health",
        "streamlit": "run `streamlit run app.py` for the legacy UI",
    }
