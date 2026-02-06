# CNCF Board Report Generator

A Python script to generate a change log for a GitHub Project Board. It's useful for notetakers in meetings where Project boards are updated.It identifies items added, moved, or modified during a specific time window (e.g., during a meeting), labels added or removed, and comments made to issues.

## Prerequisites

* Python 3.10+
* [`uv`](https://github.com/astral-sh/uv) (recommended)
* [`gh` CLI](https://cli.github.com/) authenticated

## Authentication

The script uses your local `gh` CLI credentials. You must have the `read:project` scope to access GitHub Projects (V2).

```bash
# Check your status
gh auth status

# If missing 'read:project', refresh scopes:
gh auth refresh -h github.com -s read:project
```

## Usage

Running with `uv` (auto-manages venv and dependencies):

```bash
# Generate report for today's meeting (UTC/Local auto-handled)
uv run python3 generate_board_report.py --start 11:00

# Generate report for a specific past date
uv run python3 generate_board_report.py --date 2026-02-05 --start 11:00 --end 12:30

# Full options
uv run python3 generate_board_report.py --help
```

### Command Help

```text
usage: generate_board_report.py [-h] [--date DATE] --start START [--end END]
                                [--org ORG] [--project-number PROJECT_NUMBER]
                                [--output OUTPUT] [--json-file JSON_FILE]
                                [--no-dump-json] [--verbose]

Generate TOC Meeting Change Log

options:
  -h, --help            show this help message and exit
  --date DATE           Date YYYY-MM-DD (defaults to today)
  --start START         Start time HH:MM (Local)
  --end END             End time HH:MM (Local). Defaults to now.
  --org ORG             GitHub Organization (default: cncf)
  --project-number PROJECT_NUMBER
                        Project V2 Board Number (default: 88)
  --output OUTPUT, -o OUTPUT
                        Markdown report output path (default:
                        board_report_YYYY-MM-DD.md)
  --json-file JSON_FILE
                        JSON data output path (default: board_data_YYYY-MM-
                        DD.json)
  --no-dump-json        Disable JSON data dump
  --verbose, -v         Enable verbose debug logging
```

## How It Works

1. **Fetches Data**: Uses the GitHub GraphQL API to fetch *all* items from the project board.
2. **Filters**: Processes items to find events (added, moved, labeled, commented) within the specified `--start` and `--end` time window.
3. **Outputs**:
    * **JSON**: Dumps raw API data to `board_data_YYYY-MM-DD.json` (unless disabled).
    * **Markdown**: Writes a formatted report to `board_report_YYYY-MM-DD.md`.

### GraphQL Query

To inspect the exact data being requested, here is the GraphQL query used by the script:

```graphql
query($org: String!, $number: Int!, $cursor: String) {
  organization(login: $org) {
    projectV2(number: $number) {
      title
      url
      items(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          updatedAt # When the CARD was moved/edited
          createdAt # When the CARD was added to board
          
          fieldValues(first: 20) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2FieldCommon { name } }
              }
            }
          }
          
          content {
            ... on Issue {
              title
              number
              url
              state
              updatedAt # When the ISSUE was commented/labeled/etc
              repository { name }
              
              comments(last: 10) {
                nodes {
                  createdAt
                  bodyText
                  author { login }
                }
              }

              timelineItems(last: 20) {
                nodes {
                  __typename
                  ... on LabeledEvent {
                    createdAt
                    actor { login }
                    label { name }
                  }
                  ... on UnlabeledEvent {
                    createdAt
                    actor { login }
                    label { name }
                  }
                  ... on ClosedEvent {
                    createdAt
                    actor { login }
                  }
                  ... on ReopenedEvent {
                    createdAt
                    actor { login }
                  }
                  ... on AssignedEvent {
                    createdAt
                    actor { login }
                    assignee { ... on User { login } }
                  }
                  ... on UnassignedEvent {
                    createdAt
                    actor { login }
                    assignee { ... on User { login } }
                  }
                  ... on MilestonedEvent {
                    createdAt
                    actor { login }
                    milestoneTitle
                  }
                  ... on DemilestonedEvent {
                    createdAt
                    actor { login }
                    milestoneTitle
                  }
                  ... on RenamedTitleEvent {
                    createdAt
                    actor { login }
                    previousTitle
                    currentTitle
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

## Contributions

Contributions are welcome! Please submit a pull request.

## License

Apache 2.0
