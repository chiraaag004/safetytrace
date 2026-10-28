# How to add a test case (copy this folder to data/cases/ST-XXXX)

1. **Write the gold fact sheet first** (`gold.json`): every fact with `field`, `value`, `doc` (filename), `page`, and the
   exact `quote` from the document. This is your evaluation ground truth AND the offline mock's extraction output.
2. **Write the documents** in `documents/` as `.txt` (use a line `=== PAGE BREAK ===` between pages) or `.pdf`.
   Plant the behaviour you want to test: a conflict (two documents disagree), a gap (missing field), a hidden fact
   (mark it `"hidden_on_first_pass": true` in gold.json to simulate a missed footer), a seriousness change in a follow-up.
3. **Write `case.json`**: `case_id`, `title`, `demo_today` (ISO date used for the clock), `documents` and `followups`
   lists with `path` and `doc_type` (physician_note, patient_diary, lab_report, followup_reply, investigator_brochure, other).
4. Validate quotes: `python scripts/validate_case.py ST-XXXX` (fails if any gold quote is not in its document).
5. Run it: `python scripts/run_case.py ST-XXXX --demo-human`, then add an assertion to `tests/test_end_to_end_mock.py`.

Folders starting with `_` are ignored by the app.
