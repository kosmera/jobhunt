"""Authentication URLs never expose their bearer proof in formatted errors."""

import logging
import sys

from django.test import SimpleTestCase

from accounts.logging import AuthLinkRedactingFormatter


class AuthenticationLogRedactionTests(SimpleTestCase):
    def setUp(self):
        self.formatter = AuthLinkRedactingFormatter("{levelname} {name}: {message}", style="{")

    def record(self, message, args=(), exc_info=None):
        return logging.LogRecord("django.request", logging.ERROR, __file__, 1, message, args, exc_info)

    def test_request_path_argument_is_redacted(self):
        output = self.formatter.format(self.record(
            "%s: %s", ("Internal Server Error", "/connexion/lien/private:timestamp:signature/"),
        ))
        self.assertEqual(output, "ERROR django.request: Internal Server Error: /connexion/lien/[redacted]/")

    def test_traceback_keeps_error_details_without_the_token(self):
        try:
            raise RuntimeError("Could not render https://example.org/connexion/lien/secret-proof/")
        except RuntimeError:
            record = self.record("Request failed", exc_info=sys.exc_info())
        output = self.formatter.format(record)
        self.assertNotIn("secret-proof", output)
        self.assertIn("Traceback (most recent call last)", output)
        self.assertIn("RuntimeError: Could not render https://example.org/connexion/lien/[redacted]/", output)

    def test_redacts_multiple_links_and_preserves_delimiters(self):
        output = self.formatter.format(self.record(
            'Retry /connexion/lien/first?next=/reglages/ then "/connexion/lien/second" or /connexion/lien/third here',
        ))
        self.assertIn("/connexion/lien/[redacted]?next=/reglages/", output)
        self.assertIn('"/connexion/lien/[redacted]"', output)
        self.assertIn("/connexion/lien/[redacted] here", output)
        for token in ("first", "second", "third"):
            self.assertNotIn(token, output)

    def test_ordinary_paths_and_error_details_are_unchanged(self):
        record = self.record("Bad Request: /reglages/?section=profil (status=%s)", (400,))
        self.assertEqual(
            self.formatter.format(record),
            "ERROR django.request: Bad Request: /reglages/?section=profil (status=400)",
        )
