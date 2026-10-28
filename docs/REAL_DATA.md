# Real-data validation track

## Why literature cases

Real clinical-trial SAE source documents (investigator notes, diaries, site lab reports) are confidential and never
published, so no public dataset pairs a real multi-document trial case pack with its official output. The hero demo
case (ST-0417) is therefore a synthetic pack modelled on real documents; it shows conflicts and the follow-up loop.

**Literature cases are the one place where a real input and a real regulatory output are both public.** Marketing
authorisation holders must screen the literature and submit an ICSR for every valid published case about their drug
(EU GVP Module VI; US 21 CFR 314.80). FDA's FAERS public data keeps the article reference on those ICSRs:

| | Source | What it gives |
|---|---|---|
| Input | Open-access case report (Europe PMC full text, CC licence only) | Real, messy clinical prose: history, drugs, timeline, labs, outcome |
| Expected output | The manufacturer's FAERS ICSR citing that article (openFDA) | Age, sex, suspect drugs, MedDRA PTs, seriousness, outcome, coded by company safety staff |

Limits to state openly: FAERS public data has no narrative; companies code the same article differently; one article
can yield several ICSRs (one per company); a literature case is a single document, so no cross-document conflicts.

## Commands (laptop; no LLM quota used by the first two)

```powershell
python scripts/build_meddra_vocab.py --limit 1000        # bigger public PT list (openFDA top 1000 PTs)
python scripts/fetch_literature_cases.py --n 8           # writes data/cases/LIT-PMCxxxx/ (article + expected_faers.json)
$env:LLM_MODE="live"; python eval/real_cases.py          # runs the agent on each LIT case, scores vs FAERS
python eval/real_cases.py --score-only                   # re-score saved runs without new LLM calls
```

Open `eval/results/real_<timestamp>.md`, adjudicate every miss (agent wrong / FAERS coded differently / not stated in
the article), and report raw and adjudicated agreement.

## Timing study (the "saves time and effort" number)

Within-subject and counterbalanced, so case difficulty and learning effects cancel out.

1. Pick 4 LIT cases. Two people (A, B). A does cases 1–2 manually and 3–4 with SafetyTrace; B does the reverse.
2. **Manual arm:** read the article; fill the case form (patient age/sex, suspect drug, dose, dates, events, seriousness,
   outcome, dechallenge); choose MedDRA PTs from `data/meddra_demo_vocab.txt`; write a 5–8 sentence narrative.
3. **Assisted arm:** open the case in the app; review the facts (each with its quote), MedDRA suggestions and narrative;
   correct anything wrong; sign off.
4. Stopwatch from opening the article to finished form/sign-off. Then score both arms against `expected_faers.json`.
5. Record in a table: person, case, arm, minutes, fields agreeing with FAERS, errors found later.

Report: median minutes manual vs assisted, agreement with FAERS in both arms, n, and that participants are proxy users
(not trained drug-safety associates). Context for the deck: vendor-reported manual handling is about 50 minutes per
ICSR (Infosys), so state your own measured number and its sample size next to it.
