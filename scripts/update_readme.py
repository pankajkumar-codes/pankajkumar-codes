#!/usr/bin/env python3
"""Rebuild the auto-generated sections of the profile README.

Runs inside GitHub Actions (see .github/workflows/update-readme.yml).
Standard library only, so there is nothing to install.

It rewrites the text between these marker pairs in README.md:
  <!--PROJECTS:START--> ... <!--PROJECTS:END-->   public repos table
  <!--ACTIVITY:START--> ... <!--ACTIVITY:END-->   recent public activity
Everything outside the markers is left exactly as you wrote it.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

USER = os.environ.get("GH_USER") or os.environ.get("GITHUB_REPOSITORY_OWNER", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
README = os.environ.get("README_PATH", "README.md")
MAX_REPOS = int(os.environ.get("MAX_REPOS", "12"))
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "6"))
MAX_PER_REPO = int(os.environ.get("MAX_PER_REPO", "2"))
# Repos to hide from the table, comma separated (the profile repo is always hidden).
HIDE = {r.strip().lower() for r in os.environ.get("HIDE_REPOS", "").split(",") if r.strip()}
API = "https://api.github.com"


def get(path):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-updater",
    }
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(API + path, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def day(iso):
    return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d %b %Y")


def cell(text):
    """Make text safe inside a markdown table cell."""
    return re.sub(r"\s+", " ", text or "").replace("|", "\\|").strip()


LANG_COLORS = {"Python": "3776AB", "Java": "ED8B00", "JavaScript": "F7DF1E", "TypeScript": "3178C6",
               "Go": "00ADD8", "Shell": "4EAA25", "HCL": "844FBA", "C#": "512BD4", "HTML": "E34F26"}


def badge(label, color):
    """A shields.io static badge (dash and underscore are escaped as shields requires)."""
    text = label.replace("-", "--").replace("_", "__").replace(" ", "_")
    return f"![{label}](https://img.shields.io/badge/{text}-{color}?style=flat-square)"


def load_config():
    path = os.environ.get("PROJECTS_CONFIG", "config/projects.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"categories": [], "hide": []}


def repo_row(r):
    tech = []
    if r.get("language"):
        tech.append(badge(r["language"], LANG_COLORS.get(r["language"], "555555")))
    have = {(r.get("language") or "").lower()}
    tech += [badge(t, "37474F") for t in (r.get("topics") or []) if t.lower() not in have][:4]
    stars = (f"![stars](https://img.shields.io/github/stars/{r['full_name']}"
             "?style=flat-square&label=%E2%98%85&color=37BCF6)")
    return (f"| [**{cell(r['name'])}**]({r['html_url']}) "
            f"| {cell(r['description']) or '&mdash;'} "
            f"| {' '.join(tech) or '&mdash;'} "
            f"| {stars} | {day(r['pushed_at'])} |")


def table(repos):
    rows = ["| Project | What it is | Tech | Stars | Updated |",
            "| --- | --- | --- | --- | --- |"]
    rows += [repo_row(r) for r in repos]
    return "\n".join(rows)


def projects():
    cfg = load_config()
    hide = HIDE | {h.lower() for h in cfg.get("hide", [])}
    repos = get(f"/users/{USER}/repos?type=owner&sort=pushed&per_page=100")
    repos = [r for r in repos
             if not r["fork"] and not r["archived"] and not r["private"]
             and r["name"].lower() != USER.lower() and r["name"].lower() not in hide]
    by_name = {r["name"].lower(): r for r in repos}
    out, used = [], set()
    for cat in cfg.get("categories", []):
        picked = [by_name[n.lower()] for n in cat.get("repos", []) if n.lower() in by_name]
        if picked:
            used.update(r["name"].lower() for r in picked)
            out.append(f"### {cat['title']}\n\n{table(picked)}")
    rest = [r for r in repos if r["name"].lower() not in used]
    rest.sort(key=lambda r: (r["stargazers_count"], r["pushed_at"]), reverse=True)
    if rest:
        out.append(f"### More projects\n\n{table(rest[:MAX_REPOS])}")
    return "\n\n".join(out) or "_No public repositories yet._"


def activity():
    """Latest public activity, grouped so one busy repo cannot fill the list.

    Same kind of event in the same repo on the same day becomes one line
    ("Opened 4 pull requests in X"), and each repo gets at most MAX_PER_REPO lines.
    """
    events = get(f"/users/{USER}/events/public?per_page=100")
    groups = {}  # (date, repo, kind) -> [count, label_fn_args]
    order = []
    for e in events:
        repo = e["repo"]["name"]
        short = repo.split("/")[-1]
        if short.lower() == USER.lower():
            continue
        p, kind = e.get("payload") or {}, e["type"]
        if kind == "PushEvent":
            key = "push"
        elif kind == "PullRequestEvent" and p.get("action") in ("opened", "closed"):
            pr = p.get("pull_request") or {}
            key = "pr-opened" if p["action"] == "opened" else ("pr-merged" if pr.get("merged") else "pr-closed")
        elif kind == "ReleaseEvent" and p.get("action") == "published":
            key = "release:" + cell((p.get("release") or {}).get("tag_name", ""))
        elif kind == "CreateEvent" and p.get("ref_type") == "repository":
            key = "created"
        else:
            continue
        gkey = (e["created_at"][:10], repo, key)
        if gkey not in groups:
            groups[gkey] = 0
            order.append(gkey)
        groups[gkey] += 1

    lines, per_repo = [], {}
    for date, repo, key in sorted(order, reverse=True):
        short = repo.split("/")[-1]
        if per_repo.get(repo, 0) >= MAX_PER_REPO:
            continue
        n = groups[(date, repo, key)]
        link = f"[{short}](https://github.com/{repo})"
        if key == "push":
            text = f"Pushed code to {link}"
        elif key.startswith("pr-"):
            verb = {"pr-opened": "Opened", "pr-merged": "Merged", "pr-closed": "Closed"}[key]
            text = f"{verb} {n} pull request{'s' if n > 1 else ''} in {link}"
        elif key.startswith("release:"):
            text = f"Released `{key[8:]}` of {link}"
        else:
            text = f"Created new repository {link}"
        per_repo[repo] = per_repo.get(repo, 0) + 1
        lines.append(f"- {text} &nbsp;·&nbsp; <sub>{day(date)}</sub>")
        if len(lines) >= MAX_EVENTS:
            break
    return "\n".join(lines) or "_No recent public activity._"


def replace(text, name, body):
    pattern = re.compile(rf"(<!--{name}:START-->).*?(<!--{name}:END-->)", re.S)
    if not pattern.search(text):
        print(f"warning: markers for {name} not found, section skipped", file=sys.stderr)
        return text
    return pattern.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(2)}", text)


def main():
    if not USER:
        sys.exit("GH_USER is not set")
    with open(README, encoding="utf-8") as f:
        old = f.read()
    new = replace(old, "PROJECTS", projects())
    new = replace(new, "ACTIVITY", activity())
    if new != old:
        with open(README, "w", encoding="utf-8") as f:
            f.write(new)
        print("README.md updated")
    else:
        print("README.md already up to date")


if __name__ == "__main__":
    main()
