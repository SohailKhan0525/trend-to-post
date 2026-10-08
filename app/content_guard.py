from __future__ import annotations

import re

# Strict no-country / no-geopolitical geography vocabulary for generated copy.
# Common aliases are included because the account owner asked for zero country
# references, not merely zero political discussion.
BLOCKED_COUNTRY_TERMS = (
    "afghanistan", "albania", "algeria", "andorra", "angola", "antigua and barbuda",
    "argentina", "armenia", "australia", "austria", "azerbaijan", "bahamas",
    "bahrain", "bangladesh", "barbados", "belarus", "belgium", "belize", "benin",
    "bhutan", "bolivia", "bosnia and herzegovina", "botswana", "brazil", "brunei",
    "bulgaria", "burkina faso", "burundi", "cabo verde", "cambodia", "cameroon",
    "canada", "central african republic", "chad", "chile", "china", "colombia",
    "comoros", "congo", "costa rica", "croatia", "cuba", "cyprus", "czechia",
    "denmark", "djibouti", "dominica", "dominican republic", "ecuador", "egypt",
    "el salvador", "equatorial guinea", "eritrea", "estonia", "eswatini",
    "ethiopia", "fiji", "finland", "france", "gabon", "gambia", "georgia",
    "germany", "ghana", "greece", "grenada", "guatemala", "guinea",
    "guinea-bissau", "guyana", "haiti", "honduras", "hungary", "iceland", "india",
    "indonesia", "iran", "iraq", "ireland", "israel", "italy", "ivory coast",
    "jamaica", "japan", "jordan", "kazakhstan", "kenya", "kiribati", "kuwait",
    "kyrgyzstan", "laos", "latvia", "lebanon", "lesotho", "liberia", "libya",
    "liechtenstein", "lithuania", "luxembourg", "madagascar", "malawi", "malaysia",
    "maldives", "mali", "malta", "marshall islands", "mauritania", "mauritius",
    "mexico", "micronesia", "moldova", "monaco", "mongolia", "montenegro",
    "morocco", "mozambique", "myanmar", "namibia", "nauru", "nepal",
    "netherlands", "new zealand", "nicaragua", "niger", "nigeria", "north korea",
    "north macedonia", "norway", "oman", "pakistan", "palau", "palestine",
    "panama", "papua new guinea", "paraguay", "peru", "philippines", "poland",
    "portugal", "qatar", "romania", "russia", "rwanda", "saint kitts and nevis",
    "saint lucia", "saint vincent and the grenadines", "samoa", "san marino",
    "sao tome and principe", "saudi arabia", "senegal", "serbia", "seychelles",
    "sierra leone", "singapore", "slovakia", "slovenia", "solomon islands",
    "somalia", "south africa", "south korea", "south sudan", "spain", "sri lanka",
    "sudan", "suriname", "sweden", "switzerland", "syria", "taiwan", "tajikistan",
    "tanzania", "thailand", "timor-leste", "togo", "tonga", "trinidad and tobago",
    "tunisia", "turkey", "turkmenistan", "tuvalu", "uganda", "ukraine",
    "united arab emirates", "united kingdom", "united states", "uruguay",
    "uzbekistan", "vanuatu", "vatican city", "venezuela", "vietnam", "yemen",
    "zambia", "zimbabwe",
    # Common country aliases and shorthand that are unambiguous enough to block.
    "usa", "u.s.a.", "u.s.", "uk", "u.k.", "uae", "turkiye", "britain",
    "holland", "russian federation", "south korea", "korea republic",
)

_COUNTRY_NORMALIZED = tuple(
    " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in term).split()
    )
    for term in BLOCKED_COUNTRY_TERMS
    if term.strip()
)


def contains_blocked_country_term(text: str) -> bool:
    normalized = " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in str(text)).split()
    )
    if not normalized:
        return False

    words = normalized.split()
    for term in _COUNTRY_NORMALIZED:
        parts = term.split()
        width = len(parts)
        if width and any(
            words[i:i + width] == parts
            for i in range(len(words) - width + 1)
        ):
            return True
    return False
