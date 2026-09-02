from src.extraction.categorizer import classify


def test_cocktail_from_url():
    assert classify(url="https://bar.de/cocktails") == "COCKTAIL"


def test_wine_from_link_text():
    assert classify(link_text="Our Wine List") == "WINE"


def test_happy_hour_multiword():
    assert classify(link_text="Happy Hour Specials") == "HAPPY_HOUR"


def test_wine_wins_over_generic_drinks_when_both_present_but_specific_first():
    # "Wine & Drinks" - wine is checked before the generic drinks bucket
    assert classify(link_text="Wine & Drinks") == "WINE"


def test_falls_back_to_text_sample_when_url_and_link_text_uninformative():
    result = classify(url="https://bar.de/pdfs/doc4471.pdf", text_sample="Enjoy our brunch specials every Sunday")
    assert result == "BRUNCH"


def test_defaults_to_other():
    assert classify(url="https://bar.de/about") == "OTHER"
