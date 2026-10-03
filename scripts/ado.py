#!/usr/bin/env python3
"""Azure DevOps adapter. The only component allowed to write to ADO, and only these shapes:
a clarification Task under the story, Test Case work items linked to the story, a Tested By link
from approved reused master-pack cases to the story (their content is never changed), suites in
the configured plan, a story tag, and a report comment on the story.

With azure_devops.enabled = false every command works offline against local files, so the whole
pipeline can be exercised before ADO access is set up.

Usage:
  python scripts/ado.py fetch-story <story>
  python scripts/ado.py raise-clarifications <story>
  python scripts/ado.py fetch-clarifications <story>
  python scripts/ado.py export-master-pack [--if-stale] [--full]
  python scripts/ado.py export-csv <story>
  python scripts/ado.py publish-test-cases <story> [--dry-run]
  python scripts/ado.py post-report <story> [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    show_path,
    ROOT, cache_dir, die, is_set, load_config, load_pipeline, load_state, load_yaml, log_event, now,
    print_json, run_path, save_state, story_key, story_number,
)


# ================================================================ HTML -> text

class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    p = _Text()
    p.feed(value)
    text = html.unescape("".join(p.parts))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l.rstrip() for l in text.splitlines())).strip()


# ================================================================ client

class Ado:
    def __init__(self, cfg: dict, tests: bool = False):
        """tests=True scopes the client to azure_devops.test_project (test plans and test cases)."""
        a = cfg["azure_devops"]
        for key in ("organization", "project"):
            if not is_set(a.get(key)):
                die(f"azure_devops.{key} is not set in config/framework.yaml")
        pat = os.environ.get(a.get("pat_env_var", "ADO_PAT"))
        if not pat:
            die(f"environment variable {a.get('pat_env_var', 'ADO_PAT')} (ADO personal access token) is not set")
        self.cfg = a
        self.org_url = f"https://dev.azure.com/{urllib.parse.quote(a['organization'])}"
        self.project_name = (a.get("test_project") if tests and is_set(a.get("test_project")) else None) or a["project"]
        self.project = urllib.parse.quote(self.project_name)
        self.api = a.get("api_version", "7.1")
        self.auth = "Basic " + base64.b64encode(f":{pat}".encode()).decode()

    def url(self, path: str, team: bool = False, **query) -> str:
        scope = f"{self.project}/{urllib.parse.quote(self.cfg['team'])}" if team else self.project
        query.setdefault("api-version", self.api)
        return f"{self.org_url}/{scope}/_apis/{path}?{urllib.parse.urlencode(query)}"

    def request(self, method: str, url: str, body=None, content_type="application/json", raw=False,
                headers_out: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        for attempt in range(5):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": self.auth, "Content-Type": content_type, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    if headers_out is not None:
                        headers_out.update(dict(resp.headers))
                    payload = resp.read()
                    return payload if raw else (json.loads(payload) if payload else {})
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:800]
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(int(e.headers.get("Retry-After") or 2 ** attempt))
                    continue
                raise AdoError(e.code, f"{method} {url.split('?')[0]} -> HTTP {e.code}: {detail}") from None
            except urllib.error.URLError as e:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                die(f"cannot reach Azure DevOps: {e}")

    def paged(self, url: str) -> list:
        items, token = [], None
        while True:
            headers: dict = {}
            page = self.request("GET", url + (f"&continuationToken={urllib.parse.quote(token)}" if token else ""),
                                headers_out=headers)
            items += page.get("value", [])
            token = headers.get("x-ms-continuationtoken") or headers.get("X-MS-ContinuationToken")
            if not token:
                return items

    # ---- work items
    def work_item_url(self, wid: int) -> str:
        # Organisation-level URL: links work across projects (stories and test cases live in different ones).
        return f"{self.org_url}/_apis/wit/workItems/{wid}"

    def node_exists(self, kind: str, path: str) -> bool:
        """kind: 'areas' | 'iterations'. path is the full path as ADO shows it, starting with the project name."""
        parts = [p for p in str(path).replace("/", "\\").split("\\") if p]
        if not parts or parts[0].lower() != self.project_name.lower():
            return False
        rel = "/".join(urllib.parse.quote(p) for p in parts[1:])
        try:
            self.request("GET", self.url(f"wit/classificationnodes/{kind}" + (f"/{rel}" if rel else "")))
            return True
        except AdoError as e:
            if e.code == 404:
                return False
            raise

    def get_item(self, wid: int, expand="all") -> dict:
        return self.request("GET", self.url(f"wit/workitems/{wid}", **{"$expand": expand}))

    def get_items(self, ids: list[int], fields: list[str] | None = None, omit_missing=False) -> list[dict]:
        out = []
        for i in range(0, len(ids), 200):
            body = {"ids": ids[i:i + 200]}
            if fields:
                body["fields"] = fields
            if omit_missing:
                body["errorPolicy"] = "Omit"
            out += [it for it in self.request("POST", self.url("wit/workitemsbatch"), body).get("value", []) if it]
        return out

    def create_item(self, wtype: str, patch: list) -> dict:
        return self.request("POST", self.url(f"wit/workitems/${urllib.parse.quote(wtype)}"), patch,
                            "application/json-patch+json")

    def update_item(self, wid: int, patch: list) -> dict:
        return self.request("PATCH", self.url(f"wit/workitems/{wid}"), patch, "application/json-patch+json")

    def comments(self, wid: int) -> list[dict]:
        return self.request("GET", self.url(f"wit/workItems/{wid}/comments", **{"api-version": "7.1-preview.4"})
                            ).get("comments", [])

    def add_comment(self, wid: int, text_html: str) -> dict:
        return self.request("POST", self.url(f"wit/workItems/{wid}/comments", **{"api-version": "7.1-preview.4"}),
                            {"text": text_html})

    def wiql(self, query: str) -> list[int]:
        res = self.request("POST", self.url("wit/wiql", team=True), {"query": query})
        return [w["id"] for w in res.get("workItems", [])]

    # ---- test plans
    def suites(self, plan: int) -> list[dict]:
        return self.paged(self.url(f"testplan/Plans/{plan}/suites"))

    def create_suite(self, plan: int, body: dict) -> dict:
        return self.request("POST", self.url(f"testplan/Plans/{plan}/suites"), body)

    def update_suite(self, plan: int, suite: int, body: dict) -> dict:
        return self.request("PATCH", self.url(f"testplan/Plans/{plan}/suites/{suite}"), body)

    def suite_cases(self, plan: int, suite: int) -> list[dict]:
        return self.paged(self.url(f"testplan/Plans/{plan}/Suites/{suite}/TestCase"))

    def add_to_suite(self, plan: int, suite: int, ids: list[int]) -> None:
        existing = {c["workItem"]["id"] for c in self.suite_cases(plan, suite)}
        todo = [i for i in ids if i not in existing]
        if todo:
            self.request("POST", self.url(f"testplan/Plans/{plan}/Suites/{suite}/TestCase"),
                         [{"workItem": {"id": i}} for i in todo])


class AdoError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def enabled(cfg) -> bool:
    return bool(cfg["azure_devops"].get("enabled"))


def task_is_closed(cfg: dict, task_id: int) -> bool:
    item = Ado(cfg).get_item(task_id, expand="none")
    return item["fields"].get("System.State") in cfg["azure_devops"]["clarification"]["closed_states"]


# ================================================================ fetch-story

def fingerprint(story: dict) -> str:
    material = [story.get("title", ""), story.get("description_text", ""), story.get("acceptance_criteria_text", ""),
                sorted((a.get("name"), a.get("size")) for a in story.get("attachments", []))]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def normalise_offline(story: dict) -> dict:
    story.setdefault("description_text", html_to_text(story.get("description_html")) or story.get("description", ""))
    story.setdefault("acceptance_criteria_text",
                     html_to_text(story.get("acceptance_criteria_html")) or story.get("acceptance_criteria", ""))
    story.setdefault("attachments", [])
    story.setdefault("links", [])
    story.setdefault("comments", [])
    return story


def fetch_from_ado(cfg, story: str, dest: Path) -> dict:
    ado = Ado(cfg)
    wid = story_number(story)
    item = ado.get_item(wid)
    f = item["fields"]
    max_bytes = cfg["azure_devops"].get("max_attachment_mb", 20) * 1024 * 1024
    attachments, links = [], []
    att_dir = dest / "attachments"
    for rel in item.get("relations") or []:
        kind = rel.get("rel", "")
        if kind == "AttachedFile":
            name = rel["attributes"].get("name", "attachment")
            size = rel["attributes"].get("resourceSize", 0)
            entry = {"name": name, "size": size}
            if size and size > max_bytes:
                entry["skipped"] = f"larger than {max_bytes // 1048576} MB"
            else:
                att_dir.mkdir(parents=True, exist_ok=True)
                safe = re.sub(r"[^\w.\-]+", "_", name)
                data = ado.request("GET", rel["url"] + ("&" if "?" in rel["url"] else "?") + "download=true", raw=True)
                (att_dir / safe).write_bytes(data)
                entry["local_path"] = f"attachments/{safe}"
            attachments.append(entry)
        elif "/workItems/" in rel.get("url", "") or "/workitems/" in rel.get("url", ""):
            links.append({"rel": kind, "name": rel.get("attributes", {}).get("name", kind),
                          "id": int(rel["url"].rstrip("/").split("/")[-1])})
    if links:
        details = {i["id"]: i["fields"] for i in ado.get_items(
            [l["id"] for l in links],
            ["System.Id", "System.Title", "System.WorkItemType", "System.State", "System.Description"])}
        for l in links:
            d = details.get(l["id"], {})
            l.update(title=d.get("System.Title"), type=d.get("System.WorkItemType"), state=d.get("System.State"),
                     description_text=html_to_text(d.get("System.Description"))[:2000])
    comments = [{"by": c.get("createdBy", {}).get("displayName"), "at": c.get("createdDate"),
                 "text": html_to_text(c.get("text"))} for c in ado.comments(wid)]
    return {
        "id": wid, "rev": item.get("rev"), "url": item.get("_links", {}).get("html", {}).get("href"),
        "type": f.get("System.WorkItemType"), "title": f.get("System.Title"), "state": f.get("System.State"),
        "area_path": f.get("System.AreaPath"), "iteration_path": f.get("System.IterationPath"),
        "assigned_to": (f.get("System.AssignedTo") or {}).get("displayName"),
        "tags": [t.strip() for t in (f.get("System.Tags") or "").split(";") if t.strip()],
        "priority": f.get("Microsoft.VSTS.Common.Priority"),
        "story_points": f.get("Microsoft.VSTS.Scheduling.StoryPoints"),
        "description_html": f.get("System.Description") or "",
        "description_text": html_to_text(f.get("System.Description")),
        "acceptance_criteria_html": f.get("Microsoft.VSTS.Common.AcceptanceCriteria") or "",
        "acceptance_criteria_text": html_to_text(f.get("Microsoft.VSTS.Common.AcceptanceCriteria")),
        "attachments": attachments, "links": links, "comments": comments, "fetched_at": now(),
    }


def cmd_fetch_story(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    dest = run_path(cfg, story) / "01-intake"
    dest.mkdir(parents=True, exist_ok=True)
    story_file = dest / "story.json"
    if enabled(cfg) and not state.get("offline"):
        data = fetch_from_ado(cfg, story, dest)
        tag = cfg["azure_devops"].get("story_tag_on_start")
        if tag and not state.get("tagged") and tag not in data["tags"]:
            Ado(cfg).update_item(data["id"], [{"op": "add", "path": "/fields/System.Tags",
                                               "value": "; ".join(data["tags"] + [tag])}])
            state["tagged"] = True
    else:
        if not story_file.exists():
            die(f"offline mode and no {show_path(story_file)}; run pdlc.py init with --story-file")
        data = normalise_offline(json.loads(story_file.read_text(encoding="utf-8")))
    story_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    fp = fingerprint(data)
    previous = state.get("story_fingerprint")
    state["story_fingerprint"] = fp
    intake_done = state["stages"].get("intake", {}).get("status") == "done"
    if previous and previous != fp and intake_done:
        from pdlc import reset_range
        reset_range(cfg, story, state, load_pipeline(), "intake", None, "story content changed in ADO")
        print(f"{story}: story content changed since last run -> restarting from intake")
    save_state(cfg, story, state)
    print(f"{story}: '{data.get('title')}' rev {data.get('rev', '-')}, "
          f"{len(data['attachments'])} attachments, {len(data['links'])} links, {len(data['comments'])} comments")


# ================================================================ clarifications

def _questions_for_round(cfg, story, state) -> tuple[int, list[dict]]:
    base = run_path(cfg, story)
    round_no = len(state["clarification"]["rounds"]) + 1
    followup = state["clarification"].get("pending_followup")
    if followup:
        qs = (load_yaml(base / followup) or {}).get("questions") or []
    else:
        qs = [q for q in (load_yaml(base / "02-analysis" / "story-analysis.yaml") or {}).get("questions") or []
              if q.get("status", "open") == "open"]
    return round_no, qs


def _questions_html(story: str, round_no: int, qs: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(str(q.get('id', i + 1)))}</td><td>{html.escape(q.get('question', ''))}</td>"
        f"<td>{html.escape(q.get('why_it_matters', ''))}</td><td>{'Yes' if q.get('blocking') else 'No'}</td>"
        f"<td>{html.escape(q.get('proposed_assumption') or '')}</td><td></td></tr>"
        for i, q in enumerate(qs))
    return (f"<p>Questions raised by the PDLC story-analysis agent for {story} (round {round_no}). "
            f"Please answer in the <b>Answer</b> column (or reply in Discussion quoting the question ID), "
            f"update the story's acceptance criteria where the answer changes scope, then close this task.</p>"
            f"<p>Blocking questions stop test design until answered. Non-blocking questions proceed on the "
            f"proposed assumption unless you correct it.</p>"
            f"<table border='1'><tr><th>ID</th><th>Question</th><th>Why it matters</th><th>Blocking</th>"
            f"<th>Proposed assumption</th><th>Answer</th></tr>{rows}</table>")


def cmd_raise_clarifications(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    base = run_path(cfg, story)
    round_no, qs = _questions_for_round(cfg, story, state)
    if not qs:
        die("no open questions to raise")
    ccfg = cfg["azure_devops"]["clarification"]
    title = ccfg["task_title"] + (f" (round {round_no})" if round_no > 1 else "")
    record = {"round": round_no, "title": title, "questions": qs, "raised_at": now()}
    if enabled(cfg) and not state.get("offline"):
        ado = Ado(cfg)
        wid = story_number(story)
        story_item = ado.get_item(wid, expand="relations")
        existing = None
        child_ids = [int(r["url"].split("/")[-1]) for r in story_item.get("relations") or []
                     if r.get("rel") == "System.LinkTypes.Hierarchy-Forward"]
        for c in ado.get_items(child_ids, ["System.Id", "System.Title", "System.WorkItemType"]) if child_ids else []:
            if c["fields"].get("System.WorkItemType") == "Task" and c["fields"].get("System.Title") == title:
                existing = c["id"]  # idempotent: a retry must not create a second task
        body = _questions_html(story, round_no, qs)
        fields = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": body},
            {"op": "add", "path": "/fields/System.Tags", "value": "; ".join(ccfg.get("tags", []))},
            {"op": "add", "path": "/fields/System.IterationPath", "value": story_item["fields"]["System.IterationPath"]},
            {"op": "add", "path": "/fields/System.AreaPath", "value": story_item["fields"]["System.AreaPath"]},
        ]
        if ccfg.get("assign_to"):
            fields.append({"op": "add", "path": "/fields/System.AssignedTo", "value": ccfg["assign_to"]})
        if existing:
            task = ado.update_item(existing, fields)
        else:
            fields.append({"op": "add", "path": "/relations/-", "value": {
                "rel": "System.LinkTypes.Hierarchy-Reverse", "url": ado.work_item_url(wid)}})
            task = ado.create_item("Task", fields)
        record.update(task_id=task["id"], url=task.get("_links", {}).get("html", {}).get("href"))
        print(f"{'updated' if existing else 'created'} Task {task['id']}: {title} ({len(qs)} questions)")
    else:
        clar = base / "03-clarification"
        clar.mkdir(parents=True, exist_ok=True)
        qfile = clar / f"questions-round-{round_no}.md"
        lines = [f"# {title} — {story}\n", "Forward these to the PO/BA. Save their answers as "
                 f"`{show_path(clar / f'answers-round-{round_no}.md')}` "
                 "(quote each question ID), then run /pdlc-run again.\n"]
        for q in qs:
            lines.append(f"## {q.get('id')} {'(blocking)' if q.get('blocking') else ''}\n\n{q.get('question')}\n\n"
                         f"_Why it matters:_ {q.get('why_it_matters', '')}\n\n"
                         f"_Proposed assumption:_ {q.get('proposed_assumption') or '-'}\n")
        qfile.write_text("\n".join(lines), encoding="utf-8")
        record.update(offline=True, questions_file=qfile.relative_to(base).as_posix(),
                      answers_file=f"03-clarification/answers-round-{round_no}.md")
        print(f"offline: questions written to {show_path(qfile)}")
    state["clarification"]["rounds"].append({k: v for k, v in record.items() if k != "questions"})
    state["clarification"]["pending_followup"] = None
    log_event(state, "clarification_raised", round=round_no, task=record.get("task_id"))
    save_state(cfg, story, state)
    (base / "02-analysis" / "clarification-task.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


def cmd_fetch_clarifications(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    base = run_path(cfg, story)
    rounds = state["clarification"]["rounds"]
    if not rounds:
        die("no clarification round has been raised")
    latest = rounds[-1]
    if latest.get("offline"):
        path = base / latest["answers_file"]
        print(f"offline answers: {show_path(path)} ({'present' if path.exists() else 'MISSING'})")
        return
    ado = Ado(cfg)
    task = ado.get_item(latest["task_id"], expand="none")
    comments = ado.comments(latest["task_id"])
    out = [f"# Answers — {latest['title']} (Task {latest['task_id']})\n",
           f"State: {task['fields'].get('System.State')}\n",
           "## Task description (answer table)\n", html_to_text(task["fields"].get("System.Description")), "\n## Discussion\n"]
    for c in comments:
        out.append(f"**{c.get('createdBy', {}).get('displayName')}** ({c.get('createdDate')}):\n"
                   f"{html_to_text(c.get('text'))}\n")
    path = base / "03-clarification" / f"answers-round-{latest['round']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"answers written to {show_path(path)} ({len(comments)} comments)")


# ================================================================ master pack

def shared_refs(xml_text: str | None) -> list[int]:
    """Shared Steps work item ids a test case's steps reference (<compref ref="...">)."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return sorted({int(c.get("ref")) for c in root.iter("compref") if str(c.get("ref", "")).isdigit()})


