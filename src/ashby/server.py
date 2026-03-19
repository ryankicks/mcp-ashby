# /// script
# dependencies = [
#   "mcp",
#   "requests",
#   "python-dotenv"
# ]
# ///
import asyncio
import base64
import json
import sys
import time
from typing import Any, Optional
import os
from dotenv import load_dotenv
import requests

import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio

load_dotenv()

# ---------------------------------------------------------------------------
# Stderr logging helper (#8)
# ---------------------------------------------------------------------------
def _log(msg: str):
    """Print diagnostic info to stderr (visible in Claude Desktop logs)."""
    print(f"[ashby-mcp] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Ashby API client – lazy connection (#6)
# ---------------------------------------------------------------------------
class AshbyClient:
    """Handles Ashby API operations using Basic Auth with lazy initialization."""

    def __init__(self):
        self.api_key: Optional[str] = None
        self.base_url = "https://api.ashbyhq.com"
        self.headers: dict[str, str] = {}
        self._connected = False

    def _ensure_connected(self):
        """Connect on first use instead of at server startup."""
        if self._connected:
            return
        self.api_key = os.getenv("ASHBY_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ASHBY_API_KEY environment variable not set. "
                "Get your API key from https://app.ashbyhq.com/admin/api/keys "
                "and set it as ASHBY_API_KEY in your environment."
            )
        encoded = base64.b64encode(f"{self.api_key}:".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
        }
        self._connected = True
        _log("Connected to Ashby API")

    def post(self, endpoint: str, data: Optional[dict] = None) -> dict:
        """All Ashby API endpoints are POST with JSON bodies."""
        self._ensure_connected()
        url = f"{self.base_url}{endpoint}"
        start = time.time()
        response = requests.post(url, headers=self.headers, json=data or {})
        elapsed_ms = int((time.time() - start) * 1000)
        _log(f"POST {endpoint} → {response.status_code} ({elapsed_ms}ms, {len(response.content)} bytes)")
        response.raise_for_status()
        return response.json()

    def post_all_pages(self, endpoint: str, data: Optional[dict] = None) -> list[dict]:
        """Fetch all pages from a paginated endpoint and return combined results."""
        all_results = []
        params = dict(data or {})
        while True:
            resp = self.post(endpoint, data=params)
            results = resp.get("results", [])
            all_results.extend(results)
            next_cursor = resp.get("nextCursor")
            if not next_cursor or not resp.get("moreDataAvailable", False):
                break
            params["cursor"] = next_cursor
        return all_results


ashby = AshbyClient()


# ---------------------------------------------------------------------------
# Response trimming helpers (#1)
# ---------------------------------------------------------------------------
def _pick(obj: Any, keys: list[str]) -> dict:
    """Extract only specified keys from a dict."""
    if not isinstance(obj, dict):
        return obj
    return {k: obj[k] for k in keys if k in obj}


def _trim_job(job: dict) -> dict:
    return {
        "id": job.get("id"),
        "title": job.get("title"),
        "status": job.get("status"),
        "employmentType": job.get("employmentType"),
        "departmentId": job.get("departmentId"),
        "locationId": job.get("locationId"),
        "interviewPlanId": job.get("interviewPlanId"),
        **( {"hiringTeam": [
                {"name": m.get("name"), "role": m.get("role")}
                for m in job.get("hiringTeam", [])
            ]} if job.get("hiringTeam") else {}
        ),
    }


def _trim_candidate(cand: dict) -> dict:
    emails = cand.get("emailAddresses", [])
    return {
        "id": cand.get("id"),
        "name": cand.get("name"),
        "emails": [e.get("value") for e in emails] if emails else [],
        "phoneNumbers": [p.get("value") for p in cand.get("phoneNumbers", [])] if cand.get("phoneNumbers") else [],
        "linkedInUrl": cand.get("linkedInUrl"),
        "applicationIds": cand.get("applicationIds", []),
        "tags": [t.get("name") if isinstance(t, dict) else t for t in cand.get("tags", [])],
        "createdAt": cand.get("createdAt"),
    }


def _trim_application(app: dict) -> dict:
    result: dict[str, Any] = {
        "id": app.get("id"),
        "status": app.get("status"),
        "createdAt": app.get("createdAt"),
    }
    if app.get("candidate"):
        result["candidate"] = _pick(app["candidate"], ["id", "name"])
    if app.get("currentInterviewStage"):
        result["currentInterviewStage"] = _pick(app["currentInterviewStage"], ["id", "title"])
    if app.get("job"):
        result["job"] = _pick(app["job"], ["id", "title"])
    if app.get("source"):
        result["source"] = _pick(app["source"], ["id", "title"])
    if app.get("archiveReason"):
        result["archiveReason"] = _pick(app["archiveReason"], ["id", "title"])
    return result


def _trim_interview(interview: dict) -> dict:
    result = _pick(interview, [
        "id", "status", "scheduledStartTime", "scheduledEndTime",
        "applicationId", "interviewStageId",
    ])
    if interview.get("interviewers"):
        result["interviewers"] = [
            _pick(i, ["name", "email"]) for i in interview["interviewers"]
        ]
    return result


def _trim_note(note: dict) -> dict:
    result = _pick(note, ["id", "content", "createdAt"])
    if note.get("author"):
        result["author"] = note["author"].get("name", note["author"].get("id"))
    return result


def _trim_paginated(response: dict, trimmer) -> dict:
    """Trim a standard paginated Ashby response."""
    trimmed = {
        "results": [trimmer(r) for r in response.get("results", [])],
    }
    if response.get("moreDataAvailable"):
        trimmed["moreDataAvailable"] = True
        trimmed["nextCursor"] = response.get("nextCursor")
    return trimmed


# Mapping of endpoints to their result trimmers
RESPONSE_TRIMMERS = {
    "/job.list": lambda r: _trim_paginated(r, _trim_job),
    "/job.info": lambda r: _trim_job(r.get("results", r)),
    "/job.search": lambda r: {"results": [_trim_job(j) for j in r.get("results", [])]},
    "/candidate.list": lambda r: _trim_paginated(r, _trim_candidate),
    "/candidate.search": lambda r: {"results": [_trim_candidate(c) for c in r.get("results", [])]},
    "/candidate.info": lambda r: _trim_candidate(r.get("results", r)),
    "/application.list": lambda r: _trim_paginated(r, _trim_application),
    "/application.info": lambda r: _trim_application(r.get("results", r)),
    "/interview.list": lambda r: _trim_paginated(r, _trim_interview),
    "/interview.info": lambda r: _trim_interview(r.get("results", r)),
    "/candidate.listNotes": lambda r: _trim_paginated(r, _trim_note),
}


# ---------------------------------------------------------------------------
# Agent-friendly error messages (#7)
# ---------------------------------------------------------------------------
_ERROR_HINTS = {
    401: (
        "Authentication failed. Check that your ASHBY_API_KEY is correct "
        "and hasn't been revoked at https://app.ashbyhq.com/admin/api/keys"
    ),
    403: (
        "Your API key doesn't have permission for this endpoint. "
        "Check that the required scope is enabled at https://app.ashbyhq.com/admin/api/keys"
    ),
    404: (
        "Endpoint not found. This may mean the resource ID is invalid "
        "or the Ashby API version has changed."
    ),
    429: "Rate limited by Ashby. Wait a moment and try again.",
}


def _friendly_error(endpoint: str, exc: requests.exceptions.HTTPError) -> str:
    status = exc.response.status_code if exc.response is not None else None
    hint = _ERROR_HINTS.get(status, "")
    body = ""
    if exc.response is not None:
        try:
            body = exc.response.text
        except Exception:
            pass
    parts = [f"Ashby API error on {endpoint}: HTTP {status}"]
    if hint:
        parts.append(hint)
    if body:
        parts.append(f"Response: {body}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------
SERVER_INSTRUCTIONS = (
    "This server connects to an Ashby ATS instance. "
    "Start with job_search or job_list to find job IDs, then use application_list "
    "with a jobId to see the hiring pipeline. Use pipeline_summary for a quick "
    "stage-by-stage breakdown of any job. Use candidate_full_profile to get a "
    "complete view of a candidate including their applications and notes. "
    "Use the lookup tool to fetch reference data like departments, sources, "
    "locations, users, and archive reasons. "
    "Read tools are safe to call freely; write tools (candidate_create, "
    "application_create, application_change_stage, candidate_create_note, "
    "candidate_add_tag) modify data."
)

server = Server("ashby-mcp")


# ---------------------------------------------------------------------------
# Tool definitions (#2, #3, #5, #9, #11)
# ---------------------------------------------------------------------------
TOOLS = [
    # ── Jobs ──────────────────────────────────────────────────────────────
    types.Tool(
        name="job_list",
        description=(
            "List jobs in Ashby with optional status filtering and pagination. "
            "Use this to browse all open positions or find jobs by status. "
            "Returns job ID, title, status, department, and interviewPlanId. "
            "Pass allPages=true to fetch every job in one call."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["Draft", "Open", "Closed", "Archived"]},
                    "description": "Filter by status(es). Omit to return all non-Draft jobs.",
                },
                "limit": {"type": "integer", "description": "Max results per page (default/max 100)."},
                "cursor": {"type": "string", "description": "Cursor from a previous response to get the next page."},
                "allPages": {"type": "boolean", "description": "If true, auto-paginate and return ALL results in one response."},
            },
        },
    ),
    types.Tool(
        name="job_info",
        description=(
            "Get details of a single job by its ID. Returns title, status, department, "
            "hiring team, and interviewPlanId (needed for interview_stage_list)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The job ID (UUID). Get IDs from job_list or job_search."},
            },
            "required": ["id"],
        },
    ),
    types.Tool(
        name="job_search",
        description=(
            "Search for jobs by title (partial match). Not paginated; returns all matches. "
            "Use this when you know the job title but not the ID."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Job title to search for (partial match supported)."},
            },
            "required": ["title"],
        },
    ),

    # ── Candidates ────────────────────────────────────────────────────────
    types.Tool(
        name="candidate_list",
        description=(
            "List all candidates with cursor pagination. Returns candidate name, email, "
            "phone, and application IDs. Pass allPages=true to fetch all candidates "
            "(may be slow for large orgs)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results per page (default/max 100)."},
                "cursor": {"type": "string", "description": "Cursor from previous response for next page."},
                "allPages": {"type": "boolean", "description": "If true, auto-paginate and return ALL results."},
            },
        },
    ),
    types.Tool(
        name="candidate_search",
        description=(
            "Search for candidates by email and/or name. Not paginated. "
            "Provide at least one of email or name. Use this to find a specific person."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address to search for."},
                "name": {"type": "string", "description": "Name to search for."},
            },
        },
    ),
    types.Tool(
        name="candidate_info",
        description=(
            "Get details of a single candidate by ID. For a complete profile including "
            "notes and applications, use candidate_full_profile instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The candidate ID (UUID)."},
            },
            "required": ["id"],
        },
    ),
    types.Tool(
        name="candidate_create",
        description=(
            "Create a new candidate in Ashby. Only 'name' is required. "
            "This is a WRITE operation that creates real data. "
            "Returns the created candidate's ID and details."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name of the candidate."},
                "email": {"type": "string", "description": "Primary email address."},
                "phoneNumber": {"type": "string", "description": "Phone number."},
                "linkedInUrl": {"type": "string", "description": "LinkedIn profile URL."},
                "githubUrl": {"type": "string", "description": "GitHub profile URL."},
                "sourceId": {"type": "string", "description": "Source ID for attribution. Get IDs from lookup tool with type='source'."},
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="candidate_create_note",
        description=(
            "Add a note to a candidate. Supports HTML formatting in the note body. "
            "This is a WRITE operation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "candidateId": {"type": "string", "description": "The candidate ID."},
                "note": {"type": "string", "description": "Note content (HTML supported)."},
                "sendNotifications": {
                    "type": "boolean",
                    "description": "Notify subscribed users (default false).",
                },
            },
            "required": ["candidateId", "note"],
        },
    ),
    types.Tool(
        name="candidate_list_notes",
        description=(
            "List all notes for a candidate. Returns note content, author, and date. "
            "Pass allPages=true to get all notes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "candidateId": {"type": "string", "description": "The candidate ID."},
                "limit": {"type": "integer", "description": "Max results per page."},
                "cursor": {"type": "string", "description": "Cursor for next page."},
                "allPages": {"type": "boolean", "description": "If true, auto-paginate and return ALL notes."},
            },
            "required": ["candidateId"],
        },
    ),
    types.Tool(
        name="candidate_add_tag",
        description=(
            "Add a tag to a candidate. This is a WRITE operation. "
            "Get tag IDs from lookup tool with type='candidateTag'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "candidateId": {"type": "string", "description": "The candidate ID."},
                "tagId": {"type": "string", "description": "The tag ID to add. Get IDs from lookup with type='candidateTag'."},
            },
            "required": ["candidateId", "tagId"],
        },
    ),

    # ── Applications ──────────────────────────────────────────────────────
    types.Tool(
        name="application_list",
        description=(
            "List applications (candidates in a hiring pipeline). Filter by jobId to see "
            "a specific job's pipeline, and by status to narrow results. "
            "Statuses: Active = in-progress, Hired = offered & accepted, "
            "Archived = rejected/withdrawn, Lead = sourced but not yet applied. "
            "Returns candidate name, current interview stage, source, and dates. "
            "Pass allPages=true to fetch all applications matching the filter."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "jobId": {"type": "string", "description": "Filter by job ID. Get IDs from job_search or job_list."},
                "status": {
                    "type": "string",
                    "enum": ["Active", "Hired", "Archived", "Lead"],
                    "description": "Filter by status. Active = in-progress candidates.",
                },
                "createdAfter": {
                    "type": "integer",
                    "description": "Only applications created after this timestamp (milliseconds since epoch).",
                },
                "limit": {"type": "integer", "description": "Max results per page (default/max 100)."},
                "cursor": {"type": "string", "description": "Cursor from previous response for next page."},
                "allPages": {"type": "boolean", "description": "If true, auto-paginate and return ALL matching applications."},
            },
        },
    ),
    types.Tool(
        name="application_info",
        description=(
            "Get full details for a single application by ID. Returns candidate, job, "
            "current stage, status, source, and dates."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "applicationId": {"type": "string", "description": "The application ID (UUID)."},
            },
            "required": ["applicationId"],
        },
    ),
    types.Tool(
        name="application_create",
        description=(
            "Create an application linking a candidate to a job. "
            "This is a WRITE operation that adds the candidate to the job's pipeline."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "candidateId": {"type": "string", "description": "The candidate ID."},
                "jobId": {"type": "string", "description": "The job ID."},
                "sourceId": {"type": "string", "description": "Source ID for attribution."},
                "interviewPlanId": {"type": "string", "description": "Interview plan to use (optional)."},
            },
            "required": ["candidateId", "jobId"],
        },
    ),
    types.Tool(
        name="application_change_stage",
        description=(
            "Move an application to a different interview stage. "
            "This is a WRITE operation that changes a candidate's position in the pipeline. "
            "Use interview_stage_list to find valid stage IDs for the job's interview plan. "
            "If moving to an Archived stage, you must provide archiveReasonId "
            "(get IDs from lookup with type='archiveReason')."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "applicationId": {"type": "string", "description": "The application ID."},
                "interviewStageId": {"type": "string", "description": "Target stage ID. Get from interview_stage_list."},
                "archiveReasonId": {
                    "type": "string",
                    "description": "Required when archiving. Get IDs from lookup with type='archiveReason'.",
                },
            },
            "required": ["applicationId", "interviewStageId"],
        },
    ),

    # ── Interview Stages & Plans ──────────────────────────────────────────
    types.Tool(
        name="interview_stage_list",
        description=(
            "List all interview stages for a given interview plan. Use this to see "
            "the stages in a job's pipeline. Get interviewPlanId from job_info."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "interviewPlanId": {"type": "string", "description": "The interview plan ID. Get from job_info's interviewPlanId field."},
            },
            "required": ["interviewPlanId"],
        },
    ),
    types.Tool(
        name="interview_plan_list",
        description="List all interview plans in the organization.",
        inputSchema={
            "type": "object",
            "properties": {
                "includeArchived": {"type": "boolean", "description": "Include archived plans (default false)."},
            },
        },
    ),

    # ── Interviews ────────────────────────────────────────────────────────
    types.Tool(
        name="interview_list",
        description=(
            "List all scheduled interviews with pagination. Returns interview status, "
            "times, interviewers, and linked application. Pass allPages=true to fetch all."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results per page."},
                "cursor": {"type": "string", "description": "Cursor for next page."},
                "allPages": {"type": "boolean", "description": "If true, auto-paginate and return ALL interviews."},
            },
        },
    ),
    types.Tool(
        name="interview_info",
        description="Get details of a single interview by ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The interview ID."},
            },
            "required": ["id"],
        },
    ),

    # ── Consolidated lookup tool (#5) ─────────────────────────────────────
    types.Tool(
        name="lookup",
        description=(
            "Fetch reference data from Ashby. Use the 'type' parameter to specify what to look up. "
            "Types: 'department' (org departments), 'user' (team members), 'source' (candidate sources), "
            "'archiveReason' (reasons for archiving applications — needed for application_change_stage), "
            "'location' (office locations), 'candidateTag' (tags for candidates)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["department", "user", "source", "archiveReason", "location", "candidateTag"],
                    "description": "The type of reference data to look up.",
                },
                "includeArchived": {
                    "type": "boolean",
                    "description": "Include archived/deactivated items (default false). For 'user' type, this includes deactivated users.",
                },
            },
            "required": ["type"],
        },
    ),

    # ── Composite / workflow tools (#2) ───────────────────────────────────
    types.Tool(
        name="pipeline_summary",
        description=(
            "Get a stage-by-stage summary of a job's hiring pipeline. Takes a job title "
            "(or job ID), resolves it internally, fetches ALL applications (auto-paginating), "
            "and returns counts grouped by interview stage. Use this instead of manually "
            "chaining job_search + application_list + counting."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "jobTitle": {"type": "string", "description": "Job title to search for (partial match). Provide this OR jobId."},
                "jobId": {"type": "string", "description": "Job ID (UUID). Provide this OR jobTitle."},
                "status": {
                    "type": "string",
                    "enum": ["Active", "Hired", "Archived", "Lead"],
                    "description": "Filter applications by status. Defaults to showing all statuses.",
                },
            },
        },
    ),
    types.Tool(
        name="candidate_full_profile",
        description=(
            "Get a complete view of a candidate: their info, all applications (with job "
            "titles and current stages), and all notes. Fetches everything in one call "
            "instead of requiring separate candidate_info + candidate_list_notes + "
            "application lookups."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "candidateId": {"type": "string", "description": "The candidate ID (UUID)."},
            },
            "required": ["candidateId"],
        },
    ),
]


