"""Logging config without secrets. Supports request/correlation IDs."""
import contextvars
import logging
import re
import sys

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_SECRET_PATTERNS = [
    re.compile(r"(?i)(password\s*[:=]\s*)(['\"]?)([^\s,'\"]+)(['\"]?)"),
    re.compile(r"(?i)(token\s*[:=]\s*)(['\"]?)([^\s,'\"]+)(['\"]?)"),
    re.compile(r"(?i)(secret\s*[:=]\s*)(['\"]?)([^\s,'\"]+)(['\"]?)"),
    re.compile(r"(?i)(authorization\s*:\s*Bearer\s+)(\S+)"),
]


def redact(text: str) -> str:
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(lambda m: m.group(1) + (m.group(2) if len(m.groups()) >= 2 else "") + "***REDACTED***", out)
    return out


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.request_id = request_id_var.get()
        except Exception:
            record.request_id = "-"
        # best-effort redact formatted message args already rendered? redact msg
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
        except Exception:
            pass
        return True


def configure_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s - %(message)s")
    handler.setFormatter(fmt)
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
