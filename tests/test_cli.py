from pathlib import Path

import pikepdf

from rebind.cli import main
from tests.fixtures import born_digital_pdf


def test_convert_writes_an_accessible_pdf(tmp_path: Path):
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")
    target = tmp_path / "out.pdf"

    code = main(["convert", str(source), str(target)])

    assert code == 0
    assert target.exists()
    with pikepdf.open(target) as pdf:
        assert len(pdf.pages) >= 1
        assert bool(pdf.Root.MarkInfo.Marked)      # tagged flag set
        assert str(pdf.Root.Lang) == "en"          # language set


def test_born_digital_source_is_left_with_its_text(tmp_path: Path, capsys):
    # A PDF that already has text needs no recognition; nothing is flagged.
    source = born_digital_pdf("<h1>Doc</h1><p>Already has real text.</p>", tmp_path / "in.pdf")

    code = main(["convert", str(source), str(tmp_path / "out.pdf")])

    assert code == 0
    err = capsys.readouterr().err.lower()
    assert "recogniz" not in err and "no readable text" not in err


def test_blank_image_page_is_reported_not_refused(tmp_path: Path, capsys):
    # A page with no text is no longer refused: remediation tries OCR and, when nothing is
    # recovered, keeps the page and reports it honestly.
    target = tmp_path / "blank.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(target)

    code = main(["convert", str(target), str(tmp_path / "out.pdf")])

    assert code == 0
    assert "no readable text" in capsys.readouterr().err.lower()
    assert (tmp_path / "out.pdf").exists()


def test_missing_source_is_reported_cleanly(tmp_path: Path, capsys):
    code = main(["convert", str(tmp_path / "nope.pdf"), str(tmp_path / "out.pdf")])

    assert code == 1
    assert "nope.pdf" in capsys.readouterr().err


def test_unexpected_exception_is_reported_not_raised(tmp_path: Path, capsys, monkeypatch):
    source = born_digital_pdf("<h1>T</h1><p>body</p>", tmp_path / "in.pdf")

    def boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("rebind.cli.remediate", boom)

    code = main(["convert", str(source), str(tmp_path / "out.pdf")])

    err = capsys.readouterr().err
    assert code == 1
    assert str(source) in err
    assert "boom" in err
