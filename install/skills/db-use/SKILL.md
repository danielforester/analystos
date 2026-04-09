---
name: db-use
description: Switch the active database connection, or list all configured connections. Run /db-use with no argument to see all connections; run /db-use <connection-name> to switch the active connection.
user-invocable: true
argument-hint: "[connection-name]"
allowed-tools:
  - Read
  - Edit
---

# /db-use

**Invocation:** `/db-use` or `/db-use <connection-name>`

Use this command to switch between configured database connections without editing
`active.yaml` by hand. With no argument it lists all available connections. With a
connection name it updates `active.yaml` and confirms the switch.

---

## Behavior

- **No argument:** Read `active.yaml` and list all connections with their type, display
  name, and which one is currently active.
- **With argument:** Validate the name exists, then update the `active:` field in
  `active.yaml`. Confirm the switch. If the name does not match any connection, show the
  list of valid names.

---

## Step 1: Read `active.yaml`

Read `.claude/db-connections/active.yaml`.

If the file does not exist:
```
No connection config found.
Copy `templates/connections.example.yaml` to `.claude/db-connections/active.yaml`
and fill in your connection details.
```
Stop here.

Parse:
- `active` — the currently active connection name
- `connections[]` — all defined connection profiles

---

## Step 2: No-argument mode — List Connections

If no argument was provided, output this format:

```
Configured Connections
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ▶ demo           Demo Database (SQLite)        sqlite
    oracle-prod    Production Oracle             oracle
    snowflake-dw   Data Warehouse (Snowflake)    snowflake

Active: demo
Run /db-use <name> to switch.
```

Rules:
- The currently active connection gets a `▶` prefix; all others get two spaces
- Align columns: name (left-padded to longest name), display_name, type
- If only one connection is configured, note: "Only one connection is configured —
  add more profiles to `active.yaml` to switch between them."

Stop here after listing.

---

## Step 3: Named-argument mode — Switch Connection

### 3a. Validate the name

Check if the argument matches any connection `name` in the list (case-sensitive).

If no match:
```
⚠️ No connection named "{argument}" found.

Configured connections: demo, oracle-prod, snowflake-dw
Run /db-use <name> to switch.
```
Stop here.

If the argument already matches `active`:
```
Already using "{argument}" ({display_name}).
No change made.
```
Stop here.

### 3b. Update `active.yaml`

Edit the `active:` line in `.claude/db-connections/active.yaml` to set the new value.

The `active:` key appears at the top of the file on its own line. Replace only that line:

```yaml
active: {new_connection_name}
```

Use the Edit tool with the exact old line as `old_string` and the new line as `new_string`.
Do not modify any other part of the file.

### 3c. Confirm

```
Switched to: {display_name} ({type})

  Type      : {type}
  Scope     : {schema_scope / database / sqlite.path as appropriate}
  Read-only : {Yes | No}
  Transport : {transport, default "direct"}

Run /db-status for full session details, or /db-orient to explore this database.
```

If the new connection uses `transport: mcp`, add:
```
  MCP server: {mcp_server}
```

---

## Notes

- This command edits only the `active:` field — it never modifies connection credentials
  or any other part of `active.yaml`
- `active.yaml` is gitignored; switching connections leaves no trace in git history
- To switch back, run `/db-use` to see the list, then `/db-use <previous-name>`
- The switch takes effect immediately for all subsequent commands in this session
