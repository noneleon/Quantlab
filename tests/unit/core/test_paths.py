from pathlib import Path

from quantlab.core.paths import HomeLayout, default_home


def test_explicit_env_wins():
    assert default_home({"QUANTLAB_HOME": "/x/y"}, "linux") == Path("/x/y")


def test_windows_uses_localappdata():
    home = default_home({"LOCALAPPDATA": r"C:\Users\me\AppData\Local"}, "win32")
    assert home == Path(r"C:\Users\me\AppData\Local") / "QuantLab"


def test_windows_without_localappdata_falls_back_to_profile():
    assert default_home({}, "win32") == Path.home() / "AppData" / "Local" / "QuantLab"


def test_linux_xdg_and_default():
    assert default_home({"XDG_DATA_HOME": "/xdg"}, "linux") == Path("/xdg/quantlab")
    assert default_home({}, "linux") == Path.home() / ".local" / "share" / "quantlab"


def test_layout_ensure_is_idempotent_and_non_destructive(tmp_path):
    layout = HomeLayout.resolve(tmp_path / "home")
    layout.ensure()
    marker = layout.runs_dir / "keep.txt"
    marker.write_text("user data")
    layout.ensure()
    assert marker.read_text() == "user data"
    for d in layout.directories():
        assert d.is_dir()
    assert layout.config_file == layout.root / "quantlab.toml"
    assert layout.lean_data_dir == layout.root / "data" / "lean"
