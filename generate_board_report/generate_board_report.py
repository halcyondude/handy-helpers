import subprocess
import sys
import argparse
import json
import logging
from datetime import datetime, timezone, time
from pathlib import Path

# 3rd party library
try:
    from gql import gql, Client
    from gql.transport.requests import RequestsHTTPTransport
    from gql.transport.exceptions import TransportQueryError
except ImportError:
    print("Error: Missing dependencies.")
    print("Please run: pip install gql requests")
    sys.exit(1)

# Setup Logger
logger = logging.getLogger(__name__)

def get_gh_token():
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except subprocess.CalledProcessError:
        logger.error("Please run 'gh auth login' first.")
        sys.exit(1)

def parse_time_arg(time_str, target_date):
    """Parses HH:MM string and combines with target_date into a timezone-aware datetime."""
    if not time_str: return None
    
    # Create valid datetime from date + time
    try:
        hour, minute = map(int, time_str.split(':'))
        dt = datetime.combine(target_date, time(hour=hour, minute=minute))
        return dt.astimezone() # Ensure it's aware (local system time)
    except ValueError:
        logger.error(f"Invalid time format: {time_str}. Use HH:MM.")
        sys.exit(1)

def get_transport():
    return RequestsHTTPTransport(
        url="https://api.github.com/graphql",
        headers={"Authorization": f"Bearer {get_gh_token()}"},
        verify=True,
        retries=3,
    )

def fetch_raw_items(org, project_number):
    """
    Fetches ALL items from the project board. 
    Does NOT filter by time. Returns raw list of nodes.
    """
    client = Client(transport=get_transport(), fetch_schema_from_transport=False)
    
    query_str = """
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
    """
    
    query = gql(query_str)
    
    all_items = []
    cursor = None
    has_next = True
    page_count = 0

    logger.info(f"Scanning {org} Project #{project_number}...")

    while has_next:
        page_count += 1
        logger.info(f"Fetching page {page_count}...")
        
        try:
            result = client.execute(query, variable_values={
                "org": org, 
                "number": project_number, 
                "cursor": cursor
            })
        except TransportQueryError as e:
            err_data = e.errors[0] if e.errors else {}
            if err_data.get('type') == 'INSUFFICIENT_SCOPES':
                 logger.error("Insufficient GitHub Token Scopes.")
                 logger.error("You need 'read:project' scope to access Project Boards.")
                 logger.error("Run command: gh auth refresh -h github.com -s read:project")
                 sys.exit(1)
            else:
                raise e

        if not result.get('organization'):
            logger.error(f"Organization '{org}' not found or no access.")
            sys.exit(1)
        
        if not result['organization'].get('projectV2'):
             logger.error(f"Project #{project_number} not found in org '{org}'.")
             sys.exit(1)

        data = result['organization']['projectV2']['items']
        all_items.extend(data['nodes'])
        has_next = data['pageInfo']['hasNextPage']
        cursor = data['pageInfo']['endCursor']

    logger.info(f"Fetched {len(all_items)} total items.")
    return all_items

def parse_gh_dt(iso_str):
    """Parses GH ISO strings to UTC datetime."""
    if not iso_str: return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(iso_str.replace('Z', '+00:00'))

def process_items(items, start_time, end_time):
    """
    Filters raw items based on the time window and identifies changes.
    """
    logger.info("Filtering items for changes...")
    
    # Convert window to UTC
    start_utc = start_time.astimezone(timezone.utc)
    end_utc = end_time.astimezone(timezone.utc)
    
    impacted = []

    for item in items:
        content = item.get('content')
        if not content: continue # Skip drafts

        # --- TIMESTAMPS ---
        board_updated = parse_gh_dt(item['updatedAt'])
        board_created = parse_gh_dt(item['createdAt'])
        issue_updated = parse_gh_dt(content['updatedAt'])

        # --- OPTIMIZATION ---
        if board_updated < start_utc and issue_updated < start_utc:
            continue

        changes = []

        # 1. Added to Board?
        if start_utc <= board_created <= end_utc:
            changes.append("🆕 **Added to Board**")

        # 2. Issue Events
        if content.get('timelineItems'):
            for event in content['timelineItems']['nodes']:
                # Skip events without timestamp (shouldn't happen with our query, but safety first)
                if not event.get('createdAt'): continue
                
                evt_time = parse_gh_dt(event['createdAt'])
                if start_utc <= evt_time <= end_utc:
                    actor = event.get('actor', {}).get('login', 'ghost')
                    type_name = event['__typename']
                    
                    # Ignore noisy events
                    if type_name in ['MentionedEvent', 'SubscribedEvent']:
                        continue

                    match type_name:
                        case 'LabeledEvent':
                            changes.append(f"🏷 Added label `{event['label']['name']}`")
                        case 'UnlabeledEvent':
                            changes.append(f"🏷 Removed label `{event['label']['name']}`")
                        case 'ClosedEvent':
                            changes.append(f"🔴 Closed by @{actor}")
                        case 'ReopenedEvent':
                            changes.append(f"🟢 Reopened by @{actor}")
                        case 'AssignedEvent':
                            assignee = event.get('assignee', {}).get('login')
                            changes.append(f"👤 Assigned @{assignee} by @{actor}")
                        case 'UnassignedEvent':
                            assignee = event.get('assignee', {}).get('login')
                            changes.append(f"👤 Unassigned @{assignee} by @{actor}")
                        case 'MilestonedEvent':
                            changes.append(f"⛳ Added to milestone **{event['milestoneTitle']}**")
                        case 'DemilestonedEvent':
                            changes.append(f"⛳ Removed from milestone **{event['milestoneTitle']}**")
                        case 'RenamedTitleEvent':
                            changes.append(f"✏️ Renamed from *'{event['previousTitle']}'*")
                        case _:
                            # Catch-all for unknown events
                            changes.append(f"❓ {type_name} by @{actor}")

        # 3. Comments
        if content.get('comments'):
            for comment in content['comments']['nodes']:
                c_time = parse_gh_dt(comment['createdAt'])
                if start_utc <= c_time <= end_utc:
                    snippet = comment['bodyText'][:400].replace('\n', ' ')
                    changes.append(f"💬 Comment (@{comment['author']['login']}): \"{snippet}...\"")

        # 4. Board Moves (Implicit)
        if not changes:
             if start_utc <= board_updated <= end_utc:
                changes.append("🔄 Board Item Updated")

        # Determine Board Status
        status = "No Status"
        for fv in item['fieldValues']['nodes']:
             if 'field' in fv and fv['field']['name'] == 'Status':
                status = fv['name']

        if changes:
            impacted.append({
                'repo': content['repository']['name'],
                'number': content['number'],
                'title': content['title'],
                'url': content['url'],
                'state': content['state'],
                'status': status,
                'changes': changes,
                'board_updated': board_updated
            })

    return impacted