def steps_from_xml(xml_text: str | None, shared: dict[int, list[dict]] | None = None) -> list[dict]:
    """Steps in document order, with each shared step inlined where it is referenced and marked with
    its id. ADO nests the steps that follow a shared step inside its <compref>, hence the recursion."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [{"action": html_to_text(xml_text), "expected": ""}]
    steps: list[dict] = []

    def walk(el):
        for child in el:
            if child.tag == "step":
                parts = [html_to_text(p.text) for p in child.findall("parameterizedString")]
                steps.append({"action": parts[0] if parts else "", "expected": parts[1] if len(parts) > 1 else ""})
            elif child.tag == "compref":
                ref = str(child.get("ref", ""))
                if ref.isdigit():
                    body = (shared or {}).get(int(ref))
                    steps.extend([{**s, "shared_step": int(ref)} for s in body] if body is not None else
                                 [{"action": f"[shared step {ref} not available]", "expected": "", "shared_step": int(ref)}])
                walk(child)

    walk(root)
    return steps


def cmd_export_master_pack(cfg, args):
    mp = cfg["azure_devops"]["master_pack"]
    out_dir = cache_dir(cfg) / "master-pack"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_p = out_dir / "meta.json"
    if args.if_stale and meta_p.exists():
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        age_h = (time.time() - meta.get("exported_epoch", 0)) / 3600
        if age_h < mp.get("max_age_hours", 24):
            print(f"master pack is fresh ({meta.get('count')} cases, {age_h:.1f}h old)")
            return
    target = out_dir / "master-pack.jsonl"
    # Everything is staged and swapped in only on success, with meta.json written last, so a
    # crashed export can never leave a partial pack that --if-stale would report as fresh.
    staged = out_dir / "master-pack.jsonl.tmp"
    prev_meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    sync_meta: dict = {"mode": "local_copy"}
    staged_shared = None
    if mp.get("source") == "local_jsonl" or not enabled(cfg):
        src = mp.get("local_path")
        if is_set(src) and Path(src).exists():
            shutil.copyfile(src, staged)
        elif target.exists():
            shutil.copyfile(target, staged)
        else:
            staged.write_text("", encoding="utf-8")
            print("WARNING: no master pack available (set azure_devops.master_pack); regression selection will report a gap")
    else:
        ado = Ado(cfg, tests=True)
        plan = int(mp["plan_id"])
        suites = ado.suites(plan)
        by_id = {s["id"]: s for s in suites}

        def path_of(s):
            names = []
            while s:
                names.append(s["name"])
                s = by_id.get((s.get("parentSuite") or {}).get("id"))
            return " / ".join(reversed(names))

        membership: dict[int, list[str]] = {}
        for s in suites:
            for c in ado.suite_cases(plan, s["id"]):
                membership.setdefault(c["workItem"]["id"], []).append(path_of(s))
        fields = ["System.Id", "System.Rev", "System.Title", "System.AreaPath", "System.IterationPath", "System.State",
                  "System.Tags", "Microsoft.VSTS.Common.Priority", "Microsoft.VSTS.TCM.Steps",
                  "Microsoft.VSTS.TCM.AutomationStatus"]
        fields_sig = hashlib.sha256(("|".join(fields) + "|shared-steps-v1").encode()).hexdigest()[:12]
        ids = sorted(membership)
        shared_p = out_dir / "shared-steps.json"

        # Any work-item edit bumps System.Rev, so only new or re-revised cases need their full fields
        # re-fetched. Suite membership isn't part of the work item, hence the full re-listing above.
        cached: dict[int, dict] = {}
        shared_cache: dict[int, dict] = {}
        full_days = mp.get("full_refresh_days", 7)
        last_full = prev_meta.get("last_full_export_epoch", 0)
        if args.full:
            reason = "--full requested"
        elif not target.exists() or prev_meta.get("mode") not in ("full", "incremental"):
            reason = "no previous ADO export"
        elif prev_meta.get("plan_id") != plan:
            reason = "master plan changed"
        elif prev_meta.get("fields_sig") != fields_sig:
            reason = "exported field set changed"
        elif (time.time() - last_full) / 86400 >= full_days:
            reason = f"periodic full refresh (every {full_days} days)"
        else:
            reason = None
            try:
                for line in target.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rec = json.loads(line)
                        cached[rec["id"]] = rec
            except (json.JSONDecodeError, KeyError):
                reason = "local copy unreadable"
            if reason is None and any("rev" not in r for r in cached.values()):
                reason = "local copy has no revisions"
            if reason is None and shared_p.exists():
                try:
                    shared_cache = {int(k): v for k, v in json.loads(shared_p.read_text(encoding="utf-8")).items()}
                except (json.JSONDecodeError, ValueError):
                    reason = "shared-steps cache unreadable"
            if reason:
                cached, shared_cache = {}, {}

        if reason:
            fetch_ids = ids
        else:
            revs = {it["id"]: it["fields"].get("System.Rev")
                    for it in ado.get_items(ids, ["System.Id", "System.Rev"], omit_missing=True)}
            ids = [i for i in ids if i in revs]  # drop cases deleted in ADO but still listed in a suite
            fetch_ids = [i for i in ids if i not in cached or cached[i]["rev"] != revs[i]]
        fetched = {it["id"]: it["fields"] for it in ado.get_items(fetch_ids, fields, omit_missing=True)} if fetch_ids else {}

        # Shared steps: editing one bumps only the shared step's own Rev, never the cases using it. So
        # track their revisions separately and re-fetch every unchanged case that uses a changed one.
        def refs_of(i):
            return shared_refs(fetched[i].get("Microsoft.VSTS.TCM.Steps")) if i in fetched else cached[i].get("shared_step_refs", [])
        all_refs = sorted({r for i in ids if i in fetched or i in cached for r in refs_of(i)})
        shared_revs = {it["id"]: it["fields"].get("System.Rev")
                       for it in ado.get_items(all_refs, ["System.Id", "System.Rev"], omit_missing=True)} if all_refs else {}
        stale_shared = [r for r in shared_revs if r not in shared_cache or shared_cache[r].get("rev") != shared_revs[r]]
        deleted_shared = set(shared_cache) - set(shared_revs)  # readable at the last sync, gone now
        if stale_shared:
            for it in ado.get_items(stale_shared, ["System.Id", "System.Rev", "System.Title", "Microsoft.VSTS.TCM.Steps"],
                                    omit_missing=True):
                f = it["fields"]
                shared_cache[it["id"]] = {"rev": f.get("System.Rev"), "title": f.get("System.Title"),
                                          "steps": steps_from_xml(f.get("Microsoft.VSTS.TCM.Steps"))}
        shared_cache = {r: shared_cache[r] for r in shared_revs if r in shared_cache}
        affected = set(stale_shared) | deleted_shared
        reexpand = [i for i in ids if i not in fetched and i in cached and affected & set(cached[i].get("shared_step_refs", []))]
        if reexpand:
            fetched.update({it["id"]: it["fields"] for it in ado.get_items(reexpand, fields, omit_missing=True)})
        bodies = {r: v["steps"] for r, v in shared_cache.items()}
        staged_shared = out_dir / "shared-steps.json.tmp"
        staged_shared.write_text(json.dumps({str(k): v for k, v in sorted(shared_cache.items())}, ensure_ascii=False),
                                 encoding="utf-8")

        stats = {"added": 0, "changed": 0, "reexpanded": 0, "unchanged": 0, "removed": len(set(cached) - set(ids)),
                 "shared_steps": len(shared_cache), "shared_steps_changed": len(stale_shared)}
        reexpand_set = set(reexpand)
        with open(staged, "w", encoding="utf-8") as fh:
            for i in ids:
                if i in fetched:
                    f = fetched[i]
                    xml = f.get("Microsoft.VSTS.TCM.Steps")
                    rec = {
                        "id": i, "rev": f.get("System.Rev"), "title": f.get("System.Title"),
                        "suites": membership[i],
                        "area_path": f.get("System.AreaPath"), "iteration_path": f.get("System.IterationPath"),
                        "state": f.get("System.State"),
                        "priority": f.get("Microsoft.VSTS.Common.Priority"),
                        "tags": [t.strip() for t in (f.get("System.Tags") or "").split(";") if t.strip()],
                        "automation_status": f.get("Microsoft.VSTS.TCM.AutomationStatus"),
                        "shared_step_refs": shared_refs(xml),
                        "steps": steps_from_xml(xml, bodies),
                    }
                    stats["reexpanded" if i in reexpand_set else "changed" if i in cached else "added"] += 1
                elif i in cached:
                    rec = {**cached[i], "suites": membership[i]}
                    stats["unchanged"] += 1
                else:
                    continue  # deleted or not readable in ADO since the suite listing
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if reason:
            print(f"master pack: full export ({reason})")
            last_full = time.time()
        else:
            print("master pack: incremental sync: " +", ".join(f"{k} {v}" for k, v in stats.items()))
        sync_meta = {"mode": "full" if reason else "incremental", "full_reason": reason, "plan_id": plan,
                     "fields_sig": fields_sig, "last_full_export_epoch": last_full, **stats}

    records = []
    for n, line in enumerate(staged.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                die(f"master pack line {n} is not valid JSON ({e}); kept the previous pack, staged copy at {show_path(staged)}")
    count = len(records)

    def cell(v) -> str:
        return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()

    # Steps-free search index: agents grep this to shortlist ids, then read full cases from the JSONL.
    tsv_rows = ["id\tpriority\tautomation_status\tstate\tarea_path\titeration_path\tsuites\ttitle\ttags\tshared_step_refs"]
    suite_counts: dict[str, int] = {}
    area_counts: dict[str, int] = {}
    for rec in records:
        suites = rec.get("suites") or [rec.get("area_path") or "(no suite)"]
        for key in suites:
            suite_counts[key] = suite_counts.get(key, 0) + 1
        if rec.get("area_path"):
            area_counts[rec["area_path"]] = area_counts.get(rec["area_path"], 0) + 1
        tsv_rows.append("\t".join(cell(v) for v in (
            rec.get("id"), rec.get("priority"), rec.get("automation_status"), rec.get("state"),
            rec.get("area_path"), rec.get("iteration_path"), " | ".join(suites), rec.get("title"), ";".join(rec.get("tags") or []),
            ";".join(str(r) for r in rec.get("shared_step_refs") or []))))
    staged_tsv = out_dir / "master-pack-index.tsv.tmp"
    staged_tsv.write_text("\n".join(tsv_rows) + "\n", encoding="utf-8")
    staged_md = out_dir / "index.md.tmp"
    staged_md.write_text(
        "# Master pack index\n\n## Test cases per suite\n\n" +
        "\n".join(f"- {k}: {v}" for k, v in sorted(suite_counts.items())) +
        "\n\n## Known area paths (test case modules)\n\n" +
        "\n".join(f"- {k}: {v}" for k, v in sorted(area_counts.items())) + "\n", encoding="utf-8")
    staged_meta = out_dir / "meta.json.tmp"
    staged_meta.write_text(json.dumps({"exported_at": now(), "exported_epoch": time.time(), "count": count,
                                       "source": mp.get("source"), **sync_meta}, indent=2), encoding="utf-8")

    os.replace(staged, target)
    if staged_shared:
        os.replace(staged_shared, out_dir / "shared-steps.json")
    os.replace(staged_tsv, out_dir / "master-pack-index.tsv")
    os.replace(staged_md, out_dir / "index.md")
    os.replace(staged_meta, meta_p)
    print(f"master pack: {count} test cases -> {show_path(target)} (search index: master-pack-index.tsv)")


# ================================================================ publish test cases

def steps_xml(steps: list[dict]) -> str:
    def ps(text: str) -> str:
        return f'<parameterizedString isformatted="true">{xml_escape("<P>" + html.escape(text or "") + "</P>")}</parameterizedString>'
    body = "".join(
        f'<step id="{i}" type="{"ValidateStep" if s.get("expected_result") else "ActionStep"}">'
        f'{ps(s.get("action", ""))}{ps(s.get("expected_result", ""))}<description/></step>'
        for i, s in enumerate(steps, start=1))
    return f'<steps id="0" last="{len(steps)}">{body}</steps>'


def tc_description(tc: dict) -> str:
    parts = [f"<p><b>Objective:</b> {html.escape(tc.get('objective', ''))}</p>"]
    if tc.get("preconditions"):
        parts.append("<p><b>Preconditions:</b></p><ul>" +
                     "".join(f"<li>{html.escape(str(p))}</li>" for p in tc["preconditions"]) + "</ul>")
    if tc.get("test_data"):
        parts.append(f"<p><b>Test data:</b> {html.escape(json.dumps(tc['test_data'], ensure_ascii=False))}</p>")
    if tc.get("interface_details"):
        parts.append(f"<p><b>{html.escape(str(tc.get('interface', 'interface')))} details:</b> "
                     f"{html.escape(json.dumps(tc['interface_details'], ensure_ascii=False))}</p>")
    refs = ", ".join(tc.get("acceptance_criteria_refs") or [])
    parts.append(f"<p><b>Covers:</b> {html.escape(refs)} · <b>Technique:</b> {html.escape(str(tc.get('technique', '')))}"
                 f" · <b>PDLC key:</b> {html.escape(tc.get('key', ''))}</p>")
    auto = tc.get("automation") or {}
    if auto.get("scenario_ref"):
        parts.append(f"<p><b>Automated by:</b> {html.escape(auto['scenario_ref'])}</p>")
    return "".join(parts)


TESTED_BY = "Microsoft.VSTS.Common.TestedBy-Reverse"


def load_design(cfg, story) -> tuple[list[dict], dict[int, dict]]:
    """New test cases, plus master-pack cases reused as story coverage (verdict full/partial)."""
    doc = load_yaml(run_path(cfg, story) / "06-test-design" / "test-cases.yaml") or {}
    reused: dict[int, dict] = {}
    for e in doc.get("existing_coverage") or []:
        if e.get("verdict") in ("full", "partial") and str(e.get("master_pack_id", "")).isdigit():
            r = reused.setdefault(int(e["master_pack_id"]), {"verdicts": [], "acceptance_criteria": []})
            for key, val in (("verdicts", e["verdict"]), ("acceptance_criteria", e.get("acceptance_criterion"))):
                if val and val not in r[key]:
                    r[key].append(val)
    tcs = doc.get("test_cases") or []
    if not tcs and not reused:
        die("06-test-design/test-cases.yaml has no test_cases and no reused existing_coverage")
    return tcs, reused


def load_test_cases(cfg, story) -> list[dict]:
    return load_design(cfg, story)[0]


def case_paths(cfg, tc: dict) -> tuple[str | None, str | None]:
    """(area path, iteration path) for a new test case: area from test design (the module), iteration from
    its test_type via config (the type of testing). Never taken from the story, which lives in another project."""
    iteration = (cfg["azure_devops"]["test_plan"].get("iteration_paths") or {}).get(tc.get("test_type"))
    return (tc["area_path"] if is_set(tc.get("area_path")) else None,
            iteration if is_set(iteration) else None)


def path_problems(cfg, tcs: list[dict]) -> list[str]:
    problems = []
    for tc in tcs:
        area, iteration = case_paths(cfg, tc)
        if not area:
            problems.append(f"{tc.get('key')}: no area_path (resolve its area_path_questions entry at G1)")
        if not iteration:
            problems.append(f"{tc.get('key')}: test_type '{tc.get('test_type')}' has no "
                            f"azure_devops.test_plan.iteration_paths entry in config/framework.yaml")
    return problems


def cmd_export_csv(cfg, args):
    """ADO 'Import from CSV' format — review in Excel or import manually."""
    story = story_key(args.story)
    out = run_path(cfg, story) / "06-test-design" / "test-cases.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["ID", "Work Item Type", "Title", "Test Step", "Step Action", "Step Expected", "Priority",
                    "Area Path", "Iteration Path", "Tags"])
        for tc in load_test_cases(cfg, story):
            tags = (tc.get("tags") or []) + ([tc["interface"]] if tc.get("interface") else [])
            area, iteration = case_paths(cfg, tc)
            w.writerow(["", "Test Case", tc["title"], "", "", "", tc.get("priority", 2), area or "", iteration or "",
                        "; ".join(tags)])
            for i, s in enumerate(tc.get("steps") or [], start=1):
                w.writerow(["", "", "", i, s.get("action", ""), s.get("expected_result", ""), "", "", "", ""])
    print(f"wrote {show_path(out)}")
    return out


def team_plan(tp: dict, story_area: str | None) -> int:
    """The story's team area path picks the team's test plan; the longest matching prefix wins."""
    norm = lambda p: str(p or "").replace("/", "\\").strip("\\").lower()  # noqa: E731
    area = norm(story_area)
    plans = tp.get("team_plans") or {}
    best = max((k for k in plans if area == norm(k) or area.startswith(norm(k) + "\\")), key=len, default=None)
    if best is None or not is_set(plans[best]):
        die(f"story area path '{story_area}' matches no azure_devops.test_plan.team_plans entry in "
            f"config/framework.yaml; add  \"<team area path>\": <test plan id>")
    return int(plans[best])


def sprint_name(tp: dict, story_iteration: str | None) -> str:
    """Last segment of the story's iteration path, e.g. Product\\Team 1\\26.3.7 -> 26.3.7."""
    name = str(story_iteration or "").replace("/", "\\").rstrip("\\").split("\\")[-1]
    pattern = tp.get("sprint_name_pattern")
    if not name or (pattern and not re.fullmatch(pattern, name)):
        die(f"cannot derive a sprint name from the story's iteration path '{story_iteration}' "
            f"(got '{name}', expected azure_devops.test_plan.sprint_name_pattern {pattern!r}); "
            "move the story into a sprint first")
    return name


