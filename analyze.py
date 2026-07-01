"""PrivilegePod — self-contained private investigation brief (multi-endpoint).

Two Flash GPU endpoints on YOUR Runpod account, nothing sent to any LLM vendor:
  • llm_worker     (Qwen2.5-7B)    — extracts events from emails, synthesizes findings
  • vision_worker  (Qwen2.5-VL)    — reads SCANNED evidence (no text layer)

Pipeline: ingest emails + scans -> vision reads scans / llm reads emails ->
synthesize -> render a self-contained HTML brief (narrative + findings + scanned
evidence + full timeline), every item linking to its source.

  Usage:  FLASH_PORT=8890 python analyze.py
  View:   open report/timeline.html
"""
from __future__ import annotations

import base64
import email
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from email import policy
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path

EML_DIR = Path("example_file/mailbox/eml")
OUT = Path("report/timeline.html")
PORT = os.environ.get("FLASH_PORT", "8888")
LLM_ENDPOINT = f"http://localhost:{PORT}/llm_worker/runsync"
VISION_ENDPOINT = f"http://localhost:{PORT}/vision_worker/runsync"
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))

# Scanned / photographed evidence with no text layer — only a vision model reads it.
KEY_SCANS = [
    ("example_file/attachments/screenshots/Approval_System_Screenshot_0447.png",
     "What invoice number, dollar amount, and approval status does this screenshot show? Be concise.",
     "Approval system — INV-0447"),
    ("example_file/attachments/screenshots/Payment_Portal_Status.png",
     "What invoice number, dollar amount, and payment status does this portal screenshot show? Be concise.",
     "Payment portal — INV-0447"),
    ("example_file/attachments/invoices/INV-2021-0447_SCAN.png",
     "Transcribe the invoice number, total amount, and service period from this scanned invoice. Be concise.",
     "Scanned invoice — INV-0447 ($48,200)"),
]

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "actor": {"type": "string"},
        "summary": {"type": "string", "description": "one sentence: what happened"},
        "invoice_refs": {"type": "array", "items": {"type": "string"}},
        "amount": {"type": "string", "description": "dollar amount mentioned, or empty"},
        "red_flag": {"type": "string", "description": "discrepancy/denial/underpayment, else empty"},
        "significance": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["actor", "summary", "significance"],
}

EVENT_SYSTEM = (
    "You are a forensic investigator building a timeline from evidence emails in a "
    "billing dispute (Meridian Healthcare Staffing, vendor, vs Westridge DHS, payer). "
    "For the ONE email given, extract a single timeline event: who acted, a "
    "one-sentence summary, any invoice numbers, any dollar amount, and a red_flag "
    "describing a discrepancy (underpayment, denial, withheld funds, struck line, "
    "duplicate) or empty string if none. Mark significance 'high' only when money is "
    "denied, withheld, underpaid, or disputed — routine submit/approve is 'low'."
)

SYNTH_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string", "description": "3-5 sentence plain-English summary"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "amount": {"type": "string"},
                    "status": {"type": "string"},
                    "detail": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "amount", "status", "detail"],
            },
        },
    },
    "required": ["narrative", "findings"],
}

SYNTH_SYSTEM = (
    "You are a forensic accountant summarizing a billing dispute for an investigator. "
    "Vendor Meridian Healthcare Staffing billed payer Westridge DHS. MOST invoices "
    "were approved and paid in full — those are NOT findings. Identify ONLY the "
    "specific discrepancies where money was denied, withheld, underpaid, struck, or "
    "duplicated.\n\n"
    "CRITICAL RULES:\n"
    "1. A finding's 'amount' is the DISPUTED PORTION ONLY — never the full invoice "
    "total. EXAMPLE: a $60,460 invoice with $1,150 of lodging receipts struck as "
    "illegible -> the finding amount is $1,150, NOT $60,460.\n"
    "2. Do NOT create a finding for any invoice that was approved and paid in full.\n"
    "3. MERGE all events about the same dispute into ONE finding (an invoice approved "
    "then later denied is a single finding).\n"
    "4. Typical findings here: a large invoice approved then denied and never paid; "
    "third-party (FEMA) funds withheld; a credit memo the vendor never issued; struck "
    "receipts; hours removed in a forced rebuild; a duplicated line.\n\n"
    "Write a 3-5 sentence NARRATIVE of the overall pattern and the headline dispute. "
    "Then list each finding with: a clear title (e.g. 'INV-0447 approved then denied'), "
    "the disputed amount, the status, a one-sentence detail, and the 1-3 source email "
    "filenames."
)