def generate_markdown(items, start_dt, end_dt, org, project_number, output_path):
    """Generates the Markdown report and writes to file."""
    
    start_str = start_dt.strftime('%H:%M')
    end_str = end_dt.strftime('%H:%M')
    # Use the date from start_dt for the report header, as that's the "target date"
    date_str = start_dt.strftime('%Y-%m-%d')

    lines = []
    lines.append(f"# Project Board Change Log")
    lines.append(f"")
    lines.append(f"**Date:** {date_str}")
    lines.append(f"**Window:** {start_str} to {end_str}")
    lines.append(f"**Project:** [{org} Project #{project_number}](https://github.com/orgs/{org}/projects/{project_number})")
    lines.append(f"")
        
    if not items:
        lines.append("\n*No changes detected in the specified timeframe.*")
    else:
        # Fixed 3-column table: Issue, Board Status, Actions Taken
        lines.append("\n| Issue | Board Status | Actions Taken |")
        lines.append("|---|---|---|")

        # Sort: Chronological order (oldest first)
        items.sort(key=lambda x: x['board_updated'])

        for item in items:
            repo = item['repo']
                        
            # prevent table/formatting breakage from titles like "<QHTTPX>"" in issue titles 
            # "| **[Sandbox] <QHTTPX>**<br>[sandbox#453](https://github.com/cncf/sandbox/issues/453) |"

            safe_title = item['title'].replace('|', '&#124;').replace('<', '&lt;').replace('>', '&gt;')
            safe_status = item['status'].replace('|', '&#124;').replace('<', '&lt;').replace('>', '&gt;')

            issue_cell = f"**{safe_title}**<br>[{repo}#{item['number']}]({item['url']})"
            
            change_log = "<br>".join(item['changes']).replace('|', '&#124;')
            lines.append(f"| {issue_cell} | **{safe_status}** | {change_log} |")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
        f.write("\n")
    
    return len(items)

def save_json(data, output_path):
    """Dumps raw data to JSON file."""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"Raw data dumped to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate TOC Meeting Change Log")
    
    # Time Args
    parser.add_argument('--date', type=str, help="Date YYYY-MM-DD (defaults to today)")
    parser.add_argument('--start', type=str, required=True, help="Start time HH:MM (Local)")
    parser.add_argument('--end', type=str, help="End time HH:MM (Local). Defaults to now.")
    
    # Config Args
    parser.add_argument('--org', type=str, default="cncf", help="GitHub Organization (default: cncf)")
    parser.add_argument('--project-number', type=int, default=88, help="Project V2 Board Number (default: 88)")
    
    # Output Args
    parser.add_argument('--output', '-o', type=str, help="Markdown report output path (default: board_report_YYYY-MM-DD.md)")
    parser.add_argument('--json-file', type=str, help="JSON data output path (default: board_data_YYYY-MM-DD.json)")
    parser.add_argument('--no-dump-json', action='store_true', help="Disable JSON data dump")
    
    # Debug
    parser.add_argument('--verbose', '-v', action='store_true', help="Enable verbose debug logging")

    args = parser.parse_args()

    # Configure Logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S',
        stream=sys.stderr
    )

    # Date Parsing
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        target_date = datetime.now().date()

    # Defaults for filenames based on target_date
    date_str = target_date.strftime('%Y-%m-%d')
    output_md = args.output if args.output else f"board_report_{date_str}.md"
    output_json = args.json_file if args.json_file else f"board_data_{date_str}.json"

    # Calculate Times
    start_dt = parse_time_arg(args.start, target_date)
    logger.info(f"Interpreting input time '{args.start}' as {start_dt.tzname()} time.")
    
    if args.end:
        end_dt = parse_time_arg(args.end, target_date)
    else:
        # If no end time provided:
        # If date is today, default to now().
        # If date is past, default to end of day (23:59).
        if target_date == datetime.now().date():
            end_dt = datetime.now().astimezone()
        else:
             # Default to end of that day
            end_dt = datetime.combine(target_date, datetime.max.time()).replace(microsecond=0).astimezone()

    
    # 1. Fetch
    raw_data = fetch_raw_items(args.org, args.project_number)

    # 2. Dump JSON (unless disabled)
    if not args.no_dump_json:
        save_json(raw_data, output_json)

    # 3. Process
    impacted_items = process_items(raw_data, start_dt, end_dt)

    # 4. Generate Report
    count = generate_markdown(impacted_items, start_dt, end_dt, args.org, args.project_number, output_md)
    
    print(f"✅ Report generated: {output_md} ({count} changes found)")