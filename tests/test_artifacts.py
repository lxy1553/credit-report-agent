import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from report_agent.artifacts import approve_draft, save_draft
from report_agent.daily import DailyReport
from report_agent.identity import ReviewerIdentity


REVIEWER = ReviewerIdentity("reviewer-1", "policy-v1")


class ArtifactTests(unittest.TestCase):
    def test_saves_reviewable_draft_and_evidence_manifest(self):
        report = DailyReport("2026-03-02", "demo-v2", "# 日报\n", ({"metric_id": "application_count"},))
        with tempfile.TemporaryDirectory() as temp:
            markdown, manifest = save_draft(report, Path(temp))
            self.assertEqual(markdown.read_text(), "# 日报\n")
            data = json.loads(manifest.read_text())
            self.assertEqual(data["status"], "draft")
            self.assertEqual(data["business_date"], "2026-03-02")
            self.assertEqual(data["catalog_version"], "demo-v2")
            self.assertEqual(data["evidence"], [{"metric_id": "application_count"}])
            self.assertTrue(data["markdown_sha256"])

    def test_approval_freezes_draft_with_reviewer_and_version(self):
        report = DailyReport("2026-03-02", "demo-v2", "# 日报\n", ({"metric_id": "application_count"},))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, manifest = save_draft(report, root / "drafts")
            published, approval = approve_draft(manifest, REVIEWER, root / "published", root / "approvals.db")
            self.assertEqual(published.name, "credit-daily-2026-03-02-v1.md")
            self.assertEqual(published.read_text(), "# 日报\n")
            record = json.loads(approval.read_text())
            self.assertEqual(record["status"], "approved")
            self.assertEqual(record["reviewer"], "reviewer-1")
            self.assertEqual(record["policy_version"], "policy-v1")
            self.assertEqual(record["version"], 1)
            self.assertEqual(record["markdown_sha256"], json.loads(manifest.read_text())["markdown_sha256"])
            with sqlite3.connect(root / "approvals.db") as conn:
                row = conn.execute("SELECT reviewer, policy_version, version FROM approvals").fetchone()
            self.assertEqual(row, ("reviewer-1", "policy-v1", 1))

    def test_approval_rejects_modified_draft(self):
        report = DailyReport("2026-03-02", "demo-v2", "# 日报\n", ())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            markdown, manifest = save_draft(report, root / "drafts")
            markdown.write_text("# 被改写的日报\n")
            with self.assertRaisesRegex(ValueError, "篡改"):
                approve_draft(manifest, REVIEWER, root / "published", root / "approvals.db")

    def test_approval_rejects_same_draft_twice(self):
        report = DailyReport("2026-03-02", "demo-v2", "# 日报\n", ())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, manifest = save_draft(report, root / "drafts")
            approve_draft(manifest, REVIEWER, root / "published", root / "approvals.db")
            with self.assertRaisesRegex(ValueError, "已发布"):
                approve_draft(manifest, REVIEWER, root / "published", root / "approvals.db")

    def test_revised_draft_receives_next_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = DailyReport("2026-03-02", "demo-v2", "# 初稿\n", ())
            second = DailyReport("2026-03-02", "demo-v2", "# 修订稿\n", ())
            _, first_manifest = save_draft(first, root / "drafts" / "run-1")
            _, second_manifest = save_draft(second, root / "drafts" / "run-2")
            approve_draft(first_manifest, REVIEWER, root / "published", root / "approvals.db")
            revised, _ = approve_draft(second_manifest, REVIEWER, root / "published", root / "approvals.db")
            self.assertEqual(revised.name, "credit-daily-2026-03-02-v2.md")
            self.assertEqual(revised.read_text(), "# 修订稿\n")

    def test_approval_keeps_preexisting_publication_file_on_collision(self):
        report = DailyReport("2026-03-02", "demo-v2", "# 日报\n", ())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, manifest = save_draft(report, root / "drafts")
            published = root / "published"
            published.mkdir()
            collision = published / "credit-daily-2026-03-02-v1.md"
            collision.write_text("existing publication")
            with self.assertRaises(FileExistsError):
                approve_draft(manifest, REVIEWER, published, root / "approvals.db")
            self.assertEqual(collision.read_text(), "existing publication")

    def test_approval_rejects_invalid_manifest_date(self):
        report = DailyReport("2026-03-02", "demo-v2", "# 日报\n", ())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, manifest = save_draft(report, root / "drafts")
            data = json.loads(manifest.read_text())
            data["business_date"] = "2026-03-02/../../escape"
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "业务日期"):
                approve_draft(manifest, REVIEWER, root / "published", root / "approvals.db")


if __name__ == "__main__":
    unittest.main()
