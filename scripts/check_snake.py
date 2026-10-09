"""Inspect only contribution aggregates; never request repository or commit details."""
import json
import os
import urllib.request


QUERY = """query($login: String!) {
  viewer { login }
  user(login: $login) { contributionsCollection {
    restrictedContributionsCount hasAnyRestrictedContributions
    contributionCalendar { totalContributions weeks { contributionDays {
      date weekday contributionCount contributionLevel
    } } }
  } }
}"""


def calendar(token, login):
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json", "User-Agent": "profile-snake-check"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        scopes = response.headers.get("X-OAuth-Scopes", "")
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError("GitHub GraphQL returned errors; response omitted for privacy")
    collection = result["data"]["user"]["contributionsCollection"]
    days = [d for w in collection["contributionCalendar"]["weeks"]
            for d in w["contributionDays"]]
    return {
        "owner_matches": result["data"]["viewer"]["login"].lower() == login.lower(),
        "read_user_scope": bool({"read:user", "user"} & set(scopes.replace(" ", "").split(","))),
        "scopes_header_present": bool(scopes),
        "total": collection["contributionCalendar"]["totalContributions"],
        "restricted": collection["restrictedContributionsCount"],
        "has_restricted": collection["hasAnyRestrictedContributions"],
        "active_days": sum(d["contributionCount"] > 0 for d in days),
    }


if __name__ == "__main__":
    login = os.environ["PROFILE_LOGIN"]
    for label, name in [("SNAKE_TOKEN", "SNAKE_TOKEN"), ("workflow_token", "ACTIONS_TOKEN")]:
        token = os.environ.get(name)
        if not token:
            print(label + ": missing")
            continue
        try:
            print(label + ": " + json.dumps(calendar(token, login), sort_keys=True))
        except Exception:
            raise SystemExit(label + ": aggregate request failed (sensitive details omitted)")
