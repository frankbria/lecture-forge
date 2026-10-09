import pymupdf
import pytest

from lecture_forge.source import (
    extract_text,
    open_source,
    outline,
    scanned_pages,
    slice_pdf,
)


def scanned_pdf(path, pages):
    """One page per letter: t has text, f text and a figure, s is a scan (a page image,
    no text layer), n a scan with a stamped page number, b is blank."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.clear_with(200)
    doc = pymupdf.open()
    for kind in pages:
        page = doc.new_page()
        if kind in "tf":
            page.insert_text((72, 72), "Some words here")
        if kind == "f":
            page.insert_image(pymupdf.Rect(72, 100, 300, 328), pixmap=pix)
        if kind in "sn":
            page.insert_image(page.rect, pixmap=pix)
        if kind == "n":
            page.insert_text((290, 820), "12")
    doc.save(path)
    return path


@pytest.fixture
def book(tmp_path):
    """Five-page PDF with two chapters bookmarked, like a real textbook."""
    path = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for n in range(1, 6):
        doc.new_page().insert_text((72, 72), f"Page {n} body")
    doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2], [1, "Chapter 2", 4]])
    doc.save(path)
    return path


@pytest.fixture
def notes(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text(
        "# Groups\nintro\n## Cosets\nbody\n```\n# not a heading\n```\n# Rings\nend\n"
    )
    return path


def test_pdf_source_reports_page_count(book):
    src = open_source(book)
    assert src.kind == "pdf" and src.length == 5


def test_text_source_length_is_line_count(notes):
    src = open_source(notes)
    assert src.kind == "text" and src.length == 9


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_source(tmp_path / "missing.pdf")


def test_corrupt_pdf_is_a_value_error(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_text("not a pdf")
    with pytest.raises(ValueError, match="Cannot open"):
        open_source(p)


def test_encrypted_pdf_is_rejected_up_front(tmp_path):
    p = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(p, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(ValueError, match="password"):
        open_source(p)


def test_text_is_read_as_utf8(tmp_path):
    p = tmp_path / "n.md"
    p.write_bytes("# Part 1 — Euler’s idea\n".encode())
    assert outline(open_source(p)) == [(1, "Part 1 — Euler’s idea", 1)]


def test_dangling_bookmarks_are_dropped(tmp_path):
    p = tmp_path / "b.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.set_toc([[1, "Real", 1], [1, "Nowhere", -1]])
    doc.save(p)
    assert outline(open_source(p)) == [(1, "Real", 1)]


def test_unsupported_extension_is_rejected(tmp_path):
    p = tmp_path / "book.epub"
    p.write_text("x")
    with pytest.raises(ValueError, match="Unsupported"):
        open_source(p)


def test_pdf_outline_comes_from_bookmarks(book):
    assert outline(open_source(book)) == [
        (1, "Chapter 1", 1),
        (2, "Section 1.1", 2),
        (1, "Chapter 2", 4),
    ]


def test_markdown_outline_skips_code_fences(notes):
    assert outline(open_source(notes)) == [
        (1, "Groups", 1),
        (2, "Cosets", 3),
        (1, "Rings", 8),
    ]


def test_extract_pdf_page_range_is_one_based_inclusive(book):
    text = extract_text(open_source(book), 2, 3)
    assert "Page 2 body" in text and "Page 3 body" in text
    assert "Page 1 body" not in text and "Page 4 body" not in text


def test_extract_whole_source_by_default(notes):
    assert extract_text(open_source(notes)) == notes.read_text()


def test_extract_text_line_range(notes):
    assert extract_text(open_source(notes), 3, 4) == "## Cosets\nbody\n"


@pytest.mark.parametrize("start,end", [(0, 2), (3, 2), (4, 6)])
def test_out_of_range_is_rejected(book, start, end):
    with pytest.raises(ValueError, match="range"):
        extract_text(open_source(book), start, end)


def test_slice_pdf_keeps_only_requested_pages(book, tmp_path):
    out = slice_pdf(open_source(book), 4, 5, tmp_path / "ep.pdf")
    with pymupdf.open(out) as doc:
        assert doc.page_count == 2
        assert "Page 4 body" in doc[0].get_text()


def test_slice_rejects_text_source(notes, tmp_path):
    with pytest.raises(ValueError, match="PDF"):
        slice_pdf(open_source(notes), 1, 2, tmp_path / "x.pdf")


def test_heading_keeps_hashes_that_belong_to_the_title(tmp_path):
    p = tmp_path / "langs.md"
    p.write_text("# C#\n## F# basics ##\n### Closed ###\n")
    assert outline(open_source(p)) == [
        (1, "C#", 1),
        (2, "F# basics", 2),
        (3, "Closed", 3),
    ]


def md_outline(tmp_path, text):
    p = tmp_path / "n.md"
    p.write_text(text, encoding="utf-8")
    return outline(open_source(p))


def test_setext_headings_have_their_level_and_text_line(tmp_path):
    text = "Groups\n======\nintro\n\nCosets  \n---\nbody\n# Rings\n"
    assert md_outline(tmp_path, text) == [
        (1, "Groups", 1),
        (2, "Cosets", 5),
        (1, "Rings", 8),
    ]


def test_a_rule_under_nothing_or_under_a_heading_is_no_heading(tmp_path):
    text = "intro\n\n---\n# Groups\n---\n***\n---\n"
    assert md_outline(tmp_path, text) == [(1, "Groups", 4)]


def test_tilde_fences_hide_headings_until_the_same_fence_closes_them(tmp_path):
    text = (
        "~~~python\n# not a heading\n```\nNot one\n===\n~~~\n# Groups\n"
        "````\n# hidden\n```\n````\n# Rings\n"
    )
    assert md_outline(tmp_path, text) == [(1, "Groups", 7), (1, "Rings", 12)]
    # an underline after a fence underlines the fence, not the text before it
    assert md_outline(tmp_path, "Intro\n```\ncode\n```\n===\n") == []


def test_yaml_front_matter_is_no_heading(tmp_path):
    text = "---\ntitle: Algebra notes\ntags: [math]\n---\nGroups\n======\n"
    assert md_outline(tmp_path, text) == [(1, "Groups", 5)]
    # a leading rule that never closes is no front matter: nothing is hidden
    assert md_outline(tmp_path, "---\nGroups\n======\n") == [(1, "Groups", 2)]


def test_a_note_opening_with_a_rule_is_no_front_matter(tmp_path):
    assert md_outline(tmp_path, "---\n# One\n\n---\n# Two\n") == [
        (1, "One", 2),
        (1, "Two", 5),
    ]


def test_front_matter_after_a_byte_order_mark_is_still_skipped(tmp_path):
    p = tmp_path / "n.md"
    p.write_bytes("---\ntitle: x\n---\n# Real\n".encode("utf-8-sig"))
    assert outline(open_source(p)) == [(1, "Real", 4)]


@pytest.mark.parametrize(
    "line", ["- item one", "* item", "1. first", "2) second", "> quote", "| 1 | 2 |",
             "    indented code", "  list continuation", "<div>"],
)  # fmt: skip
def test_a_rule_under_a_list_quote_table_or_indented_line_is_no_heading(tmp_path, line):
    assert md_outline(tmp_path, f"{line}\n---\n{line}\n===\n") == []


def test_front_matter_ends_before_its_first_blank_line(tmp_path):
    text = "---\nDate: 2026-10-08\n\nGroups\n======\n\nbody\n\n---\n\nRings\n======\n"
    assert md_outline(tmp_path, text) == [(1, "Groups", 4), (1, "Rings", 11)]


def test_an_underline_indented_four_spaces_continues_the_paragraph(tmp_path):
    assert md_outline(tmp_path, "Para\n    ===\nPara\n    ---\n") == []


def test_scanned_pages_are_page_images_with_at_most_a_stamped_number(tmp_path):
    src = open_source(scanned_pdf(tmp_path / "scan.pdf", "tsbsfn"))
    # a blank page and a page with a figure are no scans; a stamped page number is
    assert scanned_pages(src) == [2, 4, 6]
    assert scanned_pages(src, 3, 4) == [4]
    assert extract_text(src, 2, 2).strip() == ""  # what the planner can't count


def test_text_sources_have_no_scanned_pages(notes):
    assert scanned_pages(open_source(notes)) == []
