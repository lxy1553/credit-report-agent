"""HTTP API for submitting and checking owner-bound report tasks."""

import os
from pathlib import Path

from report_agent.artifacts import approve_draft
from report_agent.identity import AuthenticationError, OidcReviewer
from report_agent.tasks import ReportTaskStore


def create_app(task_db: Path | str | None = None, verifier: OidcReviewer | None = None,
               output_dir: Path | str | None = None, publication_dir: Path | str | None = None,
               approval_db: Path | str | None = None):
    try:
        from fastapi import Depends, FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError("HTTP API 需要安装 requirements-api.txt") from exc

    config = os.environ
    if verifier is None:
        required = ("REPORT_OIDC_ISSUER", "REPORT_OIDC_AUDIENCE", "REPORT_OIDC_JWKS_URL")
        if any(not config.get(key) for key in required):
            raise RuntimeError("API 启动需要配置 REPORT_OIDC_ISSUER、REPORT_OIDC_AUDIENCE、REPORT_OIDC_JWKS_URL")
        verifier = OidcReviewer(config[required[0]], config[required[1]], config[required[2]])
    store = ReportTaskStore(Path(task_db or config.get("REPORT_TASK_DB", "reports/tasks.db")))
    drafts_root = Path(output_dir or config.get("REPORT_OUTPUT_DIR", "reports/drafts")).resolve()
    published_root = Path(publication_dir or config.get("REPORT_PUBLICATION_DIR", "reports/published")).resolve()
    ledger_path = Path(approval_db or config.get("REPORT_APPROVAL_DB", "reports/approvals.db"))
    app = FastAPI(title="报告生成 Agent API", version="1.0.0")
    bearer = HTTPBearer(auto_error=False)

    class SubmitTask(BaseModel):
        business_date: str

    def authenticate(credentials, required_role="report_user", any_roles=None, required_domain=None):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="需要 OIDC Bearer 令牌",
                                headers={"WWW-Authenticate": "Bearer"})
        try:
            return verifier.resolve(credentials.credentials, required_role=required_role,
                                    any_roles=any_roles, required_domain=required_domain)
        except AuthenticationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    def identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        return authenticate(credentials, "report_user", {"analyst", "operator"}, "credit")

    def approver(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        return authenticate(credentials, "report_approver")

    def auditor(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        return authenticate(credentials, "report_auditor")

    def public_task(task: dict) -> dict:
        result = {key: task[key] for key in ("task_id", "business_date", "status",
                  "approval_status", "attempt_count", "created_at", "updated_at")}
        result["next_attempt_at"] = task["available_at"] if task["status"] == "queued" else None
        result["error"] = "日报生成失败，请联系管理员查看服务日志" if task["status"] == "failed" else None
        artifacts = {}
        if task["status"] == "completed":
            artifacts.update({kind: f"/v1/report-tasks/{task['task_id']}/artifacts/{kind}"
                              for kind in ("markdown", "evidence")})
            markdown = Path(task["markdown_path"]) if task["markdown_path"] else None
            if markdown:
                for kind in ("docx", "pdf"):
                    if markdown.with_suffix(f".{kind}").is_file():
                        artifacts[kind] = f"/v1/report-tasks/{task['task_id']}/artifacts/{kind}"
            if task["approval_status"] == "approved":
                artifacts["published"] = f"/v1/report-tasks/{task['task_id']}/artifacts/published"
        result["artifacts"] = artifacts
        return result

    def reviewable_task(task_id: str, credentials):
        try:
            user = authenticate(credentials, "report_user", {"analyst", "operator"}, "credit")
            task = store.get(task_id, requester=user.subject)
        except HTTPException:
            task = None
        except KeyError:
            task = None
        if task is None:
            reviewer = authenticate(credentials, "report_approver")
            actor = reviewer
            try:
                task = store.get(task_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="任务不存在") from None
        else:
            actor = user
        if task["status"] != "completed":
            raise HTTPException(status_code=409, detail="任务尚未完成")
        return task, actor

    def safe_artifact(raw_path, root: Path) -> Path:
        if not raw_path:
            raise HTTPException(status_code=404, detail="文件不存在")
        path = Path(raw_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return path

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/v1/report-tasks", status_code=202)
    def submit_task(payload: SubmitTask, user=Depends(identity)):
        try:
            task = store.submit(payload.business_date, submitted_by=user.subject,
                                policy_version=user.policy_version)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return public_task(task)

    @app.get("/v1/report-tasks/{task_id}")
    def get_task(task_id: str, user=Depends(identity)):
        try:
            task = store.get(task_id, requester=user.subject)
        except KeyError:
            raise HTTPException(status_code=404, detail="任务不存在") from None
        store.record_audit(task_id, user.subject, user.policy_version, "task.view")
        return public_task(task)

    @app.get("/v1/report-approvals/pending")
    def pending_approval(limit: int = 50, reviewer=Depends(approver)):
        try:
            tasks = store.list_pending_approval(limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        store.record_audit(None, reviewer.subject, reviewer.policy_version,
                           "approval.queue.view", {"limit": limit, "item_count": len(tasks)})
        return {"items": [public_task(task) for task in tasks]}

    @app.get("/v1/audit-events")
    def audit_events(task_id: str | None = None, limit: int = 100, user=Depends(auditor)):
        try:
            events = store.list_audit(task_id, limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        store.record_audit(task_id, user.subject, user.policy_version, "audit.view", {"limit": limit})
        return {"items": events}

    @app.get("/v1/report-tasks/{task_id}/artifacts/{kind}")
    def get_artifact(task_id: str, kind: str,
                     credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        task, actor = reviewable_task(task_id, credentials)
        markdown = safe_artifact(task["markdown_path"], drafts_root)
        if kind == "markdown":
            path = markdown
        elif kind == "evidence":
            path = safe_artifact(task["manifest_path"], drafts_root)
        elif kind in {"docx", "pdf"}:
            path = safe_artifact(markdown.with_suffix(f".{kind}"), drafts_root)
        elif kind == "published":
            if task["approval_status"] != "approved":
                raise HTTPException(status_code=409, detail="报告尚未审批发布")
            path = safe_artifact(task["published_path"], published_root)
        else:
            raise HTTPException(status_code=404, detail="文件类型不存在")
        store.record_audit(task_id, actor.subject, actor.policy_version,
                           "artifact.download_requested", {"kind": kind})
        return FileResponse(path, filename=path.name)

    @app.post("/v1/report-tasks/{task_id}/approve")
    def approve_task(task_id: str, reviewer=Depends(approver)):
        try:
            task = store.get(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="任务不存在") from None
        if task["status"] != "completed":
            raise HTTPException(status_code=409, detail="任务尚未完成")
        if task["approval_status"] != "pending":
            raise HTTPException(status_code=409, detail="报告已审批")
        manifest = safe_artifact(task["manifest_path"], drafts_root)
        try:
            published, approval = approve_draft(manifest, reviewer, published_root, ledger_path)
            store.mark_approved(task_id, published, approval)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        store.record_audit(task_id, reviewer.subject, reviewer.policy_version, "report.approve")
        return {"task_id": task_id, "approval_status": "approved",
                "published_artifact": f"/v1/report-tasks/{task_id}/artifacts/published"}

    return app
