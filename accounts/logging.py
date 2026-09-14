"""Keep email authentication proofs out of application diagnostics."""

import logging
import re


_AUTH_LINK = re.compile(r"(/connexion/lien/)[^/\s?\"'<>]+")


class AuthLinkRedactingFormatter(logging.Formatter):
    """Redact after formatting, including tokens carried by exception tracebacks."""

    def format(self, record):
        return _AUTH_LINK.sub(r"\1[redacted]", super().format(record))
