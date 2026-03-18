# Ashby MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects Claude to your [Ashby](https://www.ashbyhq.com/) ATS. Browse jobs, manage candidates, track applications through your hiring pipeline, and more — all through natural conversation.

## Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** — a fast Python package manager
- **An Ashby API key** with `candidatesRead`, `jobsRead`, and `candidatesWrite` permissions
  - Generate one at https://app.ashbyhq.com/admin/api/keys

## Setup

### 1. Install uv

If you don't already have `uv` installed:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# macOS via Homebrew
brew install uv
```

After installing, find the absolute path to the `uvx` command (you'll need this for the config):

```bash
which uvx
```

This will return something like `/Users/yourname/.local/bin/uvx`. Keep this path handy.

### 2. Get your Ashby API key

Go to https://app.ashbyhq.com/admin/api/keys and create a new API key. Make sure it has at least:

- `candidatesRead`
- `jobsRead`
- `candidatesWrite` (if you want to create candidates, add notes, or move applications)

### 3. Configure Claude Desktop

Open your Claude Desktop config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add the following to the `mcpServers` section, replacing the placeholders with your actual values:

```json
{
  "mcpServers": {
    "ashby": {
      "command": "/absolute/path/to/uvx",
      "args": [
        "--from", "git+https://github.com/PlenishAI/mcp-ashby.git",
        "ashby"
      ],
      "env": {
        "ASHBY_API_KEY": "your-ashby-api-key"
      }
    }
  }
}
```

> **Important:** Replace `/absolute/path/to/uvx` with the output from `which uvx` (e.g. `/Users/yourname/.local/bin/uvx`). Claude Desktop does not inherit your shell's PATH, so the full path is required.

### 4. Restart Claude Desktop

Quit and reopen Claude Desktop. The Ashby server should connect automatically. You can verify by asking Claude something like "list my open jobs in Ashby."

## Alternative: Local Clone Setup

If the `git+` URL method doesn't work (e.g. private repo or network restrictions), you can clone the repo locally:

```bash
git clone https://github.com/PlenishAi/mcp-ashby.git ~/projects/mcp-ashby
```

Then use `uv` (not `uvx`) in your config, pointing to the local directory:

```json
{
  "mcpServers": {
    "ashby": {
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory", "/absolute/path/to/mcp-ashby",
        "run", "ashby"
      ],
      "env": {
        "ASHBY_API_KEY": "your-ashby-api-key"
      }
    }
  }
}
```

> **Note:** When using a local clone, run `which uv` to get the path — the command is `uv`, not `uvx`.

To update, just `git pull` from the repo directory.

## Available Tools

### Jobs

| Tool | Description |
|---|---|
| `job_list` | List all jobs with optional status filter (Draft, Open, Closed, Archived) |
| `job_info` | Get details of a single job by ID |
| `job_search` | Search jobs by title |

### Candidates

| Tool | Description |
|---|---|
| `candidate_list` | List all candidates with cursor pagination |
| `candidate_search` | Search candidates by email and/or name |
| `candidate_info` | Get full details of a candidate by ID |
| `candidate_create` | Create a new candidate |
| `candidate_create_note` | Add an HTML-formatted note to a candidate |
| `candidate_list_notes` | List all notes for a candidate |
| `candidate_add_tag` | Add a tag to a candidate |
| `candidate_tag_list` | List all available candidate tags |

### Applications

| Tool | Description |
|---|---|
| `application_list` | List applications with optional jobId, status, and date filters |
| `application_info` | Get full details of an application by ID |
| `application_create` | Create an application linking a candidate to a job |
| `application_change_stage` | Move an application to a different interview stage |

### Interviews & Pipeline

| Tool | Description |
|---|---|
| `interview_stage_list` | List interview stages for a given interview plan |
| `interview_plan_list` | List all interview plans |
| `interview_list` | List all interviews with cursor pagination |
| `interview_info` | Get details of a single interview by ID |

### Reference Data

| Tool | Description |
|---|---|
| `department_list` | List all departments |
| `user_list` | List all users (team members) |
| `source_list` | List all candidate sources |
| `archive_reason_list` | List all archive reasons |
| `location_list` | List all locations |

## Troubleshooting

### "No such file or directory" on startup

Claude Desktop can't find `uvx`. Make sure you're using the **full absolute path** from `which uvx` in your config, not just `"uvx"`.

### "Package not found in registry"

If you see a PyPI resolution error, make sure the `--from` argument uses the `git+https://` URL, not a package name.

### "module has no attribute 'run_async'"

You're running an older version of the server code against a newer MCP SDK. Pull the latest version of this repo.

### API errors (401/403)

Your Ashby API key is missing or doesn't have the required permissions. Verify it at https://app.ashbyhq.com/admin/api/keys.

## API Reference

This server communicates with the [Ashby REST API](https://developers.ashbyhq.com/reference/introduction):

- **Base URL:** `https://api.ashbyhq.com`
- **Auth:** Basic Auth (API key as username, empty password)
- **Method:** All endpoints are POST with JSON bodies
- **Pagination:** Cursor-based (`moreDataAvailable` + `nextCursor`)

## License

MIT
