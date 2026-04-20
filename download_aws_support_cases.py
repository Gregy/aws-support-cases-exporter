#!/usr/bin/env python3
"""
Download all AWS Support cases (conversations + metadata) via the internal console API.

Usage:
  1. Open AWS Support Console in your browser (support.console.aws.amazon.com)
  2. Open DevTools > Network tab
  3. Find the POST request to /support/tb/creds
  4. Right-click > Copy as cURL
  5. Save the cookie string to a file: cookies.txt
  6. Save the x-csrf-token value

  Run:
    python3 download_aws_support_cases.py --cookies-file cookies.txt --csrf-token "eyJ..."

  Or pass cookies inline:
    python3 download_aws_support_cases.py --cookies "aws-consoleInfo=...; aws-creds=...; ..." --csrf-token "eyJ..."
"""

import argparse
import datetime
import json
import os
import sys
import time

import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials


ENDPOINT = "https://support.us-east-1.amazonaws.com/"
CREDS_URL = "https://support.console.aws.amazon.com/support/tb/creds"
REGION = "us-east-1"
SERVICE = "support"
CONTENT_TYPE = "application/x-amz-json-1.1"

BROWSER_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.8",
    "origin": "https://support.console.aws.amazon.com",
    "referer": "https://support.console.aws.amazon.com/",
    "sec-ch-ua": '"Brave";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
    "sec-gpc": "1",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "x-amz-user-agent": "aws-sdk-js/2.1691.0 promise",
}


class CredentialManager:
    """Manages AWS temporary credentials, refreshing via the console's /tb/creds endpoint."""

    def __init__(self, cookies, csrf_token):
        self.cookies = cookies
        self.csrf_token = csrf_token
        self._credentials = None
        self._expiration = None
        # Refresh 2 minutes before expiry
        self._refresh_buffer = datetime.timedelta(minutes=2)

    def _fetch_credentials(self):
        """Call /support/tb/creds to get fresh temporary credentials."""
        print("  Refreshing credentials via /support/tb/creds...", flush=True)
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.8",
            "origin": "https://support.console.aws.amazon.com",
            "referer": "https://support.console.aws.amazon.com/support/home",
            "sec-ch-ua": '"Brave";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sec-gpc": "1",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "x-csrf-token": self.csrf_token,
            "x-retries": "0",
            "content-length": "0",
        }

        resp = requests.post(
            CREDS_URL,
            headers=headers,
            cookies=self._parse_cookies(),
            timeout=30,
        )

        if resp.status_code != 200:
            raise Exception(
                f"Failed to refresh credentials: {resp.status_code} {resp.text[:300]}"
            )

        data = resp.json()
        self._credentials = Credentials(
            access_key=data["accessKeyId"],
            secret_key=data["secretAccessKey"],
            token=data.get("sessionToken"),
        )

        exp_str = data.get("expiration", "")
        if exp_str:
            # Parse ISO format like "2026-04-20T15:09:14.000Z"
            self._expiration = datetime.datetime.fromisoformat(
                exp_str.replace("Z", "+00:00")
            )
        else:
            # Assume 15 minutes if no expiration given
            self._expiration = datetime.datetime.now(
                datetime.timezone.utc
            ) + datetime.timedelta(minutes=15)

        print(
            f"    Credentials refreshed. Expires: {self._expiration.isoformat()}",
            flush=True,
        )
        return self._credentials

    def _parse_cookies(self):
        """Parse cookie string into a dict for requests."""
        cookies = {}
        for part in self.cookies.split(";"):
            part = part.strip()
            if "=" in part:
                key, _, value = part.partition("=")
                cookies[key.strip()] = value.strip()
        return cookies

    def get_credentials(self):
        """Get current credentials, refreshing if expired or about to expire."""
        now = datetime.datetime.now(datetime.timezone.utc)
        if (
            self._credentials is None
            or self._expiration is None
            or now >= self._expiration - self._refresh_buffer
        ):
            return self._fetch_credentials()
        return self._credentials


