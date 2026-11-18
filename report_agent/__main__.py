import argparse
import os
from pathlib import Path
import socket
import time

from report_agent.artifacts import approve_draft, save_draft
from report_agent.credit_client import CreditClient
from report_agent.daily import build_daily_report
from report_agent.identity import OidcReviewer
from report_agent.render import export_documents
from report_agent.tasks import ReportTaskStore
from report_agent.worker import run_once


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description="生成待人工审核的信贷指标日报")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--date", help="生成草稿：业务日期 YYYY-MM-DD")
    action.add_argument("--approve-manifest", help="审核发布：草稿证据清单路径")
    action.add_argument("--enqueue-date", help="提交持久化日报任务并返回任务 ID")
    action.add_argument("--task-id", help="查询任务状态")
    action.add_argument("--worker-once", action="store_true", help="领取并处理一个任务")
    action.add_argument("--worker", action="store_true", help="持续轮询并处理日报任务")
    action.add_argument("--recover-expired", action="store_true", help="重新排队已过期租约的任务")
    action.add_argument("--serve-api", action="store_true", help="启动受 OIDC 保护的报告任务 HTTP API")
    action.add_argument("--serve-demo", action="store_true", help="启动仅监听本机的合成数据演示服务")
    parser.add_argument("--output-dir", help="草稿目录（默认读取 REPORT_OUTPUT_DIR）")
    parser.add_argument("--publication-dir", help="发布目录（默认读取 REPORT_PUBLICATION_DIR）")
    parser.add_argument("--approval-db", help="审批账本路径（默认读取 REPORT_APPROVAL_DB）")
    parser.add_argument("--task-db", help="任务库路径（默认读取 REPORT_TASK_DB）")
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{os.getpid()}")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--export-formats", nargs="+", choices=("docx", "pdf"),
                        default=("docx", "pdf"), help="草稿附带的文档格式")
    args = parser.parse_args(argv)
    config = os.environ if environ is None else environ
    output_dir = Path(args.output_dir or config.get("REPORT_OUTPUT_DIR", "reports/drafts"))
    publication_dir = Path(args.publication_dir or config.get("REPORT_PUBLICATION_DIR", "reports/published"))
    approval_db = Path(args.approval_db or config.get("REPORT_APPROVAL_DB", "reports/approvals.db"))
    task_db = Path(args.task_db or config.get("REPORT_TASK_DB", "reports/tasks.db"))
    if args.serve_api:
        try:
            import uvicorn
        except ImportError:
            parser.error("HTTP API 需要安装 requirements-api.txt")
        from report_agent.api import create_app
        uvicorn.run(create_app(task_db, output_dir=output_dir, publication_dir=publication_dir,
                               approval_db=approval_db), host=config.get("REPORT_API_HOST", "127.0.0.1"),
                    port=int(config.get("REPORT_API_PORT", "8081")))
        return 0
    if args.serve_demo:
        try:
            import uvicorn
        except ImportError:
            parser.error("演示服务需要安装 API 依赖：python3 -m pip install -e '.[api]'")
        from report_agent.demo import create_demo_app
        app = create_demo_app(task_db, output_dir, publication_dir, approval_db)
        uvicorn.run(app, host="127.0.0.1", port=int(config.get("REPORT_DEMO_PORT", "8081")))
        return 0
    if args.enqueue_date:
        task = ReportTaskStore(task_db).submit(args.enqueue_date)
        print(f"任务已排队：{task['task_id']}（{task['status']}）")
        return 0
    if args.task_id:
        import json
        print(json.dumps(ReportTaskStore(task_db).get(args.task_id), ensure_ascii=False, indent=2))
        return 0
    if args.recover_expired:
        recovered = ReportTaskStore(task_db).recover_expired()
        print(f"已重新排队 {recovered} 个租约过期任务")
        return 0
    if args.worker_once or args.worker:
        store = ReportTaskStore(task_db)
        base_url = config.get("CREDIT_API_URL", "http://127.0.0.1:8000")
        token = config.get("CREDIT_API_TOKEN", "")
        while True:
            task = run_once(store, args.worker_id, base_url, token, output_dir,
                            args.export_formats)
            if task is not None:
                print(f"任务 {task['task_id']}：{task['status']}")
            if not args.worker:
                return 0 if task is not None else 1
            if task is None:
                time.sleep(max(0.2, args.poll_seconds))
    if args.approve_manifest:
        required = ("REPORT_OIDC_ISSUER", "REPORT_OIDC_AUDIENCE", "REPORT_OIDC_JWKS_URL",
                    "REPORT_APPROVAL_TOKEN")
        if any(not config.get(key) for key in required):
            parser.error("审核发布需要 OIDC 配置与 REPORT_APPROVAL_TOKEN")
        verifier = OidcReviewer(config["REPORT_OIDC_ISSUER"], config["REPORT_OIDC_AUDIENCE"],
                                config["REPORT_OIDC_JWKS_URL"])
        reviewer = verifier.resolve(config["REPORT_APPROVAL_TOKEN"])
        published, approval = approve_draft(Path(args.approve_manifest), reviewer,
                                             publication_dir, approval_db)
        print(f"已发布：{published}\n审批记录：{approval}")
        return 0
    base_url = config.get("CREDIT_API_URL", "http://127.0.0.1:8000")
    token = config.get("CREDIT_API_TOKEN", "")
    client = CreditClient(base_url, token)
    report = build_daily_report(client, args.date)
    markdown, manifest = save_draft(report, output_dir)
    documents = export_documents(markdown, args.export_formats)
    print(f"草稿：{markdown}\n证据清单：{manifest}")
    for document in documents:
        print(f"文档：{document}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
