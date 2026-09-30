#!/usr/bin/env python3
"""Deterministic attachment text extraction for story intake.

Attachments arrive under runs/<story>/01-intake/attachments/ as raw bytes — either
downloaded by `ado.py fetch-story` (online mode) or placed there by hand (offline mode,
alongside a local story.json). Claude's Read tool already handles images and PDFs
natively (multimodal, paginated for long PDFs) and plain text formats need no conversion
at all. What it cannot parse is Office Open XML: Word (.docx) and Excel (.xlsx), which are
zipped XML, not text. The story-intake agent has no Bash tool (by design — design/review
agents get no shell, see docs/guardrails.md), so this conversion has to happen
deterministically, before the agent runs, same as everything else this repo calls
"scripts dispose."

For every convertible attachment this writes a `<name>.extracted.md` sidecar next to it
(plain text; tables rendered as markdown pipe tables) and a single
`attachments/extraction-manifest.json` recording, per attachment, what was attempted and
why anything was skipped — so the intake agent's `unreadable_reason` is only ever used for
a genuine gap, not a silent one, and a human reviewing citations can see the same record.

Out of scope: legacy binary .doc/.xls/.ppt (pre-2007 Office formats) — recorded as
unsupported with a clear reason rather than pulled in via a heavier, less reliable parser.
Re-save as .docx/.xlsx/.pptx and re-attach, or extend EXTRACTORS below following the same
pattern (a new pack-like extension point, same spirit as test-design-packs/ and
language-packs/: add a case, don't grow a special-purpose framework feature).

Runs after `ado.py fetch-story` as a second `before:` command on the intake stage in
pipeline.yaml, so it applies identically in online and offline mode.

Usage:
  python scripts/extract_attachments.py <story>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_config, now, print_json, run_path, show_path, story_key  # noqa: E402

# Formats Claude's Read tool already handles without help from this script.
NATIVE = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
          ".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml"}

LEGACY_UNSUPPORTED = {
    ".doc": "legacy binary Word (.doc); re-save as .docx and re-attach, or extend this script",
    ".xls": "legacy binary Excel (.xls); re-save as .xlsx and re-attach, or extend this script",
    ".ppt": "legacy binary PowerPoint (.ppt); re-save as .pptx and re-attach, or extend this script",
}


def extract_docx(path: Path) -> str:
    from docx import Document  # python-docx
    doc = Document(str(path))
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for i, table in enumerate(doc.tables, start=1):
        parts.append(f"[Table {i}]")
        for row in table.rows:
            parts.append("| " + " | ".join(c.text.strip() for c in row.cells) + " |")
    return "\n\n".join(parts).strip()


def extract_xlsx(path: Path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    parts = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        parts.append(f"## Sheet: {sheet_name}")
        for row in ws.iter_rows(values_only=True):
            if any(c is not None for c in row):
                parts.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
        parts.append("")
    return "\n".join(parts).strip()


EXTRACTORS = {".docx": ("python-docx", extract_docx), ".xlsx": ("openpyxl", extract_xlsx)}


def process(cfg, story: str) -> dict:
    att_dir = run_path(cfg, story) / "01-intake" / "attachments"
    manifest: list[dict] = []
    if att_dir.exists():
        for p in sorted(att_dir.iterdir()):
            if not p.is_file() or p.name.endswith(".extracted.md") or p.name == "extraction-manifest.json":
                continue
            ext = p.suffix.lower()
            entry = {"name": p.name}
            if ext in NATIVE:
                entry.update(status="native", note="Read tool handles this format directly; no extraction needed")
            elif ext in EXTRACTORS:
                pkg, fn = EXTRACTORS[ext]
                try:
                    text = fn(p)
                except ImportError:
                    entry.update(status="skipped", reason=f"{pkg} not installed; pip install -r requirements.txt")
                except Exception as e:  # noqa: BLE001 - one bad attachment must not break intake
                    entry.update(status="failed", reason=f"{type(e).__name__}: {e}")
                else:
                    out = p.with_name(p.name + ".extracted.md")
                    out.write_text(text or "(no extractable text found)", encoding="utf-8")
                    entry.update(status="extracted", extracted_to=show_path(out), chars=len(text))
            elif ext in LEGACY_UNSUPPORTED:
                entry.update(status="unsupported", reason=LEGACY_UNSUPPORTED[ext])
            else:
                entry.update(status="unsupported",
                              reason=f"no extractor for {ext or '(no extension)'}; record unreadable_reason")
            manifest.append(entry)
    result = {"story": story, "processed_at": now(), "attachments": manifest}
    if att_dir.exists() or manifest:
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "extraction-manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    if len(sys.argv) != 2:
        print("usage: python scripts/extract_attachments.py <story>", file=sys.stderr)
        sys.exit(1)
    cfg = load_config()
    story = story_key(sys.argv[1])
    print_json(process(cfg, story))


if __name__ == "__main__":
    main()
