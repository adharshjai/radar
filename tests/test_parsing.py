from datetime import date, datetime, timezone

from radar.dates import classify_line, dated_lines, find_dates, find_relative_dates
from radar.models import html_links, html_to_text, parse_dt
from radar.sources.canvas import derive_assignment_status, extract_text
from radar.sources.platforms import detect_platform, is_interesting_link


def test_parse_dt_variants():
    assert parse_dt("2026-09-10T03:59:00Z") == datetime(2026, 9, 10, 3, 59, tzinfo=timezone.utc)
    assert parse_dt("2026-09-10T03:59:00.1234567") == datetime(2026, 9, 10, 3, 59, 0, 123456, tzinfo=timezone.utc)
    assert parse_dt("2026-09-10T03:59:00-04:00").hour == 7
    assert parse_dt(None) is None
    assert parse_dt("garbage") is None


def test_html_helpers():
    html = "<p>Hello <b>world</b></p><ul><li>one</li><li><a href='https://x.com/a'>link text</a></li></ul><script>bad()</script>"
    assert html_to_text(html) == "Hello world\none\nlink text"
    assert html_links(html) == [("https://x.com/a", "link text")]


def test_platform_detection():
    assert detect_platform("https://app.labflow.com/launch/1", None) == ("LabFlow", "tool")
    assert detect_platform("https://www.gradescope.com/courses/1", "x")[0] == "Gradescope"
    assert detect_platform("https://example.org/reading", "Reading") == (None, "link")
    assert detect_platform("https://youtu.be/abc", "")[1] == "media"


def test_interesting_links():
    assert not is_interesting_link("mailto:a@b.edu", "canvas.x.edu")
    assert not is_interesting_link("https://canvas.x.edu/courses/1/files/2", "canvas.x.edu")
    assert is_interesting_link("https://canvas.x.edu/courses/1/external_tools/7", "canvas.x.edu")
    assert is_interesting_link("https://app.labflow.com/x", "canvas.x.edu")
    assert not is_interesting_link("https://x.com/logo.png", "canvas.x.edu")


def test_assignment_status_matrix():
    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    past, future = "2026-09-01T00:00:00Z", "2026-09-20T00:00:00Z"
    a = lambda **k: {"submission_types": ["online_upload"], **k}  # noqa: E731
    assert derive_assignment_status(a(due_at=future), {}, now) == "open"
    assert derive_assignment_status(a(due_at=past), {}, now) == "missing"
    assert derive_assignment_status(a(due_at=past, submission_types=["external_tool"]), {}, now) == "pastdue"
    assert derive_assignment_status(a(due_at=past, submission_types=["on_paper"]), {}, now) == "info"
    assert derive_assignment_status(a(due_at=past), {"workflow_state": "submitted", "submitted_at": past}, now) == "submitted"
    assert derive_assignment_status(a(due_at=past), {"workflow_state": "graded", "score": 9}, now) == "graded"
    assert derive_assignment_status(a(due_at=future), {"excused": True}, now) == "excused"
    assert derive_assignment_status(a(due_at=future, unlock_at=future), {}, now) == "locked"
    assert derive_assignment_status(a(due_at=future), {"missing": True}, now) == "missing"


def test_find_dates_and_year_inference():
    anchor = date(2026, 9, 9)
    text = "Exam 1: Friday, September 19 at 7 PM. Final: Dec 14, 2026. HW due 9/26. Drop deadline 16 Sept."
    found = [d for d, _, _ in find_dates(text, anchor, date(2026, 8, 20), date(2026, 12, 20))]
    assert found == [date(2026, 9, 19), date(2026, 12, 14), date(2026, 9, 26), date(2026, 9, 16)]
    # January mentioned in September resolves to next year when the term window says so
    jan = find_dates("Project due January 5", anchor, date(2026, 8, 20), date(2027, 1, 20))
    assert jan[0][0] == date(2027, 1, 5)
    rel = dict((w, d) for d, w in find_relative_dates("due tomorrow, review this friday", anchor))
    assert rel["tomorrow"] == date(2026, 9, 10)
    assert rel["this friday"] == date(2026, 9, 11)


def test_dated_lines_classification():
    tz = timezone.utc
    text = "Exam 1: September 19, 2026 at 7:00 PM in SH 120\nNo class October 12 (fall break)\nLab report due Sept 23"
    lines = dated_lines(text, date(2026, 9, 9), date(2026, 8, 20), date(2026, 12, 20), tz)
    hints = {l["line"][:6]: l["hint"] for l in lines}
    assert hints["Exam 1"] == "exam" and hints["No cla"] == "no_class" and hints["Lab re"] == "deadline"
    exam = next(l for l in lines if l["hint"] == "exam")
    assert exam["has_time"] and exam["when"].hour == 19
    assert classify_line("Midterm on Friday") == "exam"


def test_extract_text_docx_and_html():
    from demo.fixtures import _docx
    assert "Exam 1" in extract_text(_docx(["Exam 1: Sept 19", "Grading"]), "", "syllabus.docx")
    assert extract_text(b"<p>hi <b>there</b></p>", "text/html", "") == "hi there"
    assert extract_text(b"plain", "text/plain", "") == "plain"
    assert extract_text(b"\x00\x01", "application/octet-stream", "blob.bin") == ""
