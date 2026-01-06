#!/usr/bin/env python3
"""
Migrate project and workspace paths to use ${HOME} for portability.

This script converts absolute paths like `/Users/mcutler/...` to use
environment variables like `${HOME}/...`, making paths portable across
different environments (host, Docker, CI/CD).

Usage:
    python migrate_paths.py

The script will:
1. Show what paths will be migrated
2. Ask for confirmation
3. Update the database
"""

import re
import sqlite3
from pathlib import Path


def migrate_paths():
    """Migrate paths in the gluon database to use environment variables."""
    db_path = Path.home() / ".gluon" / "gluon.db"

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Patterns to match and replace
    home_dir = str(Path.home())
    patterns = [
        (f"^{re.escape(home_dir)}/", "${HOME}/", "home directory"),
    ]

    tables_to_migrate = ["projects", "workspaces"]
    total_updated = 0

    for table in tables_to_migrate:
        print(f"\n{'=' * 60}")
        print(f"Migrating {table}")
        print("=" * 60)

        # Get all rows with paths
        cursor.execute(f"SELECT id, name, path FROM {table}")
        rows = cursor.fetchall()

        if not rows:
            print(f"No {table} found.")
            continue

        updates = []
        for row_id, name, path in rows:
            new_path = path
            for pattern, replacement, desc in patterns:
                if re.match(pattern, path):
                    new_path = re.sub(pattern, replacement, path)
                    break

            if new_path != path:
                updates.append((row_id, name, path, new_path))
                print(f"\n  {name}:")
                print(f"    Old: {path}")
                print(f"    New: {new_path}")

        if not updates:
            print(f"No paths to migrate in {table}.")
            continue

        # Ask for confirmation
        print(f"\nFound {len(updates)} {table} to update.")
        response = input(f"Update these {table}? (y/n): ").strip().lower()

        if response == "y":
            for row_id, name, old_path, new_path in updates:
                cursor.execute(f"UPDATE {table} SET path = ? WHERE id = ?", (new_path, row_id))
                total_updated += 1
                print(f"  ✓ Updated {name}")

    # Commit changes
    if total_updated > 0:
        conn.commit()
        print(f"\n{'=' * 60}")
        print(f"✓ Successfully updated {total_updated} paths")
        print(f"{'=' * 60}")
        print("\nYour paths are now portable across environments!")
        print("- macOS: ${HOME} → /Users/mcutler")
        print("- Docker: ${HOME} → /home/gluon")
        print("- CI/CD: ${HOME} → whatever the environment specifies")
    else:
        print("\nNo paths needed updating.")

    conn.close()


if __name__ == "__main__":
    migrate_paths()
