# PrivilegePod 🔒

**Private investigation timelines from a pile of evidence — built by an
open-source model running on your own Runpod serverless GPUs. Confidential
documents never leave your infrastructure.**

Investigators, auditors, and litigators work with privileged material they
cannot paste into OpenAI or Anthropic. PrivilegePod runs the analysis on an
open-source model (Qwen2.5) on **Runpod Flash** GPUs, so the evidence stays
private — and turns a folder of emails into a clickable "what happened" timeline.

---

## What it does

Point it at a folder of emails. For each one, a model on your GPU extracts a
structured timeline event — **who acted, what happened, amounts, invoice
references, and any red flag** (denial, underpayment, withheld funds). The events
are assembled into a single HTML timeline where **every event expands to show the
exact source email** it came from.

## Input

A corpus of `.eml` emails (the evidence). The bundled demo (`example_file/`) is a
synthetic billing dispute — Meridian Healthcare Staffing vs. Westridge DHS, 34
emails with invoice / receipt / spreadsheet attachments.

## Output

`report/timeline.html` — a self-contained, chronological timeline:

- one event per email, sorted by date
- red flags highlighted (denials, underpayments, withheld funds)
- amounts and invoice numbers surfaced
- **click any event to read its source email inline**
- header shows which model + GPU produced it

## How you view it

It's a single HTML file. Open it in any browser:

```bash
open report/timeline.html
```

Everything is embedded — no server, no external fetches. Hand the file to a
colleague and it just works.

---

## How it runs on Runpod Flash

A two-endpoint pipeline, both Flash `@Endpoint`s on serverless GPUs that scale to
zero when idle — no Dockerfile:

- **`llm_worker.py`** — text LLM (Qwen2.5-7B): structured-JSON event extraction
  and synthesis.
- **`vision_worker.py`** — vision LLM (Qwen2.5-VL): reads the *scanned* evidence
  (the photographed $48,200 invoice, the approval/denial screenshots) that has no
  text layer, and feeds it into the analysis.
- **`analyze.py`** — ingests the emails locally and drives both endpoints (8-way
  parallel), then renders the brief.

```
  emails ──▶ analyze.py (local ingest) ──▶ llm_worker @Endpoint
                                            (Qwen2.5 on a Runpod GPU)
                                                   │
            timeline.html  ◀── structured events ─┘
  nothing is sent to any external LLM provider
```

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install runpod-flash

flash login                 # or put RUNPOD_API_KEY in .env.local
flash dev                   # serves the llm_worker endpoint; note the port it prints

# in another shell (use the port flash printed):
FLASH_PORT=8888 python analyze.py
open report/timeline.html
```

## Demo (showing it live)

You demo your own terminal + browser + the Runpod console — not the editor:

1. **Runpod dashboard** → Serverless → `privilege_llm`: idle at 0 workers, then run
   the pipeline and watch workers scale up and the request counter climb. The
   vendor's own console is the proof.
2. **Terminal:** `flash dev` streams every Runpod call (POST, worker provisioning, GPU).
3. **Terminal:** `FLASH_PORT=<port> python analyze.py` builds the brief; calls flow live.
4. **Browser:** `open report/timeline.html` — the deliverable.

A punchy one-liner for the stage:

```bash
FLASH_PORT=<port> python live_call.py
# →  POST .../llm_worker/runsync   (private model on your Runpod GPU)
# ←  responded in 1.8s   GPU: NVIDIA GeForce RTX 4090 · $0.00 to any vendor
```

You can also hit the endpoint interactively in the browser via Swagger at
`http://localhost:<port>/docs`.

## Repo layout

```
PrivilegePod/
├── llm_worker.py     # private text LLM Flash GPU endpoint (Qwen2.5-7B)
├── vision_worker.py  # private vision Flash GPU endpoint (Qwen2.5-VL) for scans
├── analyze.py        # ingest emails -> events -> findings -> brief
├── live_call.py      # one live call, prints the Runpod GPU it ran on (demo)
├── ingest.py         # standalone .eml parser
├── example_file/     # synthetic evidence corpus
└── report/           # generated timeline.html
```

> Synthetic demo data — names, companies, and amounts are fictional. Built for
> the Runpod Flash hackathon.
