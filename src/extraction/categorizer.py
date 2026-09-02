"""Classify a menu into one of the fixed menu_category values.

Priority order matters: URL/link-text signals are checked first (cheap,
usually decisive - a link literally called "Cocktails" IS the cocktail
menu), and we only fall back to scanning extracted text for a handful of
venues where the URL/name gives no clue (e.g. a generic "/menu.pdf").

Order of the CATEGORY_KEYWORDS dict matters: more specific categories
(COCKTAIL, WINE, BEER, SPIRITS, HAPPY_HOUR, BRUNCH, ROOM_SERVICE, SEASONAL)
are checked before the broad catch-alls (DRINKS, FOOD) so e.g. a "Wine List"
page isn't miscategorized as generic DRINKS.
"""
from __future__ import annotations

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "COCKTAIL": ["cocktail", "cocktails", "signature drink"],
    "WINE": ["wine", "wein", "weinkarte", "sommelier"],
    "BEER": ["beer", "bier", "bierkarte", "brew"],
    "SPIRITS": ["spirits", "spirituosen", "whisky", "whiskey", "gin", "rum", "tequila", "vodka", "wodka"],
    "HAPPY_HOUR": ["happy hour", "happyhour"],
    "BRUNCH": ["brunch"],
    "ROOM_SERVICE": ["room service", "zimmerservice", "in-room"],
    "SEASONAL": ["seasonal", "saison", "winter menu", "summer menu", "christmas", "weihnacht"],
    "FOOD": ["food", "speisekarte", "dinner", "lunch", "essen", "kitchen", "dishes"],
    "DRINKS": ["drinks", "drink menu", "getränkekarte", "getraenkekarte", "getränke", "beverage"],
}

# Checked in this order so more specific categories win over broad ones.
CATEGORY_PRIORITY = [
    "COCKTAIL", "WINE", "BEER", "SPIRITS", "HAPPY_HOUR",
    "BRUNCH", "ROOM_SERVICE", "SEASONAL", "FOOD", "DRINKS",
]


def classify(url: str = "", link_text: str = "", text_sample: str = "") -> str:
    """Returns one of the fixed menu_category values, defaulting to OTHER."""
    haystack_primary = f"{link_text} {url}".lower()
    for category in CATEGORY_PRIORITY:
        keywords = CATEGORY_KEYWORDS[category]
        if any(kw in haystack_primary for kw in keywords):
            return category

    # Fall back to scanning a sample of the extracted text if URL/link text
    # gave no signal (e.g. an opaque filename like /pdfs/doc-4471.pdf).
    if text_sample:
        haystack_secondary = text_sample[:2000].lower()
        for category in CATEGORY_PRIORITY:
            keywords = CATEGORY_KEYWORDS[category]
            if any(kw in haystack_secondary for kw in keywords):
                return category

    return "OTHER"
