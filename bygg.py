# /// script
# requires-python = ">=3.10"
# dependencies = ["openpyxl>=3.1"]
# ///
"""Bygger portalen Mot Lidingö.

    uv run bygg.py               bygger docs/ (det GitHub Pages visar) och artefakt/mot-lidingo.html
    uv run bygg.py --publicera   bygger, sparar en commit och skickar upp till GitHub

Appen ligger i src/app.html och planen i src/plan.json. Passen på klockan läses från Excelfilen
via ../Garmin/skicka_till_garmin.py, så de blir exakt de pass som skickas till Garmin.
Om planen och Excelfilen inte stämmer överens stannar bygget och visar vilka datum som skiljer.
"""
import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC, DOCS, ART = HERE / "src", HERE / "docs", HERE / "artefakt"
GARMIN = HERE.parent / "Garmin"

# The page wrapper: the same base styles a Claude artifact gets, plus what a home-screen web app needs.
SHELL = """<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="theme-color" content="#EDF2F0" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0E161A" media="(prefers-color-scheme: dark)">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Mot Lidingö">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon-180.png">
{head}
<style>:root{{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}html{{scroll-padding-top:env(safe-area-inset-top,0px)}}body{{margin:0;padding:0;font:14px -apple-system,BlinkMacSystemFont,sans-serif;background:#fff;color:#000}}img{{max-width:100%}}[hidden]:not([hidden=until-found i]){{display:none!important}}</style>
</head><body>
{body}
<script>if ("serviceWorker" in navigator) addEventListener("load", function () {{ navigator.serviceWorker.register("sw.js"); }});</script>
</body></html>
"""

# Offline support: the page itself is fetched fresh when online (so updates show at once), the saved copy when not.
SW = """const CACHE = "motlidingo-{version}";
const FILES = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg", "./icon-180.png", "./icon-192.png", "./icon-512.png"];
self.addEventListener("install", e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(caches.keys()
  .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {{
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET") return;
  if (req.mode === "navigate") {{
    e.respondWith(fetch(req.url, {{ cache: "no-cache" }}).then(res => {{
      if (res.ok) {{ const copy = res.clone(); caches.open(CACHE).then(c => c.put("./index.html", copy)); }}
      return res;
    }}).catch(() => caches.match("./index.html")));
    return;
  }}
  if (url.origin === location.origin || url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {{
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {{
      if (res.ok) {{ const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }}
      return res;
    }})));
  }}
}});
"""

MANIFEST = {
    "name": "Mot Lidingö", "short_name": "Mot Lidingö", "lang": "sv", "start_url": "./", "scope": "./", "display": "standalone",
    "background_color": "#EDF2F0", "theme_color": "#EDF2F0",
    "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
              {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
}


def compact(s):
    if s["type"] == "RepeatGroupDTO":
        return {"r": s["numberOfIterations"], "s": [compact(x) for x in s["workoutSteps"]]}
    o = {"k": s["stepType"]["stepTypeKey"], "e": s["endCondition"]["conditionTypeKey"]}
    for key, val in (("v", s.get("endConditionValue")), ("n", s.get("description")), ("z", s.get("zoneNumber")),
                     ("st", (s.get("strokeType") or {}).get("strokeTypeKey"))):
        if val:
            o[key] = val
    return o


def watch_data():
    """The workouts exactly as skicka_till_garmin.py builds them, keyed by date."""
    sys.path.insert(0, str(GARMIN))
    sys.dont_write_bytecode = True
    import skicka_till_garmin as g
    sessions = g.read_sessions(g.EXCEL)
    built, skipped = g.build_all(sessions, 25)
    workouts, by_date = {}, {}
    for b in built:
        p = b["payload"]
        workouts.setdefault(b["key"], {"name": p["workoutName"], "steps": [compact(x) for x in p["workoutSegments"][0]["workoutSteps"]]})
        by_date[b["date"]] = b["key"]
    status = GARMIN / "garmin_status.json"
    sent = json.loads(status.read_text(encoding="utf8"))["scheduled"] if status.exists() else {}
    synced = datetime.date.fromtimestamp(status.stat().st_mtime) if status.exists() else datetime.date.today()
    not_sent = sorted(d for d, k in by_date.items() if sent.get(d, {}).get("key") != k)
    data = {"synced": synced.isoformat(), "workouts": workouts, "byDate": by_date, "skipped": {s["date"]: why for s, why in skipped}}
    return data, sessions, not_sent


def check(plan, sessions, watch):
    days = {d["date"]: d for w in plan["weeks"] for d in w["days"] if d["kind"] != "rest"}
    excel = {s["date"]: s for s in sessions}
    differ = sorted(d for d in set(days) | set(excel) if d not in days or d not in excel or days[d]["desc"] != excel[d]["desc"])
    no_watch = [d for d in days if d not in watch["byDate"] and d not in watch["skipped"]]
    if differ or no_watch:
        sys.exit("Planen i src/plan.json och Excelfilen skiljer sig åt för " + ", ".join(differ + no_watch) +
                 ".\nUppdatera src/plan.json (be Claude om det) innan du bygger.")
    return len(days)


def inject(page, token, data):
    assert page.count(token) == 1, token
    return page.replace(token, json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publicera", action="store_true", help="spara en commit och skicka upp till GitHub")
    args = ap.parse_args()

    plan = json.loads((SRC / "plan.json").read_text(encoding="utf8"))
    watch, sessions, not_sent = watch_data()
    n = check(plan, sessions, watch)
    page = inject(inject((SRC / "app.html").read_text(encoding="utf8"), "__PLAN_JSON__", plan), "__WATCH_JSON__", watch)

    # Claude artifact version: the page as is.
    ART.mkdir(exist_ok=True)
    (ART / "mot-lidingo.html").write_text(page, encoding="utf8")

    # Web version: title, fonts and the rest of the page's head material move into a real <head>.
    head, body = page.split("<style>", 1)
    head = "\n".join(l for l in head.strip().splitlines() if "claude:" not in l)
    index = SHELL.format(head=head, body="<style>" + body.strip())
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(index, encoding="utf8")
    (DOCS / "sw.js").write_text(SW.format(version=hashlib.sha1(index.encode()).hexdigest()[:10]), encoding="utf8")
    (DOCS / "manifest.webmanifest").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1) + "\n", encoding="utf8")
    (DOCS / ".nojekyll").write_text("")
    for f in (SRC / "static").iterdir():
        shutil.copy2(f, DOCS / f.name)

    print(f"Byggt: {n} pass, {len(watch['byDate'])} på klockan, {len(watch['skipped'])} inte på klockan.")
    print(f"  docs/index.html {len(index.encode()) // 1024} kB, artefakt/mot-lidingo.html")
    if not_sent:
        print(f"Obs: {len(not_sent)} pass skiljer sig från det som ligger i Garmin, till exempel {not_sent[0]}. "
              "Kör uv run skicka_till_garmin.py --alla i Garmin-mappen.")

    if args.publicera:
        subprocess.run(["git", "add", "-A"], cwd=HERE, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE).returncode == 0:
            print("Inget nytt att publicera.")
            return
        subprocess.run(["git", "commit", "-q", "-m", f"Uppdatera portalen {datetime.date.today().isoformat()}"], cwd=HERE, check=True)
        subprocess.run(["git", "push", "-q"], cwd=HERE, check=True)
        print("Publicerat. GitHub Pages visar den nya versionen inom någon minut.")


if __name__ == "__main__":
    main()
