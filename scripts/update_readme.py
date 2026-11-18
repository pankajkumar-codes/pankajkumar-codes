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
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "5"))
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


def projects():
    repos = get(f"/users/{USER}/repos?type=owner&sort=pushed&per_page=100")
    repos = [r for r in repos
             if not r["fork"] and not r["archived"] and not r["private"]
             and r["name"].lower() != USER.lower()
             and r["name"].lower() not in HIDE]
    # Most starred first, then most recently pushed.
    repos.sort(key=lambda r: (r["stargazers_count"], r["pushed_at"]), reverse=True)
    rows = ["| Project | What it is | Tech | Stars | Last update |",
            "| --- | --- | --- | --- | --- |"]
    for r in repos[:MAX_REPOS]:
        full = r["full_name"]
        tech = [r["language"]] if r.get("language") else []
        have = {t.lower() for t in tech}
        tech += [t for t in (r.get("topics") or []) if t.lower() not in have][:4]
        stars = (f"![stars](https://img.shields.io/github/stars/{full}"
                 "?style=flat-square&label=%E2%98%85&color=37BCF6)")
        rows.append(
            f"| [**{cell(r['name'])}**]({r['html_url']}) "
            f"| {cell(r['description']) or '&mdash;'} "
            f"| {' · '.join(f'`{t}`' for t in tech) or '&mdash;'} "
            f"| {stars} | {day(r['pushed_at'])} |")
    if len(rows) == 2:
        return "_No public repositories yet._"
    return "\n".join(rows)


def activity():
    events = get(f"/users/{USER}/events/public?per_page=100")
    lines, seen = [], set()
    for e in events:
        repo = e["repo"]["name"]
        short = repo.split("/")[-1]
        if short.lower() == USER.lower():
            continue
        p, kind = e.get("payload") or {}, e["type"]
        link = f"[{short}](https://github.com/{repo})"
        if kind == "PushEvent":
            text = f"Pushed code to {link}"
        elif kind == "PullRequestEvent" and p.get("action") in ("opened", "closed"):
            pr = p.get("pull_request") or {}
            if p["action"] == "opened":
                verb = "Opened"
            else:
                verb = "Merged" if pr.get("merged") else "Closed"
            num = p.get("number") or pr.get("number")
            text = f"{verb} pull request #{num} in {link}"
        elif kind == "ReleaseEvent" and p.get("action") == "published":
            tag = cell((p.get("release") or {}).get("tag_name", ""))
            text = f"Released `{tag}` of {link}"
        elif kind == "CreateEvent" and p.get("ref_type") == "repository":
            text = f"Created new repository {link}"
        elif kind == "IssuesEvent" and p.get("action") == "opened":
            text = f"Opened issue #{(p.get('issue') or {}).get('number')} in {link}"
        else:
            continue
        key = (text, e["created_at"][:10])  # repeats on one day collapse to one line
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {text} &nbsp;·&nbsp; <sub>{day(e['created_at'])}</sub>")
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
