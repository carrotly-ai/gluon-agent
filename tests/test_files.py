"""Tests for the file scanner module."""

import tempfile
from pathlib import Path

from gluon.files import (
    _should_exclude_dir,
    _should_exclude_file,
    clear_cache,
    get_project_files,
    scan_project_files,
)


def test_should_exclude_dir():
    """Test directory exclusion patterns."""
    assert _should_exclude_dir("node_modules") is True
    assert _should_exclude_dir(".git") is True
    assert _should_exclude_dir("__pycache__") is True
    assert _should_exclude_dir(".venv") is True
    assert _should_exclude_dir("src") is False
    assert _should_exclude_dir("tests") is False


def test_should_exclude_file():
    """Test file exclusion patterns."""
    assert _should_exclude_file("test.pyc") is True
    assert _should_exclude_file(".DS_Store") is True
    assert _should_exclude_file(".env.local") is True
    assert _should_exclude_file("index.ts") is False
    assert _should_exclude_file("README.md") is False


def test_scan_project_files_empty_dir():
    """Test scanning an empty directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        files = scan_project_files(Path(tmpdir))
        assert files == []


def test_scan_project_files_root_files():
    """Test scanning root-level files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create some root files
        (tmppath / "README.md").write_text("# Test")
        (tmppath / "package.json").write_text("{}")
        (tmppath / ".DS_Store").write_text("")  # Should be excluded

        files = scan_project_files(tmppath, scan_paths=["."])
        paths = [f.path for f in files]

        assert "README.md" in paths
        assert "package.json" in paths
        assert ".DS_Store" not in paths


def test_scan_project_files_src_directory():
    """Test scanning src directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        src_dir = tmppath / "src"
        src_dir.mkdir()
        (src_dir / "index.ts").write_text("export {}")
        (src_dir / "utils.ts").write_text("export {}")

        files = scan_project_files(tmppath, scan_paths=["src"])
        paths = [f.path for f in files]
        types = {f.path: f.type for f in files}

        assert "src" in paths
        assert types["src"] == "directory"
        assert "src/index.ts" in paths
        assert types["src/index.ts"] == "file"


def test_scan_project_files_excludes_node_modules():
    """Test that node_modules is excluded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        src_dir = tmppath / "src"
        src_dir.mkdir()
        (src_dir / "index.ts").write_text("export {}")

        nm_dir = src_dir / "node_modules"
        nm_dir.mkdir()
        (nm_dir / "package.json").write_text("{}")

        files = scan_project_files(tmppath, scan_paths=["src"])
        paths = [f.path for f in files]

        assert "src/index.ts" in paths
        assert "src/node_modules" not in paths
        assert "src/node_modules/package.json" not in paths


def test_get_project_files_caching():
    """Test that file list is cached."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "test.txt").write_text("test")

        # First call should scan
        files1, truncated1 = get_project_files("test-project", tmppath)
        assert len(files1) == 1

        # Add a new file
        (tmppath / "test2.txt").write_text("test2")

        # Second call should return cached result
        files2, truncated2 = get_project_files("test-project", tmppath)
        assert len(files2) == 1  # Still 1, cached

        # Force refresh should see new file
        files3, truncated3 = get_project_files("test-project", tmppath, force_refresh=True)
        assert len(files3) == 2

        clear_cache()


def test_get_project_files_prefix_filter():
    """Test filtering by prefix."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        src_dir = tmppath / "src"
        src_dir.mkdir()
        (src_dir / "index.ts").write_text("")
        (src_dir / "utils.ts").write_text("")
        (tmppath / "README.md").write_text("")

        files, _ = get_project_files("test-project-2", tmppath, prefix="src")
        paths = [f.path for f in files]

        assert "src" in paths
        assert "src/index.ts" in paths
        assert "README.md" not in paths

        clear_cache()


