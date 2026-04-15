#!/usr/bin/env python3
"""
kb-to-obsidian.py — Obsidian vault conversion for the AnalystOS knowledge base.

Converts a standard markdown knowledge base (as produced by /db-document, /db-orient,
and /db-capture) into an Obsidian-compatible vault by:

  - Rewriting relative markdown links to [[wikilinks]]
  - Optionally injecting YAML frontmatter from parsed header fields (--frontmatter)
  - Optionally converting ⚠️ warning patterns to Obsidian callout syntax (--callouts)
  - Writing all output to a separate directory — never modifying source files

The output directory is safe to delete and regenerate at any time.
Add it to .gitignore (the framework's default .gitignore already does this).

Usage:
    python scripts/kb-to-obsidian.py [OPTIONS]

Options:
    --source PATH      Source KB directory (default: db-knowledge/)
    --output PATH      Output vault directory (default: .claude/_obsidian-vault/)
    --frontmatter      Inject YAML frontmatter into files that don't have it
    --callouts         Convert ⚠️ bullet patterns to Obsidian callout blocks
    --watch            Re-run automatically when source files change
    --help             Show this help message and exit

Examples:
    python scripts/kb-to-obsidian.py
    python scripts/kb-to-obsidian.py --frontmatter --callouts
    python scripts/kb-to-obsidian.py --source db-knowledge/ --output ~/vault/ --watch
"""

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path


# ── Markdown link pattern ─────────────────────────────────────────────────────
# Matches [link text](relative/path.md) — standard markdown links to .md files.
# Does NOT match external URLs (http/https) or anchor-only links (#section).
MD_LINK_PATTERN = re.compile(
    r"\[([^\]]+)\]\((?!https?://)(?!#)([^)]+\.md[^)]*)\)"
)

