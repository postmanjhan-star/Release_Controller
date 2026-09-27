"""Shared redaction for diagnostic output and audit metadata."""

import re

SECRET_KEY = re.compile(r"token|password|authorization|secret|credential|cookie|api.?key", re.I)
SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+)[^\s\"',;]+|"
    r"((?:[\w-]*(?:token|password|secret|credential)[\w-]*)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"
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
        value = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", value)
        return SECRET_TEXT.sub(lambda m: (m.group(1) or m.group(2)) + "[REDACTED]", value)
    return value
