# AWS Support Cases Exporter

> **Warning:** This tool uses undocumented internal AWS APIs that may change or break at any time without notice. It is not endorsed by or affiliated with AWS. Use at your own risk.

> **Disclaimer:** This project was 100% vibecoded with [Claude Code](https://claude.ai/claude-code). No humans wrote any of this code. It worked on the first real run (after a couple of API response format fixes). Review before trusting.

Exports all AWS Support cases (metadata + full conversation threads) to JSON.

AWS does not expose its Support Center data through a public API unless you have a Business or Enterprise support plan. This tool works around that by using the same internal API that the AWS Support Console uses in your browser, authenticated via your existing browser session cookies.

## Prerequisites

- Python 3.8+
- `requests` and `botocore` packages

```bash
pip install requests botocore
```

## Getting your session credentials

The script authenticates by calling the same credential-refresh endpoint the console uses (`/support/tb/creds`). You need two values from your browser:

1. Open the [AWS Support Console](https://support.console.aws.amazon.com/support/home) in your browser
2. Open DevTools (F12) > **Network** tab
3. Reload the page or navigate within the Support Console
4. Filter requests for `tb/creds` — find the POST request to `/support/tb/creds`
5. From that request, copy:
   - The **Cookie** header value (the full string after `-b` if viewing as cURL) — save it to a file called `cookies.txt`
   - The **`x-csrf-token`** request header value

## Usage

### Basic: export all cases

```bash
python3 download_aws_support_cases.py \
  --cookies-file cookies.txt \
  --csrf-token 'eyJrZXl...'
```

### Only fetch the case list (no conversations)

```bash
python3 download_aws_support_cases.py \
  --cookies-file cookies.txt \
  --csrf-token 'eyJrZXl...' \
  --cases-only
```

### Resume after interruption

If the script is interrupted, it saves progress periodically. You can resume fetching case details from where you left off:

```bash
python3 download_aws_support_cases.py \
  --cookies-file cookies.txt \
  --csrf-token 'eyJrZXl...' \
  --resume aws_support_cases_list.json \
  --skip 15
```

### Custom output path

```bash
python3 download_aws_support_cases.py \
  --cookies-file cookies.txt \
  --csrf-token 'eyJrZXl...' \
  -o my_cases.json
```

### Pass cookies inline

```bash
python3 download_aws_support_cases.py \
  --cookies 'aws-consoleInfo=...; aws-creds=...; aws-userInfo=...' \
  --csrf-token 'eyJrZXl...'
```

## Output

The script produces:

| File | Contents |
|------|----------|
| `aws_support_cases.json` | Full export: metadata, all cases, and all conversation messages |
| `aws_support_cases_list.json` | Case summaries only (saved early as a checkpoint) |
| `aws_support_cases_progress.json` | Incremental progress (saved every 10 cases) |

The main output structure:

```json
{
  "metadata": {
    "downloaded_at": "2026-04-20T15:10:50.239440+00:00",
    "total_cases": 29,
    "errors": 0
  },
  "cases": [
    {
      "caseId": "case-...",
      "displayId": "176962049400621",
      "subject": "Experiencing network faults",
      "status": "resolved",
      "serviceCode": "amazon-elastic-compute-cloud-linux",
      "categoryCode": "instance-issue",
      "severityCode": "normal",
      "submittedBy": "user@example.com",
      "timeCreated": "2026-01-28T17:14:54.089Z",
      "recentCommunications": {
        "communications": [
          {
            "body": "...",
            "submittedBy": "...",
            "timeCreated": "..."
          }
        ]
      },
      "_communicationCount": 6
    }
  ]
}
```

## How it works

1. Calls `/support/tb/creds` with your browser session cookies to obtain temporary AWS credentials (access key, secret key, session token)
2. Uses those credentials to sign requests (AWS SigV4) to the internal Support API at `support.us-east-1.amazonaws.com`
3. Paginates through `SearchForCases` to build a complete case list
4. For each case, calls `DescribeCases` to get full details and the first page of communications
5. If a case has more communications, paginates them via `DescribeCommunications`
6. Credentials are automatically refreshed before they expire

## Notes

- Session cookies typically remain valid for several hours. If they expire mid-run, log in again in the browser and provide fresh cookies with `--resume`.
- The script includes rate limiting (0.3-0.5s between requests) to avoid throttling.
- Delete `cookies.txt` after use since it contains session credentials.
