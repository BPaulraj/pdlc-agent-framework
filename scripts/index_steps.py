#!/usr/bin/env python3
"""Deterministic, language-agnostic index of the automation repo at automation.base_ref.

Writes to <cache>/automation-index/:
  step-catalog.md / step-catalog.jsonl  every step-definition pattern with file:line
  features.md                           every feature file with its scenarios and tags
  step-usage.md                         how often each Gherkin step line is used
  file-tree.md                          directory overview with file counts per extension
  index-meta.json                       commit indexed, counts, detected languages

Recognised step-definition styles: Cucumber-JVM (Java/Kotlin), Cucumber-JS / Playwright-BDD
(JS/TS), behave / pytest-bdd (Python), SpecFlow / Reqnroll (C#), Cucumber-Ruby, godog (Go).
The pdlc-automation-indexer agent reads these files and adds the judgement (components, conventions).
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, show_path, cache_dir, configured_path, die, load_config, now  # noqa: E402

STEP_PATTERNS = [
    # Java / Kotlin: @Given("...") / @When(value = "...")
    ("jvm", re.compile(r'@(Given|When|Then|And|But)\s*\(\s*(?:value\s*=\s*)?"((?:[^"\\]|\\.)*)"')),
    # JS / TS: Given('...'), When("..."), Then(`...`), Given(/regex/)
    ("js", re.compile(r'\b(Given|When|Then|And|But|defineStep)\s*\(\s*(?:\'((?:[^\'\\]|\\.)*)\'|"((?:[^"\\]|\\.)*)"|`([^`]*)`|/((?:[^/\\]|\\.)+)/)')),
    # Python behave / pytest-bdd: @given('...'), @when(parsers.parse("..."))
    ("py", re.compile(r'@(given|when|then|step)\s*\(\s*(?:parsers\.\w+\(\s*)?[urbf]*[\'"]((?:[^\'"\\]|\\.)*)[\'"]')),
    # C# SpecFlow / Reqnroll: [Given(@"...")]
    ("cs", re.compile(r'\[\s*(Given|When|Then|StepDefinition)\s*\(\s*@?"((?:[^"\\]|\\.|"")*)"')),
    # Ruby: Given(/^...$/) do
    ("rb", re.compile(r'^\s*(Given|When|Then)\s*\(?\s*/(.*?)/')),
    # Go godog: ctx.Step(`^...$`, fn)
    ("go", re.compile(r'\.Step\(\s*`([^`]*)`')),
]
CODE_EXT = {".java", ".kt", ".js", ".ts", ".mjs", ".cjs", ".py", ".cs", ".rb", ".go"}


def git(repo: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        die(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def read_blobs(repo: Path, commit: str, paths: list[str]):
    """Stream file contents at a commit via `git cat-file --batch`."""
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    for path in paths:
        proc.stdin.write(f"{commit}:{path}\n".encode())
        proc.stdin.flush()
        header = proc.stdout.readline().decode().split()
        if len(header) < 3 or header[1] != "blob":
            continue
        data = proc.stdout.read(int(header[2]))
        proc.stdout.read(1)
        yield path, data.decode("utf-8", errors="replace")
    proc.stdin.close()
    proc.wait()


def main():
    cfg = load_config()
    repo = configured_path(cfg, "automation_repo")
    if not repo or not (repo / ".git").exists():
        die("paths.automation_repo is not set to a git repository")
    base_ref = cfg["automation"]["base_ref"]
    commit = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}").strip()
    ignore = set(cfg["automation"].get("ignore_dirs", []))
    globs = cfg["automation"].get("source_globs", ["**/*"])
    all_files = [p for p in git(repo, "ls-tree", "-r", "--name-only", commit).splitlines()
                 if not ignore.intersection(p.split("/")[:-1])]
    wanted = [p for p in all_files if any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(p, g.replace("**/", ""))
                                          for g in globs)]
    out = cache_dir(cfg) / "automation-index"
    out.mkdir(parents=True, exist_ok=True)

    steps, features, usage = [], [], Counter()
    for path, text in read_blobs(repo, commit, wanted):
        ext = Path(path).suffix.lower()
        if ext == ".feature":
            feat = {"path": path, "feature": "", "tags": [], "scenarios": []}
            pending_tags: list[str] = []
            for n, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if s.startswith("@"):
                    pending_tags += s.split()
                elif s.startswith("Feature:"):
                    feat["feature"], feat["tags"], pending_tags = s[8:].strip(), pending_tags, []
                elif re.match(r"(Scenario|Scenario Outline|Scenario Template|Example):", s):
                    feat["scenarios"].append({"name": s.split(":", 1)[1].strip(), "line": n, "tags": pending_tags})
                    pending_tags = []
                elif re.match(r"(Given|When|Then|And|But|\*)\s", s):
                    usage[re.sub(r'"[^"]*"|<[^>]+>|\b\d+(\.\d+)?\b', "{p}", s.split(None, 1)[1])] += 1
            features.append(feat)
        elif ext in CODE_EXT:
            for n, line in enumerate(text.splitlines(), 1):
                for style, rx in STEP_PATTERNS:
                    m = rx.search(line)
                    if m:
                        groups = [g for g in m.groups() if g is not None]
                        keyword = groups[0] if style != "go" else "Step"
                        pattern = groups[-1] if len(groups) > 1 or style == "go" else ""
                        steps.append({"keyword": keyword, "pattern": pattern, "file": path, "line": n, "style": style})
                        break

    with open(out / "step-catalog.jsonl", "w", encoding="utf-8") as fh:
        for s in steps:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    by_file = defaultdict(list)
    for s in steps:
        by_file[s["file"]].append(s)
    lines = [f"# Step catalog — {len(steps)} step definitions at {base_ref}@{commit[:10]}\n",
             "Search this before proposing any new step. Reuse or parameterise existing ones.\n"]
    for f in sorted(by_file):
        lines.append(f"\n## {f}\n")
        lines += [f"- `{s['keyword']}` `{s['pattern']}` (line {s['line']})" for s in by_file[f]]
    (out / "step-catalog.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    fl = [f"# Features — {len(features)} files, {sum(len(f['scenarios']) for f in features)} scenarios\n"]
    for f in sorted(features, key=lambda x: x["path"]):
        fl.append(f"\n## {f['path']} — {f['feature']} {' '.join(f['tags'])}\n")
        fl += [f"- L{s['line']}: {s['name']} {' '.join(s['tags'])}" for s in f["scenarios"]]
    (out / "features.md").write_text("\n".join(fl) + "\n", encoding="utf-8")

    (out / "step-usage.md").write_text("# Gherkin step usage (parameters normalised to {p})\n\n" + "\n".join(
        f"- {c}× {s}" for s, c in usage.most_common()) + "\n", encoding="utf-8")

    tree = defaultdict(Counter)
    for p in all_files:
        parts = p.split("/")
        tree["/".join(parts[:min(len(parts) - 1, 4)]) or "."][Path(p).suffix or "(none)"] += 1
    (out / "file-tree.md").write_text("# File tree (first 4 levels, files per extension)\n\n" + "\n".join(
        f"- {d}/ — " + ", ".join(f"{e}:{c}" for e, c in cnt.most_common()) for d, cnt in sorted(tree.items())) + "\n",
        encoding="utf-8")

    langs = Counter(Path(p).suffix.lower() for p in all_files if Path(p).suffix.lower() in CODE_EXT | {".feature"})
    meta = {"generated_at": now(), "repo": str(repo), "base_ref": base_ref, "commit": commit,
            "files": len(all_files), "step_definitions": len(steps), "feature_files": len(features),
            "extensions": dict(langs.most_common()),
            "build_files": [p for p in all_files if Path(p).name in (
                "pom.xml", "build.gradle", "build.gradle.kts", "package.json", "pyproject.toml", "requirements.txt",
                "setup.cfg", "pytest.ini", "behave.ini", "cucumber.js", "cucumber.json", "playwright.config.ts",
                "go.mod", "Gemfile") or p.endswith((".csproj", ".sln"))][:50]}
    (out / "index-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"indexed {len(all_files)} files at {base_ref}@{commit[:10]}: {len(steps)} step definitions, "
          f"{len(features)} feature files -> {show_path(out)}")


if __name__ == "__main__":
    main()
