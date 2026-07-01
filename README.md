<div align="center">

# PrivilegePod 🔒

**Private investigation briefs from a pile of evidence — open-source models on your own Runpod GPUs.**
_Confidential documents never leave your infrastructure._

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
&nbsp;![Python](https://img.shields.io/badge/Python-3.11%E2%80%933.12-3776AB?logo=python&logoColor=white)
&nbsp;![Runpod Flash](https://img.shields.io/badge/Runpod-Flash-6C3EF6)
&nbsp;![Models](https://img.shields.io/badge/models-Qwen2.5--7B_%2B_Qwen2.5--VL-FF6A00?logo=huggingface&logoColor=white)
&nbsp;![Endpoints](https://img.shields.io/badge/Flash_endpoints-2_(text_%2B_vision)-blueviolet)
&nbsp;![Vendor egress](https://img.shields.io/badge/LLM_vendor_egress-%240.00-3FB950)

</div>

Investigators, auditors, and litigators work with privileged material they cannot
paste into OpenAI or Anthropic. PrivilegePod runs the analysis on open-source
models (Qwen2.5 text + Qwen2.5-VL vision) on **Runpod Flash** GPUs, so the
evidence stays private — and turns a folder of emails and scanned documents into
a clickable investigation brief.

---

## What it does

Point it at a folder of emails. A text model on your GPU extracts a structured
event from each one (who acted, amounts, invoice refs, red flags); a vision model
reads the *scanned* evidence that has no text layer; and a synthesis pass distills
it all into a short narrative + a **findings table** of the actual money
discrepancies. The result is a single HTML brief where **every event expands to
show the exact source email** it came from.

## Input

A corpus of `.eml` emails (the evidence). The bundled demo (`example_file/`) is a
synthetic billing dispute — Meridian Healthcare Staffing vs. Westridge DHS, 34
emails with invoice / receipt / spreadsheet attachments.

## Output

`report/timeline.html` — a self-contained investigation brief:

- a plain-English **narrative** + a **findings table** (disputed amounts and their
  status) with a total-exposure figure
- a **scanned evidence** section showing what the vision model read off the images
- the full chronological **timeline**, where you **click any event to read its
  source email inline**
- header shows which models + GPU produced it ($0.00 to any vendor)

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
  emails ─┐                    ┌─▶ llm_worker    @Endpoint (Qwen2.5-7B  text)
          ├─▶ analyze.py ──────┤
  scans ──┘                    └─▶ vision_worker @Endpoint (Qwen2.5-VL  vision)
                                          │  events + scanned evidence
                                          ▼
                          llm_worker synthesis ──▶ report/timeline.html
  both models run on YOUR Runpod GPUs — nothing is sent to any external LLM provider
```

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install runpod-flash

flash login                 # or put RUNPOD_API_KEY in .env.local
flash dev                   # serves the llm_worker + vision_worker endpoints; note the port

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
