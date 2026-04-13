# AnalystOS — Setup Guide

This guide walks you through installing AnalystOS into Claude Code
and configuring it for your first project.

---

## Prerequisites

- [Claude Code](https://claude.ai/code) installed and authenticated
- Python 3.9+ on your PATH (for hooks)
- `pip install pyyaml` (required by the cost-gate hook for Oracle/Athena connections)
- For Oracle: Oracle client libraries + `cx_Oracle` or `python-oracledb`
- For Athena: AWS CLI configured, `boto3` installed
- For SQLite demo: no extra prerequisites — `starter-project` ships with a pre-built demo database
- For MCP-based connections:  relevant MCP installed (or built! :-) ...)

---

## Part 1 — Global Install (do once per machine)

The framework's core components (skills and hooks) are installed globally so they're
available in every project.

**OPTIONAL**:  An install/upgrade script is included:
```bash
python scripts/install.py --dry-run
```
will tell you what updates would be made (but not make them).
```bash
python scripts/install.py
```
will do it for real!

### 1a. Copy skills and hooks

Each skill is a directory containing a `SKILL.md` file — copy the whole directory.

```bash
# Create directories if they don't exist
mkdir -p ~/.claude/skills
mkdir -p ~/.claude/hooks

# Copy from this repo — reference skills (used internally by commands)
cp -r install/skills/db-dialect       ~/.claude/skills/
cp -r install/skills/db-cost-check    ~/.claude/skills/
cp -r install/skills/db-introspect    ~/.claude/skills/
cp -r install/skills/db-sample        ~/.claude/skills/
cp -r install/skills/db-soql          ~/.claude/skills/
cp -r install/skills/db-explain-result ~/.claude/skills/
cp -r install/skills/db-doc-writer    ~/.claude/skills/

# Copy slash commands (user-invocable via /db-*)
cp -r install/skills/db-orient        ~/.claude/skills/
cp -r install/skills/db-explain       ~/.claude/skills/
cp -r install/skills/db-profile       ~/.claude/skills/
cp -r install/skills/db-joins         ~/.claude/skills/
cp -r install/skills/db-status        ~/.claude/skills/
cp -r install/skills/db-query         ~/.claude/skills/
cp -r install/skills/db-gotchas       ~/.claude/skills/
cp -r install/skills/db-document      ~/.claude/skills/
cp -r install/skills/db-capture       ~/.claude/skills/
cp -r install/skills/db-index         ~/.claude/skills/
cp -r install/skills/db-use           ~/.claude/skills/

# Copy hooks
cp install/hooks/db-safety.py      ~/.claude/hooks/
cp install/hooks/db-cost-gate.py   ~/.claude/hooks/

# Make hooks executable
chmod +x ~/.claude/hooks/db-safety.py
chmod +x ~/.claude/hooks/db-cost-gate.py
```

### 1b. Register hooks in settings.json

Open (or create) `~/.claude/settings.json` and add the hooks block.

If `settings.json` doesn't exist yet:
```bash
cp install/settings.json ~/.claude/settings.json
```

If it already exists, merge the `hooks` block from `install/settings.json` into your
existing file. The key section to add:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": ".*",
        "hooks": [{ "type": "command", "command": "python ~/.claude/hooks/db-safety.py" }]
      },
      {
        "matcher": ".*",
        "hooks": [{ "type": "command", "command": "python ~/.claude/hooks/db-cost-gate.py" }]
      }
    ]
  }
}
```

### 1c. Verify the install

```bash
# Test the safety hook — should output {"action": "block", ...}
echo '{"tool":"bash","input":{"command":"DELETE FROM users WHERE 1=1"}}' \
  | python ~/.claude/hooks/db-safety.py

# Test with a safe query — should output {"action": "allow"}
echo '{"tool":"bash","input":{"query":"SELECT * FROM users"}}' \
  | python ~/.claude/hooks/db-safety.py
```

---

## Part 2 — Project Setup (using starter-project)

The fastest way to set up a new analyst project is to copy `starter-project/` from the
analystos repo. It is a complete, ready-to-run project pre-configured for the bundled
SQLite demo database.

### 2a. Copy starter-project

```bash
# From the analystos repo root — copy to wherever your project lives
cp -r starter-project/ ~/my-analyst-project/

cd ~/my-analyst-project/
```

That's it for the demo. Open Claude Code here and it will auto-load the connection and KB.

### 2b. Switch to your real database

When you're ready to connect to your own database:

```bash
# Edit the active connection config
$EDITOR .claude/db-connections/active.yaml
```

- Set `active:` to the name of your connection (e.g., `oracle-prod`)
- Add a connection profile under `connections:` — use `connections.example.yaml` as reference
- Fill in your database host, credentials, and schema scope
- Set cost thresholds appropriate for your environment

**Never commit `active.yaml` in your own project** — it's gitignored by default (see `.gitignore`),
so it's safe to store credentials directly in the file.

**Oracle credentials** go directly in `active.yaml`:
```yaml
oracle:
  user: my_read_only_user
  password: my_password
  host: db.example.com
  ...
