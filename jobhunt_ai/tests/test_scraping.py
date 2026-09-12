"""HTTP source failures must remain actionable without reaching the LLM."""

from unittest import mock

import httpx
from django.test import SimpleTestCase

from jobhunt_ai.scraping.sources import ScrapeError, _fetch_direct


class DirectScrapingTests(SimpleTestCase):
    url = "https://source.example/jobs?token=private-token"

    def response(self, status: int, html: str = "") -> httpx.Response:
        return httpx.Response(status, text=html, request=httpx.Request("GET", self.url))

    def test_http_errors_report_the_cause_without_exposing_request_data(self):
        for status in (401, 403, 404, 410, 429, 503):
            with self.subTest(status=status), mock.patch(
                "httpx.get", return_value=self.response(status),
            ), self.assertRaises(ScrapeError) as raised:
                _fetch_direct(self.url)
            self.assertIn(str(status), str(raised.exception))
            self.assertNotIn("private-token", str(raised.exception))
            self.assertNotIn("source.example", str(raised.exception))

    def test_http_200_verification_page_is_not_returned_as_job_content(self):
        for title in ("Challenge Validation", "Just a moment...", "Access Denied"):
            with self.subTest(title=title), mock.patch(
                "httpx.get",
                return_value=self.response(200, f"<title>{title}</title><p>Verify you are human</p>"),
            ), self.assertRaisesRegex(ScrapeError, "anti-robot"):
                _fetch_direct(self.url)

    def test_job_describing_access_controls_is_still_valid_content(self):
        html = (
            "<title>Security Engineer</title><p>Investigate access denied errors.</p>"
            '<a href="https://source.example/job/1">Apply</a>'
        )
        with mock.patch("httpx.get", return_value=self.response(200, html)):
            text = _fetch_direct(self.url)
        self.assertIn("Investigate access denied errors.", text)
        self.assertIn("https://source.example/job/1", text)