# ── Frontmatter field patterns ────────────────────────────────────────────────
# Parses header metadata from the first ~20 lines of a KB table file.
FRONTMATTER_FIELDS = {
    "schema":          re.compile(r"\*\*Schema:\*\*\s*`?(\w+)`?", re.IGNORECASE),
    "database":        re.compile(r"\*\*Database:\*\*\s*(.+)", re.IGNORECASE),
    "last_documented": re.compile(r"\*\*Last Documented:\*\*\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    "documented_by":   re.compile(r"\*\*Documented By:\*\*\s*(.+)", re.IGNORECASE),
    "rows_approx":     re.compile(r"\*\*Rows \(approx\):\*\*\s*(.+)", re.IGNORECASE),
}

# ── Status field ──────────────────────────────────────────────────────────────
STATUS_PATTERN = re.compile(r"\*\*Status:\*\*\s*(draft|reviewed|trusted)", re.IGNORECASE)

# ── Warning/gotcha patterns for callout conversion ────────────────────────────
# Matches: "- ⚠️ **Title** — description"
# Converts to Obsidian callout block syntax.
WARNING_BULLET_PATTERN = re.compile(
    r"^([ \t]*)- ⚠️ \*\*(.+?)\*\*\s*[—–-]\s*(.+)$",
    re.MULTILINE,
)

# ── Files to skip ─────────────────────────────────────────────────────────────
SKIP_FILES = {"README.md"}  # Index is not a knowledge file; skip wikilink conversion


def convert_links_to_wikilinks(content: str, source_file: Path, source_root: Path) -> str:
    """
    Rewrite relative markdown links to Obsidian [[wikilinks]].

    Standard: [orders](../orders/orders.md) → [[orders]]
    With alias: [View orders](_schema-overview.md) → [[_schema-overview|View orders]]
    """
    def replace_link(match: re.Match) -> str:
        link_text = match.group(1)
        link_target = match.group(2)

        # Strip anchor fragment for wikilink target
        target_path = link_target.split("#")[0]
        anchor = link_target[len(target_path):]  # e.g. "#section"

        # Resolve to just the filename stem (Obsidian matches by filename)
        target_stem = Path(target_path).stem

        # If link text matches the target stem (case-insensitive), use simple wikilink
        if link_text.lower() == target_stem.lower():
            return f"[[{target_stem}{anchor}]]"
        else:
            # Use Obsidian alias syntax: [[target|display text]]
            return f"[[{target_stem}{anchor}|{link_text}]]"

    return MD_LINK_PATTERN.sub(replace_link, content)


def parse_frontmatter_values(content: str) -> dict:
    """
    Extract metadata fields from the body of a KB table file.
    Returns a dict of field → value for any fields found.
    """
    values = {}

    # Only scan the first 30 lines for header metadata
    header = "\n".join(content.splitlines()[:30])

    for field, pattern in FRONTMATTER_FIELDS.items():
        match = pattern.search(header)
        if match:
            values[field] = match.group(1).strip().rstrip("*").strip()

    # Status
    status_match = STATUS_PATTERN.search(header)
    if status_match:
        values["status"] = status_match.group(1).lower()
    else:
        values.setdefault("status", "draft")

    # Grain — look for "One row per {grain}" in Grain section
    grain_match = re.search(r"## Grain\s*\n+One row per (.+?)(?:\.|$)", content, re.IGNORECASE)
    if grain_match:
        values["grain"] = grain_match.group(1).strip().rstrip(".")

    return values


def inject_frontmatter(content: str, source_file: Path) -> str:
    """
    Inject YAML frontmatter into a KB file if it doesn't already have it.
    Parses field values from the file body.
    """
    # Already has frontmatter
    if content.startswith("---\n"):
        return content

    values = parse_frontmatter_values(content)
    if not values:
        return content  # Nothing to inject

    # Build frontmatter block
    lines = ["---"]
    field_order = ["schema", "database", "grain", "status", "last_documented", "documented_by", "rows_approx"]
    for field in field_order:
        if field in values:
            lines.append(f"{field}: {values[field]}")
    lines.append("---")
    lines.append("")

    return "\n".join(lines) + content


def convert_callouts(content: str) -> str:
    """
    Convert ⚠️ warning bullet patterns to Obsidian callout blocks.

    Input:  - ⚠️ **Soft delete** — filter WHERE deleted_at IS NULL
    Output:
    > [!warning] Soft delete
    > filter WHERE deleted_at IS NULL
    """
    def replace_warning(match: re.Match) -> str:
        indent = match.group(1)
        title = match.group(2).strip()
        body = match.group(3).strip()
        return f"{indent}> [!warning] {title}\n{indent}> {body}"

    return WARNING_BULLET_PATTERN.sub(replace_warning, content)


def has_existing_frontmatter(content: str) -> bool:
    """Return True if the file already starts with YAML frontmatter."""
    return content.startswith("---\n")


def process_file(
    source_file: Path,
    source_root: Path,
    output_root: Path,
    *,
    inject_fm: bool,
    convert_callout_syntax: bool,
) -> None:
    """
    Process a single source file and write the result to the output directory.
    """
    # Determine output path (mirrors source structure)
    rel_path = source_file.relative_to(source_root)
    output_file = output_root / rel_path
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Read source (always UTF-8)
    try:
        content = source_file.read_text(encoding="utf-8")
    except OSError as e:
        print(f"  ⚠️  Could not read {source_file}: {e}", file=sys.stderr)
        return

    # Skip files that should be copied verbatim
    if source_file.name in SKIP_FILES:
        output_file.write_text(content, encoding="utf-8")
        return

    # 1. Inject frontmatter (if requested and not already present)
    if inject_fm and not has_existing_frontmatter(content):
        content = inject_frontmatter(content, source_file)

    # 2. Convert ⚠️ patterns to callouts (if requested)
    if convert_callout_syntax:
        content = convert_callouts(content)

    # 3. Rewrite relative .md links to [[wikilinks]]
    content = convert_links_to_wikilinks(content, source_file, source_root)

    # Write output
    output_file.write_text(content, encoding="utf-8")


def sync_vault(
    source_root: Path,
    output_root: Path,
    *,
    inject_fm: bool,
    convert_callout_syntax: bool,
    verbose: bool = True,
) -> int:
    """
    Sync the source KB to the output vault. Returns the number of files processed.
    """
    output_root.mkdir(parents=True, exist_ok=True)

    # Copy the .obsidian config stub if it doesn't exist (blank vault config)
    obsidian_dir = output_root / ".obsidian"
    if not obsidian_dir.exists():
        obsidian_dir.mkdir()
        # Minimal app.json to suppress Obsidian's first-run prompts
        (obsidian_dir / "app.json").write_text("{}", encoding="utf-8")

    count = 0
    for source_file in sorted(source_root.rglob("*")):
        if not source_file.is_file():
            continue

        # Skip hidden files and the Obsidian config directory if it somehow exists in source
        if any(part.startswith(".") for part in source_file.parts):
            continue

        # Only process markdown and SQL files; copy everything else verbatim
        if source_file.suffix in (".md", ".sql"):
            process_file(
                source_file,
                source_root,
                output_root,
                inject_fm=inject_fm,
                convert_callout_syntax=convert_callout_syntax,
            )
        else:
            rel_path = source_file.relative_to(source_root)
            output_file = output_root / rel_path
            output_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, output_file)

        count += 1
        if verbose:
            rel = source_file.relative_to(source_root)
            print(f"  ✓  {rel}")

    return count


