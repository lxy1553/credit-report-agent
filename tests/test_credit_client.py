import json
import unittest
from io import BytesIO
from unittest.mock import patch

from report_agent.credit_client import CreditClient


class CreditClientTests(unittest.TestCase):
    def test_calls_certified_endpoints_with_single_day_final_scope(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request.get_method(), request.full_url,
                             request.get_header("Authorization"),
                             json.loads(request.data) if request.data else None))
            response = ([{"id": "application_count"}] if request.get_method() == "GET"
                        else {"status": "ok"})
            return BytesIO(json.dumps(response).encode())

        with patch("report_agent.credit_client.urlopen", side_effect=fake_urlopen):
            client = CreditClient("http://127.0.0.1:8000", "secret")
            self.assertEqual(client.catalog(), [{"id": "application_count"}])
            self.assertEqual(client.query_metric("application_count", "2026-03-02"), {"status": "ok"})
            self.assertEqual(requests, [
                ("GET", "http://127.0.0.1:8000/v1/catalog", "Bearer secret", None),
                ("POST", "http://127.0.0.1:8000/v1/metrics/query", "Bearer secret", {
                    "metric_id": "application_count", "start_date": "2026-03-02",
                    "end_date": "2026-03-02", "data_status": "FINAL",
                    "group_by": ["biz_date"], "max_rows": 1,
                }),
            ])


if __name__ == "__main__":
    unittest.main()
