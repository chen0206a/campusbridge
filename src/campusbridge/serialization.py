import json


def strict_json_object(value):
    """Never repair fences, duplicate keys, NaN, commentary, arrays or truncated JSON."""
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = item
        return result
    def nonfinite(value):
        raise ValueError("Nonfinite JSON value")
    parsed = json.loads(value, object_pairs_hook=object_pairs, parse_constant=nonfinite) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object")
    return parsed
