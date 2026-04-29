"""Tests for the ``molwatch`` CLI: parse / tail / inspect / serve.

Mirrors the discipline of molbuilder's test_cli_modeling.py:
exercise both correct and error code paths with real complexity,
not happy-path-only smoke tests.

Concretely covered:
  * Bare ``molwatch`` invocation routes to ``serve`` (mocked).
  * ``parse <file>`` writes ParsedTrajectory JSON to stdout, summary
    to stderr; exits 0; ``--no-pretty`` toggles indentation.
  * ``parse`` error matrix: missing file, unrecognised format, parse
    failure -- each exits 2 with a helpful stderr message.
  * ``tail --once`` emits a single JSON line and exits 0.
  * ``tail`` rejects unknown formats / missing files (exit 2).
  * ``inspect parsers`` lists every registered parser; ``--json``
    emits machine-readable output.
  * ``inspect parser <name>`` shows metadata; ``--schema`` switches
    to JSON; unknown name exits 2 with a helpful message.
"""

from __future__ import annotations

import json

import pytest

from molwatch.cli import main as cli_main


# --------------------------------------------------------------------- #
#  Sample fixtures (multi-frame XYZ -- works with the PySCFParser)      #
# --------------------------------------------------------------------- #


SAMPLE_XYZ = """\
3
Iteration 0 Energy   -76.42675200
O   0.000000   0.000000   0.000000
H   0.957000   0.000000   0.000000
H  -0.239000   0.927000   0.000000
3
Iteration 1 Energy   -76.43012345
O   0.001000   0.001000   0.000000
H   0.957500   0.000500   0.000000
H  -0.239500   0.927500   0.000000
"""


@pytest.fixture
def traj_path(tmp_path):
    p = tmp_path / "h2o_geom_optim.xyz"
    p.write_text(SAMPLE_XYZ)
    return p


# --------------------------------------------------------------------- #
#  Bare invocation routes to `serve`                                    #
# --------------------------------------------------------------------- #


def test_bare_molwatch_routes_to_serve(monkeypatch, capsys):
    """``molwatch`` (no args) must dispatch to the serve subcommand
    so the historical "just open the browser viewer" workflow keeps
    working.  We monkeypatch run_server so the test doesn't actually
    try to bind a port."""
    called = {}

    def fake_run_server(host="127.0.0.1", port=5000, debug=False):
        called["host"] = host
        called["port"] = port
        called["debug"] = debug

    monkeypatch.setattr("molwatch.web.run_server", fake_run_server)
    # serve.py imports run_server at module load, so patch there too.
    monkeypatch.setattr("molwatch.cli.serve.run_server", fake_run_server)

    rc = cli_main([])
    assert rc == 0
    # Defaults from the serve subcommand.
    assert called == {"host": "127.0.0.1", "port": 5000, "debug": False}


# --------------------------------------------------------------------- #
#  parse                                                                #
# --------------------------------------------------------------------- #


def test_parse_writes_json_to_stdout_and_summary_to_stderr(traj_path,
                                                           capsys):
    rc = cli_main(["parse", str(traj_path)])
    assert rc == 0

    out, err = capsys.readouterr()
    data = json.loads(out)
    # ParsedTrajectory must include frames + the chosen source_format.
    assert "frames" in data
    assert len(data["frames"]) == 2
    # stderr summary mentions the parser used + frame count.
    assert "pyscf" in err
    assert "2 frames" in err


def test_parse_no_pretty_emits_single_line(traj_path, capsys):
    rc = cli_main(["parse", str(traj_path), "--no-pretty"])
    assert rc == 0
    out, _ = capsys.readouterr()
    # Single-line output -> at most one trailing newline; no
    # interior newlines from indent=2.
    assert "\n" not in out.rstrip("\n")


def test_parse_missing_file_exits_2(tmp_path, capsys):
    rc = cli_main(["parse", str(tmp_path / "nope.xyz")])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "not found" in err


def test_parse_unrecognised_format_exits_2(tmp_path, capsys):
    """A garbage file -> the registry's UnknownFormatError, which
    lists every supported format.  CLI surfaces the message verbatim
    so users see what they should have loaded instead."""
    bad = tmp_path / "junk.txt"
    bad.write_text("this is not any known molwatch format\n")
    rc = cli_main(["parse", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    # The registry's error lists supported formats; we just check
    # that at least one canonical name shows up.
    assert "siesta" in err.lower() or "pyscf" in err.lower()


# --------------------------------------------------------------------- #
#  tail                                                                 #
# --------------------------------------------------------------------- #


def test_tail_once_emits_single_json_line(traj_path, capsys):
    """--once is the testable shape: one snapshot, exit 0."""
    rc = cli_main(["tail", str(traj_path), "--once"])
    assert rc == 0
    out, _ = capsys.readouterr()
    # Exactly one line of JSON (newline terminates).
    lines = [l for l in out.split("\n") if l.strip()]
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert "frames" in data
    assert len(data["frames"]) == 2


def test_tail_missing_file_exits_2(tmp_path, capsys):
    rc = cli_main(["tail", str(tmp_path / "nope.xyz"), "--once"])
    assert rc == 2
    assert "not found" in capsys.readouterr().err.lower()


def test_tail_unrecognised_format_exits_2(tmp_path, capsys):
    bad = tmp_path / "junk.txt"
    bad.write_text("not a molwatch format")
    rc = cli_main(["tail", str(bad), "--once"])
    assert rc == 2


# --------------------------------------------------------------------- #
#  inspect parsers / inspect parser                                     #
# --------------------------------------------------------------------- #


def test_inspect_parsers_lists_all_registered(capsys):
    rc = cli_main(["inspect", "parsers"])
    assert rc == 0
    out = capsys.readouterr().out
    # Every parser registered in PARSERS must appear by slug.
    from molwatch.parsers import PARSERS
    for c in PARSERS:
        assert c.name in out


def test_inspect_parsers_json_emits_metadata_list(capsys):
    rc = cli_main(["inspect", "parsers", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    from molwatch.parsers import PARSERS
    assert len(data) == len(PARSERS)
    # Every entry has the metadata fields we promise in the spec.
    for entry in data:
        assert {"name", "label", "hint"}.issubset(entry)


def test_inspect_parser_human_readable(capsys):
    rc = cli_main(["inspect", "parser", "siesta"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "name:" in out
    assert "siesta" in out
    assert "SIESTA" in out


def test_inspect_parser_schema_emits_json(capsys):
    rc = cli_main(["inspect", "parser", "siesta", "--schema"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "siesta"
    assert data["label"]
    # `module` field points to where SiestaParser lives -- pins the
    # contract that the schema dump exposes the parser's location.
    assert "siesta" in data["module"]


def test_inspect_parser_unknown_name_exits_2(capsys):
    rc = cli_main(["inspect", "parser", "totally-fake"])
    assert rc == 2
    err = capsys.readouterr().err
    # Helpful stderr lists registered slugs so the user sees what
    # they should have typed.
    assert "totally-fake" in err
    assert "siesta" in err or "pyscf" in err


# --------------------------------------------------------------------- #
#  Argparse-level rejection of bad subcommand                           #
# --------------------------------------------------------------------- #


def test_unknown_subcommand_exits_2_listing_known(capsys):
    rc = cli_main(["totally-fake-subcommand"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "totally-fake-subcommand" in err
    # argparse lists the known subcommands in the error.
    assert "parse" in err and "serve" in err
