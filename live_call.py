"""live_call.py — one live call to the private Runpod LLM endpoint, for demos.

Shows a request going out and the Runpod GPU/host it came back from — a clean
5-second "this is running on my own GPU, not OpenAI" moment on stage.

  Usage:  FLASH_PORT=8890 python live_call.py ["your text here"]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

PORT = os.environ.get("FLASH_PORT", "8890")
URL = f"http://localhost:{PORT}/llm_worker/runsync"

user = sys.argv[1] if len(sys.argv) > 1 else (
    "FILENAME: INV-2021-0447_SCAN.pdf\n"
    "EXCERPT: Invoice INV-2021-0447. Total due $48,200 for RN staffing, period ending 07/09/2021."
)
payload = {"input": {"input_data": {
    "system": "Classify this billing document and extract its amount. Return JSON.",
    "user": user,
    "schema": {"type": "object", "properties": {
        "label": {"type": "string"}, "amount": {"type": "string"}}, "required": ["label"]},
    "max_tokens": 120,
}}}

print(f"→  POST {URL}")
print("   private open-source model on YOUR Runpod GPU — nothing goes to OpenAI/Anthropic")
t = time.time()
req = urllib.request.Request(
    URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=900) as r:
    out = json.loads(r.read()).get("output") or {}
dt = time.time() - t
rt = out.get("runtime", {})

print(f"\n←  responded in {dt:.1f}s")
print(f"   GPU:    {rt.get('gpu', '?')}")
print(f"   host:   {rt.get('host', '?')}  ({rt.get('os', '?')}, CUDA {rt.get('cuda', '?')})")
print(f"   model:  {out.get('model', '?')}")
print(f"   result: {json.dumps(out.get('json'))}")
print("\n   $0.00 to any LLM vendor — it ran on your own GPU.")
