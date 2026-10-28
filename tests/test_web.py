"""The web UI's API: open a case, run the agent, record human decisions, sign off (mock mode, no network)."""
import time

from fastapi.testclient import TestClient

from web.server import app


def _wait(c, case_id):
    for _ in range(300):
        s = c.get(f"/api/state/{case_id}").json()
        if not s["running"]:
            return s
        time.sleep(0.05)
    raise AssertionError("agent did not finish")


def test_full_review_flow_through_the_api():
    c = TestClient(app)
    assert any(x["id"] == "ST-0417" for x in c.get("/api/cases").json()["cases"])
    v = c.post("/api/open", json={"case_id": "ST-0417", "run_id": None}).json()
    assert v["case"]["status"] == "new" and v["trace"] == []
    assert c.post("/api/run", json={"case_id": "ST-0417"}).json()["started"]
    v = _wait(c, "ST-0417")
    assert v["case"]["status"] == "awaiting_human" and v["error"] is None
    labels = [x["label"] for x in v["tasks"]]
    assert labels[0].startswith("Approve") and "questions for the site" in labels[0]  # ask the site before deciding
    assert any(x.startswith("Decide event onset date") for x in labels)
    assert any(r["criterion"] == "Time to onset" and r["status"] == "missing" for r in v["causality"]["evidence"])  # onset in conflict
    assert v["rsi"]["proposal"] == "unexpected" and "Pruritus" in v["rsi"]["terms"]
    # approve the questions -> they go into the letter
    for g in [g for g in v["gaps"] if g["status"] == "drafted"]:
        v = c.post("/api/act", json={"case_id": "ST-0417", "action": "approve_query", "gap_id": g["id"], "text": g["query"]}).json()["state"]
    assert v["letter"] >= 2 and any(t["label"].startswith("Decide event onset date (after the site replies)") for t in v["tasks"])
    letter = c.get("/api/export/ST-0417/letter").text
    assert "Request for follow-up information" in letter and "104-0217" in letter
    assert "data-f=" in v["docs_html"] and v["coverage"]["trusted"] > 10     # evidence spans for the linked views
    assert v["trace"][0]["name"] == "Load documents"                        # plain-English agent steps
    # site reply -> agent resumes in the background
    c.post("/api/act", json={"case_id": "ST-0417", "action": "followup", "path": v["followups"][0]["path"]})
    v = _wait(c, "ST-0417")
    conflict = v["conflicts"][0]
    v = c.post("/api/act", json={"case_id": "ST-0417", "action": "resolve", "conflict_id": conflict["id"], "choice": "2026-03-12"}).json()["state"]
    assert any(g["status"] == "answered" for g in v["gaps"])                     # the reply answered the conflict question
    v = c.post("/api/act", json={"case_id": "ST-0417", "action": "expectedness", "value": "unexpected"}).json()["state"]
    assert c.post("/api/act", json={"case_id": "ST-0417", "action": "causality", "category": "very likely"}).status_code == 400
    v = c.post("/api/act", json={"case_id": "ST-0417", "action": "causality", "category": "probable", "note": "dechallenge positive"}).json()["state"]
    ev = {r["criterion"]: r["status"] for r in v["causality"]["evidence"]}
    assert ev["Time to onset"] == "supports" and ev["Dechallenge (drug stopped)"] == "supports"
    first, *rest = v["meddra"]
    v = c.post("/api/act", json={"case_id": "ST-0417", "action": "meddra", "fact_id": first["fid"], "pt": first["options"][0]}).json()["state"]
    for m in rest:
        v = c.post("/api/act", json={"case_id": "ST-0417", "action": "merge_event", "fact_id": m["fid"], "into": first["fid"]}).json()["state"]
    assert all(m["selected"] or m["merged_into"] for m in v["meddra"])
    assert any(t["view"] == "resume" for t in v["tasks"])                   # narrative is stale -> resume task
    c.post("/api/run", json={"case_id": "ST-0417"})
    v = _wait(c, "ST-0417")
    assert v["signoff"]["ok"], v["signoff"]["reasons"]
    r = c.post("/api/act", json={"case_id": "ST-0417", "action": "signoff", "who": "Tester"}).json()
    assert r["state"]["case"]["status"] == "signed_off" and r["state"]["tasks"] == []
    assert c.get("/api/export/ST-0417/trace").status_code == 200


def test_raw_originals_are_served_and_only_from_the_case_folder():
    c = TestClient(app)
    c.post("/api/open", json={"case_id": "ST-0417", "run_id": None})
    c.post("/api/run", json={"case_id": "ST-0417"})
    v = _wait(c, "ST-0417")
    r = c.get(f"/api/raw/ST-0417/{v['docs'][0]['id']}")
    assert r.status_code == 200 and "INVESTIGATOR CLINICAL NOTE" in r.text
    assert c.get("/api/raw/ST-0417/D99").status_code in (404, 500)
    assert c.get("/api/raw/ST-0417/..%2F..%2Fsafetytrace%2Fconfig.py").status_code in (404, 500)


def test_pasted_site_reply_becomes_a_new_document_and_the_agent_resumes():
    from pathlib import Path
    c = TestClient(app)
    c.post("/api/open", json={"case_id": "ST-0421", "run_id": None})
    c.post("/api/run", json={"case_id": "ST-0421"})
    v = _wait(c, "ST-0421")
    before = (v["case"]["version"], len(v["docs"]))
    assert c.post("/api/act", json={"case_id": "ST-0421", "action": "reply_text", "text": "  "}).status_code == 400
    folder = Path("data/cases/ST-0421/followups")
    existing = set(folder.glob("site_reply_*.txt")) if folder.exists() else set()
    try:
        c.post("/api/act", json={"case_id": "ST-0421", "action": "reply_text", "who": "Tester",
                                 "text": "Outcome: recovered. ALT 48 U/L on 28-Apr-2026."})
        v = _wait(c, "ST-0421")
        assert v["case"]["version"] == before[0] + 1 and len(v["docs"]) == before[1] + 1
        assert v["error"] is None
    finally:
        for f in set(folder.glob("site_reply_*.txt")) - existing:
            f.unlink()


def test_reset_starts_the_case_again_and_removes_only_pasted_replies():
    from pathlib import Path
    c = TestClient(app)
    c.post("/api/open", json={"case_id": "ST-0421", "run_id": None})
    c.post("/api/run", json={"case_id": "ST-0421"})
    _wait(c, "ST-0421")
    c.post("/api/act", json={"case_id": "ST-0421", "action": "reply_text", "text": "Outcome: recovered."})
    v = _wait(c, "ST-0421")
    assert v["case"]["version"] == 2
    folder = Path("data/cases/ST-0421/followups")
    keep = folder / "site_reply_followup_keep.txt"
    keep.write_text("prepared reply", encoding="utf-8")
    try:
        r = c.post("/api/reset", json={"case_id": "ST-0421"}).json()
        assert r["removed_replies"] == 1 and keep.exists()
        s = r["state"]
        assert s["case"]["status"] == "new" and s["case"]["version"] == 1 and s["trace"] == [] and s["docs"] == []
        assert c.post("/api/reset", json={"case_id": "NOPE"}).status_code == 404
    finally:
        keep.unlink(missing_ok=True)
        for f in folder.glob("site_reply_2*.txt"):
            f.unlink()
