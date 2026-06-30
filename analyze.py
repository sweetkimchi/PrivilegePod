"""PrivilegePod — self-contained private investigation brief.

Reads an evidence corpus of .eml files and, using an open-source model on YOUR
Runpod GPU (the Flash `llm_worker` endpoint), produces an investigator's brief:

  1. extract one structured event per email (parallel),
  2. synthesize the events into a plain-English narrative + a findings table
     (the distinct money discrepancies, each with amount/status/evidence),
  3. render a single self-contained HTML report that LEADS with the summary and
     findings, with the full timeline as supporting detail. Every finding and
     event links to its source email. Nothing leaves your infrastructure.

  Usage:  FLASH_PORT=8890 python analyze.py
  View:   open report/timeline.html
"""
from __future__ import annotations

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
PORT = os.environ.get("FLASH_PORT", "8890")
ENDPOINT = f"http://localhost:{PORT}/llm_worker/runsync"
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))

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
        "narrative": {"type": "string", "description": "3-5 sentence plain-English summary of what happened"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "amount": {"type": "string"},
                    "status": {"type": "string", "description": "e.g. 'approved then denied, unpaid'"},
                    "detail": {"type": "string", "description": "one sentence"},
                    "evidence": {"type": "array", "items": {"type": "string"}, "description": "source email filenames"},
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
    "4. Typical findings here are: a large invoice approved then denied and never paid; "
    "third-party (FEMA) funds withheld; a credit memo the vendor never issued; struck "
    "receipts; hours removed in a forced rebuild; a duplicated line.\n\n"
    "Write a 3-5 sentence NARRATIVE of the overall pattern and the headline dispute. "
    "Then list each finding with: a clear title (e.g. 'INV-0447 approved then denied'), "
    "the disputed amount, the status (e.g. 'approved then denied, unpaid'; 'withheld'; "
    "'set aside'), a one-sentence detail, and the 1-3 source email filenames."
)


def call_llm(system: str, user: str, schema: dict, max_tokens: int) -> dict:
    payload = {"input": {"input_data": {
        "system": system, "user": user, "schema": schema, "max_tokens": max_tokens,
    }}}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read()).get("output") or {}


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
    out = call_llm(EVENT_SYSTEM, user, EVENT_SCHEMA, 300)
    ev = out.get("json") or {}
    ev["_doc"] = doc
    ev["_runtime"] = out.get("runtime", {})
    return ev


def synthesize(events: list[dict]) -> dict:
    lines = []
    for e in events:
        doc = e["_doc"]
        date = doc["dt"].strftime("%Y-%m-%d") if doc["dt"] else "?"
        lines.append(
            f"{date} | {e.get('actor','?')} | {e.get('amount','') or '-'} | "
            f"{', '.join(e.get('invoice_refs') or []) or '-'} | "
            f"{e.get('red_flag','') or '-'} | {e.get('summary','')} "
            f"[file: {doc['name']}]"
        )
    user = "EVENTS (chronological):\n" + "\n".join(lines)
    out = call_llm(SYNTH_SYSTEM, user, SYNTH_SCHEMA, 1800)
    return out.get("json") or {}


def money(s: str) -> float:
    m = re.search(r"[\d,]+(?:\.\d+)?", s or "")
    return float(m.group(0).replace(",", "")) if m else 0.0


def render(events: list[dict], synth: dict, runtime: dict) -> None:
    events.sort(key=lambda e: (e["_doc"]["dt"] is None, e["_doc"]["dt"]))
    gpu = runtime.get("gpu", "Runpod GPU")
    model = runtime.get("model", "open-source model")
    findings = synth.get("findings") or []
    total = sum(money(f.get("amount", "")) for f in findings)

    # findings table
    frows = []
    for f in findings:
        ev_links = " ".join(
            f'<a href="#ev-{escape(n)}">{escape(n)}</a>' for n in (f.get("evidence") or [])
        )
        frows.append(f"""
      <tr>
        <td class="famt">{escape(f.get('amount','') or '—')}</td>
        <td><b>{escape(f.get('title',''))}</b><div class="fdetail">{escape(f.get('detail',''))}</div></td>
        <td class="fstatus">{escape(f.get('status',''))}</td>
        <td class="fev">{ev_links}</td>
      </tr>""")

    # timeline rows
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
          {f'<span class="sig sig-{escape(sig)}">{escape(sig)}</span>' if sig=='high' else ''}
        </div>
        <div class="summary">{escape(e.get('summary',''))}</div>
        {f'<div class="flag">⚠ {escape(flag)}</div>' if flag else ''}
        <details class="src"><summary>📧 {escape(doc['name'])} · {escape(doc['subject'][:70])}</summary>
          <pre>{escape(head + doc['body'])}</pre></details>
      </div>
    </div>""")

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>PrivilegePod — Investigation Brief</title>
<style>
  body{{font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0d1117;color:#e6edf3}}
  header{{padding:26px 40px;background:#161b22;border-bottom:1px solid #30363d}}
  h1{{margin:0 0 6px;font-size:22px}} h2{{font-size:15px;letter-spacing:.04em;text-transform:uppercase;color:#8b949e;margin:34px 0 12px}}
  .priv{{color:#3fb950;font-weight:600;font-size:13px}}
  main{{padding:8px 40px 60px;max-width:1000px}}
  .summary-box{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px 22px;margin-top:24px}}
  .summary-box .narr{{font-size:16px}}
  .exposure{{display:flex;align-items:baseline;gap:12px;margin-top:16px;padding-top:16px;border-top:1px solid #30363d}}
  .exposure b{{font-size:30px;color:#ff7b72}} .exposure span{{color:#8b949e;font-size:13px}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  td{{padding:12px 10px;border-bottom:1px solid #21262d;vertical-align:top}}
  .famt{{font-weight:700;color:#79c0ff;white-space:nowrap}}
  .fdetail{{color:#8b949e;font-size:13px;margin-top:3px}}
  .fstatus{{color:#e3b341;font-size:13px}}
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
  <div class="priv">🔒 100% private · {escape(model)} on {escape(gpu)} (Runpod) · $0.00 to any LLM vendor</div>
</header>
<main>
  <div class="summary-box">
    <div class="narr">{escape(synth.get('narrative','(no summary)'))}</div>
    <div class="exposure"><b>${total:,.0f}</b><span>total across {len(findings)} flagged findings · {len(events)} emails reviewed</span></div>
  </div>

  <h2>Findings</h2>
  <table><tbody>{''.join(frows) or '<tr><td>No findings.</td></tr>'}</tbody></table>

  <h2>Full timeline · {len(events)} events</h2>
  <details class="timeline"><summary>show the complete chronological timeline (click any event for its source email)</summary>
    {''.join(trows)}
  </details>
</main>
</body></html>"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html)


def main() -> None:
    docs = [parse_eml(p) for p in sorted(EML_DIR.glob("*.eml"))]
    print(f"ingested {len(docs)} emails; extracting events on Runpod ({ENDPOINT}) ...")
    events: list[dict] = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(extract_event, d): d for d in docs}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                events.append(fut.result())
            except Exception as e:
                print(f"  event {i} ERROR: {e}")
    print(f"extracted {len(events)} events; synthesizing findings ...")
    synth = {}
    try:
        synth = synthesize(events)
    except Exception as e:
        print(f"  synthesis ERROR: {e}")
    runtime = next((e["_runtime"] for e in events if e.get("_runtime")), {})
    render(events, synth, runtime)
    print(f"\nnarrative: {synth.get('narrative','')[:160]}")
    print(f"findings: {len(synth.get('findings') or [])}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
