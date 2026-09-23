#!/usr/bin/env python3
"""build_digest.py — computes digest.json and alerts.json for the Lican Ray orchard CWD.

Reads state.json from the secret Gist and writes digest.json and alerts.json.
Runs (a) in Claude's sandbox after every data change and (b) every Monday in GitHub Actions
(.github/workflows/weekly-digest.yml), so the Monday email never reads a stale summary.

IMPORTANT: index.html has its own copy of this logic in JavaScript. The two do not share code.
If you change due dates, status thresholds, sorting or grouping here, change index.html too.
"""
import json, os, sys, urllib.request, urllib.error
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

GIST_ID = "fb784b2ee123ed26dbd339f003984d65"
DASHBOARD = "https://fernando951.github.io/lican-ray/"
DONE_RECENT_DAYS = 14
HORIZON_DAYS = 30
MONTHS = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
STATUS_RANK = {"overdue": 0, "soon": 1, "ok": 2, "asneeded": 2}


def fetch_state():
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "lican-ray-digest"}
    tok = os.environ.get("GH_TOKEN")
    if tok:
        headers["Authorization"] = "Bearer " + tok
    ts = int(datetime.now().timestamp())
    url = f"https://api.github.com/gists/{GIST_ID}?t={ts}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            g = json.load(r)
    except urllib.error.HTTPError:
        # API refused (e.g. rate limit): fall back to the Gist's raw file
        raw = f"https://gist.githubusercontent.com/Fernando951/{GIST_ID}/raw/state.json?t={ts}"
        with urllib.request.urlopen(urllib.request.Request(raw, headers={"User-Agent": "lican-ray-digest"}), timeout=30) as r:
            return json.load(r)
    f = g["files"]["state.json"]
    if f.get("truncated"):
        with urllib.request.urlopen(urllib.request.Request(f["raw_url"], headers=headers), timeout=30) as r:
            return json.load(r)
    return json.loads(f["content"])