# Map tool names -> Ashby API endpoints (for simple pass-through tools)
TOOL_ENDPOINT_MAP = {
    "job_list": "/job.list",
    "job_info": "/job.info",
    "job_search": "/job.search",
    "candidate_list": "/candidate.list",
    "candidate_search": "/candidate.search",
    "candidate_info": "/candidate.info",
    "candidate_create": "/candidate.create",
    "candidate_create_note": "/candidate.createNote",
    "candidate_list_notes": "/candidate.listNotes",
    "candidate_add_tag": "/candidate.addTag",
    "application_list": "/application.list",
    "application_info": "/application.info",
    "application_create": "/application.create",
    "application_change_stage": "/application.change_stage",
    "interview_stage_list": "/interviewStage.list",
    "interview_plan_list": "/interviewPlan.list",
    "interview_list": "/interview.list",
    "interview_info": "/interview.info",
}

# Lookup type -> (endpoint, archive_param_name)
LOOKUP_TYPE_MAP = {
    "department": ("/department.list", "includeArchived"),
    "user": ("/user.list", "includeDeactivated"),
    "source": ("/source.list", "includeArchived"),
    "archiveReason": ("/archiveReason.list", None),
    "location": ("/location.list", None),
    "candidateTag": ("/candidateTag.list", "includeArchived"),
}

