from src.enrichment.menu_validator import _combined_confidence, _keyword_hit_ratio, _price_signal


def test_keyword_ratio_high_for_real_menu_text():
    text = """
    Our cocktail menu features gin, vodka, rum and whisky based drinks,
    plus a curated wein and bier selection. Try our signature longdrink.
    """
    assert _keyword_hit_ratio(text) >= 0.6


def test_keyword_ratio_low_for_unrelated_text():
    text = "Welcome to our restaurant. We serve pizza, pasta and salads."
    assert _keyword_hit_ratio(text) < 0.3


def test_keyword_ratio_zero_for_empty_text():
    assert _keyword_hit_ratio("") == 0.0


def test_specific_drink_names_are_detected_without_generic_words():
    # A real menu that lists specific drinks/prices rather than category
    # words like "cocktail" or "gin" - this used to score ~0 and get
    # misclassified as NO_MENU_FOUND.
    text = """
    Negroni 12€
    Aperol Spritz 9,50€
    Moscow Mule 11€
    Old Fashioned 13€
    Margarita 10,50€
    """
    assert _combined_confidence(text) >= 0.6


def test_price_signal_detects_priced_list():
    text = "Negroni 12,50€  Spritz 9€  Mule 11,00 €  Sour 10€  Martini 13€  Gimlet 12€"
    assert _price_signal(text) >= 0.8


def test_price_signal_zero_for_no_prices():
    assert _price_signal("Welcome to our bar, open every night from 6pm.") == 0.0


def test_combined_confidence_food_only_page_does_not_reach_valid_alone():
    # Prices without any drink signal (e.g. a food menu) should NOT alone
    # reach VALID_MENU confidence via the price bonus.
    text = "Pizza Margherita 9€  Pasta Carbonara 12€  Salad 7€  Tiramisu 6€"
    assert _combined_confidence(text) < 0.6
