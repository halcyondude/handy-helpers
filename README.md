# CNCF TOC Meeting Change Logger

This tool generates an automated summary of all changes made to the CNCF TOC Project Board (Project #88) and its underlying issues during a specific time window.

It is designed for use during or immediately after TOC meetings to generate accurate, detailed meeting notes without manual tracking.

## 🎯 Purpose

During high-velocity meetings (like TOC "Project Board Walks" or "Heavy Lifting" sessions), many actions occur simultaneously:
*   Issues are dragged between columns (Status changes).
*   Labels are added/removed (e.g., `sandbox`, `stale`).
*   Comments are added to issues.
*   New issues are added to the board.

Capturing these manually is error-prone. This script scans the actual API state to produce a **Markdown Table** of exactly what happened, ready to be pasted into HackMD or Google Docs.

## 📋 Prerequisites

1.  **Python 3.8+**
2.  **GitHub CLI (`gh`)**: This script uses the `gh` CLI to handle authentication securely.
    *   [Install instructions](https://cli.github.com/manual/installation)
3.  **Python Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## 🔐 Authentication

This script leverages your local GitHub CLI credentials.

**IMPORTANT:** You must grant the `read:project` scope to your `gh` token:

```bash
gh auth refresh -h github.com -s read:project
```

Select `GitHub.com`, `HTTPS` if asked.

## 🚀 Usage

The script requires a `--start` time (in 24-hour format, local time). It defaults to "now" for the end time.

### Basic: The meeting just finished
You started the meeting at 10:00 AM and just finished.
```bash
python3 generate_board_report.py --start 10:00
```
*   Generates `board_report_YYYY-MM-DD.md`
*   Generates `board_data_YYYY-MM-DD.json` (Dump of raw API data)

### Custom Output Files
Specify filenames for the report and JSON dump.
```bash
python3 generate_board_report.py --start 09:00 -o my_meeting_notes.md --json-file my_data.json
```

### Disable JSON Dump
If you don't want the raw data file:
```bash
python3 generate_board_report.py --start 09:00 --no-dump-json
```

### Advanced Config
Target a specific board or organization and enable verbose logging.
```bash
python3 generate_board_report.py --start 09:00 --org cncf --project-number 123 -v
```

## 🏗 Design Overview & Architecture

### 1. GraphQL vs. REST
We utilize the **GitHub GraphQL API** rather than the REST API.
*   **Projects V2 Support:** The REST API for "Classic" projects does not work with the new GitHub Projects (V2). GraphQL is the only way to inspect these boards programmatically.
*   **Efficiency:** A single query fetches the Board Item, its Status field, the underlying Issue details, recent Comments, and recent Timeline Events (labels/closes). This reduces what would be hundreds of REST calls into a few paginated GraphQL requests.

### 2. The "Dual Timestamp" Optimization
To handle "Heavy Lifting" sessions efficiently, the script employs a smart filtering strategy:
*   It fetches **all** items on the board (paginated).
*   It checks two timestamps:
    1.  `ProjectV2Item.updatedAt`: Changes when a card is moved or field-edited.
    2.  `Issue.updatedAt`: Changes when a comment is added or label applied.
*   **Optimization:** If *neither* timestamp falls within the meeting window, the item is immediately discarded. This allows the script to process hundreds of board items in seconds while only analyzing the relevant ones.

### 3. Change Detection Logic
The script distinguishes between different types of activity:
*   **Added to Board:** detected via `createdAt` on the Project Item.
*   **Explicit Events:** It scans recent timeline events, including:
    *   Labels (Added/Removed)
    *   Assignments (Assigned/Unassigned)
    *   Milestones (Added/Removed)
    *   Title Renames
    *   State Changes (Closed/Reopened)
    *   *Catches generic "Unknown" events as fallbacks.*
*   **Comments:** It scans recent comments and snippets them (up to 120 chars).
*   **Implicit Moves:** If an item was updated during the meeting but has no explicit events (comments/labels), the script infers it was a **Board Move** (Status change).

### 4. Auth Strategy
We use `subprocess` to call `gh auth token`. This retrieves the OAuth token from your local CLI session. This is superior to using environment variables (`GITHUB_TOKEN`) because it inherits your specific user permissions without managing secrets in the script.

## ⚠️ Limitations

*   **Event Depth:** The script fetches the last 20 timeline events and 10 comments. If an issue had 50 updates *during the meeting*, only the most recent 20 will appear.
*   **Implicit Status Changes:** GitHub's API does not provide a specific "Moved Column" event with a timestamp in the standard timeline. We infer this based on the `updatedAt` timestamp of the card.