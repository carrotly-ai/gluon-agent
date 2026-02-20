"""Tests for expand_path() in models.py."""

from pathlib import Path

from gluon.models import expand_path


class TestTildeExpansion:
    """Tests for ~ home directory expansion."""

    def test_tilde_expands_to_home(self):
        result = expand_path("~")
        assert result == Path.home()
        assert result.is_absolute()

    def test_tilde_subdir_expands(self):
        result = expand_path("~/subdir")
        assert result == Path.home() / "subdir"
        assert result.is_absolute()

    def test_tilde_nested_subdir(self):
        result = expand_path("~/a/b/c")
        assert result == Path.home() / "a" / "b" / "c"


class TestEnvVarExpansion:
    """Tests for environment variable expansion."""

    def test_dollar_home_expands(self, monkeypatch):
        monkeypatch.setenv("HOME", "/fake/home")
        result = expand_path("$HOME/subdir")
        assert result == Path("/fake/home/subdir")

    def test_braced_home_expands(self, monkeypatch):
        monkeypatch.setenv("HOME", "/fake/home")
        result = expand_path("${HOME}/subdir")
        assert result == Path("/fake/home/subdir")

    def test_custom_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_PROJECT_DIR", "/opt/projects")
        result = expand_path("$MY_PROJECT_DIR/app")
        assert result == Path("/opt/projects/app")

    def test_undefined_env_var_left_as_is(self, monkeypatch):
        monkeypatch.delenv("UNDEFINED_VAR_XYZ", raising=False)
        result = expand_path("$UNDEFINED_VAR_XYZ/subdir")
        # os.path.expandvars leaves undefined vars as-is
        assert "$UNDEFINED_VAR_XYZ" in str(result)


class TestAbsolutePaths:
    """Tests for absolute path handling."""

    def test_absolute_path_unchanged(self):
        result = expand_path("/usr/local/bin")
        assert result == Path("/usr/local/bin")

    def test_absolute_path_with_trailing_slash(self):
        result = expand_path("/usr/local/bin/")
        assert str(result) == "/usr/local/bin"


class TestRelativePaths:
    """Tests for relative path handling."""

    def test_relative_path_unchanged(self):
        result = expand_path("src/main.py")
        assert result == Path("src/main.py")
        assert not result.is_absolute()

    def test_dot_relative_path(self):
        result = expand_path("./src")
        assert result == Path("src")


class TestNoSpecialChars:
    """Tests for paths without special characters."""

    def test_plain_path_unchanged(self):
        result = expand_path("/opt/project/src")
        assert result == Path("/opt/project/src")

    def test_path_object_input(self):
        result = expand_path(Path("/opt/project"))
        assert result == Path("/opt/project")
