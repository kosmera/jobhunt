"""Brevo's HTTP boundary, retry classification and subscription preservation."""

import json
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings

from accounts.brevo import BrevoError, ContactSyncResult, sync_contact


@override_settings(JOBHUNT_BREVO_API_KEY="test-secret", JOBHUNT_BREVO_LAUNCH_LIST_ID=7)
class BrevoContactTests(SimpleTestCase):
    def contact(self, **changes):
        return {"id": 42, "emailBlacklisted": False, "listIds": [7], **changes}

    def run_sync(self, responses, *, email="candidate+launch@example.org"):
        requests = []
        responses = iter(responses)

        def respond(request):
            requests.append(request)
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        client_factory = httpx.Client

        def create_client(**kwargs):
            return client_factory(transport=httpx.MockTransport(respond), **kwargs)

        with patch("accounts.brevo.httpx.Client", side_effect=create_client) as factory:
            result = sync_contact(email)
        return result, requests, factory.call_args.kwargs

    def test_create_sends_minimal_contact_then_reads_suppression(self):
        result, requests, config = self.run_sync([
            httpx.Response(404), httpx.Response(201, json={"id": 42}),
            httpx.Response(200, json=self.contact()),
        ])
        self.assertEqual(result, ContactSyncResult(contact_id=42, email_blocked=False))
        self.assertEqual([request.method for request in requests], ["GET", "POST", "GET"])
        self.assertEqual(str(requests[0].url),
                         "https://api.brevo.com/v3/contacts/candidate%2Blaunch%40example.org?identifierType=email_id")
        self.assertEqual(json.loads(requests[1].content), {
            "email": "candidate+launch@example.org", "updateEnabled": False, "listIds": [7],
        })
        self.assertTrue(all(request.headers["api-key"] == "test-secret" for request in requests))
        self.assertEqual(config["timeout"], 10.0)
        self.assertFalse(config["follow_redirects"])

    @override_settings(JOBHUNT_BREVO_LAUNCH_LIST_ID=None)
    def test_list_is_optional_and_no_unprovisioned_attributes_are_sent(self):
        _, requests, _ = self.run_sync([
            httpx.Response(404), httpx.Response(201, json={"id": 42}),
            httpx.Response(200, json=self.contact(listIds=[])),
        ])
        self.assertEqual(json.loads(requests[1].content), {
            "email": "candidate+launch@example.org", "updateEnabled": False,
        })

    def test_existing_contact_membership_is_a_read_only_noop(self):
        _, requests, _ = self.run_sync([httpx.Response(200, json=self.contact())])
        self.assertEqual(len(requests), 1)

    def test_existing_contact_only_adds_list_without_changing_other_data(self):
        result, requests, _ = self.run_sync([
            httpx.Response(200, json=self.contact(listIds=[2], ext_id="existing-id")),
            httpx.Response(204), httpx.Response(200, json=self.contact(listIds=[2, 7])),
        ])
        self.assertFalse(result.email_blocked)
        self.assertEqual(json.loads(requests[1].content), {"listIds": [7]})

    def test_global_or_list_unsubscribe_skips_all_writes(self):
        for changes in ({"emailBlacklisted": True}, {"listUnsubscribed": [7]}):
            with self.subTest(changes=changes):
                result, requests, _ = self.run_sync([
                    httpx.Response(200, json=self.contact(listIds=[], **changes)),
                ])
                self.assertTrue(result.email_blocked)
                self.assertEqual(len(requests), 1)

    def test_concurrent_unsubscribe_after_list_add_is_returned_to_worker(self):
        result, _, _ = self.run_sync([
            httpx.Response(200, json=self.contact(listIds=[])), httpx.Response(204),
            httpx.Response(200, json=self.contact(emailBlacklisted=True)),
        ])
        self.assertTrue(result.email_blocked)

    def test_duplicate_create_race_rereads_and_preserves_suppression(self):
        result, requests, _ = self.run_sync([
            httpx.Response(404), httpx.Response(400, json={"code": "duplicate_parameter"}),
            httpx.Response(200, json=self.contact(emailBlacklisted=True, listIds=[])),
        ])
        self.assertTrue(result.email_blocked)
        self.assertEqual([request.method for request in requests], ["GET", "POST", "GET"])

    def test_duplicate_create_race_with_missing_list_retries_before_more_requests(self):
        with self.assertRaises(BrevoError) as caught:
            self.run_sync([
                httpx.Response(404), httpx.Response(400, json={"code": "duplicate_parameter"}),
                httpx.Response(200, json=self.contact(listIds=[])),
            ])
        self.assertEqual(caught.exception.code, "brevo_list_not_visible")
        self.assertTrue(caught.exception.retryable)

    def test_transient_and_permanent_http_errors_have_only_safe_status_codes(self):
        for status in (400, 401, 403, 408, 425, 429, 500, 502, 503):
            with self.subTest(status=status), self.assertRaises(BrevoError) as caught:
                self.run_sync([httpx.Response(status, json={"message": "test-secret candidate@example.org"})])
            self.assertEqual(str(caught.exception), f"brevo_http_{status}")
            self.assertEqual(caught.exception.retryable, status in (408, 425, 429) or status >= 500)

    def test_other_create_error_is_not_treated_as_duplicate(self):
        with self.assertRaises(BrevoError) as caught:
            self.run_sync([
                httpx.Response(404), httpx.Response(400, json={"code": "invalid_parameter"}),
            ])
        self.assertEqual(caught.exception.code, "brevo_http_400")
        self.assertFalse(caught.exception.retryable)

    def test_non_json_create_error_is_still_permanent(self):
        with self.assertRaises(BrevoError) as caught:
            self.run_sync([httpx.Response(404), httpx.Response(400, text="test-secret")])
        self.assertEqual(caught.exception.code, "brevo_http_400")
        self.assertFalse(caught.exception.retryable)

    def test_timeout_and_transport_errors_are_sanitized_and_retryable(self):
        for error in (httpx.ReadTimeout("test-secret"), httpx.ConnectError("candidate@example.org")):
            with self.subTest(error=type(error)), self.assertRaises(BrevoError) as caught:
                self.run_sync([error])
            self.assertEqual(str(caught.exception), "brevo_network")
            self.assertTrue(caught.exception.retryable)
            self.assertTrue(caught.exception.__suppress_context__)

    def test_redirect_is_not_followed(self):
        with self.assertRaises(BrevoError) as caught:
            self.run_sync([httpx.Response(307, headers={"Location": "https://other.example"})])
        self.assertEqual(caught.exception.code, "brevo_http_307")
        self.assertFalse(caught.exception.retryable)

    def test_malformed_suppression_response_never_allows_welcome(self):
        for body in ([], {}, self.contact(emailBlacklisted="false"), self.contact(listUnsubscribed="7")):
            with self.subTest(body=body), self.assertRaises(BrevoError) as caught:
                self.run_sync([httpx.Response(200, json=body)])
            self.assertEqual(caught.exception.code, "brevo_invalid_response")
            self.assertTrue(caught.exception.retryable)

    def test_contact_missing_after_create_is_retryable(self):
        with self.assertRaises(BrevoError) as caught:
            self.run_sync([httpx.Response(404), httpx.Response(201), httpx.Response(404)])
        self.assertEqual(caught.exception.code, "brevo_contact_not_visible")
        self.assertTrue(caught.exception.retryable)

    def test_list_missing_after_update_is_retryable(self):
        with self.assertRaises(BrevoError) as caught:
            self.run_sync([
                httpx.Response(200, json=self.contact(listIds=[])), httpx.Response(204),
                httpx.Response(200, json=self.contact(listIds=[])),
            ])
        self.assertEqual(caught.exception.code, "brevo_list_not_visible")
        self.assertTrue(caught.exception.retryable)

    def test_bad_configuration_and_email_never_make_network_requests(self):
        cases = [
            ({"JOBHUNT_BREVO_API_KEY": ""}, "candidate@example.org", "brevo_missing_api_key"),
            ({"JOBHUNT_BREVO_API_KEY": "@Microsoft.KeyVault(SecretUri=https://test.vault)"},
             "candidate@example.org", "brevo_missing_api_key"),
            ({"JOBHUNT_BREVO_LAUNCH_LIST_ID": 0}, "candidate@example.org", "brevo_invalid_list_id"),
            ({"JOBHUNT_BREVO_LAUNCH_LIST_ID": True}, "candidate@example.org", "brevo_invalid_list_id"),
            ({}, "bad-email", "brevo_invalid_email"),
        ]
        for config, email, code in cases:
            with self.subTest(config=config, email=email), override_settings(**config):
                with patch("accounts.brevo.httpx.Client") as factory, self.assertRaises(BrevoError) as caught:
                    sync_contact(email)
                self.assertEqual(caught.exception.code, code)
                self.assertFalse(caught.exception.retryable)
                factory.assert_not_called()
