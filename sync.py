"""Keeps the homework list in sync.

Sources of truth:
  data/items.json   every item, its checked state, and ids already cleared
  data/scrape.json  new items found in Schoology by the daily Claude check
  the pinned GitHub issue "Homework"  where you check boxes and adds lines

Each run:
  1. read the issue, copy checked boxes and new hand-added lines into items.json
  2. if scrape.json is new, clear checked items on the weekly cleanup day, then add new items
  3. rewrite the issue and README.md from items.json

Runs in GitHub Actions (see .github/workflows/sync.yml). Needs GITHUB_TOKEN and GITHUB_REPOSITORY.
Run locally with --dry-run to print the issue body without calling GitHub.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
ROOT = Path(__file__).parent
ITEMS = ROOT / "data" / "items.json"
SCRAPE = ROOT / "data" / "scrape.json"
README = ROOT / "README.md"
MARK = "<!-- hw-tracker: keep this line -->"
KINDS = [("hw", "Homework"), ("tests", "Tests and quizzes"), ("reminders", "Reminders")]
HEAD_TO_KIND = {h.lower(): k for k, h in KINDS}
CONFIG_FILE = ROOT / "config.json"
try:
    CONFIG = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
except FileNotFoundError:
    CONFIG = {}
# your classes, in the order you want them shown. Set in config.json
CLASSES = CONFIG.get("classes", [])
# short names you might type when adding things by hand, like "calc" for "AP Calc AB"
ALIASES = {k.lower(): v for k, v in CONFIG.get("aliases", {}).items()}
# your homework page. Defaults to https://<your GitHub name>.github.io/Homework-App/
PAGE = CONFIG.get("page") or (
    f"https://{os.environ['GITHUB_REPOSITORY'].split('/')[0]}.github.io/Homework-App/"
    if os.environ.get("GITHUB_REPOSITORY") else "")
# every class name seen so far, filled in by main()
KNOWN = list(CLASSES)
SGY = CONFIG.get("schoology", "https://basised-dc.schoology.com")


def now():
    return dt.datetime.now(TZ)


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------- GitHub API ----------
def gh(method, path, body=None):
    token = os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(
        "https://api.github.com" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else {}


def graphql(query, variables):
    return gh("POST", "/graphql", {"query": query, "variables": variables})


# ---------- dates ----------
def parse_date(s):
    """Accepts 10/2, 10/2/26, 10/2/2026, 2026-10-02, oct 2. Returns YYYY-MM-DD or ''."""
    s = s.strip().lower()
    if not s:
        return ""
    today = now().date()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", s)
    if m:
        mo, d = int(m[1]), int(m[2])
        y = int(m[3]) if m[3] else today.year
        if y < 100:
            y += 2000
        date = dt.date(y, mo, d)
        if not m[3] and date < today - dt.timedelta(days=60):
            date = dt.date(y + 1, mo, d)
        return date.isoformat()
    for fmt in ("%b %d", "%B %d"):
        try:
            p = dt.datetime.strptime(s.replace(",", ""), fmt)
            date = dt.date(today.year, p.month, p.day)
            if date < today - dt.timedelta(days=60):
                date = dt.date(today.year + 1, p.month, p.day)
            return date.isoformat()
        except ValueError:
            pass
    return ""


def nice(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%a %b')} {d.day}"


# ---------- issue body ----------
def fix_class(name):
    low = re.sub(r"\s+", " ", name.strip().lower())
    aliases = ALIASES
    if low in aliases:
        return aliases[low]
    for c in KNOWN:
        if low == c.lower():
            return c
    for c in KNOWN:
        if len(low) >= 4 and low in c.lower():
            return c
    return name.strip() or "Other"


LINE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*?)\s*$")
ID = re.compile(r"<!--\s*id:([A-Za-z0-9_.:-]+)\s*-->")


def read_issue(body, items):
    """Copy check states and hand-added lines from the issue into items. Returns count of changes."""
    by_id = {i["id"]: i for i in items["items"]}
    kind, cls, changes = None, None, 0
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            kind = HEAD_TO_KIND.get(line[3:].strip().lower())
            cls = None
            continue
        if line.startswith("### "):
            cls = fix_class(line[4:])
            continue
        m = LINE.match(raw)
        if not m or kind is None:
            continue
        done = m[1].lower() == "x"
        text = m[2]
        idm = ID.search(text)
        if idm:
            it = by_id.get(idm[1])
            if it and it.get("done") != done:
                it["done"] = done
                it["doneAt"] = now().isoformat() if done else None
                changes += 1
            continue
        # a new line you typed. Formats: "Class | thing | date", "thing | date", or just "thing"
        parts = [p.strip() for p in text.split("|")]
        c = cls
        if kind != "reminders" and len(parts) >= 3:
            c, name, date = fix_class(parts[0]), parts[1], parts[2]
        elif len(parts) >= 2:
            name, date = " | ".join(parts[:-1]), parts[-1]
            if not parse_date(date):
                if kind == "reminders":
                    name, date = " | ".join(parts), ""
                else:
                    c, name, date = fix_class(parts[0]), " | ".join(parts[1:]), ""
        else:
            name, date = parts[0], ""
        if not name:
            continue
        new_id = "m-" + now().strftime("%y%m%d%H%M%S") + f"-{changes}"
        items["items"].append({
            "id": new_id, "kind": kind, "c": "" if kind == "reminders" else (c or "Other"),
            "n": name, "detail": "", "due": parse_date(date), "dueNote": "" if parse_date(date) or not date else date,
            "url": "", "src": "manual", "done": done, "added": now().date().isoformat(),
        })
        changes += 1
    return changes


def item_line(i):
    box = "x" if i.get("done") else " "
    name = i["n"]
    if i.get("url"):
        url = i["url"] if i["url"].startswith("http") else SGY + i["url"]
        name = f"[{name}]({url})"
    when = ""
    if i.get("due"):
        late = dt.date.fromisoformat(i["due"]) < now().date() and not i.get("done")
        when = f"{'due' if i['kind'] == 'hw' else ''} {nice(i['due'])}{' ' + i['dueTime'] if i.get('dueTime') else ''}".strip()
        if late:
            when = f"**{when}, late**"
    elif i["kind"] != "reminders":
        when = i.get("dueNote") or "no date yet"
    tag = " · added by you" if i.get("src") == "manual" and i["kind"] != "reminders" else ""
    out = f"- [{box}] {name}{' · ' + when if when else ''}{tag} <!--id:{i['id']}-->"
    if i.get("tr"):
        out += f"\n  ({i['tr']})"
    if i.get("detail"):
        out += f"\n  {i['detail']}"
    return out


def sort_key(i):
    return (i.get("due") or "9999", i["n"].lower())


def render(items, for_issue=True):
    t = now()
    stamp = items.get("lastCheck") or ""
    lines = []
    if for_issue:
        lines += [MARK,
                  f"<!-- state lastCheckAt={items.get('lastCheckAt') or ''} maxId={items.get('maxId') or 0} -->",
                  f"<!-- classes: {'|'.join(KNOWN)} -->",
                  f"Last Schoology check: {stamp or 'not yet'}. List updated {t.strftime('%a %b')} {t.day}, {t.strftime('%I:%M %p').lstrip('0').lower()}.",
                  "",
                  "Check a box when you finish. Checked items clear on Sunday, anything not checked carries over.",
                  "To add something, click the three dots, Edit, and add a line under the right section, like",
                  "`- [ ] Calc | Read p.40-52 | 10/2` or under Reminders `- [ ] Bring goggles | 10/1`",
                  f"Easier way: [open my homework page]({PAGE})",
                  ""]
    else:
        lines += ["# Homework", "",
                  f"**[Open my homework page]({PAGE})**", "",
                  f"Read only copy. Check things off in the pinned **Homework** issue. Last Schoology check: {stamp or 'not yet'}.", ""]
    live = [i for i in items["items"]]
    for kind, head in KINDS:
        group = [i for i in live if i["kind"] == kind]
        lines.append(f"## {head}")
        if not group:
            lines += ["Nothing here right now.", ""]
            continue
        if kind == "reminders":
            lines += [item_line(i) for i in sorted(group, key=sort_key)]
            lines.append("")
            continue
        for c in sorted({i["c"] for i in group}):
            lines.append(f"### {c}")
            lines += [item_line(i) for i in sorted([i for i in group if i["c"] == c], key=sort_key)]
            lines.append("")
    text = "\n".join(lines)
    if not for_issue:
        text = ID.sub("", text).replace(" \n", "\n")
    return text


# ---------- weekly cleanup and new items ----------
def cleanup(items, scrape):
    """Remove checked items, and Schoology items that are past due and no longer listed as not turned in."""
    today = now().date()
    still_out = set(scrape.get("notTurnedIn", [])) if scrape else None
    keep, removed = [], 0
    for i in items["items"]:
        gone = i.get("done")
        if (not gone and still_out is not None and i.get("src") == "schoology" and i.get("due")
                and dt.date.fromisoformat(i["due"]) < today and i["id"] not in still_out):
            gone = True
        if not gone and i["kind"] == "tests" and i.get("due") and dt.date.fromisoformat(i["due"]) < today:
            gone = True  # test date passed, so it was taken
        if gone:
            items["cleared"].append(i["id"])
            removed += 1
        else:
            keep.append(i)
    items["items"] = keep
    items["cleared"] = items["cleared"][-2000:]
    items["lastCleanup"] = today.isoformat()
    return removed


def add_new(items, scrape):
    known = {i["id"] for i in items["items"]} | set(items["cleared"])
    by_id = {i["id"]: i for i in items["items"]}
    added = 0
    today = now().date()
    drop = set(scrape.get("remove", []))
    if drop:
        items["items"] = [i for i in items["items"] if i["id"] not in drop or i.get("src") == "manual"]
        items["cleared"] += [d for d in drop if d not in items["cleared"]]
        by_id = {i["id"]: i for i in items["items"]}
        known |= drop
    for n in scrape.get("items", []):
        if n["id"] in by_id:
            cur = by_id[n["id"]]
            if cur.get("src") == "manual":
                continue
            for f in ("due", "dueTime", "dueNote", "detail", "n", "tr"):
                if n.get(f) and n.get(f) != cur.get(f):
                    cur[f] = n[f]
            continue
        if n["id"] in known:
            continue
        if n.get("kind") == "tests" and n.get("due") and dt.date.fromisoformat(n["due"]) < today:
            continue  # past tests were already taken
        n = {"kind": "hw", "c": "Other", "detail": "", "due": "", "url": "", "src": "schoology", **n,
             "done": False, "added": now().date().isoformat()}
        items["items"].append(n)
        added += 1
    return added


SCRAPE_MARK = "<!-- scrape -->"


def scrape_from_comment(repo):
    """If this run was started by a comment holding scrape data, save it to data/scrape.json and delete the comment."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).exists():
        return
    event = json.loads(Path(path).read_text())
    c = event.get("comment") or {}
    body = c.get("body") or ""
    if not body.startswith(SCRAPE_MARK):
        return
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if m:
        data = json.loads(m[1])
        save(SCRAPE, data)
        print("scrape data saved from comment", data.get("runId"))
    try:
        gh("DELETE", f"/repos/{repo}/issues/comments/{c['id']}")
    except Exception as e:
        print("could not delete comment:", e)


