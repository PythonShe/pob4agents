"""Author Notes passthrough and PoB color-escape cleaning (`get_notes`)."""

from __future__ import annotations

import re
from typing import Any

from ..bridge_protocol import BridgeRejectedError
from ..errors import DaemonRequestError

# Matches PoB's Lua `StripEscapes(text)`:
#   text:gsub("%^%d", ""):gsub("%^x%x%x%x%x%x%x", "")
_POB_NOTE_ESCAPE_RE = re.compile(r"\^\d|\^x[0-9A-Fa-f]{6}")
# Notes are free text from whoever published the build and reach a terminal and an agent's context.
# Whole ANSI sequences (CSI and OSC) go first, then what is left of the C0/C1 controls, the bidi
# overrides that reorder displayed text, and the characters a terminal hides yet a model reads
# (soft hyphen, zero-width space, word joiner, invisible operators, the Hangul filler, BOM,
# interlinear annotation, the Unicode tag block, the variation selector supplement). Tabs and
# newlines are kept, and so is what real text needs: ZWJ and ZWNJ, the directional marks of
# Arabic and Hebrew, the Mongolian vowel separator.
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CONTROL_CHAR_RE = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\xad\u200b\u202a-\u202e\u2060-\u2064\u2066-\u2069"
    r"\u3164\ufeff\ufff9-\ufffb\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)
_LINE_BREAKS_RE = re.compile(r"\r\n|[\r\u2028\u2029]")


def strip_pob_note_escapes(text: str | None) -> str:
    """Strip PoB color codes (`^xRRGGBB` and `^0`..`^9`) matching PoB Lua `StripEscapes`."""
    if not text:
        return ""
    return _POB_NOTE_ESCAPE_RE.sub("", str(text))


def clean_notes_text(raw: str | None) -> str:
    """Clean raw PoB NotesTab text while preserving paragraphs, URLs, and bullet lists."""
    if not raw or not isinstance(raw, str):
        return ""
    normalized = _LINE_BREAKS_RE.sub("\n", raw)
    no_ansi = _CONTROL_CHAR_RE.sub("", _ANSI_CSI_RE.sub("", normalized))
    stripped = strip_pob_note_escapes(no_ansi)
    lines = [line.rstrip() for line in stripped.split("\n")]
    return "\n".join(lines).strip("\n")


def build_notes_payload(raw_text: str | None) -> dict[str, Any]:
    """Assemble full untruncated cleaned `notes` and `notesMeta` for `--json` and formatters."""
    cleaned = clean_notes_text(raw_text)
    has_notes = bool(cleaned)
    line_count = len(cleaned.splitlines()) if has_notes else 0
    return {
        "notes": cleaned,
        "notesMeta": {
            "hasNotes": has_notes,
            "lineCount": line_count,
            "charCount": len(cleaned),
        },
    }


def notes_payload_from_rpc(res: Any) -> dict[str, Any]:
    """Convert a raw `get_notes` RPC result (or error dict) into `{notes, notesMeta}`."""
    raw_text = ""
    if isinstance(res, dict) and not res.get("_error") and res.get("ok") is not False:
        val = res.get("text")
        if isinstance(val, str):
            raw_text = val
    return build_notes_payload(raw_text)


def fetch_build_notes(worker: Any) -> dict[str, Any]:
    """Fetch author notes via `get_notes`, gracefully falling back on unsupported mocks/engines."""
    try:
        get_notes_fn = getattr(worker, "get_notes", None)
        res = get_notes_fn() if callable(get_notes_fn) else worker.call("get_notes", {})
        return notes_payload_from_rpc(res)
    except (AttributeError, KeyError, TypeError, AssertionError, DaemonRequestError, BridgeRejectedError):
        return build_notes_payload("")
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "get_notes" in msg or "unknown" in msg or "unsupported" in msg:
            return build_notes_payload("")
        raise
