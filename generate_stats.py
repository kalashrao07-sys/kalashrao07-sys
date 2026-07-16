#!/usr/bin/env python3
"""
generate_stats.py

Queries the GitHub GraphQL API for live profile stats (repos, stars,
followers, commits, top languages) plus a computed "time in degree" line,
and renders a dark, monospace SVG stat card at stats/kalash_stats.svg.

Run locally:
    ACCESS_TOKEN=ghp_xxx python3 generate_stats.py

Run in CI: see .github/workflows/update-stats.yml (runs this on a schedule
and commits the updated SVG back to the repo).
"""

import os
import sys
import datetime
from dateutil.relativedelta import relativedelta
import requests

# ---------------------------------------------------------------------------
# Config — the only 3 lines you should need to touch
# ---------------------------------------------------------------------------
GITHUB_USERNAME = "kalashrao07-sys"
DEGREE_START_DATE = datetime.date(2024, 8, 1)  # <-- replace with your actual first day of college
OUTPUT_SVG_PATH = "stats/kalash_stats.svg"

GRAPHQL_URL = "https://api.github.com/graphql"
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN")

if not ACCESS_TOKEN:
    sys.exit("ACCESS_TOKEN environment variable is not set.")

HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}"}


def run_query(query: str, variables: dict) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        headers=HEADERS,
        json={"query": query, "variables": variables},
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"GraphQL request failed [{resp.status_code}]: {resp.text}")
    payload = resp.json()
    if "errors" in payload:
        sys.exit(f"GraphQL returned errors: {payload['errors']}")
    return payload["data"]


