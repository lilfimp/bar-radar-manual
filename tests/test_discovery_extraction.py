from src.extraction.discovery import normalize_url, _classify_source_type


def test_normalize_strips_trailing_slash():
    assert normalize_url("https://bar.de/menu/") == normalize_url("https://bar.de/menu")


def test_normalize_strips_fragment():
    assert normalize_url("https://bar.de/menu#drinks") == normalize_url("https://bar.de/menu")


def test_normalize_different_paths_are_different():
    assert normalize_url("https://bar.de/menu") != normalize_url("https://bar.de/wine")


def test_classify_pdf():
    assert _classify_source_type("https://bar.de/files/cocktails.pdf") == "PDF"


def test_classify_image():
    assert _classify_source_type("https://bar.de/img/menu.jpg") == "IMAGE"


def test_classify_html_default():
    assert _classify_source_type("https://bar.de/cocktails") == "HTML_PAGE"