# Tools that support the allPages parameter
PAGINATED_TOOLS = {
    "job_list", "candidate_list", "candidate_list_notes",
    "application_list", "interview_list",
}

# Write tools — these get a confirmation reminder in the response (#10)
WRITE_TOOLS = {
    "candidate_create", "application_create", "application_change_stage",
    "candidate_create_note", "candidate_add_tag",
}


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return TOOLS


# ---------------------------------------------------------------------------
# Composite tool handlers (#2)
# ---------------------------------------------------------------------------
async def _handle_pipeline_summary(arguments: dict) -> str:
    job_id = arguments.get("jobId")
    job_title = arguments.get("jobTitle")
    status_filter = arguments.get("status")

    if not job_id and not job_title:
        return json.dumps({"error": "Provide either jobTitle or jobId."})

    # Resolve job ID from title if needed
    job_name = job_title
    if not job_id:
        search_resp = ashby.post("/job.search", data={"title": job_title})
        jobs = search_resp.get("results", [])
        if not jobs:
            return json.dumps({"error": f"No jobs found matching '{job_title}'."})
        if len(jobs) > 1:
            return json.dumps({
                "error": f"Multiple jobs match '{job_title}'. Please be more specific or use a jobId.",
                "matches": [{"id": j.get("id"), "title": j.get("title")} for j in jobs],
            })
        job_id = jobs[0].get("id")
        job_name = jobs[0].get("title", job_title)
    else:
        # Fetch job name
        try:
            job_resp = ashby.post("/job.info", data={"id": job_id})
            job_name = job_resp.get("results", {}).get("title", job_id)
        except Exception:
            job_name = job_id

    # Fetch all applications for this job
    params: dict[str, Any] = {"jobId": job_id}
    if status_filter:
        params["status"] = status_filter
    all_apps = ashby.post_all_pages("/application.list", data=params)

    # Group by stage
    stage_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for app in all_apps:
        stage = app.get("currentInterviewStage", {})
        stage_name = stage.get("title", "Unknown") if isinstance(stage, dict) else "Unknown"
        stage_counts[stage_name] = stage_counts.get(stage_name, 0) + 1
        app_status = app.get("status", "Unknown")
        status_counts[app_status] = status_counts.get(app_status, 0) + 1

    return json.dumps({
        "job": {"id": job_id, "title": job_name},
        "totalApplications": len(all_apps),
        "byStage": stage_counts,
        "byStatus": status_counts,
    }, indent=2)