PROFILE_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    repositoriesContributedTo(
      first: 1
      contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, REPOSITORY]
    ) {
      totalCount
    }
    repositories(
      first: 100
      after: $after
      ownerAffiliation: [OWNER]
      isFork: false
      privacy: PUBLIC
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 5, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""

COMMITS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
    }
  }
}
"""


def fetch_profile() -> dict:
    followers = 0
    contributed_to = 0
    created_at = None
    total_repos = 0
    total_stars = 0
    language_sizes: dict[str, int] = {}
    after = None

    while True:
        data = run_query(PROFILE_QUERY, {"login": GITHUB_USERNAME, "after": after})
        user = data["user"]
        created_at = created_at or user["createdAt"]
        followers = user["followers"]["totalCount"]
        contributed_to = user["repositoriesContributedTo"]["totalCount"]
        repos = user["repositories"]
        total_repos = repos["totalCount"]

        for repo in repos["nodes"]:
            total_stars += repo["stargazerCount"]
            for edge in repo["languages"]["edges"]:
                name = edge["node"]["name"]
                language_sizes[name] = language_sizes.get(name, 0) + edge["size"]

        if repos["pageInfo"]["hasNextPage"]:
            after = repos["pageInfo"]["endCursor"]
        else:
            break

    return {
        "created_at": created_at,
        "followers": followers,
        "contributed_to": contributed_to,
        "total_repos": total_repos,
        "total_stars": total_stars,
        "language_sizes": language_sizes,
    }


def fetch_total_commits(created_at_iso: str) -> int:
    """Sums public commit contributions year-by-year (GraphQL caps each
    contributionsCollection window at 1 year)."""
    start_year = int(created_at_iso[:4])
    current_year = datetime.datetime.utcnow().year
    total = 0
    for year in range(start_year, current_year + 1):
        window_from = f"{year}-01-01T00:00:00Z"
        window_to = f"{year}-12-31T23:59:59Z"
        data = run_query(
            COMMITS_QUERY,
            {"login": GITHUB_USERNAME, "from": window_from, "to": window_to},
        )
        total += data["user"]["contributionsCollection"]["totalCommitContributions"]
    return total


def top_languages(language_sizes: dict, limit: int = 4):
    ranked = sorted(language_sizes.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(v for _, v in ranked) or 1
    return [(name, round(size / total * 100, 1)) for name, size in ranked[:limit]]


def time_in_degree() -> str:
    diff = relativedelta(datetime.date.today(), DEGREE_START_DATE)
    return f"{diff.years} yrs, {diff.months} mo, {diff.days} days"


SVG_TEMPLATE = """<svg width="480" height="{height}" viewBox="0 0 480 {height}" xmlns="http://www.w3.org/2000/svg">
  <style>
    .bg {{ fill: #1a1b27; }}
    .border {{ fill: none; stroke: #414868; stroke-width: 1; }}
    .title {{ font: 600 14px 'Fira Code', Consolas, monospace; fill: #7aa2f7; }}
    .label {{ font: 400 13px 'Fira Code', Consolas, monospace; fill: #bb9af7; }}
    .value {{ font: 400 13px 'Fira Code', Consolas, monospace; fill: #c0caf5; }}
    .dim {{ font: 400 13px 'Fira Code', Consolas, monospace; fill: #565f89; }}
  </style>
  <rect class="bg" width="480" height="{height}" rx="8"/>
  <rect class="border" x="0.5" y="0.5" width="479" height="{height_minus1}" rx="8"/>
  <text x="20" y="32" class="title">kalash@KLE-Belagavi — GitHub Stats (live)</text>
  <line x1="20" y1="42" x2="460" y2="42" stroke="#414868" stroke-width="1"/>
{rows}
</svg>
"""

ROW_TEMPLATE = (
    '  <text x="20" y="{y}">'
    '<tspan class="label">{label}</tspan>'
    '<tspan class="dim"> {dots} </tspan>'
    '<tspan class="value">{value}</tspan>'
    '</text>\n'
)


def build_rows(fields):
    rows = ""
    y = 66
    for label, value in fields:
        dots = "." * max(2, 26 - len(label))
        rows += ROW_TEMPLATE.format(y=y, label=label, dots=dots, value=value)
        y += 24
    return rows, y


def build_svg(stats: dict) -> str:
    langs = top_languages(stats["language_sizes"])
    lang_str = " · ".join(f"{name} {pct}%" for name, pct in langs) if langs else "n/a"

    fields = [
        ("Repos", str(stats["total_repos"])),
        ("Contributed to", str(stats["contributed_to"])),
        ("Stars", str(stats["total_stars"])),
        ("Followers", str(stats["followers"])),
        ("Commits (public)", str(stats["total_commits"])),
        ("Top languages", lang_str),
        ("Time in degree", time_in_degree()),
    ]
    rows, last_y = build_rows(fields)
    height = last_y + 14
    return SVG_TEMPLATE.format(height=height, height_minus1=height - 1, rows=rows)


EVENTS_URL = f"https://api.github.com/users/{GITHUB_USERNAME}/events/public"
README_PATH = "README.md"
ACTIVITY_START = "<!--START_SECTION:activity-->"
ACTIVITY_END = "<!--END_SECTION:activity-->"


def describe_event(event: dict) -> str | None:
    etype = event.get("type")
    repo = event.get("repo", {}).get("name", "?")

    if etype == "PushEvent":
        n = len(event.get("payload", {}).get("commits", []) or [])
        n = max(n, 1)
        return f"🔨 Pushed {n} commit(s) to `{repo}`"
    if etype == "CreateEvent":
        ref_type = event.get("payload", {}).get("ref_type", "repository")
        return f"🎉 Created {ref_type} in `{repo}`"
    if etype == "PullRequestEvent":
        action = event.get("payload", {}).get("action", "updated")
        return f"🔀 {action.capitalize()} a pull request in `{repo}`"
    if etype == "IssuesEvent":
        action = event.get("payload", {}).get("action", "updated")
        return f"📌 {action.capitalize()} an issue in `{repo}`"
    if etype == "WatchEvent":
        return f"⭐ Starred `{repo}`"
    if etype == "ForkEvent":
        return f"🍴 Forked `{repo}`"
    return None


def fetch_recent_activity(limit: int = 5) -> list[str]:
    resp = requests.get(EVENTS_URL, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        print(f"Warning: could not fetch events [{resp.status_code}], skipping activity section")
        return []

    lines = []
    for event in resp.json():
        desc = describe_event(event)
        if desc:
            lines.append(f"- {desc}")
        if len(lines) >= limit:
            break
    return lines


def update_readme_activity(lines: list[str]):
    if not os.path.exists(README_PATH):
        print(f"Warning: {README_PATH} not found, skipping activity update")
        return
    if not lines:
        return

    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if ACTIVITY_START not in content or ACTIVITY_END not in content:
        print("Warning: activity markers not found in README.md, skipping")
        return

    before = content.split(ACTIVITY_START)[0]
    after = content.split(ACTIVITY_END)[1]
    new_block = ACTIVITY_START + "\n" + "\n".join(lines) + "\n" + ACTIVITY_END
    new_content = before + new_block + after

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"Updated {README_PATH} activity section ({len(lines)} events)")


def main():
    profile = fetch_profile()
    profile["total_commits"] = fetch_total_commits(profile["created_at"])
    svg = build_svg(profile)

    os.makedirs(os.path.dirname(OUTPUT_SVG_PATH), exist_ok=True)
    with open(OUTPUT_SVG_PATH, "w", encoding="utf-8") as f:
        f.write(svg)

    print(f"Wrote {OUTPUT_SVG_PATH}")
    print(
        f"  Repos: {profile['total_repos']}  Stars: {profile['total_stars']}  "
        f"Commits: {profile['total_commits']}  Followers: {profile['followers']}"
    )

    activity_lines = fetch_recent_activity()
    update_readme_activity(activity_lines)


if __name__ == "__main__":
    main()
