import unittest

from report_agent.daily import build_daily_report


class FakeCreditService:
    def __init__(self, *, status="ok", data_status="FINAL", warnings=None, version="demo-v2"):
        self.calls = []
        self.status = status
        self.data_status = data_status
        self.warnings = warnings or []
        self.version = version

    def catalog(self):
        return [
            {"id": key, "name": name, "unit": unit, "definition": definition,
             "citation": {"id": f"metric:{key}", "version": self.version}}
            for key, name, unit, definition in [
                ("application_count", "申请笔数", "笔", "当日申请数。"),
                ("approval_rate", "申请通过率", "比例", "当日通过数/申请数"),
                ("loan_balance", "贷款余额", "元", "日末贷款本金"),
            ]
        ]

    def query_metric(self, metric_id, day):
        self.calls.append((metric_id, day))
        values = {"application_count": 12, "approval_rate": 0.75, "loan_balance": 1234.5}
        return {
            "status": self.status, "metric_id": metric_id, "data_status": self.data_status,
            "rows": [{"biz_date": day, "value": values[metric_id]}],
            "warnings": self.warnings,
            "citations": [
                {"id": f"metric:{metric_id}", "version": self.version},
                {"id": f"batch:b1:{day}", "source": "ads_credit_daily", "biz_date": day,
                 "version": "b1", "updated_at": "2026-03-03T00:00:00Z", "data_status": "FINAL"},
            ],
            "trace_id": f"trace-{metric_id}",
        }


class DailyReportTests(unittest.TestCase):
    def test_builds_report_from_three_certified_metrics(self):
        service = FakeCreditService()
        report = build_daily_report(service, "2026-03-02")
        self.assertEqual(service.calls, [
            ("application_count", "2026-03-02"),
            ("approval_rate", "2026-03-02"),
            ("loan_balance", "2026-03-02"),
        ])
        self.assertIn("申请笔数：12 笔", report.markdown)
        self.assertIn("申请通过率：75.00%", report.markdown)
        self.assertIn("贷款余额：1,234.50 元", report.markdown)
        self.assertNotIn("。；", report.markdown)
        self.assertEqual(report.catalog_version, "demo-v2")
        self.assertEqual(len(report.evidence), 3)

    def test_rejects_realtime_result_for_formal_report(self):
        with self.assertRaisesRegex(ValueError, "FINAL"):
            build_daily_report(FakeCreditService(data_status="REALTIME"), "2026-03-02")

    def test_rejects_incomplete_publication(self):
        with self.assertRaisesRegex(ValueError, "未发布"):
            build_daily_report(FakeCreditService(warnings=["日期范围存在未发布分区"]), "2026-03-02")

    def test_rejects_mismatched_catalog_version(self):
        service = FakeCreditService()
        service.version = "other"
        catalog = service.catalog()
        service.catalog = lambda: [{**item, "citation": {"id": item["citation"]["id"], "version": "demo-v2"}} for item in catalog]
        with self.assertRaisesRegex(ValueError, "版本"):
            build_daily_report(service, "2026-03-02")

    def test_rejects_mixed_publication_batches(self):
        service = FakeCreditService()
        original = service.query_metric

        def mixed_query(metric_id, day):
            result = original(metric_id, day)
            if metric_id == "loan_balance":
                result["citations"][1]["version"] = "b2"
            return result

        service.query_metric = mixed_query
        with self.assertRaisesRegex(ValueError, "批次"):
            build_daily_report(service, "2026-03-02")


if __name__ == "__main__":
    unittest.main()
