import sys
from importlib.metadata import version

import pytest

from duet import __version__
from duet.cli import main


def test_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["duet", "--version"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == f"duet {version('duet')}"
    assert __version__ == version("duet")


def test_help_without_arguments(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["duet"])

    main()

    assert "usage: duet" in capsys.readouterr().out