def mkdate(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:  # 29 Feb in a non-leap year
        return date(y, m, 28)


def fmt(d):
    return f"{d.day} {MONTHS[d.month-1]} {d.year}"


def occurrence(sch, y):
    if sch["type"] == "date":
        s = mkdate(y, sch["month"], sch["day"])
        return s, s
    s = mkdate(y, sch["start"]["month"], sch["start"]["day"])
    e = mkdate(y, sch["end"]["month"], sch["end"]["day"])
    if e < s:
        e = mkdate(y + 1, sch["end"]["month"], sch["end"]["day"])
    return s, e


def in_season(active, d):
    s, e = occurrence({"type": "window", **active}, d.year)
    s0, e0 = occurrence({"type": "window", **active}, d.year - 1)
    return s <= d <= e or s0 <= d <= e0


def next_season_start(active, d):
    for y in (d.year, d.year + 1):
        s, _ = occurrence({"type": "window", **active}, y)
        if s >= d:
            return s
    return d


def compute(task, state, today):
    st = state["settings"]
    co = task.get("coming_up_days", st["coming_up_days"])
    od = task.get("overdue_days", st["overdue_days"])
    track = date.fromisoformat(st.get("tracking_start", "1900-01-01"))
    entries = [e for e in state.get("log", []) if e["task"] == task["id"]]
    dues = {e.get("due") for e in entries}
    done = sorted(date.fromisoformat(e["done"]) for e in entries if not e.get("skipped"))
    last_done = done[-1] if done else None
    sch = task["schedule"]

    if sch["type"] == "interval":
        every = sch["every_days"]
        nd = last_done + timedelta(days=every) if last_done else max(today, track)
        if sch.get("active") and not in_season(sch["active"], nd):
            nd = next_season_start(sch["active"], nd)
        cur_s = cur_e = nd
        period_start = last_done or (nd - timedelta(days=every))
    else:
        occ = [occurrence(sch, y) for y in range(today.year - 2, today.year + 3)]
        handled = lambda o: o[0].isoformat() in dues or o[0] < track
        prev = [o for o in occ if o[0] <= today]
        prev = prev[-1] if prev else None
        nxt = [o for o in occ if o[0] > today][0]
        cur = prev if (prev and not handled(prev)) else nxt
        cur_s, cur_e = cur
        i = occ.index(cur)
        period_start = occ[i - 1][0]

    if task.get("optional"):
        status = "asneeded"
    elif today > cur_e + timedelta(days=od):
        status = "overdue"
    elif today >= cur_s - timedelta(days=co):
        status = "soon"
    else:
        status = "ok"
    return {
        "id": task["id"], "crop": task.get("crop"), "name": task["name"], "category": task.get("category"),
        "status": status, "due": cur_s, "due_end": cur_e, "last_done": last_done,
        "days_until": (cur_s - today).days, "days_overdue": (today - cur_e).days,
        "period_start": period_start,
    }


def sort_key(c):
    if c["status"] == "overdue":
        return (0, -c["days_overdue"], c["name"])
    return (STATUS_RANK[c["status"]], c["due"].toordinal(), c["name"])


def dose_text(task):
    d = task.get("dose")
    if not d:
        return None
    if isinstance(d, str):
        return d
    parts = []
    if "viejos" in d:
        parts.append("Viejos: " + d["viejos"])
    if "nuevos" in d:
        parts.append(("Nuevos: " if "viejos" in d else "Solo nuevos: ") + d["nuevos"])
    return " / ".join(parts)


def when_text(c, today):
    if c["status"] == "overdue":
        return f"vencida hace {c['days_overdue']} días (tocaba {fmt(c['due'])})"
    n = c["days_until"]
    if n > 1:
        return f"en {n} días ({fmt(c['due'])})"
    if n == 1:
        return "mañana"
    if n == 0:
        return "hoy"
    return f"tocaba el {fmt(c['due'])}"


def build(state, today):
    tasks = {t["id"]: t for t in state["tasks"]}
    comp = sorted((compute(t, state, today) for t in state["tasks"]), key=sort_key)

    def item(c):
        t = tasks[c["id"]]
        return {"task": c["id"], "crop": c["crop"], "name": c["name"], "status": c["status"],
                "due": c["due"].isoformat(), "due_end": c["due_end"].isoformat(),
                "when": when_text(c, today), "dose": dose_text(t), "trigger": t.get("trigger")}

    overdue = [dict(item(c), days_overdue=c["days_overdue"]) for c in comp if c["status"] == "overdue"]
    upcoming = [dict(item(c), days_until=c["days_until"]) for c in comp
                if c["status"] != "overdue" and c["days_until"] <= HORIZON_DAYS]

    recent = []
    for e in sorted(state.get("log", []), key=lambda e: e["done"], reverse=True):
        dd = date.fromisoformat(e["done"])
        if (today - dd).days <= DONE_RECENT_DAYS and dd <= today:
            recent.append({"task": e["task"], "name": tasks.get(e["task"], {}).get("name", e["task"]),
                           "done": e["done"], "skipped": bool(e.get("skipped")), "note": e.get("note", "")})

    issues = [i for i in state.get("issues", []) if i.get("status") in ("open", "evaluate")]
    issues.sort(key=lambda i: (0 if i["status"] == "open" else 1, i.get("opened", "")))

    notes = []
    for m in state.get("milestones", []):
        for y in (today.year - 1, today.year):
            s, e = occurrence({"type": "window", "start": m["start"], "end": m["end"]}, y)
            if e >= today and s <= today + timedelta(days=HORIZON_DAYS):
                rng = fmt(s) if s == e else f"{s.day} {MONTHS[s.month-1]} – {fmt(e)}"
                notes.append(f"{m['name']}: {rng}")
    seasonal = "; ".join(notes) or None

    digest = {"generated_at": today.isoformat(), "dashboard": DASHBOARD, "name": "Huerto Lican Ray",
              "overdue": overdue, "due_next_30_days": upcoming, "done_recently": recent,
              "open_issues": issues, "seasonal_note": seasonal}

    alerts = []
    for c in comp:
        t = tasks[c["id"]]
        in_window = c["due"] <= today <= c["due_end"] + timedelta(
            days=t.get("overdue_days", state["settings"]["overdue_days"]))
        if c["status"] == "overdue" or (in_window and c["status"] in ("soon", "asneeded")):
            alerts.append(item(c))
    alerts_doc = {"generated_at": today.isoformat(), "dashboard": DASHBOARD, "name": "Huerto Lican Ray",
                  "items": alerts}
    return digest, alerts_doc


def main():
    state = fetch_state()
    today = datetime.now(ZoneInfo(state["settings"]["timezone"])).date()  # real date, never assumed
    digest, alerts = build(state, today)
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    with open(os.path.join(out, "digest.json"), "w", encoding="utf-8") as f:
        json.dump(digest, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out, "alerts.json"), "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)
    print(f"today={today} overdue={len(digest['overdue'])} next30={len(digest['due_next_30_days'])} alerts={len(alerts['items'])}")


if __name__ == "__main__":
    main()
