"""Temporary read-only audit: print only contribution counts, dates, and checks.

Match the requested repository using a SHA-256 fingerprint. No private repository
names, commit identifiers, messages, source files, or credentials enter the report.
"""
import hashlib
import json
import os
from pathlib import Path
import urllib.request

import check_snake


QUERY = """query($login: String!) {
 user(login:$login) { contributionsCollection {
  contributionCalendar { totalContributions weeks { contributionDays {
   date weekday contributionCount contributionLevel
  } } }
  commitContributionsByRepository(maxRepositories:100) {
   repository { id }
   contributions(first:100) {
    nodes { commitCount occurredAt }
    pageInfo {hasNextPage}
   }
  }
 } }
}"""


def main():
    expected = json.loads(os.environ["EXPECTED_COUNTS"])
    fingerprint = os.environ["REPOSITORY_FINGERPRINT"]
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": os.environ["PROFILE_LOGIN"]}}).encode(),
        headers={"Authorization": "Bearer " + os.environ["SNAKE_TOKEN"],
                 "Content-Type": "application/json", "User-Agent": "aggregate-contribution-audit"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError("Repository contribution query denied or incomplete; sensitive response omitted")
    collection = result["data"]["user"]["contributionsCollection"]
    calendar = collection["contributionCalendar"]
    days = [{"date": d["date"], "x": x, "y": d["weekday"], "count": d["contributionCount"],
             "level": check_snake.LEVELS[d["contributionLevel"]]}
            for x, week in enumerate(calendar["weeks"]) for d in week["contributionDays"]]
    contributions = collection["commitContributionsByRepository"]
    matches = [c for c in contributions if c.get("repository") and
               hashlib.sha256(c["repository"]["id"].encode()).hexdigest() == fingerprint]
    if len(matches) != 1:
        raise RuntimeError("Requested repository not uniquely present in owner contribution collection")
    connection = matches[0]["contributions"]
    if connection["pageInfo"]["hasNextPage"]:
        raise RuntimeError("More than 100 contribution dates; pagination required")
    credited = {c["occurredAt"][:10]: c["commitCount"] for c in connection["nodes"]}
    expected_total = sum(expected.values())
    credited_total = sum(credited.values())
    levels = {(d["x"], d["y"]): d["level"] for d in days}
    by_date = {d["date"]: d for d in days}
    published = {}
    svg_grids = []
    for name in check_snake.PALETTES:
        url = f"https://raw.githubusercontent.com/{os.environ['PROFILE_LOGIN']}/{os.environ['PROFILE_LOGIN']}/output/{name}?audit={os.environ['GITHUB_RUN_ID']}"
        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read()
        grid = check_snake.svg_grid(raw, name)
        svg_grids.append(grid)
        published[name] = {"sha256": hashlib.sha256(raw).hexdigest(),
                           "matches_current_calendar": grid == levels,
                           "verified_cells": len(grid)}
    rows = []
    for date in sorted(expected.keys() | credited.keys()):
        day = by_date.get(date)
        rows.append({"date": date, "branch_author_commits": expected.get(date, 0),
                     "official_repository_commits": credited.get(date, 0),
                     "official_all_contributions": day["count"] if day else None,
                     "both_snakes_have_active_cell": bool(day and all(
                         grid.get((day["x"], day["y"]), 0) > 0 for grid in svg_grids))})
    report = {"official_total_contributions": calendar["totalContributions"],
              "branch_author_commit_total": expected_total,
              "official_repository_commit_total": credited_total,
              "daily_attribution_matches": credited == expected,
              "published": published, "dates": rows}
    print(json.dumps(report, indent=2))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf8") as summary:
        summary.write("### Aggregate-only contribution audit\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")
    if credited_total != expected_total or any(not x["matches_current_calendar"] for x in published.values()):
        raise RuntimeError("Contribution or published-image discrepancy detected")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit("Aggregate audit failed; private details omitted") from None