def remember_classes(items):
    """Keep a list of every class ever seen so its card stays on the page even when empty."""
    seen = set(items.get("classes", [])) | {i["c"] for i in items["items"] if i.get("kind") != "reminders" and i.get("c")}
    items["classes"] = sorted(seen)
    KNOWN[:] = list(CLASSES) + [c for c in items["classes"] if c not in CLASSES]


def main():
    dry = "--dry-run" in sys.argv
    if not dry:
        scrape_from_comment(os.environ.get("GITHUB_REPOSITORY", ""))
    items = load(ITEMS, {"items": [], "cleared": []})
    items.setdefault("cleared", [])
    remember_classes(items)
    scrape = load(SCRAPE, None)
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    issue = None
    if not dry and items.get("issue"):
        issue = gh("GET", f"/repos/{repo}/issues/{items['issue']}")
        if issue.get("state") != "open":
            gh("PATCH", f"/repos/{repo}/issues/{items['issue']}", {"state": "open"})
        read_issue(issue.get("body", ""), items)

    if scrape and scrape.get("test"):
        print("test scrape received", scrape.get("runId"))
        items["lastTest"] = scrape.get("runId")
    elif scrape and scrape.get("runId") != items.get("lastScrape"):
        today = now().date()
        last = items.get("lastCleanup")
        weekly = today.weekday() == 6 or not last or (today - dt.date.fromisoformat(last)).days >= 7
        if weekly and scrape.get("ok", True):
            print("weekly cleanup removed", cleanup(items, scrape))
        print("added", add_new(items, scrape))
        items["lastScrape"] = scrape.get("runId")
        if scrape.get("maxId"):
            items["maxId"] = max(int(scrape["maxId"]), int(items.get("maxId") or 0))
        if scrape.get("ok", True):
            t = dt.datetime.fromisoformat(scrape["checkedAt"]).astimezone(TZ)
            items["lastCheck"] = f"{t.strftime('%a %b')} {t.day}, {t.strftime('%I:%M %p').lstrip('0').lower()}"
            items["lastCheckAt"] = t.isoformat()
        else:
            items["lastCheck"] = (items.get("lastCheck") or "") + " (latest check failed, " + scrape.get("reason", "unknown") + ")"

    remember_classes(items)
    note = scrape.get("summary") if scrape and scrape.get("runId") != items.get("lastNote") else None
    body = render(items, True)
    if dry:
        print(body)
        return
    if items.get("issue"):
        if issue.get("body") != body:
            gh("PATCH", f"/repos/{repo}/issues/{items['issue']}", {"body": body})
    else:
        new = gh("POST", f"/repos/{repo}/issues", {"title": "Homework", "body": body})
        items["issue"] = new["number"]
        try:
            graphql("mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{number}}}", {"id": new["node_id"]})
        except Exception as e:  # pinning is nice to have
            print("could not pin issue:", e)
    if note and items.get("issue"):
        # posting a comment makes GitHub send you an email and app alert
        gh("POST", f"/repos/{repo}/issues/{items['issue']}/comments", {"body": note})
        items["lastNote"] = scrape.get("runId")
    save(ITEMS, items)
    README.write_text(render(items, False), encoding="utf-8")


if __name__ == "__main__":
    main()
