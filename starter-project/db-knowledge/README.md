# Knowledge Base — {Project Name}

This directory contains the accumulated knowledge about the databases used in this project.
It is maintained by the DB Analyst Framework and is designed to be version-controlled and shared.

**Last updated:** {YYYY-MM-DD}
**Maintained by:** {team or analyst name}

---

## Schemas

| Schema | Tables Documented | Last Updated | Notes |
|---|---|---|---|
| *(Add schemas here as they are documented)* | — | — | — |

---

## Quick Links

- [Cross-schema gotchas](./_gotchas.md)
- [Open questions](./_open-questions.md)

---

## How to Use This KB

- **During a session:** Claude loads this README and all schema overviews at startup.
  Ask about any table and Claude will check here first before querying the database.

- **Adding entries:** Use `/db-document` to draft a new table entry, or `/db-capture`
  to save a named query. Use `/db-orient` to generate a full schema overview.

- **Sharing:** This directory is designed to be committed to git (see `.gitignore` for
  what's excluded). Push changes after documenting significant findings.

---

## Directory Structure

```
db-knowledge/
├── README.md                    ← This file (schema index)
├── _gotchas.md                  ← Cross-schema warnings and traps
├── _open-questions.md           ← Unresolved questions
└── {schema}/
    ├── _schema-overview.md      ← Entity clusters, key relationships, common patterns
    ├── {table}.md               ← Per-table data dictionary entry
    └── _queries/
        └── {query-name}.sql     ← Named, reusable canonical queries
```