def cmd_publish_test_cases(cfg, args):
    from pdlc import approval_valid, _gate
    story = story_key(args.story)
    state = load_state(cfg, story)
    stages = load_pipeline()
    for gate_id in ("G1-test-approval", "G3-automation-and-publish-approval"):
        if not approval_valid(cfg, story, state, _gate(stages, gate_id)):
            die(f"{gate_id} has no valid approval for the current artifacts; refusing to publish", 2)
    base = run_path(cfg, story)
    out_dir = base / "14-publish"
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt_p = out_dir / "publish-receipt.json"
    receipt = json.loads(receipt_p.read_text(encoding="utf-8")) if receipt_p.exists() else {"test_cases": {}}
    tcs, reused = load_design(cfg, story)
    problems = path_problems(cfg, tcs)
    if problems:
        die("cannot publish:\n  " + "\n  ".join(problems))
    if not enabled(cfg) or state.get("offline"):
        csv_path = cmd_export_csv(cfg, args)
        receipt.update(offline=True, published_at=now(), csv=show_path(csv_path),
                       note="ADO disabled: import the CSV via Test Plans > Import, link reused_cases to the story "
                            "(Tested By) by hand, or enable azure_devops and re-run.")
        receipt["reused_cases"] = {str(i): {**info, "link": "manual (offline)"} for i, info in reused.items()}
        receipt_p.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(f"offline: {len(tcs)} test cases exported to {show_path(csv_path)}; "
              f"{len(reused)} reused master-pack cases to link by hand")
        return
    a = cfg["azure_devops"]
    tp = a["test_plan"]
    story_data = json.loads((base / "01-intake" / "story.json").read_text(encoding="utf-8"))
    wid = story_number(story)
    plan = team_plan(tp, story_data.get("area_path"))
    sprint = sprint_name(tp, story_data.get("iteration_path"))
    names = {"story": story, "sprint": sprint, "title": story_data.get("title", "")}
    story_suite_name = f"{story} {names['title']}"[:250]
    reg_suite_name = tp.get("regression_suite_name", "{sprint} Regression").format(**names)[:250]
    story_reg_name = tp.get("story_regression_suite_name", "{story} regression").format(**names)[:250]
    reg = load_yaml(base / "08-regression" / "regression-selection.yaml") or {}
    reg_ids = sorted({int(r["id"]) for r in reg.get("selected") or [] if str(r.get("id", "")).isdigit()})
    if args.dry_run:
        print_json({"test_plan_id": plan, "sprint_suite": sprint, "story_suite": story_suite_name,
                    "regression_suite": f"{reg_suite_name} / {story_reg_name} (query-based)",
                    "regression_case_ids": reg_ids})
    ado = None if args.dry_run else Ado(cfg, tests=True)
    if ado:
        # Pre-flight: every path must already exist, so nothing is created when one is wrong.
        missing = [f"area path '{p}'" for p in sorted({case_paths(cfg, tc)[0] for tc in tcs})
                   if not ado.node_exists("areas", p)]
        missing += [f"iteration path '{p}'" for p in sorted({case_paths(cfg, tc)[1] for tc in tcs})
                    if not ado.node_exists("iterations", p)]
        if missing:
            die(f"not found in ADO project '{ado.project_name}' (create them there, or fix test-cases.yaml / "
                "azure_devops.test_plan.iteration_paths); nothing was published:\n  " + "\n  ".join(missing))
    for tc in tcs:
        area, iteration = case_paths(cfg, tc)
        key = tc.get("key") or tc["title"]
        patch = [
            {"op": "add", "path": "/fields/System.Title", "value": tc["title"]},
            {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.Steps", "value": steps_xml(tc.get("steps") or [])},
            {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": int(tc.get("priority", 2))},
            {"op": "add", "path": "/fields/System.Description", "value": tc_description(tc)},
            {"op": "add", "path": "/fields/System.Tags",
             "value": "; ".join((tp.get("tags") or []) + (tc.get("tags") or []) +
                                 ([tc["interface"]] if tc.get("interface") else []) + [story])},
            {"op": "add", "path": "/fields/System.AreaPath", "value": area},
            {"op": "add", "path": "/fields/System.IterationPath", "value": iteration},
        ]
        existing = receipt["test_cases"].get(key)
        if args.dry_run:
            print_json({"key": key, "update" if existing else "create": patch})
            continue
        if existing:
            item = ado.update_item(existing["id"], patch)
        else:
            patch.append({"op": "add", "path": "/relations/-", "value": {
                "rel": TESTED_BY, "url": ado.work_item_url(wid)}})
            item = ado.create_item("Test Case", patch)
        receipt["test_cases"][key] = {"id": item["id"], "title": tc["title"],
                                      "url": item.get("_links", {}).get("html", {}).get("href")}
        receipt_p.write_text(json.dumps(receipt, indent=2), encoding="utf-8")  # persist after every write

    # Reused master-pack cases: only a Tested By link to the story is added; their content is never touched.
    reusable_states = a["master_pack"].get("reusable_states") or ["Ready"]
    receipt["reused_cases"] = {}  # rebuilt each publish; existing links are detected, so a re-run is safe
    for rid, info in reused.items():
        if args.dry_run:
            print_json({"reuse": rid, "add_link": f"{TESTED_BY} -> {story}", **info})
            continue
        item = ado.get_item(rid, expand="relations")
        f = item.get("fields", {})
        linked = any(r.get("rel") == TESTED_BY and r.get("url", "").rstrip("/").lower().endswith(f"/workitems/{wid}")
                     for r in item.get("relations") or [])
        if not linked:
            ado.update_item(rid, [{"op": "add", "path": "/relations/-",
                                   "value": {"rel": TESTED_BY, "url": ado.work_item_url(wid)}}])
        st = f.get("System.State")
        receipt["reused_cases"][str(rid)] = {
            "title": f.get("System.Title"), "state": st, **info, "link": "already linked" if linked else "added",
            "warning": None if st in reusable_states else f"state '{st}' is not in master_pack.reusable_states"}
        receipt_p.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    if args.dry_run:
        return

    # Team plan: <sprint> static folder > story suite;  <sprint> Regression > <story> regression (query-based).
    suites = ado.suites(plan)
    root = next((s for s in suites if not s.get("parentSuite")), None)
    if not root:
        die(f"test plan {plan} has no root suite")
    parent_of = lambda s: (s.get("parentSuite") or {}).get("id")  # noqa: E731
    kind_of = lambda s: str(s.get("suiteType", "")).lower()  # noqa: E731

    def find(name, kind, parent=None):
        return [s for s in suites if s.get("name", "").lower() == name.lower() and kind_of(s) == kind.lower()
                and (parent is None or parent_of(s) == parent)]

    def create(body):
        s = ado.create_suite(plan, body)
        suites.append(s)
        return s

    sprint_matches = find(sprint, "staticTestSuite")
    if len(sprint_matches) > 1:
        die(f"test plan {plan} has {len(sprint_matches)} static suites named '{sprint}' "
            f"(ids {[s['id'] for s in sprint_matches]}); keep one and re-run")
    sprint_suite = sprint_matches[0] if sprint_matches else create(
        {"suiteType": "staticTestSuite", "name": sprint, "parentSuite": {"id": root["id"]}})

    notes = []
    if tp.get("suite_strategy", "requirement_based") == "requirement_based":
        existing_req = [s for s in suites if s.get("requirementId") == wid]
        suite = existing_req[0] if existing_req else create(
            {"suiteType": "requirementTestSuite", "name": story_suite_name,
             "parentSuite": {"id": sprint_suite["id"]}, "requirementId": wid})
        if existing_req and parent_of(suite) != sprint_suite["id"]:
            notes.append(f"the story's requirement suite {suite['id']} sits outside sprint folder '{sprint}' "
                         "(carried-over story?); reused it rather than creating a second one")
    else:
        found = find(story_suite_name, "staticTestSuite", sprint_suite["id"])
        suite = found[0] if found else create(
            {"suiteType": "staticTestSuite", "name": story_suite_name, "parentSuite": {"id": sprint_suite["id"]}})
    ids = [v["id"] for v in receipt["test_cases"].values()] + [int(k) for k in receipt["reused_cases"]]
    ado.add_to_suite(plan, suite["id"], ids)
    receipt["suite"] = {"plan_id": plan, "sprint": sprint, "sprint_suite_id": sprint_suite["id"],
                        "suite_id": suite["id"], "name": suite.get("name")}

    if reg_ids:
        reg_root = (find(reg_suite_name, "staticTestSuite", root["id"]) or [None])[0] or create(
            {"suiteType": "staticTestSuite", "name": reg_suite_name, "parentSuite": {"id": root["id"]}})
        query = ("SELECT [System.Id] FROM WorkItems WHERE [System.WorkItemType] IN GROUP 'Microsoft.TestCaseCategory'"
                 f" AND [System.Id] IN ({', '.join(map(str, reg_ids))})")
        found = find(story_reg_name, "dynamicTestSuite", reg_root["id"])
        if found:
            reg_suite = found[0]
            ado.update_suite(plan, reg_suite["id"], {"queryString": query})
        else:
            reg_suite = create({"suiteType": "dynamicTestSuite", "name": story_reg_name,
                                "parentSuite": {"id": reg_root["id"]}, "queryString": query})
        receipt["regression_suite"] = {"sprint_suite_id": reg_root["id"], "name": f"{reg_suite_name} / {story_reg_name}",
                                       "suite_id": reg_suite["id"], "test_case_ids": reg_ids}
    receipt["notes"] = notes
    receipt["published_at"] = now()
    receipt_p.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    n_reused = len(receipt["reused_cases"])
    log_event(state, "published", test_cases=len(ids) - n_reused, reused_cases=n_reused, suite=suite["id"])
    save_state(cfg, story, state)
    print(f"published {len(ids) - n_reused} new + {n_reused} reused test cases to plan {plan} suite {suite['id']} ({suite['name']})")
    for k, v in receipt["reused_cases"].items():
        if v.get("warning"):
            print(f"WARNING: reused case {k}: {v['warning']}")
    for n in notes:
        print(f"NOTE: {n}")


# ================================================================ report comment

def md_to_html(md: str) -> str:
    out, in_list, table = [], False, []

    def flush_table():
        if table:
            rows = [r for r in table if not re.fullmatch(r"\|?[\s:\-|]+\|?", r)]
            cells = lambda r: [html.escape(c.strip()) for c in r.strip().strip("|").split("|")]  # noqa: E731
            out.append("<table border='1'>" + "".join(
                "<tr>" + "".join(f"<td>{c}</td>" for c in cells(r)) + "</tr>" for r in rows) + "</table>")
            table.clear()

    for line in md.splitlines():
        if line.strip().startswith("|"):
            table.append(line)
            continue
        flush_table()
        if line.startswith("- ") or line.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        m = re.match(r"(#+)\s+(.*)", line)
        if m:
            out.append(f"<h{min(len(m.group(1)) + 1, 5)}>{html.escape(m.group(2))}</h{min(len(m.group(1)) + 1, 5)}>")
        elif line.strip():
            out.append(f"<p>{html.escape(line)}</p>")
    flush_table()
    if in_list:
        out.append("</ul>")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", "".join(out))


def cmd_post_report(cfg, args):
    story = story_key(args.story)
    state = load_state(cfg, story)
    base = run_path(cfg, story)
    report = (base / "16-report" / "traceability-report.md").read_text(encoding="utf-8")
    receipt_p = base / "16-report" / "post-receipt.json"
    if not enabled(cfg) or state.get("offline") or args.dry_run:
        receipt = {"offline": True, "at": now(), "note": "report not posted; ADO disabled or dry run"}
    else:
        c = Ado(cfg).add_comment(story_number(story), md_to_html(report))
        receipt = {"comment_id": c.get("id"), "at": now()}
    receipt_p.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print_json(receipt)


# ================================================================ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch-story", "raise-clarifications", "fetch-clarifications", "export-csv"):
        sub.add_parser(name).add_argument("story")
    p = sub.add_parser("export-master-pack"); p.add_argument("--if-stale", action="store_true")
    p.add_argument("--full", action="store_true", help="re-fetch every case instead of syncing changed revisions")
    p = sub.add_parser("publish-test-cases"); p.add_argument("story"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("post-report"); p.add_argument("story"); p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    try:
        {"fetch-story": cmd_fetch_story, "raise-clarifications": cmd_raise_clarifications,
         "fetch-clarifications": cmd_fetch_clarifications, "export-master-pack": cmd_export_master_pack,
         "export-csv": cmd_export_csv, "publish-test-cases": cmd_publish_test_cases,
         "post-report": cmd_post_report}[args.cmd](cfg, args)
    except AdoError as e:
        die(str(e))


if __name__ == "__main__":
    main()
