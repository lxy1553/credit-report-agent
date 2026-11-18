"""Local-only mock Credit_Metric_RAG and identity provider for demonstrations."""

from datetime import date

from report_agent.api import create_app
from report_agent.identity import AuthenticationError, ReviewerIdentity


DEMO_CREDIT_TOKEN = "demo-credit-service-token"


class DemoIdentityVerifier:
    TOKENS = {
        "demo-report-user-token": ReviewerIdentity(
            "demo-user-001", "demo-policy-v1", frozenset({"report_user", "analyst"}),
            frozenset({"credit"})),
        "demo-approver-token": ReviewerIdentity(
            "demo-approver-001", "demo-policy-v1", frozenset({"report_approver"}), frozenset()),
        "demo-auditor-token": ReviewerIdentity(
            "demo-auditor-001", "demo-policy-v1", frozenset({"report_auditor"}), frozenset()),
    }

    def resolve(self, token: str, required_role="report_approver", any_roles=None,
                required_domain=None) -> ReviewerIdentity:
        identity = self.TOKENS.get(token)
        if identity is None:
            raise AuthenticationError("演示身份令牌无效")
        if required_role not in identity.roles:
            raise AuthenticationError("演示身份缺少所需角色")
        if any_roles and not (identity.roles & any_roles):
            raise AuthenticationError("演示身份缺少信贷指标读取角色")
        if required_domain and required_domain not in identity.domains:
            raise AuthenticationError("演示身份缺少信贷数据域权限")
        return identity


def create_demo_app(task_db=None, output_dir=None, publication_dir=None, approval_db=None):
    try:
        from fastapi import Depends, HTTPException
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError("演示服务需要安装 API 依赖：python3 -m pip install -e '.[api]'") from exc

    app = create_app(task_db, verifier=DemoIdentityVerifier(), output_dir=output_dir,
                     publication_dir=publication_dir, approval_db=approval_db)
    service_bearer = HTTPBearer(auto_error=False)

    class MetricQuery(BaseModel):
        metric_id: str
        start_date: str
        end_date: str
        data_status: str
        group_by: list[str]
        max_rows: int

    def require_service_token(credentials: HTTPAuthorizationCredentials | None):
        if (credentials is None or credentials.scheme.lower() != "bearer"
                or credentials.credentials != DEMO_CREDIT_TOKEN):
            raise HTTPException(status_code=401, detail="无效的演示数据服务令牌")

    metric_definitions = {
        "application_count": {
            "id": "application_count", "name": "申请笔数", "unit": "笔",
            "definition": "当日提交的信贷申请总笔数。",
        },
        "approval_rate": {
            "id": "approval_rate", "name": "申请通过率", "unit": "比例",
            "definition": "当日审批通过笔数除以当日有效申请笔数。",
        },
        "loan_balance": {
            "id": "loan_balance", "name": "贷款余额", "unit": "元",
            "definition": "业务日末贷款本金余额。",
        },
    }
    values = {"application_count": 126, "approval_rate": 0.7143, "loan_balance": 1_284_500.75}

    @app.get("/v1/catalog")
    def catalog(credentials: HTTPAuthorizationCredentials | None = Depends(service_bearer)):
        require_service_token(credentials)
        return [{**definition, "citation": {"id": f"metric:{metric_id}", "version": "demo-v1"}}
                for metric_id, definition in metric_definitions.items()]

    @app.post("/v1/metrics/query")
    def query_metric(payload: MetricQuery, credentials: HTTPAuthorizationCredentials | None = Depends(service_bearer)):
        require_service_token(credentials)
        if payload.metric_id not in metric_definitions:
            raise HTTPException(status_code=404, detail="演示目录中不存在该指标")
        try:
            start = date.fromisoformat(payload.start_date)
            end = date.fromisoformat(payload.end_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="日期格式必须为 YYYY-MM-DD") from None
        if (start != end or start.isoformat() != payload.start_date or payload.data_status != "FINAL"
                or payload.group_by != ["biz_date"] or payload.max_rows != 1):
            raise HTTPException(status_code=422, detail="不符合演示日报的单日 FINAL 查询契约")
        day = payload.start_date
        metric_id = payload.metric_id
        return {
            "status": "ok", "metric_id": metric_id, "data_status": "FINAL",
            "rows": [{"biz_date": day, "value": values[metric_id]}], "warnings": [],
            "citations": [
                {"id": f"metric:{metric_id}", "version": "demo-v1"},
                {"id": f"batch:demo-{day}", "source": "ads_credit_daily", "biz_date": day,
                 "version": f"demo-batch-{day}", "updated_at": f"{day}T23:59:59Z",
                 "data_status": "FINAL"},
            ],
            "trace_id": f"demo-trace-{metric_id}-{day}",
        }

    return app