def make_signed_request(cred_manager, target, payload):
    """Make a SigV4-signed request to the internal AWS Support API."""
    credentials = cred_manager.get_credentials()
    body = json.dumps(payload)

    headers = {
        "Content-Type": CONTENT_TYPE,
        "X-Amz-Target": target,
    }
    headers.update(BROWSER_HEADERS)

    request = AWSRequest(method="POST", url=ENDPOINT, data=body, headers=headers)
    SigV4Auth(credentials, SERVICE, REGION).add_auth(request)

    response = requests.post(
        ENDPOINT,
        data=body,
        headers=dict(request.headers),
        timeout=60,
    )

    if response.status_code != 200:
        print(
            f"  ERROR {response.status_code}: {response.text[:500]}", file=sys.stderr
        )
        raise Exception(f"API request failed: {response.status_code}")

    return response.json()


def search_all_cases(cred_manager):
    """Paginate through SearchForCases to get all case IDs."""
    all_cases = []
    next_token = None
    page = 0

    while True:
        page += 1
        payload = {
            "maxResults": 20,
            "sortBy": [{"field": "creationDate", "direction": "desc"}],
        }
        if next_token:
            payload["nextToken"] = next_token

        print(f"  Fetching case list page {page}...", flush=True)
        result = make_signed_request(
            cred_manager, "AWSSupport_internal_v1.SearchForCases", payload
        )

        cases = result.get("caseSearchResults", result.get("cases", []))
        all_cases.extend(cases)
        print(f"    Got {len(cases)} cases (total so far: {len(all_cases)})")

        next_token = result.get("nextToken")
        if not next_token or not cases:
            break

        time.sleep(0.3)

    return all_cases


def describe_case_detail(cred_manager, display_id):
    """Get case details + first page of communications."""
    payload = {
        "displayId": display_id,
        "includeCommunications": True,
        "includeResolvedCases": True,
    }
    result = make_signed_request(
        cred_manager, "AWSSupport_internal_v1.DescribeCases", payload
    )
    cases = result.get("cases", [])
    return cases[0] if cases else None


def get_remaining_communications(cred_manager, case_id, next_token):
    """Paginate remaining communications using DescribeCommunications."""
    all_comms = []

    while next_token:
        payload = {
            "caseId": case_id,
            "nextToken": next_token,
        }
        result = make_signed_request(
            cred_manager,
            "AWSSupport_internal_v1.DescribeCommunications",
            payload,
        )
        comms = result.get("communications", [])
        all_comms.extend(comms)
        next_token = result.get("nextToken")
        time.sleep(0.3)

    return all_comms


