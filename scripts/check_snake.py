"""Verify contribution aggregates and the pinned Platane/snk SVG format.

Never request repository names or commit details. Keep daily snapshots in RUNNER_TEMP;
publish only aggregate counts and hashes, never credentials or API response bodies.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


QUERY = """query($login: String!) {
  viewer { login }
  user(login: $login) { contributionsCollection {
    restrictedContributionsCount
    contributionCalendar { totalContributions weeks { contributionDays {
      date weekday contributionCount contributionLevel
    } } }
  } }
}"""
LEVELS = {name: i for i, name in enumerate([
    "NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"
])}
PALETTES = {
    "github-contribution-grid-snake.svg": ("#111111", ["#EBEDF0", "#D0D7DE", "#A8B0B8", "#6B7280", "#111111"]),
    "github-contribution-grid-snake-dark.svg": ("#F5F5F5", ["#161B22", "#30363D", "#545D66", "#9AA1A9", "#E6E8EA"]),
}


def calendar(token, login):
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json", "User-Agent": "profile-snake-check"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                scopes = set(response.headers.get("X-OAuth-Scopes", "").replace(" ", "").split(","))
                result = json.load(response)
            break
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"GitHub aggregate request failed: HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError("GitHub aggregate request timed out or could not connect") from None
        time.sleep(2 ** attempt)
    if result.get("errors"):
        raise RuntimeError("GitHub GraphQL returned errors; response omitted for privacy")
    collection = result["data"]["user"]["contributionsCollection"]
    days = [{"date": d["date"], "x": x, "y": d["weekday"],
             "count": d["contributionCount"], "level": LEVELS[d["contributionLevel"]]}
            for x, w in enumerate(collection["contributionCalendar"]["weeks"])
            for d in w["contributionDays"]]
    total = collection["contributionCalendar"]["totalContributions"]
    if not days or sum(d["count"] for d in days) != total:
        raise RuntimeError("Calendar total does not match daily contribution counts")
    return {
        "owner_matches": result["data"]["viewer"]["login"].lower() == login.lower(),
        "read_user_scope": bool({"read:user", "user"} & scopes),
        "repo_scope": "repo" in scopes,
        "total": total, "restricted": collection["restrictedContributionsCount"],
        "active_days": sum(d["count"] > 0 for d in days), "days": days,
    }


def check_visibility(source, baseline):
    if not source["owner_matches"]:
        raise RuntimeError("SNAKE_TOKEN must belong to the profile owner")
    if not source["read_user_scope"]:
        raise RuntimeError("SNAKE_TOKEN needs a classic PAT with read:user (or user); repo alone is insufficient")
    source_days = {d["date"]: d["count"] for d in source["days"]}
    base_days = {d["date"]: d["count"] for d in baseline["days"]}
    if source_days.keys() != base_days.keys():
        raise RuntimeError("Calendar date ranges changed during verification; retry the workflow")
    # Different viewers can receive different daily attribution. Do not require
    # the workflow-token calendar to be a cell-by-cell subset of the owner's.
    # SVG verification below uses only the owner's actual calendar.
    additional = source["total"] - baseline["total"]
    if additional < 0:
        raise RuntimeError("Owner calendar total is below the workflow-token total; retry")
    if additional <= 0 and source["restricted"] <= 0:
        raise RuntimeError("No private-contribution coverage could be verified; check profile visibility and SNAKE_TOKEN. Keeping previous images")
    return additional


def svg_grid(svg, filename):
    root = ET.fromstring(svg)
    style = root.find("{http://www.w3.org/2000/svg}style")
    if style is None or "@keyframes" not in (style.text or ""):
        raise RuntimeError("Missing SVG animation")
    css = style.text
    snake, colors = PALETTES[filename]
    for key, color in [("cs", snake), *[(f"c{i}", c) for i, c in enumerate(colors)]]:
        if not re.search(r"--" + key + ":" + re.escape(color) + r"[;}]", css, re.I):
            raise RuntimeError("SVG monochrome palette changed")
    grid = {}
    for rect in root.findall("{http://www.w3.org/2000/svg}rect"):
        classes = rect.get("class", "").split()
        if not classes or classes[0] != "c":
            continue
        x, y = int(rect.get("x")), int(rect.get("y"))
        if (x - 2) % 16 or (y - 2) % 16:
            raise RuntimeError("Unexpected SVG grid geometry")
        position = ((x - 2) // 16, (y - 2) // 16)
        if position in grid:
            raise RuntimeError("Duplicate SVG grid cell")
        level = 0
        if len(classes) == 2:
            match = re.search(r"\.c\." + re.escape(classes[1]) + r"\{fill:var\(--c([1-4])\)", css)
            if not match:
                raise RuntimeError("Missing SVG contribution color rule")
            level = int(match.group(1))
        elif len(classes) != 1:
            raise RuntimeError("Unexpected SVG cell classes")
        grid[position] = level
    return grid


def verify(snapshot, output_dir):
    source = snapshot["source"]
    additional = check_visibility(source, snapshot["baseline"])
    expected = {(d["x"], d["y"]): d["level"] for d in source["days"]}
    hashes = {}
    for filename in PALETTES:
        raw = (output_dir / filename).read_bytes()
        if svg_grid(raw, filename) != expected:
            raise RuntimeError("SVG cells differ from the verified calendar; retry if contributions changed during generation")
        hashes[filename] = hashlib.sha256(raw).hexdigest()
    return {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "total_contributions": source["total"],
        "workflow_token_contributions": snapshot["baseline"]["total"],
        "additional_contributions_with_owner_token": additional,
        "restricted_contributions": source["restricted"],
        "active_days": source["active_days"], "verified_cells_per_svg": len(expected),
        "from": source["days"][0]["date"], "to": source["days"][-1]["date"],
        "calendar_sha256": hashlib.sha256(json.dumps(source["days"], sort_keys=True).encode()).hexdigest(),
        "svg_sha256": hashes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["snapshot", "verify"])
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    if args.mode == "snapshot":
        token = os.environ.get("SNAKE_TOKEN")
        if not token:
            raise RuntimeError("SNAKE_TOKEN is missing; refusing a silent public-only fallback")
        login = os.environ["PROFILE_LOGIN"]
        data = {"source": calendar(token, login),
                "baseline": calendar(os.environ["ACTIONS_TOKEN"], login)}
        additional = check_visibility(data["source"], data["baseline"])
        args.snapshot.write_text(json.dumps(data), encoding="utf-8")
        for name, item in data.items():
            print(name + ": " + json.dumps({k: v for k, v in item.items() if k != "days"}, sort_keys=True))
        print(f"Additional contributions with owner token: {additional}")
    else:
        result = verify(json.loads(args.snapshot.read_text(encoding="utf-8")), args.output_dir)
        report = json.dumps(result, indent=2) + "\n"
        (args.output_dir / "snake-verification.json").write_text(report, encoding="utf-8")
        print(report)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("### Verified contribution snake\n\n```json\n" + report + "```\n")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit("Contribution verification failed; sensitive diagnostic details omitted") from None
