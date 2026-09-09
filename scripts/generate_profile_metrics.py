#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape


GRAPHQL_URL = "https://api.github.com/graphql"


def github_data(username: str, token: str) -> dict:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=364)
    query = """
    query ProfilePulse($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
        repositories(
          first: 100
          ownerAffiliations: [OWNER]
          privacy: PUBLIC
          isFork: false
        ) {
          totalCount
        }
      }
    }
    """
    payload = json.dumps(
        {
            "query": query,
            "variables": {
                "login": username,
                "from": start.isoformat(),
                "to": now.isoformat(),
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "white-xr-profile-assets",
        },
        method="POST",
    )
    result = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            if not retryable or attempt == 2:
                raise RuntimeError(
                    f"GitHub GraphQL request failed: HTTP {error.code}"
                ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise RuntimeError(
                    f"GitHub GraphQL request failed after 3 attempts: {error.reason}"
                ) from error
        time.sleep(2**attempt)
    if result is None:
        raise RuntimeError("GitHub GraphQL request returned no result")
    if result.get("errors"):
        messages = "; ".join(item.get("message", "unknown error") for item in result["errors"])
        raise RuntimeError(f"GitHub GraphQL returned errors: {messages}")
    user = result.get("data", {}).get("user")
    if user is None:
        raise RuntimeError(f"GitHub user not found: {username}")
    return user


def metrics(user: dict) -> dict:
    calendar = user["contributionsCollection"]["contributionCalendar"]
    weeks = calendar["weeks"][-52:]
    weekly_counts = [
        sum(day["contributionCount"] for day in week["contributionDays"])
        for week in weeks
    ]
    if len(weekly_counts) < 52:
        weekly_counts = [0] * (52 - len(weekly_counts)) + weekly_counts
    active_days = sum(
        1
        for week in weeks
        for day in week["contributionDays"]
        if day["contributionCount"] > 0
    )
    return {
        "total_contributions": calendar["totalContributions"],
        "active_days": active_days,
        "public_systems": user["repositories"]["totalCount"],
        "weekly_counts": weekly_counts,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d UTC"),
    }


def render_svg(username: str, data: dict, theme: str) -> str:
    dark = theme == "dark"
    foreground = "#F5F5F5" if dark else "#111111"
    text = "#D0D7DE" if dark else "#333333"
    muted = "#8B949E" if dark else "#6B7280"
    border = "#30363D" if dark else "#D0D7DE"
    quiet = "#21262D" if dark else "#EBEDF0"
    values = (
        (str(data["total_contributions"]), "CONTRIBUTIONS / 365D"),
        (str(data["active_days"]), "ACTIVE DAYS"),
        (str(data["public_systems"]), "PUBLIC SYSTEMS"),
        ("PY / C++", "PRIMARY STACK"),
    )
    columns = (70, 350, 630, 910)
    metric_groups = []
    for index, ((value, label), x) in enumerate(zip(values, columns)):
        size = 34 if index < 3 else 30
        metric_groups.append(
            f'<text x="{x}" y="82" class="value" font-size="{size}">{escape(value)}</text>'
        )
        metric_groups.append(
            f'<text x="{x}" y="110" class="label">{escape(label)}</text>'
        )

    counts = data["weekly_counts"]
    maximum = max(counts) if counts else 0
    bars = []
    start_x = 50
    step = 21.15
    baseline = 264
    for index, count in enumerate(counts):
        if count <= 0 or maximum <= 0:
            height = 2
            color = quiet
            opacity = 1.0
        else:
            height = 6 + round(58 * math.pow(count / maximum, 0.58))
            color = foreground
            opacity = 0.32 + 0.68 * math.pow(count / maximum, 0.55)
        x = start_x + index * step
        bars.append(
            f'<rect x="{x:.2f}" y="{baseline - height}" width="13" height="{height}" '
            f'rx="3" fill="{color}" opacity="{opacity:.2f}"/>'
        )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="300" viewBox="0 0 1200 300" role="img" aria-labelledby="title desc">
  <title id="title">WHITE-XR system pulse</title>
  <desc id="desc">Live GitHub activity metrics and a 52 week contribution stream for {escape(username)}.</desc>
  <style>
    text {{ font-family: "JetBrains Mono", Consolas, monospace; fill: {text}; }}
    .value {{ fill: {foreground}; font-weight: 700; letter-spacing: 1px; }}
    .label {{ fill: {muted}; font-size: 12px; letter-spacing: 1.2px; }}
    .meta {{ fill: {muted}; font-size: 11px; letter-spacing: 1px; }}
  </style>
  <rect x="1" y="1" width="1198" height="298" rx="14" fill="none" stroke="{border}" stroke-width="2"/>
  <text x="50" y="35" class="meta">LIVE / 365 DAYS</text>
  <text x="1150" y="35" class="meta" text-anchor="end">UPDATED {escape(data["updated"])}</text>
  {''.join(metric_groups)}
  <path d="M320 52V120M600 52V120M880 52V120" stroke="{border}" stroke-width="1"/>
  <path d="M50 145H1150" stroke="{border}" stroke-width="1"/>
  <text x="50" y="180" class="meta">ACTIVITY_STREAM / 52 WEEKS</text>
  <text x="1150" y="180" class="meta" text-anchor="end">PERCEPTION → GEOMETRY → CONTROL → ACTION</text>
  {''.join(bars)}
</svg>
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate monochrome GitHub profile metrics.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    token = os.environ.get("PROFILE_TOKEN")
    if not token:
        raise SystemExit("PROFILE_TOKEN is required")
    data = metrics(github_data(args.username, token))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        path = args.output_dir / f"profile-system-{theme}.svg"
        path.write_text(render_svg(args.username, data, theme), encoding="utf-8")
        print(f"generated {path}")


if __name__ == "__main__":
    main()
