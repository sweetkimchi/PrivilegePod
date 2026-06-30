# PrivilegePod 🔒

**Run confidential-document AI analysis on open-source models on your own Runpod
GPUs — so privileged evidence never touches OpenAI or Anthropic.**

PrivilegePod is the private inference layer for [AuditRouter](../AuditRouter), a
billed-vs-paid reconciliation engine. AuditRouter reads a pile of emails +
documents + a payer's ledger and reconstructs, per invoice, what was billed vs.
what was actually paid — emitting a master spreadsheet, a hyperlinked PDF, and a
click-to-source evidence viewer.

Its one privacy leak: the LLM pipeline calls a hosted vendor (Claude / GPT).
For privileged or confidential matters — litigation discovery, government
audits, healthcare billing — that is a non-starter: **you legally cannot paste
the evidence into someone else's API.** PrivilegePod swaps the brain for an
open-source model (Qwen2.5) running on **Runpod Flash serverless GPUs**, behind
AuditRouter's existing provider interface. Same answer, same clickable proof,
**nothing leaves your infrastructure.**

---

## The idea in one line

> The "Router" in AuditRouter used to route to Anthropic/OpenAI. PrivilegePod
> makes it route to **your own GPU**. Every token stays private; the routing log
> reads `qwen-2.5 (runpod)` at **$0.00 to any vendor.**

---

## What goes IN  (the input)

**1. An evidence corpus — the confidential material.** Whatever a matter
actually generated:

| Kind | Formats | In the demo corpus (`example_file/`) |
|------|---------|--------------------------------------|
| Emails | `.eml`, `.mbox` export | 34 emails |
| Invoices | PDF (text **and** scanned/photographed) | 11 invoice PDFs incl. a blurry photo scan |
| Receipts / screenshots | PDF, PNG | lodging receipts, vendor-portal screenshots |
| Spreadsheets | XLSX | billing register, FEMA summary, roster |
| Payer ledger | XLSX | `WDHS_Warrant_Register_2021.xlsx` (what was paid) |

**2. A matter preset (YAML)** describing the domain so the engine stays generic:
entity names (vendor/payer), line-type vocabulary, invoice-number patterns, and
which spreadsheet columns are amounts. Ships with `nurse-staffing.yaml`.

**3. Per-request input to the Flash GPU endpoint** (what the model actually
sees). The endpoint is a private, structured-output LLM call:

```jsonc
POST /llm_worker/runsync
{
  "input": { "input_data": {
    "system": "You are a litigation-support analyst reconciling invoices…",
    "user":   "SUBJECT: Invoice INV-2021-0447 …\nBODY: …",
    "schema": { /* JSON Schema, e.g. MatchResult / ExtractionPlan */ },
    "max_tokens": 1024
  }}
}
```

The `schema` forces the model (via vLLM **guided-JSON decoding**) to return
exactly the shape AuditRouter expects — the in-house replacement for Anthropic's
`messages.parse(output_format=PydanticModel)`.

## What comes OUT  (the output)

- **Per-invoice reconciliation:** billed vs. paid vs. unpaid, with a verification
  status for each figure.
- **Audit findings:** duplicates set aside, amounts removed between invoice
  rebuilds, struck/illegible receipts, third-party (e.g. FEMA) funds kept, etc.
- **Deliverables:** `master.xlsx`, a hyperlinked `MASTER_SUMMARY.pdf`, per-finding
  PDFs, and a clickable evidence viewer where every dollar traces to the exact
  email / attachment / cell it came from.
- **A routing log** proving every LLM call went to `qwen-2.5 (runpod)` —
  `$0.00` to any external vendor.

On the bundled demo corpus the target is the published ground truth:
**$66,350 floor / $72,430 verified unpaid**, including the headline exhibit —
**INV-2021-0447 ($48,200): approved, then denied, paid $0.**

---

## Architecture

```
                       confidential corpus (emails, PDFs, xlsx, ledger)
                                          │
                          ┌───────────────┴───────────────┐
                          │   AuditRouter pipeline (local) │
                          │   index → match → classify →   │
                          │   extract → reconcile → render │
                          └───────────────┬───────────────┘
                                          │  every LLM call
                                          ▼
                        AuditRouter  llm/FlashProvider  (drop-in)
                          builds prompt + JSON Schema, POSTs ↓
                                          │
            ──────────────────────────────┼──────────────────────────  your infra ends here ↑
                                          ▼
                    Runpod Flash  ›  @Endpoint privilege_llm  (GPU)
                    vLLM serving Qwen2.5-Instruct + guided-JSON decoding
                       scales 0→N workers per demand, back to 0 when idle
```

