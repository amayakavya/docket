# Docket

An email-to-resolution operations platform for a service provider. Inbound mail is
read, classified against a taxonomy, scored for risk and priority, routed to the desk
that owns it, then tracked through a workflow against service-level targets.

The worked example is a connectivity provider: broadband, mobile and leased lines.
Nothing in the domain is hardcoded outside two files, so pointing it at a different
business is an editing job rather than a rewrite.

## Architecture

```
React 19 + Vite console            port 5173
  └── FastAPI backend              port 8010
        ├── SQLite via SQLAlchemy
        ├── SentenceTransformers   embeddings for classification
        ├── BERT NER               entity extraction from mail bodies
        └── a local language model summaries, drafts, free-text work
```

## The flow

1. **Intake.** The mail is parsed, attachments are read, quoted history is stripped and
   scope is checked. Mail about a competitor, or about nothing, is answered and closed
   rather than routed into a desk queue.
2. **Classification.** The taxonomy resolves the subject. Keywords match on word
   boundaries, so "sue" does not fire on "issue"; embeddings are the fallback.
3. **Risk, priority and regulatory exposure** are scored independently, so a polite mail
   about unauthorised use still outranks an angry one about a bill.
4. **Routing.** Confident cases go to the owning desk. Cases the classifier is unsure of
   enter a review queue and are blocked from desk action until a person releases them.
   The system does not guess when it is unsure.
5. **Workflow and SLA.** Each case moves through states with an audit record behind it,
   and resolution time is measured from the frozen resolution timestamp rather than from
   the last row update, which drifts.

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
./start.sh                 # backend 8010, console 5173
```

Fixtures, both deterministic from a fixed seed:

```bash
.venv/bin/python -c "from backend.app.services.seed_subscribers import seed_subscribers"
.venv/bin/python seed_cases.py 4       # four cases per classification
```

Tests use unittest, not pytest:

```bash
.venv/bin/python -m unittest backend.tests.test_regressions -v
```

## Changing the domain

| | |
| --- | --- |
| `backend/app/catalog/taxonomy.py` | categories, classifications, keywords, desks, SLA tiers |
| `backend/app/services/intake_triage.py` | competitor names and the off-topic organisations |
| `backend/app/services/seed_subscribers.py` | what a customer record looks like |
| `frontend/src/styles.css` | nine colour tokens at the top drive the whole console |

Desks are named by key in the taxonomy and expanded once, so renaming a desk is a
one-line change rather than a search across the codebase.

## Notes

Authentication issues development tokens by role: admin, auditor, compliance officer,
resolution agent, escalation manager, security analyst, intake operator, support
engineer. It is convenience for local work, not an authentication system.

Every fixture is fictional. Telephone numbers begin 55, which is not an allocated
mobile series, and every address is at example.com, so nothing here can reach a real
person. The competitor names are invented.
