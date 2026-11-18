import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from report_agent.__main__ import main
from report_agent.artifacts import save_draft
from report_agent.daily import DailyReport
from report_agent.identity import ReviewerIdentity
from test_daily_report import FakeCreditService


class CliTests(unittest.TestCase):
    def test_generates_draft_files_from_service(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("report_agent.__main__.CreditClient", return_value=FakeCreditService()):
                code = main(["--date", "2026-03-02", "--output-dir", temp],
                            environ={"CREDIT_API_URL": "http://127.0.0.1:8000", "CREDIT_API_TOKEN": "secret"})
            self.assertEqual(code, 0)
            self.assertTrue((Path(temp) / "credit-daily-2026-03-02.md").exists())
            self.assertTrue((Path(temp) / "credit-daily-2026-03-02.evidence.json").exists())

    def test_approves_existing_draft_without_api_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, manifest = save_draft(DailyReport("2026-03-02", "demo-v2", "# 日报\n", ()), root / "drafts")
            class Verifier:
                def resolve(self, token):
                    if token != "signed-token":
                        raise ValueError("unexpected token")
                    return ReviewerIdentity("reviewer-1", "policy-v1")

            config = {"REPORT_OIDC_ISSUER": "issuer", "REPORT_OIDC_AUDIENCE": "report-agent",
                      "REPORT_OIDC_JWKS_URL": "https://example.test/jwks",
                      "REPORT_APPROVAL_TOKEN": "signed-token"}
            with patch("report_agent.__main__.OidcReviewer", return_value=Verifier()):
                code = main(["--approve-manifest", str(manifest),
                             "--publication-dir", str(root / "published"),
                             "--approval-db", str(root / "approvals.db")], environ=config)
            self.assertEqual(code, 0)
            self.assertTrue((root / "published" / "credit-daily-2026-03-02-v1.approval.json").exists())

    def test_refuses_approval_without_oidc_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, manifest = save_draft(DailyReport("2026-03-02", "demo-v2", "# 日报\n", ()), root / "drafts")
            with self.assertRaises(SystemExit) as caught:
                main(["--approve-manifest", str(manifest), "--publication-dir", str(root / "published")],
                     environ={})
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse((root / "published").exists())


if __name__ == "__main__":
    unittest.main()