async def _handle_candidate_full_profile(arguments: dict) -> str:
    candidate_id = arguments["candidateId"]

    # Fetch candidate info
    cand_resp = ashby.post("/candidate.info", data={"id": candidate_id})
    candidate = _trim_candidate(cand_resp.get("results", cand_resp))

    # Fetch notes
    all_notes = ashby.post_all_pages("/candidate.listNotes", data={"candidateId": candidate_id})
    notes = [_trim_note(n) for n in all_notes]

    # Fetch applications for this candidate
    application_ids = candidate.get("applicationIds", [])
    applications = []
    for app_id in application_ids:
        try:
            app_resp = ashby.post("/application.info", data={"applicationId": app_id})
            applications.append(_trim_application(app_resp.get("results", app_resp)))
        except Exception as e:
            applications.append({"id": app_id, "error": str(e)})

    return json.dumps({
        "candidate": candidate,
        "applications": applications,
        "notes": notes,
    }, indent=2)


# ---------------------------------------------------------------------------
# Lookup handler (#5)
# ---------------------------------------------------------------------------
async def _handle_lookup(arguments: dict) -> str:
    lookup_type = arguments.get("type")
    if lookup_type not in LOOKUP_TYPE_MAP:
        return json.dumps({"error": f"Unknown lookup type: {lookup_type}. Valid: {list(LOOKUP_TYPE_MAP.keys())}"})

    endpoint, archive_param = LOOKUP_TYPE_MAP[lookup_type]
    params: dict[str, Any] = {}
    if archive_param and arguments.get("includeArchived"):
        params[archive_param] = True

    response = ashby.post(endpoint, data=params if params else None)
    return json.dumps(response, indent=2)


