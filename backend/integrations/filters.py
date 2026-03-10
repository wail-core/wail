"""
Generic filter engine — applies to any list of normalized dicts.

Used by calendar, payments, CRM integrations etc.
Each filter: { field, operator, value }
All filters are combined with AND logic (all must match).
"""


def apply_filters(items: list[dict], filters: list[dict]) -> list[dict]:
    if not filters:
        return items
    return [item for item in items if _matches_all(item, filters)]


def _matches_all(item: dict, filters: list[dict]) -> bool:
    return all(_matches(item, f) for f in filters)


def _matches(item: dict, f: dict) -> bool:
    field    = f.get("field", "")
    operator = f.get("operator", "contains")
    value    = str(f.get("value", ""))

    raw = item.get(field)

    if operator == "exists":
        return raw is not None and raw != ""
    if operator == "not_exists":
        return raw is None or raw == ""

    item_str  = str(raw).lower() if raw is not None else ""
    value_str = value.lower()

    if operator == "contains":
        return value_str in item_str
    if operator == "equals":
        return item_str == value_str
    if operator == "not_equals":
        return item_str != value_str
    if operator == "starts_with":
        return item_str.startswith(value_str)
    if operator == "ends_with":
        return item_str.endswith(value_str)

    # Numeric
    try:
        n = float(raw) if raw is not None else 0.0
        v = float(value)
        if operator == "greater_than":
            return n > v
        if operator == "less_than":
            return n < v
    except (ValueError, TypeError):
        pass

    return False


OPERATORS = [
    "contains",
    "equals",
    "not_equals",
    "starts_with",
    "ends_with",
    "greater_than",
    "less_than",
    "exists",
    "not_exists",
]
