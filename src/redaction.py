"""Filter configured credentials before text becomes a prompt or observation."""
from __future__ import annotations

import hashlib


def redact(value, settings):
    """Redact structured values before serialization can escape a credential."""
    if isinstance(value, str):
        for secret in (settings.gitlab_token, settings.llm_api_key):
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, dict):
        return {redact(key, settings): redact(item, settings) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, settings) for item in value]
    return value


def observation_fingerprints(settings):
    """Workers need matching evidence, never the publication credential itself."""
    return [(len(secret), hashlib.sha256(secret.encode()).hexdigest())
            for secret in (settings.gitlab_token, settings.llm_api_key) if secret]


class ObservationFilter:
    def __init__(self, fingerprints=()):
        self._fingerprints = {}
        for length, digest in fingerprints:
            self._fingerprints.setdefault(length, set()).add(digest)

    def __call__(self, text):
        # Match whole configured values in raw text before JSON/SDK serialization.
        # This small fixed set of fingerprints is private runtime configuration;
        # it is never offered as tool input or put in a model message.
        spans = []
        for length, digests in self._fingerprints.items():
            for start in range(len(text) - length + 1):
                if hashlib.sha256(text[start:start + length].encode()).hexdigest() in digests:
                    spans.append((start, start + length))
        merged = []
        for start, end in sorted(spans):
            if merged and start < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        for start, end in reversed(merged):
            text = text[:start] + '[REDACTED]' + text[end:]
        return text
