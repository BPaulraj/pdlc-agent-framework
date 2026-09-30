"""Shared helpers for the PDLC scripts: config, paths, run state, hashing."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("PDLC_CONFIG") or ROOT / "config" / "framework.yaml")
PIPELINE_PATH = ROOT / "pipeline" / "pipeline.yaml"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def load_yaml(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config() -> dict:
    return load_yaml(CONFIG_PATH)


def load_pipeline() -> list[dict]:
    return load_yaml(PIPELINE_PATH)["stages"]


def is_set(value) -> bool:
    return value not in (None, "", "SET_ME")


def configured_path(cfg: dict, key: str) -> Path | None:
    value = cfg.get("paths", {}).get(key)
    if not is_set(value):
        return None
    p = Path(value)
    return p if p.is_absolute() else (ROOT / p)


def cache_dir(cfg: dict) -> Path:
    return ROOT / cfg["framework"].get("cache_dir", ".pdlc/cache")


def runs_dir(cfg: dict) -> Path:
    return ROOT / cfg["framework"].get("runs_dir", "runs")


def story_key(story: str | int) -> str:
    """Accept 1234, '1234', 'US-1234', 'us1234' -> 'US-1234'."""
    m = re.fullmatch(r"(?i)(?:us-?)?(\d+)", str(story).strip())
    if not m:
        die(f"not a story id: {story!r} (expected e.g. 1234 or US-1234)")
    return f"US-{m.group(1)}"


def story_number(story: str | int) -> int:
    return int(story_key(story).split("-")[1])


def run_path(cfg: dict, story: str) -> Path:
    return runs_dir(cfg) / story_key(story)


def resolve_output(cfg: dict, story: str, rel: str) -> Path:
    if rel.startswith("{cache}/"):
        return cache_dir(cfg) / rel[len("{cache}/"):]
    return run_path(cfg, story) / rel


# ---------------------------------------------------------------- state

def state_path(cfg: dict, story: str) -> Path:
    return run_path(cfg, story) / "state.json"


def load_state(cfg: dict, story: str) -> dict:
    p = state_path(cfg, story)
    if not p.exists():
        die(f"no run for {story_key(story)}; start one with: python scripts/pdlc.py init {story_key(story)}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(cfg: dict, story: str, state: dict) -> None:
    p = state_path(cfg, story)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def log_event(state: dict, event: str, **data) -> None:
    state.setdefault("history", []).append({"at": now(), "event": event, **data})


# ---------------------------------------------------------------- hashing

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def expand_files(base: Path, rels: list[str]) -> list[Path]:
    """Expand a list of files/dirs (dirs end with '/') into a sorted file list."""
    files: list[Path] = []
    for rel in rels:
        p = base / rel
        if rel.endswith("/") or p.is_dir():
            if p.is_dir():
                files.extend(x for x in p.rglob("*") if x.is_file())
        elif p.is_file():
            files.append(p)
    return sorted(set(files))


def hash_artifacts(base: Path, rels: list[str]) -> tuple[str, dict[str, str]]:
    """Combined hash plus per-file hashes (paths relative to base, forward slashes)."""
    per_file = {f.relative_to(base).as_posix(): sha256_file(f) for f in expand_files(base, rels)}
    combined = hashlib.sha256(json.dumps(per_file, sort_keys=True).encode()).hexdigest()
    return combined, per_file


def show_path(p: Path) -> str:
    """Project-relative posix path when inside the project, else absolute posix path."""
    p = Path(p)
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def print_json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))
