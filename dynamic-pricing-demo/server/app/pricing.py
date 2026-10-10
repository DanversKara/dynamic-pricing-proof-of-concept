"""Turns (product, matched profile, rule) into what the price tag shows."""


def money(v):
    return f"${v:,.2f}"


def compute(product, rule):
    """Return (real_price, fake_was_price_or_None)."""
    base = product["default_price"]
    if rule and rule.get("base_price") is not None:
        base = rule["base_price"]
    price = float(base)
    for m in (rule or {}).get("markups", []):
        price *= 1 + float(m.get("percent") or 0) / 100.0
    price = round(price + 1e-9, 2)

    was = None
    d = float((rule or {}).get("shown_discount_pct") or 0)
    if 0 < d < 100:
        # The "was" price is invented so the real price *looks* like a d% discount.
        was = round(price / (1 - d / 100.0) + 1e-9, 2)
    return price, was


def _ascii(s):
    return (s or "").encode("ascii", "ignore").decode()


def tag_payload(product, profile, rule, show_profile=True):
    price, was = compute(product, rule if profile else None)
    return {
        "product": _ascii(product["name"])[:24],
        "unit": _ascii(product["unit"])[:16],
        "price": money(price),
        "price_value": price,
        "was": money(was) if (profile and was) else "",
        "badge": _ascii((rule or {}).get("badge", "")) if profile else "",
        "tag": (f"PROFILE {_ascii(profile['code'])}" if profile else "") if show_profile else "",
        "profile_id": profile["id"] if profile else None,
        "profile_code": profile["code"] if profile else "E",
        "profile_name": profile["name"] if profile else "No profile (default)",
    }
