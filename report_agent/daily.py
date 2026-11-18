from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


METRICS = ("application_count", "approval_rate", "loan_balance")


@dataclass(frozen=True)
class DailyReport:
    day: str
    catalog_version: str
    markdown: str
    evidence: tuple[dict, ...]


def build_daily_report(service, day: str) -> DailyReport:
    try:
        parsed_day = date.fromisoformat(day)
        if parsed_day.isoformat() != day:
            raise ValueError
    except ValueError as exc:
        raise ValueError("业务日期必须为 YYYY-MM-DD") from exc

    catalog = {item["id"]: item for item in service.catalog()}
    if any(metric not in catalog for metric in METRICS):
        raise ValueError("认证目录缺少日报所需指标或当前身份无权访问")

    evidence = []
    versions = set()
    publication_sets = set()
    lines = [f"# 信贷指标日报 · {day}", "", "数据状态：FINAL", "", "## 指标概览", ""]
    for metric_id in METRICS:
        definition = catalog[metric_id]
        result = service.query_metric(metric_id, day)
        if result.get("status") != "ok" or result.get("metric_id") != metric_id:
            raise ValueError(f"{metric_id} 取数失败或指标不匹配")
        if result.get("data_status") != "FINAL":
            raise ValueError("正式报告只接纳 FINAL 数据")
        if result.get("warnings"):
            raise ValueError("存在未发布或不完整的数据分区")
        rows = result.get("rows", [])
        if len(rows) != 1 or rows[0].get("biz_date") != day:
            raise ValueError("指标结果必须恰好覆盖所选业务日期")
        try:
            value = Decimal(str(rows[0]["value"]))
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("指标值缺失或无效") from exc
        if not value.is_finite():
            raise ValueError("指标值必须为有限数值")
        citations = result.get("citations", [])
        catalog_citation = definition.get("citation", {})
        metric_citations = [c for c in citations if c.get("id") == catalog_citation.get("id")]
        batches = [c for c in citations if c.get("source") == "ads_credit_daily" and
                   c.get("biz_date") == day and c.get("data_status") == "FINAL" and
                   c.get("version") and c.get("updated_at")]
        if not metric_citations or not batches or not result.get("trace_id"):
            raise ValueError("认证引用、发布批次或审计 Trace ID 缺失")
        publication_sets.add(tuple(sorted((c["version"], c["updated_at"]) for c in batches)))
        if len(publication_sets) != 1:
            raise ValueError("报告指标使用了不同的数据发布批次")
        version = catalog_citation.get("version")
        if not version or metric_citations[0].get("version") != version:
            raise ValueError("指标口径版本不一致")
        versions.add(version)
        if len(versions) != 1:
            raise ValueError("报告指标口径版本不一致")

        unit = definition["unit"]
        if unit == "比例":
            if not 0 <= value <= 1:
                raise ValueError("比例指标超出 0 至 1")
            display = f"{value * 100:.2f}%"
        elif unit == "笔":
            if value != value.to_integral_value() or value < 0:
                raise ValueError("笔数必须为非负整数")
            display = f"{value:,.0f} 笔"
        elif unit == "元":
            if value < 0:
                raise ValueError("金额不能为负")
            display = f"{value:,.2f} 元"
        else:
            raise ValueError("未支持的指标单位")
        lines.append(f"- {definition['name']}：{display} [证据 {len(evidence) + 1}]")
        evidence.append({
            "metric_id": metric_id, "name": definition["name"], "value": str(value),
            "unit": unit, "definition": definition["definition"], "catalog_version": version,
            "batch_citations": batches, "metric_citation": metric_citations[0],
            "trace_id": result["trace_id"],
        })

    lines.extend(["", "## 口径与来源", ""])
    for index, item in enumerate(evidence, 1):
        batches = ", ".join(c["version"] for c in item["batch_citations"])
        definition = item["definition"].rstrip("。；")
        lines.append(f"{index}. {item['name']}：{definition}；认证版本 {item['catalog_version']}；发布批次 {batches}；Trace ID {item['trace_id']}。")
    lines.extend(["", "本报告由认证数据自动生成，发布前需人工审核。", ""])
    return DailyReport(day, next(iter(versions)), "\n".join(lines), tuple(evidence))
