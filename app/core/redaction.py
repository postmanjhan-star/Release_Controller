"""Shared redaction for diagnostic output and audit metadata."""

import re

DDNS_HOST = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:synology\.me|myds\.me|diskstation\.me|"
    r"duckdns\.org|ddns\.net|no-ip\.(?:org|com)|dynu\.(?:net|com)|"
    r"hopto\.org|myqnapcloud\.com)\b",
    re.I,
)
HTTP_URL = re.compile(r"https?://[^\s\"'<>]+", re.I)
PRIVATE_HEADER = re.compile(
    r"((?:proxy-authorization|authorization|set-cookie|cookie)[\"']?\s*[:=]\s*)"
    r"(?:[\"'][^\"'\r\n]*[\"']|[^\r\n,}]+)",
    re.I,
)
AUTH_QUERY = re.compile(r"([?&](?:code|state|access_token|refresh_token|token)=)[^&\s\"']+", re.I)
HEADER_COLLECTION = re.compile(r"(headers[\"']?\s*[:=]\s*)[^\r\n]+", re.I)
SECRET_KEY = re.compile(r"token|password|authorization|secret|credential|cookie|api.?key", re.I)
SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+)[^\s\"',;]+|"
    r"((?:[\w-]*(?:token|password|secret|credential|api[-_]?key)[\w-]*)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"
)


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if SECRET_KEY.search(str(k)) else redact(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = DDNS_HOST.sub("[REDACTED_HOST]", value)
        value = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", value)
        return SECRET_TEXT.sub(lambda m: (m.group(1) or m.group(2)) + "[REDACTED]", value)
    return value


def redact_log(value: str, secrets=()) -> str:
    """Redact the final formatted record, including args and exception chains."""
    value = HTTP_URL.sub("[REDACTED_URL]", value)
    value = HEADER_COLLECTION.sub(r"\1[REDACTED]", value)
    value = PRIVATE_HEADER.sub(r"\1[REDACTED]", value)
    value = AUTH_QUERY.sub(r"\1[REDACTED]", value)
    return redact(value, secrets)
