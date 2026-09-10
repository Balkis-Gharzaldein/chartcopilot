"""End-to-end test of ChartCopilot backend pipeline."""
import json
from pathlib import Path
from ingestion import ingest_file
from fastapi.testclient import TestClient
from api.main import app


def test_full_pipeline():
    """Test the full pipeline: upload → plan → execute → results with figure_json."""
    FIXTURE = Path("sample_data/messy_sales_example.xlsx")
    data = FIXTURE.read_bytes()

    # Step 1: Upload workbook
    client = TestClient(app)
    plan_resp = client.post(
        "/api/workbooks",
        files={"file": ("test.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert plan_resp.status_code == 200, f"Upload failed: {plan_resp.text}"
    workbook_id = plan_resp.json()["workbook_id"]
    print(f"✓ Uploaded workbook: {workbook_id}")

    # Step 2: Set guideline
    guideline_resp = client.post(
        f"/api/workbooks/{workbook_id}/guideline",
        json={"text_area": "Show sales over time by region"},
    )
    assert guideline_resp.status_code == 200
    lines = guideline_resp.json()["lines"]
    print(f"✓ Guideline set: {len(lines)} lines")

    # Step 3: Plan charts
    plan_resp = client.post(
        f"/api/workbooks/{workbook_id}/plan",
        json={"lines": lines},
    )
    assert plan_resp.status_code == 200
    specs = plan_resp.json()["specs"]
    print(f"✓ Planned {len(specs)} chart specs")

    # Step 4: Execute charts
    exec_resp = client.post(
        f"/api/workbooks/{workbook_id}/execute",
        json={},
    )
    assert exec_resp.status_code == 200, exec_resp.text
    results = exec_resp.json()["results"]
    figure_results = [r for r in results if r.get("figure_json")]
    print(f"✓ Executed {len(results)} charts, {len(figure_results)} with figure_json")

    # Step 5: Verify results
    assert len(figure_results) > 0, "No charts with figure_json generated!"
    r = figure_results[0]
    spec = r["spec"]

    print(f"  Chart type: {spec['chart_type']}")
    print(f"  Chart title: {spec['title']}")
    print(f"  Figure JSON length: {len(r['figure_json'])} chars")
    print(f"  Figure data rows: {len(r.get('figure_data', []))}")
    print(f"  Verified: {r.get('verified', False)}")
    print(f"  Computed summary: {r.get('computed_summary', {})}")

    # Step 6: Test refine
    refine_resp = client.post(
        f"/api/workbooks/{workbook_id}/refine",
        json={
            "target_index": 0,
            "message": "Change colors to blue and orange",
        },
    )
    assert refine_resp.status_code == 200
    refine_data = refine_resp.json()
    print(f"✓ Refine response: target_index={refine_data.get('target_index')}")
    if refine_data.get("results"):
        uc = refine_data["results"][0]
        print(f"  Updated chart type: {uc['spec']['chart_type']}")
        print(f"  Updated chart title: {uc['spec']['title']}")
        print(f"  Refinement log: {refine_data.get('reply', 'N/A')}")

    print()
    print("=== ALL E2E TESTS PASSED ===")


if __name__ == "__main__":
    test_full_pipeline()