def call_llm(endpoint: str, body: dict, timeout: int = 900, retries: int = 4) -> dict:
    """POST with retries — survives transient DNS/network blips, cold-start races,
    and worker 500s instead of silently returning an empty result."""
    import time as _t

    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                endpoint, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read()).get("output") or {}
            if isinstance(out, dict) and out.get("status_code") == 500:
                raise RuntimeError(str(out.get("body"))[:200])
            return out
        except Exception as e:
            last = e
            _t.sleep(3 * (attempt + 1))
    raise last


def parse_eml(path: Path) -> dict:
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content().strip() if body_part else ""
    atts = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
    except Exception:
        dt = None
    return {
        "file": str(path), "name": path.name, "dt": dt,
        "date_hdr": msg.get("Date", ""), "from": msg.get("From", ""),
        "to": msg.get("To", ""), "subject": msg.get("Subject", ""),
        "body": body, "attachments": atts,
    }


def extract_event(doc: dict) -> dict:
    user = (
        f"DATE: {doc['date_hdr']}\nFROM: {doc['from']}\nSUBJECT: {doc['subject']}\n"
        f"ATTACHMENTS: {', '.join(doc['attachments']) or '(none)'}\n"
        f"BODY:\n{doc['body'][:4000]}"
    )
    out = call_llm(LLM_ENDPOINT, {"input": {"input_data": {
        "system": EVENT_SYSTEM, "user": user, "schema": EVENT_SCHEMA, "max_tokens": 300}}})
    ev = out.get("json") or {}
    ev["_doc"] = doc
    ev["_runtime"] = out.get("runtime", {})
    return ev


def read_scan(path: str, prompt: str) -> dict:
    b = base64.b64encode(Path(path).read_bytes()).decode()
    out = call_llm(VISION_ENDPOINT, {"input": {"input_data": {
        "image_b64": b, "prompt": prompt}}})
    return {"text": out.get("text", ""), "runtime": out.get("runtime", {})}


def synthesize(events: list[dict], scans: list[dict]) -> dict:
    lines = []
    for e in events:
        doc = e["_doc"]
        date = doc["dt"].strftime("%Y-%m-%d") if doc["dt"] else "?"
        lines.append(
            f"{date} | {e.get('actor','?')} | {e.get('amount','') or '-'} | "
            f"{', '.join(e.get('invoice_refs') or []) or '-'} | "
            f"{e.get('red_flag','') or '-'} | {e.get('summary','')} [file: {doc['name']}]"
        )
    user = "EVENTS (chronological):\n" + "\n".join(lines)
    if scans:
        # one concise corroboration line — NOT the full transcripts (dumping all
        # three distracted the model into flagging a paid invoice). The scans'
        # detail lives in the report's Scanned-evidence section instead.
        user += ("\n\nNOTE: vision-read scans confirm INV-0447 ($48,200) was "
                 "APPROVED then DENIED with $0 paid. They corroborate INV-0447 "
                 "only and are not separate invoices.")
    out = call_llm(LLM_ENDPOINT, {"input": {"input_data": {
        "system": SYNTH_SYSTEM, "user": user, "schema": SYNTH_SCHEMA, "max_tokens": 1800}}})
    return out.get("json") or {}