# ---------------------------------------------------------------------------
# Main tool dispatcher
# ---------------------------------------------------------------------------
@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    _log(f"Tool call: {name} with args: {json.dumps(arguments)}")

    try:
        # Composite tools
        if name == "pipeline_summary":
            text = await _handle_pipeline_summary(arguments)
            return [types.TextContent(type="text", text=text)]

        if name == "candidate_full_profile":
            text = await _handle_candidate_full_profile(arguments)
            return [types.TextContent(type="text", text=text)]

        # Consolidated lookup tool
        if name == "lookup":
            text = await _handle_lookup(arguments)
            return [types.TextContent(type="text", text=text)]

        # Standard pass-through tools
        endpoint = TOOL_ENDPOINT_MAP.get(name)
        if not endpoint:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

        # Handle allPages parameter (#9)
        use_all_pages = arguments.pop("allPages", False)

        if use_all_pages and name in PAGINATED_TOOLS:
            all_results = ashby.post_all_pages(endpoint, data=arguments if arguments else None)
            # Apply trimmer if available
            trimmer_map = {
                "/job.list": _trim_job,
                "/candidate.list": _trim_candidate,
                "/application.list": _trim_application,
                "/interview.list": _trim_interview,
                "/candidate.listNotes": _trim_note,
            }
            trimmer = trimmer_map.get(endpoint)
            if trimmer:
                all_results = [trimmer(r) for r in all_results]
            result = {"results": all_results, "totalCount": len(all_results)}
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        # Standard single-page call
        response = ashby.post(endpoint, data=arguments if arguments else None)

        # Trim response (#1)
        trimmer_fn = RESPONSE_TRIMMERS.get(endpoint)
        if trimmer_fn:
            response = trimmer_fn(response)

        return [types.TextContent(type="text", text=json.dumps(response, indent=2))]

    except requests.exceptions.HTTPError as e:
        error_msg = _friendly_error(
            TOOL_ENDPOINT_MAP.get(name, name), e
        )
        _log(f"HTTP error: {error_msg}")
        return [types.TextContent(type="text", text=error_msg)]
    except Exception as e:
        endpoint = TOOL_ENDPOINT_MAP.get(name, name)
        _log(f"Error: {endpoint} - {e}")
        return [types.TextContent(type="text", text=f"Error calling {endpoint}: {e}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def run():
    """Run the MCP server over stdio."""
    _log("Starting ashby-mcp server v0.3.0")
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="ashby",
                server_version="0.3.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(run())
