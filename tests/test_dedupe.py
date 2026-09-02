from src.discovery.dedupe import dedupe_venues, is_likely_duplicate, normalize_name


def make_venue(name, city="Berlin", lat=52.52, lon=13.405):
    return {"venue_name": name, "city": city, "latitude": lat, "longitude": lon}


def test_normalize_name_strips_noise_words():
    assert normalize_name("The Cocktail Bar") == "cocktail"
    assert normalize_name("Bonanza Bar") == "bonanza"


def test_identical_venue_is_duplicate():
    a = make_venue("Buck and Breck")
    b = make_venue("Buck and Breck")
    assert is_likely_duplicate(a, b)


def test_same_name_different_city_is_not_duplicate():
    a = make_venue("Le Lion", city="Hamburg")
    b = make_venue("Le Lion", city="Berlin")
    assert not is_likely_duplicate(a, b)


def test_far_apart_same_name_is_not_duplicate():
    a = make_venue("Standard", lat=52.52, lon=13.405)
    b = make_venue("Standard", lat=52.60, lon=13.50)  # several km away
    assert not is_likely_duplicate(a, b)


def test_dedupe_venues_removes_near_duplicates():
    venues = [
        make_venue("Buck and Breck Bar"),
        make_venue("Buck and Breck"),  # near-duplicate, same coords
        make_venue("Windburger", lat=52.50, lon=13.39),
    ]
    result = dedupe_venues(venues)
    assert len(result) == 2