def watch_loop(
    source_root: Path,
    output_root: Path,
    *,
    inject_fm: bool,
    convert_callout_syntax: bool,
    poll_interval: float = 2.0,
) -> None:
    """
    Poll the source directory for changes and re-sync when files are modified.
    Uses mtime-based polling (no external dependencies).
    """
    print(f"Watching {source_root} for changes (Ctrl+C to stop)…")

    def get_mtimes() -> dict:
        return {
            str(f): f.stat().st_mtime
            for f in source_root.rglob("*")
            if f.is_file()
        }

    last_mtimes = get_mtimes()

    try:
        while True:
            time.sleep(poll_interval)
            current_mtimes = get_mtimes()

            changed = (
                set(current_mtimes) - set(last_mtimes)  # new files
                | {f for f in current_mtimes if current_mtimes[f] != last_mtimes.get(f)}  # modified
            )

            if changed:
                print(f"\n[{time.strftime('%H:%M:%S')}] Changes detected in {len(changed)} file(s):")
                count = sync_vault(
                    source_root,
                    output_root,
                    inject_fm=inject_fm,
                    convert_callout_syntax=convert_callout_syntax,
                    verbose=True,
                )
                print(f"Vault updated ({count} files). Watching…")
                last_mtimes = get_mtimes()

    except KeyboardInterrupt:
        print("\nStopped watching.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert an AnalystOS knowledge base to an Obsidian-compatible vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("db-knowledge/"),
        metavar="PATH",
        help="Source KB directory (default: db-knowledge/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".claude/_obsidian-vault/"),
        metavar="PATH",
        help="Output vault directory (default: .claude/_obsidian-vault/)",
    )
    parser.add_argument(
        "--frontmatter",
        action="store_true",
        help="Inject YAML frontmatter from parsed header fields",
    )
    parser.add_argument(
        "--callouts",
        action="store_true",
        help="Convert ⚠️ warning patterns to Obsidian callout blocks",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Re-run automatically when source files change",
    )

    args = parser.parse_args()

    source_root: Path = args.source.expanduser().resolve()
    output_root: Path = args.output.expanduser().resolve()

    if not source_root.exists():
        print(f"Error: Source directory not found: {source_root}", file=sys.stderr)
        sys.exit(1)

    if source_root == output_root:
        print("Error: Source and output directories must be different.", file=sys.stderr)
        sys.exit(1)

    print(f"Source : {source_root}")
    print(f"Output : {output_root}")
    print(f"Options: frontmatter={args.frontmatter}, callouts={args.callouts}, watch={args.watch}")
    print()

    count = sync_vault(
        source_root,
        output_root,
        inject_fm=args.frontmatter,
        convert_callout_syntax=args.callouts,
        verbose=True,
    )

    print(f"\nDone. {count} file(s) written to {output_root}")

    if args.watch:
        print()
        watch_loop(
            source_root,
            output_root,
            inject_fm=args.frontmatter,
            convert_callout_syntax=args.callouts,
        )


if __name__ == "__main__":
    main()