```
If `user` or `password` are absent, the connector will prompt for them interactively.

**Athena** uses your AWS profile — no credentials needed in `active.yaml`:
```bash
aws sso login --profile my-analytics-profile
```

---

## Part 3 — Demo Database Quickstart

Once you've completed Part 2a (copy `starter-project/` and open Claude Code inside it), the demo is ready — no credentials or cloud access required.

Claude will greet you with: `Connected to AnalystOS Demo Database (sqlite). KB loaded...`

Try these starter commands:
- `/db-status` — confirm the connection is loaded, see the KB state, and list all configured connections
- `/db-use` — list configured connections; `/db-use <name>` to switch the active connection
- `/db-orient` — get a full structured orientation to the demo schema
- `/db-explain sales_orders` — plain-English explanation of the orders table
- `/db-explain column: sales_customers.deleted_at` — understand the soft-delete pattern
- `/db-profile sales_order_items` — statistical profile of the line items table
- `/db-joins sales_orders sales_customers hr_employees` — discover all join paths
- `"What are the known gotchas in this database?"` — natural language also works

The demo database schema is documented in `.claude/db-connections/demo-db/README.md`.

---

## Part 4 — Team Sharing

### Phase 1 — Shared Drive

Suitable for small teams without a git workflow:

1. Put the project's `db-knowledge/` folder on a shared drive (Dropbox, Google Drive, etc.)
2. Each analyst symlinks or copies it to their local project
3. Merge changes manually when multiple people update the same file

### Phase 2 — Git Repository

Recommended for teams that want history, reviews, and no merge conflicts:

1. Create a git repo containing `.claude/` (without `db-connections/active.yaml`)
2. Each analyst clones the repo and creates their own `active.yaml` locally
3. After a session, commit KB changes: `git add db-knowledge && git commit`
4. Open PRs for significant changes (new schema overviews, major gotchas)

**What to commit:**
- `db-knowledge/**` — all KB files
- `.claude/CLAUDE.md` — project instructions
- `.claude/db-connections/connections.example.yaml` — template (no real credentials)

**Never commit:**
- `.claude/db-connections/active.yaml` — real credentials / connection details

---

## Part 5 — MCP Server Setup (Optional)

By default the framework executes database queries via Bash (Python's `sqlite3` module for
SQLite, Python drivers for other databases). If you have an MCP server for your database,
you can configure the framework to call it instead — no Python drivers needed, and query
results come back as structured JSON rather than shell output.

### When to use MCP transport

| Situation | Recommended transport |
|---|---|
| Quick demo with SQLite | Either — `direct` requires no extra setup |
| No Python drivers installed | `mcp` — avoids driver install entirely |
| Snowflake with official MCP server | `mcp` — cleaner output, better error messages |
| Oracle / Athena | `direct` — no official MCP servers yet |

### Step 5a. Configure the connection

In your project's `.claude/db-connections/active.yaml`, set two fields on the connection
you want to use via MCP:

```yaml
- name: my-sqlite-db
  type: sqlite
  transport: mcp          # Switch from direct to mcp
  mcp_server: sqlite      # Must match the server name registered in Claude Code
  sqlite:
    path: ./data/mydb.db
```

The `mcp_server` value must match exactly how the server appears in Claude Code's MCP
settings — it becomes the middle segment of every tool call:
`mcp__{mcp_server}__read_query`, `mcp__{mcp_server}__list_tables`, etc.

### Step 5b. SQLite MCP quickstart

The official SQLite MCP server (`@modelcontextprotocol/server-sqlite`) is the reference
implementation. To install and register it:

```bash
# Install the SQLite MCP server
npx -y @modelcontextprotocol/server-sqlite /path/to/your.db
```

Then register it in Claude Code's MCP settings with the name `sqlite`. Once registered,
set `transport: mcp` and `mcp_server: sqlite` in your `active.yaml`.

### Step 5c. Hooks and safety

No hook changes are needed. The `db-safety` and `db-cost-gate` hooks already intercept
MCP tool calls — they scan the `query` field in MCP tool inputs the same way they scan
Bash commands.

### Step 5d. Suppressing approval prompts (optional)

MCP tool calls from within skills will prompt for approval the first time. To suppress
these prompts, add the specific MCP tool names to the `allowed-tools` frontmatter of the
relevant skill files in `~/.claude/skills/`:

```yaml
# In ~/.claude/skills/db-orient/SKILL.md frontmatter:
allowed-tools:
  - Read
  - Bash
  - Write
  - mcp__sqlite__read_query
  - mcp__sqlite__list_tables
  - mcp__sqlite__describe_table
```

Repeat for any skill that runs queries (`db-query`, `db-profile`, `db-joins`, etc.).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Hooks not firing | Hook not registered in `settings.json` | Re-check Part 1b; restart Claude Code |
| `ModuleNotFoundError: yaml` | PyYAML not installed | `pip install pyyaml` |
| `No active connection found` | `active.yaml` missing | Follow Part 2b |
| Safety hook blocking safe queries | False positive on keyword in command | Check the query for reserved words in non-SQL context; file an issue |
| `/db-orient` not recognized | Skill not installed globally | Re-run the `cp -r install/skills/db-orient ~/.claude/skills/` command in Part 1a |
| Cost gate not triggering | Dialect not Oracle/Athena | Expected — SQLite has no cost gate |
| Wrong SQL dialect | Dialect mismatch | Check `type:` in `active.yaml` matches your DB |
| `/db-use` shows wrong connections | Stale `active.yaml` | Verify `connections:` list in `active.yaml` has all your profiles |
| `/db-use <name>` says "not found" | Name mismatch | Connection `name:` field is case-sensitive — run `/db-use` with no args to see exact names |