def test_get_project_files_limit():
    """Test result limiting."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create many files
        for i in range(10):
            (tmppath / f"file{i}.txt").write_text("")

        files, truncated = get_project_files("test-project-3", tmppath, limit=5)

        assert len(files) == 5
        assert truncated is True

        clear_cache()


# ============================================================
# New tests for comprehensive file/directory coverage
# ============================================================


def test_scan_project_files_mixed_content():
    """Verify both files AND directories are returned together (CRITICAL)."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create directories (whitelisted names)
        (tmppath / "src").mkdir()
        (tmppath / "tests").mkdir()
        # Create files in src
        (tmppath / "src" / "index.ts").write_text("export {}")
        (tmppath / "src" / "utils.ts").write_text("export {}")
        # Create root files
        (tmppath / "README.md").write_text("# Test")
        (tmppath / "config.json").write_text("{}")

        files = scan_project_files(tmppath)

        # Check we have both types
        types = {f.type for f in files}
        assert "directory" in types, "No directories found in results"
        assert "file" in types, "No files found in results"

        # Count each type
        dir_count = sum(1 for f in files if f.type == "directory")
        file_count = sum(1 for f in files if f.type == "file")
        assert dir_count >= 2, f"Expected at least 2 directories, got {dir_count}"
        assert file_count >= 2, f"Expected at least 2 files, got {file_count}"


def test_scan_project_files_sort_order():
    """Test directories come before files, both alphabetically sorted."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create directories (using whitelisted names)
        (tmppath / "src").mkdir()  # Will be 's' alphabetically
        (tmppath / "tests").mkdir()  # Will be 't' alphabetically
        # Create files in src
        (tmppath / "src" / "a_file.ts").write_text("")
        (tmppath / "src" / "z_file.ts").write_text("")

        files = scan_project_files(tmppath, scan_paths=["src", "tests"])

        # Directories should come first
        first_file_idx = next(i for i, f in enumerate(files) if f.type == "file")
        for i in range(first_file_idx):
            assert files[i].type == "directory", f"Expected directory at position {i}, got {files[i].type}"

        # Within directories, alphabetically sorted
        dirs = [f.path for f in files if f.type == "directory"]
        assert dirs == sorted(dirs, key=str.lower), "Directories not alphabetically sorted"


def test_scan_project_files_nested_structure():
    """Test deeply nested files and directories are scanned."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create nested structure
        nested_dir = tmppath / "src" / "components" / "Button"
        nested_dir.mkdir(parents=True)
        (nested_dir / "index.tsx").write_text("export {}")
        (nested_dir / "styles.css").write_text("")

        utils_dir = tmppath / "src" / "utils"
        utils_dir.mkdir(parents=True)
        (utils_dir / "helpers.ts").write_text("")

        files = scan_project_files(tmppath, scan_paths=["src"])
        paths = [f.path for f in files]
        types = {f.path: f.type for f in files}

        # Check all directory levels appear
        assert "src" in paths
        assert types["src"] == "directory"
        assert "src/components" in paths
        assert types["src/components"] == "directory"
        assert "src/components/Button" in paths
        assert types["src/components/Button"] == "directory"

        # Check nested files appear with full paths
        assert "src/components/Button/index.tsx" in paths
        assert types["src/components/Button/index.tsx"] == "file"
        assert "src/utils/helpers.ts" in paths
        assert types["src/utils/helpers.ts"] == "file"