def main():
    parser = argparse.ArgumentParser(
        description="Download all AWS Support cases via the internal console API"
    )
    parser.add_argument(
        "--cookies",
        help="Browser cookie string (the value after -b in curl)",
    )
    parser.add_argument(
        "--cookies-file",
        help="Path to file containing the browser cookie string",
    )
    parser.add_argument(
        "--csrf-token",
        help="x-csrf-token header value from the /support/tb/creds request",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="aws_support_cases.json",
        help="Output file path (default: aws_support_cases.json)",
    )
    parser.add_argument(
        "--cases-only",
        action="store_true",
        help="Only fetch case list, skip fetching full communications",
    )
    parser.add_argument(
        "--resume",
        help="Resume from a previously saved case list JSON file",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        help="Skip the first N cases when fetching details (use with --resume)",
    )
    args = parser.parse_args()

    # Load cookies
    cookies = args.cookies
    if args.cookies_file:
        with open(args.cookies_file) as f:
            cookies = f.read().strip()
    if not cookies:
        print("ERROR: Provide cookies via --cookies or --cookies-file", file=sys.stderr)
        sys.exit(1)

    csrf_token = args.csrf_token
    if not csrf_token:
        print("ERROR: Provide --csrf-token (from the x-csrf-token header)", file=sys.stderr)
        sys.exit(1)

    cred_manager = CredentialManager(cookies, csrf_token)

    # Test credentials
    print("Testing credentials...", flush=True)
    try:
        make_signed_request(
            cred_manager,
            "AWSSupport_internal_v1.SearchForCases",
            {
                "maxResults": 10,
                "sortBy": [{"field": "creationDate", "direction": "desc"}],
            },
        )
        print("  Credentials OK!\n")
    except Exception as e:
        print(f"  Credential test failed: {e}", file=sys.stderr)
        print(
            "  Your session cookies may have expired. Log in again in the browser.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 1: Get all case summaries
    if args.resume:
        print(f"Resuming from {args.resume}...")
        with open(args.resume) as f:
            data = json.load(f)
        if isinstance(data, list):
            case_list = data
        elif "cases" in data:
            case_list = data["cases"]
        else:
            case_list = data
        print(f"  Loaded {len(case_list)} cases from file")
    else:
        print("[Step 1/2] Fetching all case summaries...")
        case_list = search_all_cases(cred_manager)
        print(f"  Total cases found: {len(case_list)}")

        # Save intermediate result
        interim_file = args.output.replace(".json", "_list.json")
        with open(interim_file, "w") as f:
            json.dump(case_list, f, indent=2, default=str)
        print(f"  Case list saved to {interim_file}")

    if args.cases_only:
        print(
            f"\nDone! Case list saved to {args.output.replace('.json', '_list.json')}"
        )
        return

    # Step 2: Fetch full details + communications for each case
    total = len(case_list)
    skip = args.skip
    if skip > 0:
        print(f"\nSkipping first {skip} cases (already downloaded)")

    # Load existing progress if resuming with skip
    full_cases = []
    if skip > 0:
        progress_file = args.output.replace(".json", "_progress.json")
        if os.path.exists(progress_file):
            with open(progress_file) as f:
                progress_data = json.load(f)
            full_cases = progress_data.get("cases", [])
            print(f"  Loaded {len(full_cases)} already-downloaded cases from progress file")

    errors = []

    print(f"\n[Step 2/2] Fetching full details for cases {skip+1}-{total}...")

    for i in range(skip, total):
        case_summary = case_list[i]
        display_id = (
            case_summary.get("displayId") or case_summary.get("caseId", "")
        )
        subject = case_summary.get("subject", "N/A")[:60]
        print(
            f"  [{i+1}/{total}] Case {display_id}: {subject}...",
            flush=True,
        )

        try:
            case_detail = describe_case_detail(cred_manager, display_id)

            if case_detail:
                # Collect first page of communications
                recent = case_detail.get("recentCommunications", {})
                all_comms = recent.get("communications", [])
                comm_next_token = recent.get("nextToken")

                # If there are more pages, paginate via DescribeCommunications
                if comm_next_token:
                    case_id = case_detail.get("caseId", "")
                    extra_comms = get_remaining_communications(
                        cred_manager, case_id, comm_next_token
                    )
                    all_comms.extend(extra_comms)

                if "recentCommunications" in case_detail:
                    case_detail["recentCommunications"][
                        "communications"
                    ] = all_comms
                    case_detail["recentCommunications"].pop("nextToken", None)
                case_detail["_communicationCount"] = len(all_comms)
                full_cases.append(case_detail)
                print(f"    {len(all_comms)} communications")
            else:
                case_summary["_error"] = "DescribeCases returned empty"
                full_cases.append(case_summary)
                print("    WARNING: no detail returned")

        except Exception as e:
            print(f"    ERROR: {e}")
            case_summary["_error"] = str(e)
            full_cases.append(case_summary)
            errors.append({"displayId": display_id, "error": str(e)})

        # Save progress every 10 cases
        if (i + 1) % 10 == 0 or i == total - 1:
            progress_file = args.output.replace(".json", "_progress.json")
            with open(progress_file, "w") as f:
                json.dump(
                    {
                        "cases": full_cases,
                        "progress": f"{i+1}/{total}",
                        "last_index": i,
                    },
                    f,
                    indent=2,
                    default=str,
                )
            if (i + 1) % 10 == 0:
                print(f"    (progress saved: {i+1}/{total})")

        time.sleep(0.5)

    # Save final output
    output = {
        "metadata": {
            "downloaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total_cases": len(full_cases),
            "errors": len(errors),
        },
        "cases": full_cases,
    }

    if errors:
        output["errors"] = errors

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nDone!")
    print(f"  Total cases: {len(full_cases)}")
    print(f"  Errors: {len(errors)}")
    print(f"  Output: {args.output}")


if __name__ == "__main__":
    main()
