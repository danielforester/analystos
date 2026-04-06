# DB Analyst Framework — Setup Guide

This guide walks you through installing the DB Analyst Framework into Claude Code
and configuring it for your first project.

---

## Prerequisites

- [Claude Code](https://claude.ai/code) installed and authenticated
- Python 3.9+ on your PATH (for hooks)
- `pip install pyyaml` (required by the cost-gate hook for Oracle/Athena connections)
- For Oracle: Oracle client libraries + `cx_Oracle` or `python-oracledb`
- For Athena: AWS CLI configured, `boto3` installed
- For SQLite demo: just `sqlite3` (included with Python and most OS installs)

---

## Part 1 — Global Install (do once per machine)

The framework's core components (skills and hooks) are installed globally so they're
available in every project.

### 1a. Copy skills and hooks

```bash
# Create directories if they don't exist
mkdir -p ~/.claude/skills
mkdir -p ~/.claude/hooks

# Copy from this repo — skills
cp install/skills/db-dialect.md    ~/.claude/skills/
cp install/skills/db-cost-check.md ~/.claude/skills/
cp install/skills/db-introspect.md ~/.claude/skills/
cp install/skills/db-sample.md     ~/.claude/skills/
cp install/skills/db-orient.md     ~/.claude/skills/
cp install/skills/db-explain.md    ~/.claude/skills/
cp install/skills/db-profile.md    ~/.claude/skills/
cp install/skills/db-joins.md      ~/.claude/skills/
cp install/skills/db-status.md     ~/.claude/skills/

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

## Part 2 — Per-Project Setup

Repeat these steps for each analyst project or repository.

### 2a. Create the .claude directory structure

```bash
mkdir -p .claude/db-connections
mkdir -p .claude/db-knowledge
```

### 2b. Copy the project templates

```bash
# From the analystos repo root:
cp templates/project-CLAUDE.md         .claude/CLAUDE.md
cp templates/connections.example.yaml   .claude/db-connections/connections.example.yaml
cp templates/db-knowledge/README.md     .claude/db-knowledge/README.md
cp templates/db-knowledge/_gotchas.md   .claude/db-knowledge/_gotchas.md
cp templates/db-knowledge/_open-questions.md .claude/db-knowledge/_open-questions.md
cp templates/.gitignore                 .gitignore   # or merge into existing
```

### 2c. Configure your connection

```bash
# Copy the example and fill in your values
cp .claude/db-connections/connections.example.yaml .claude/db-connections/active.yaml
```

Edit `active.yaml`:
- Set `active:` to the name of your connection
- Fill in your database host, credentials (as env var names), and schema scope
- Set cost thresholds appropriate for your environment

**Never commit `active.yaml`** — verify it's in `.gitignore`.

### 2d. Set credential environment variables

Add your credentials to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.):

```bash
# Oracle example
export ORACLE_USER=my_read_only_user
export ORACLE_PASSWORD=my_password

# Athena example
export AWS_PROFILE=my-analytics-profile
```

Or use a `.env` file at the project root (also gitignored):
```bash
ORACLE_USER=my_read_only_user
ORACLE_PASSWORD=my_password
```

---

## Part 3 — Demo Database Quickstart (SQLite, no credentials)

The fastest way to try the framework is the built-in demo database.

```bash
# 1. Create the database (from the analystos repo root)
sqlite3 demo/demo.db < demo/create-demo-db.sql

# 2. Set up the demo project structure
mkdir -p my-demo-project/.claude/db-connections
mkdir -p my-demo-project/.claude/db-knowledge

cp demo/demo-connections.yaml my-demo-project/.claude/db-connections/active.yaml
cp -r demo/demo-knowledge/    my-demo-project/.claude/db-knowledge/
cp templates/project-CLAUDE.md my-demo-project/.claude/CLAUDE.md

# 3. Open Claude Code in the demo project
cd my-demo-project
claude
```

Claude will greet you with: `Connected to DB Analyst Demo Database (sqlite). KB loaded...`

Try these starter commands:
- `/db-status` — confirm the connection is loaded and see the KB state
- `/db-orient` — get a full structured orientation to the demo schema
- `/db-explain sales_orders` — plain-English explanation of the orders table
- `/db-explain column: sales_customers.deleted_at` — understand the soft-delete pattern
- `/db-profile sales_order_items` — statistical profile of the line items table
- `/db-joins sales_orders sales_customers hr_employees` — discover all join paths
- `"What are the known gotchas in this database?"` — natural language also works

---

## Part 4 — Team Sharing

### Phase 1 — Shared Drive

Suitable for small teams without a git workflow:

1. Put the project's `.claude/db-knowledge/` folder on a shared drive (Dropbox, Google Drive, etc.)
2. Each analyst symlinks or copies it to their local project
3. Merge changes manually when multiple people update the same file

### Phase 2 — Git Repository

Recommended for teams that want history, reviews, and no merge conflicts:

1. Create a git repo containing `.claude/` (without `db-connections/active.yaml`)
2. Each analyst clones the repo and creates their own `active.yaml` locally
3. After a session, commit KB changes: `git add .claude/db-knowledge && git commit`
4. Open PRs for significant changes (new schema overviews, major gotchas)

**What to commit:**
- `.claude/db-knowledge/**` — all KB files
- `.claude/CLAUDE.md` — project instructions
- `.claude/db-connections/connections.example.yaml` — template (no real credentials)

**Never commit:**
- `.claude/db-connections/active.yaml` — real credentials / connection details

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Hooks not firing | Hook not registered in `settings.json` | Re-check Part 1b; restart Claude Code |
| `ModuleNotFoundError: yaml` | PyYAML not installed | `pip install pyyaml` |
| `No active connection found` | `active.yaml` missing | Follow Part 2c |
| Safety hook blocking safe queries | False positive on keyword in command | Check the query for reserved words in non-SQL context; file an issue |
| `/db-orient` not recognized | Skill not installed globally | Re-run the `cp install/skills/...` commands in Part 1a |
| Cost gate not triggering | Dialect not Oracle/Athena | Expected — SQLite has no cost gate |
| Wrong SQL dialect | Dialect mismatch | Check `type:` in `active.yaml` matches your DB |