def test_get_project_files_limit_truncates_files_first():
    """With low limit, directories preserved (sorted first), files truncated."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create many directories (whitelisted names)
        for name in ["src", "tests", "docs", "scripts", "lib", "app", "config", "api"]:
            d = tmppath / name
            d.mkdir()
            # Add a file in each
            (d / "index.ts").write_text("")

        # Also create root files
        for i in range(5):
            (tmppath / f"root{i}.txt").write_text("")

        files, truncated = get_project_files("test-limit-dirs", tmppath, limit=8)

        # With limit=8 and directories sorted first, first 8 should be directories
        assert len(files) == 8
        assert truncated is True

        # All 8 should be directories (since there are 8+ whitelisted dirs)
        dir_count = sum(1 for f in files if f.type == "directory")
        assert dir_count == 8, f"Expected 8 directories, got {dir_count}"

        clear_cache()


def test_get_project_files_prefix_filter_both_types():
    """Test prefix filter works on both files AND directories."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create src with nested structure
        (tmppath / "src").mkdir()
        (tmppath / "src" / "utils").mkdir()
        (tmppath / "src" / "index.ts").write_text("")
        (tmppath / "src" / "utils" / "helpers.ts").write_text("")
        # Create tests (should be excluded by filter)
        (tmppath / "tests").mkdir()
        (tmppath / "tests" / "test.py").write_text("")

        files, _ = get_project_files("test-prefix-both", tmppath, prefix="src")
        paths = [f.path for f in files]
        types = {f.path: f.type for f in files}

        # src directory should appear
        assert "src" in paths
        assert types["src"] == "directory"

        # src subdirectory should appear
        assert "src/utils" in paths
        assert types["src/utils"] == "directory"

        # src files should appear
        assert "src/index.ts" in paths
        assert types["src/index.ts"] == "file"

        # tests should NOT appear (filtered out)
        assert "tests" not in paths
        assert "tests/test.py" not in paths

        clear_cache()


def test_scan_project_files_root_only():
    """Test project with only root files (no matching DEFAULT_SCAN_PATHS)."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create only root files, no whitelisted directories
        (tmppath / "README.md").write_text("# Test")
        (tmppath / "setup.py").write_text("")
        (tmppath / "Makefile").write_text("")
        # Create a non-whitelisted directory (should NOT appear)
        (tmppath / "custom_dir").mkdir()
        (tmppath / "custom_dir" / "file.txt").write_text("")

        # Scan only root (.)
        files = scan_project_files(tmppath, scan_paths=["."])
        paths = [f.path for f in files]
        types = {f.path: f.type for f in files}

        # Root files should appear
        assert "README.md" in paths
        assert types["README.md"] == "file"
        assert "setup.py" in paths
        assert "Makefile" in paths

        # Non-whitelisted directory should NOT appear
        assert "custom_dir" not in paths
        assert "custom_dir/file.txt" not in paths

        # No directories should appear (root scan doesn't add directories)
        dir_count = sum(1 for f in files if f.type == "directory")
        assert dir_count == 0, f"Expected 0 directories for root-only scan, got {dir_count}"


def test_scan_project_files_empty_directories():
    """Test empty whitelisted directories are still listed."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create empty whitelisted directories
        (tmppath / "src").mkdir()
        (tmppath / "tests").mkdir()
        # Create one with content for comparison
        (tmppath / "docs").mkdir()
        (tmppath / "docs" / "README.md").write_text("")

        files = scan_project_files(tmppath, scan_paths=["src", "tests", "docs"])
        paths = [f.path for f in files]
        types = {f.path: f.type for f in files}

        # Empty directories should appear
        assert "src" in paths
        assert types["src"] == "directory"
        assert "tests" in paths
        assert types["tests"] == "directory"

        # Non-empty directory should also appear
        assert "docs" in paths
        assert "docs/README.md" in paths


def test_scan_project_files_files_inside_nested_dirs():
    """Test files inside deeply nested directories are found."""
    clear_cache()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create deep nesting (within max_depth=4)
        deep_dir = tmppath / "src" / "a" / "b" / "c"
        deep_dir.mkdir(parents=True)
        (deep_dir / "deep.ts").write_text("export {}")

        files = scan_project_files(tmppath, scan_paths=["src"])
        paths = [f.path for f in files]
        types = {f.path: f.type for f in files}

        # The deep file should appear
        assert "src/a/b/c/deep.ts" in paths, f"Deep file not found. Paths: {paths}"
        assert types["src/a/b/c/deep.ts"] == "file"

        # All intermediate directories should appear
        assert "src/a" in paths
        assert "src/a/b" in paths
        assert "src/a/b/c" in paths
