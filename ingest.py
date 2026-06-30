"""PrivilegePod ingest (stage 1, local proof).

Parses a folder of .eml files into normalized document records: headers, plain-text
body, and a list of attachments (name, mime type, size, whether it has a text layer).
Writes data/ingest_manifest.json and prints a summary table.

Uses only the Python standard library so it runs anywhere with zero install.
"""
from __future__ import annotations

import email
import json
from email import policy
from pathlib import Path

EML_DIR = Path("example_file/mailbox/eml")
OUT = Path("data/ingest_manifest.json")


def parse_eml(path: Path) -> dict:
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)

    # plain-text body
    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content().strip() if body_part else ""

    attachments = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        name = part.get_filename() or "(unnamed)"
        ctype = part.get_content_type()
        # text-layer PDFs contain the marker "/Text"; scans (image-only) usually don't
        has_text_layer = ctype == "application/pdf" and b"/Text" in payload
        attachments.append(
            {
                "filename": name,
                "content_type": ctype,
                "size_bytes": len(payload),
                "needs_ocr": ctype.startswith("image/")
                or (ctype == "application/pdf" and not has_text_layer),
            }
        )

    return {
        "source_file": str(path),
        "message_id": msg.get("Message-ID", "").strip("<>"),
        "date": msg.get("Date", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "cc": msg.get("Cc", ""),
        "subject": msg.get("Subject", ""),
        "body": body,
        "attachments": attachments,
    }


def main() -> None:
    paths = sorted(EML_DIR.glob("*.eml"))
    docs = [parse_eml(p) for p in paths]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(docs, indent=2))

    n_att = sum(len(d["attachments"]) for d in docs)
    n_ocr = sum(a["needs_ocr"] for d in docs for a in d["attachments"])
    print(f"Parsed {len(docs)} emails | {n_att} attachments | {n_ocr} need OCR\n")
    print(f"{'#':>2}  {'date':<26} {'from':<34} subject")
    print("-" * 110)
    for i, d in enumerate(docs, 1):
        sender = d["from"][:33]
        subj = d["subject"][:44]
        flag = " [OCR]" if any(a["needs_ocr"] for a in d["attachments"]) else ""
        print(f"{i:>2}  {d['date'][:25]:<26} {sender:<34} {subj}{flag}")
    print(f"\nWrote manifest -> {OUT}")


if __name__ == "__main__":
    main()
