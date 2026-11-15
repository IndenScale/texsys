"""JSONC tolerance shared by workspace settings and extension manifest readers."""

import re

_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\r\n]*|/\*[\s\S]*?\*/')
_TRAILING_COMMA = re.compile(r'"(?:[^"\\]|\\.)*"|,(?=\s*[}\]])')


def strip_jsonc(text):
  # Keep offsets so callers can edit the original source if needed.
  text = _TOKENS.sub(lambda match: match[0] if match[0].startswith('"') else
                     re.sub(r"[^\r\n]", " ", match[0]), text)
  return _TRAILING_COMMA.sub(lambda match: " " if match[0] == "," else match[0], text)