Nothing crosses the line into a third-party LLM. The model runs on Runpod
serverless GPUs **you** control.

### Flash endpoints

| File | Endpoint | Hardware | Job |
|------|----------|----------|-----|
| `llm_worker.py` | `privilege_llm` | GPU (RTX 4090 / A5000) | Private structured LLM: `{system, user, schema}` → guided JSON |
| `cpu_worker.py` | `cpu_worker` | CPU | Lightweight processing (scaffold) |
| `gpu_worker.py` | `gpu_worker` | GPU | Hardware probe used to prove real GPU execution |

---

## Quickstart

```bash
# 0) prerequisites: a Runpod account + API key (https://runpod.io → Settings → API Keys)
python3 -m venv .venv && source .venv/bin/activate
pip install runpod-flash

# 1) authenticate (saves your key) — or put RUNPOD_API_KEY=... in .env.local
flash login

# 2) run the dev server; it auto-discovers @Endpoint functions
flash dev                         # serves http://localhost:8888

# 3) call the private LLM (first call cold-starts a GPU worker on Runpod)
curl -X POST http://localhost:8888/llm_worker/runsync \
  -H 'Content-Type: application/json' \
  -d '{"input":{"input_data":{
        "system":"Reply with JSON only.",
        "user":"Classify: INV-2021-0447_SCAN.pdf",
        "schema":{"type":"object","properties":{"label":{"type":"string"}},"required":["label"]},
        "max_tokens":64}}}'
```

To run the full reconciliation privately, point AuditRouter at this endpoint:

```bash
# in AuditRouter/backend
export LLM_BACKEND=flash
export FLASH_ENDPOINT_URL=http://localhost:8888
python -m auditrouter.demo          # same pipeline, private brain
```

---

## Proof it's a *real* Runpod GPU (not a mock)

`gpu_worker` was invoked through `flash dev`; a real worker cold-started and
returned its own hardware identity in **~59 seconds**:

```json
{"status":"COMPLETED","output":{
   "message":"PrivilegePod GPU proof",
   "gpu":{"available":true,"name":"NVIDIA GeForce RTX 4090"},
   "python_version":"3.12.12"}}
```

A Mac has no NVIDIA device and reports `darwin / 3.13`; this is a **real RTX 4090
on Linux / 3.12.12**. The Flash dev log shows the live provisioning against
Runpod's control plane — searching real datacenters for capacity, then pulling
the worker image:

```
POST /gpu_worker/runsync
gpu_worker │ waiting  No workers available … gpu type ADA_24 in US-CA-2, US-IL-1, …
gpu_worker │ pulling image
```

`llm_worker` additionally returns a `_runpod_proof` block (`host`, `os`, `gpu`,
`cuda`) on every inference, so each private LLM call is self-verifying.

### Cost shape

Serverless bills only while your code runs, and scales to **zero** when idle.
CPU runs cost a fraction of a cent; a 4090 is ~$0.0002–0.0005/sec, so the
~1-minute proof above cost a couple of cents. The corpus is ~61 LLM calls — a
queue-based batch that spins workers up on demand and back down after, which is
exactly the workload Runpod Flash is built for.

---

## Repo layout

```
PrivilegePod/
├── llm_worker.py     # the private structured-LLM Flash GPU endpoint (vLLM + Qwen2.5)
├── gpu_worker.py     # GPU hardware probe (proves real Runpod execution)
├── cpu_worker.py     # CPU worker (scaffold)
├── ingest.py         # local .eml → normalized records (stage-1 proof)
├── example_file/     # synthetic evidence corpus + ground-truth answer key
└── data/             # generated manifests
```

## Status

- [x] Runpod Flash project + auth; end-to-end execution proven (CPU + **RTX 4090**)
- [x] Offline baseline reproduces ground truth ($66,350 / $72,430) via AuditRouter
- [x] `privilege_llm` GPU endpoint (vLLM + Qwen2.5, guided JSON) authored
- [ ] Provision `privilege_llm` + validate structured output on a live worker
- [ ] `FlashProvider` wired into AuditRouter; full private reconciliation run
- [ ] Vision endpoint (Qwen2.5-VL) for the scanned $48,200 exhibit

> Synthetic demo data only. Names, companies, amounts, and the `.example` email
> domain are fictional.
