class SchemaError(RuntimeError):
    """Raised when an adapter payload does not match the worker contract."""


def require_schema(payload: dict, expected: int = 1) -> dict:
    if payload.get("schema_version") != expected:
        raise SchemaError(f"expected schema_version={expected}, got {payload.get('schema_version')!r}")
    return payload
