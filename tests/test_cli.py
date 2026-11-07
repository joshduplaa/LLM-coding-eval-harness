"""The CLI's help text renders for the top level and every subcommand.

argparse treats `%` in help strings as a format character, so an unescaped "95%" crashed
`evalharness --help` (found while checking the README quickstart in Milestone 6).
"""

import pytest

from evalharness.cli import build_parser

SUBCOMMANDS = ["list-tasks", "validate", "run", "runs", "report", "import", "serve"]


@pytest.mark.parametrize("argv", [["--help"]] + [[c, "--help"] for c in SUBCOMMANDS])
def test_help_renders(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out