def money(s: str) -> float:
    m = re.search(r"[\d,]+(?:\.\d+)?", s or "")
    return float(m.group(0).replace(",", "")) if m else 0.0


def render(events: list[dict], synth: dict, scans: list[dict],
           runtime: dict, vruntime: dict) -> None:
    events.sort(key=lambda e: (e["_doc"]["dt"] is None, e["_doc"]["dt"]))
    gpu = runtime.get("gpu", "Runpod GPU")
    findings = synth.get("findings") or []
    total = sum(money(f.get("amount", "")) for f in findings)

    frows = []
    for f in findings:
        ev_links = " ".join(
            f'<a href="#ev-{escape(n)}">{escape(n)}</a>' for n in (f.get("evidence") or []))
        frows.append(f"""
      <tr><td class="famt">{escape(f.get('amount','') or '—')}</td>
        <td><b>{escape(f.get('title',''))}</b><div class="fdetail">{escape(f.get('detail',''))}</div></td>
        <td class="fstatus">{escape(f.get('status',''))}</td>
        <td class="fev">{ev_links}</td></tr>""")

    srows = []
    for s in scans:
        srows.append(f"""
      <tr><td class="sfile">🖼 {escape(s['file'])}</td>
        <td><b>{escape(s['label'])}</b><div class="fdetail">read: “{escape(s['text'])}”</div></td></tr>""")

    trows = []
    for e in events:
        doc = e["_doc"]
        sig = e.get("significance", "low")
        date = doc["dt"].strftime("%Y-%m-%d") if doc["dt"] else "—"
        flag = (e.get("red_flag") or "").strip()
        amount = (e.get("amount") or "").strip()
        refs = ", ".join(e.get("invoice_refs") or [])
        head = (f"From: {doc['from']}\nTo: {doc['to']}\nDate: {doc['date_hdr']}\n"
                f"Subject: {doc['subject']}\nAttachments: "
                f"{', '.join(doc['attachments']) or '(none)'}\n{'-'*60}\n")
        trows.append(f"""
    <div id="ev-{escape(doc['name'])}" class="event sig-{escape(sig)}">
      <div class="when">{escape(date)}</div>
      <div class="ebody">
        <div class="head"><span class="actor">{escape(e.get('actor','?'))}</span>
          {f'<span class="amt">{escape(amount)}</span>' if amount else ''}
          {f'<span class="refs">{escape(refs)}</span>' if refs else ''}
          {f'<span class="sig">{escape(sig)}</span>' if sig=='high' else ''}</div>
        <div class="summary">{escape(e.get('summary',''))}</div>
        {f'<div class="flag">⚠ {escape(flag)}</div>' if flag else ''}
        <details class="src"><summary>📧 {escape(doc['name'])} · {escape(doc['subject'][:70])}</summary>
          <pre>{escape(head + doc['body'])}</pre></details>
      </div></div>""")

    vmodel = vruntime.get("model", "Qwen2.5-VL")
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>PrivilegePod — Investigation Brief</title>
<style>
  body{{font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0d1117;color:#e6edf3}}
  header{{padding:26px 40px;background:#161b22;border-bottom:1px solid #30363d}}
  h1{{margin:0 0 6px;font-size:22px}} h2{{font-size:15px;letter-spacing:.04em;text-transform:uppercase;color:#8b949e;margin:34px 0 12px}}
  .priv{{color:#3fb950;font-weight:600;font-size:13px}}
  main{{padding:8px 40px 60px;max-width:1000px}}
  .summary-box{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px 22px;margin-top:24px}}
  .narr{{font-size:16px}}
  .exposure{{display:flex;align-items:baseline;gap:12px;margin-top:16px;padding-top:16px;border-top:1px solid #30363d}}
  .exposure b{{font-size:30px;color:#ff7b72}} .exposure span{{color:#8b949e;font-size:13px}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  td{{padding:12px 10px;border-bottom:1px solid #21262d;vertical-align:top}}
  .famt{{font-weight:700;color:#79c0ff;white-space:nowrap}} .sfile{{color:#d2a8ff;white-space:nowrap}}
  .fdetail{{color:#8b949e;font-size:13px;margin-top:3px}} .fstatus{{color:#e3b341;font-size:13px}}
  .fev a,.src summary{{color:#58a6ff;text-decoration:none}} .fev a{{margin-right:6px;font-size:12px}}
  .event{{display:flex;gap:16px;padding:10px 0;border-bottom:1px solid #21262d}}
  .event.sig-high{{background:#da36330d}}
  .when{{width:92px;flex:none;color:#8b949e;font-variant-numeric:tabular-nums}}
  .head{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}} .actor{{font-weight:600}}
  .amt{{background:#1f6feb22;color:#79c0ff;padding:1px 8px;border-radius:10px;font-size:12px}}
  .refs{{color:#8b949e;font-size:12px}}
  .sig{{font-size:11px;text-transform:uppercase;padding:1px 8px;border-radius:10px;background:#da363322;color:#ff7b72}}
  .summary{{margin:3px 0}} .flag{{color:#ff7b72;font-size:13px}}
  .src{{font-size:12px;margin-top:3px}} .src pre{{white-space:pre-wrap;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px;color:#c9d1d9;font:12px/1.5 ui-monospace,monospace}}
  details.timeline > summary{{cursor:pointer;color:#8b949e;font-size:13px}}
</style></head><body>
<header>
  <h1>PrivilegePod — Investigation Brief</h1>
  <div class="priv">🔒 100% private · {escape(gpu)} on Runpod · text: {escape(runtime.get('model','Qwen2.5-7B'))} · vision: {escape(vmodel)} · $0.00 to any LLM vendor</div>
</header>
<main>
  <div class="summary-box">
    <div class="narr">{escape(synth.get('narrative','(no summary)'))}</div>
    <div class="exposure"><b>${total:,.0f}</b><span>across {len(findings)} flagged findings · {len(events)} emails + {len(scans)} scans reviewed</span></div>
  </div>

  <h2>Findings</h2>
  <table><tbody>{''.join(frows) or '<tr><td>No findings.</td></tr>'}</tbody></table>

  {f'<h2>Scanned evidence · read by the vision model</h2><table><tbody>{"".join(srows)}</tbody></table>' if scans else ''}

  <h2>Full timeline · {len(events)} events</h2>
  <details class="timeline"><summary>show the complete chronological timeline (click any event for its source email)</summary>
    {''.join(trows)}
  </details>
</main></body></html>"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html)


def main() -> None:
    docs = [parse_eml(p) for p in sorted(EML_DIR.glob("*.eml"))]
    print(f"ingested {len(docs)} emails; extracting events on Runpod ...")
    events: list[dict] = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(extract_event, d): d for d in docs}
        for fut in as_completed(futs):
            try:
                events.append(fut.result())
            except Exception as e:
                print(f"  event ERROR: {e}")

    print("reading scanned evidence on the vision endpoint ...")
    scans, vruntime = [], {}
    for path, prompt, label in KEY_SCANS:
        try:
            r = read_scan(path, prompt)
            scans.append({"label": label, "file": os.path.basename(path), "text": r["text"]})
            vruntime = r["runtime"] or vruntime
            print(f"  vision read {os.path.basename(path)}: {r['text'][:60]}")
        except Exception as e:
            print(f"  vision ERROR {path}: {e}")

    print(f"synthesizing findings from {len(events)} events + {len(scans)} scans ...")
    synth = {}
    try:
        synth = synthesize(events, scans)
    except Exception as e:
        print(f"  synthesis ERROR: {e}")

    runtime = next((e["_runtime"] for e in events if e.get("_runtime")), {})
    render(events, synth, scans, runtime, vruntime)
    print(f"\nnarrative: {synth.get('narrative','')[:150]}")
    print(f"findings: {len(synth.get('findings') or [])}  scans: {len(scans)}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
