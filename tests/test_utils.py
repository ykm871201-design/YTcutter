import pytest

from app.utils import (TimeFormatError, format_time, normalize_youtube_url, parse_time,
                       sanitize_filename, unique_path, validate_range)


@pytest.mark.parametrize("text,sec", [
    ("00:01:23.500", 83.5), ("1:02:03", 3723), ("01:30", 90), ("45", 45),
    ("12.25", 12.25), ("00:00:00,120", 0.12), (" 00:10:00.000 ", 600),
])
def test_parse_ok(text, sec):
    assert parse_time(text) == pytest.approx(sec)


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "00:60:00", "00:00:61", "1.2345",
                                  "-5", "00:-1:00", "1::2"])
def test_parse_bad(text):
    with pytest.raises(TimeFormatError):
        parse_time(text)


def test_format_roundtrip():
    assert format_time(3723.456) == "01:02:03.456"
    assert format_time(0) == "00:00:00.000"
    assert format_time(59.9996) == "00:01:00.000"
    assert parse_time(format_time(1234.567)) == pytest.approx(1234.567)


def test_validate_range():
    assert validate_range(1, 5, 10) is None
    assert "커야" in validate_range(5, 5, 10)
    assert "커야" in validate_range(6, 5, 10)
    assert "넘습니다" in validate_range(1, 11, 10)
    assert validate_range(1, 10, 10) is None
    assert "짧" in validate_range(1, 1.05, 10)


def test_sanitize():
    assert sanitize_filename('a<b>:c"/d\\e|f?g*') == "a_b__c__d_e_f_g_"
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("  제목 테스트. ") == "제목 테스트"
    assert sanitize_filename("") == "video"


def test_unique_path(tmp_path):
    p = tmp_path / "한글 영상.mp4"
    assert unique_path(p) == p
    p.write_bytes(b"x")
    p2 = unique_path(p)
    assert p2.name == "한글 영상 (1).mp4"
    p2.write_bytes(b"x")
    assert unique_path(p).name == "한글 영상 (2).mp4"


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("youtu.be/dQw4w9WgXcQ?t=10", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&index=3",
     "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
     "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/playlist?list=PLabc", "https://www.youtube.com/playlist?list=PLabc"),
])
def test_url_ok(url, expected):
    assert normalize_youtube_url(url) == (expected, None)


@pytest.mark.parametrize("url", ["", "https://vimeo.com/123", "https://www.youtube.com/watch?v=short",
                                 "https://www.youtube.com/@channel", "not a url"])
def test_url_bad(url):
    norm, err = normalize_youtube_url(url)
    assert norm is None and err
