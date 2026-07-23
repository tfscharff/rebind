from pathlib import Path

import pikepdf

from rebind.cli import main
from tests.fixtures import born_digital_pdf


def test_convert_subcommand_writes_a_pdf(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    code = main(["convert", str(source), str(target)])

    assert code == 0
    assert target.exists()
    with pikepdf.open(target) as pdf:
        assert len(pdf.pages) >= 1


def test_convert_reports_a_scanned_source_without_a_traceback(tmp_path: Path, capsys):
    target = tmp_path / "scan.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)

    code = main(["convert", str(target), str(tmp_path / "out.pdf")])

    assert code == 1
    assert "scanned" in capsys.readouterr().err.lower()


def test_missing_source_is_reported_cleanly(tmp_path: Path, capsys):
    code = main(["convert", str(tmp_path / "nope.pdf"), str(tmp_path / "out.pdf")])

    assert code == 1
    assert "nope.pdf" in capsys.readouterr().err


def test_unexpected_exception_is_reported_not_raised(tmp_path: Path, capsys, monkeypatch):
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")

    def boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("rebind.cli.convert", boom)

    code = main(["convert", str(source), str(tmp_path / "out.pdf")])

    err = capsys.readouterr().err
    assert code == 1
    assert str(source) in err
    assert "boom" in err
