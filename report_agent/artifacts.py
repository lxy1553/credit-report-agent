import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from report_agent.daily import DailyReport
from report_agent.identity import ReviewerIdentity


def save_draft(report: DailyReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"credit-daily-{report.day}"
    markdown = output_dir / f"{stem}.md"
    manifest = output_dir / f"{stem}.evidence.json"
    if markdown.exists() or manifest.exists():
        raise ValueError("该日期的草稿已存在，请选择新的输出目录")
    markdown.write_text(report.markdown, encoding="utf-8")
    manifest.write_text(json.dumps({
        "status": "draft", "business_date": report.day,
        "catalog_version": report.catalog_version,
        "markdown_sha256": hashlib.sha256(report.markdown.encode()).hexdigest(),
        "evidence": list(report.evidence),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return markdown, manifest


def approve_draft(manifest_path: Path, reviewer: ReviewerIdentity, publication_dir: Path,
                  database_path: Path) -> tuple[Path, Path]:
    if not isinstance(reviewer, ReviewerIdentity):
        raise ValueError("审批需要经过验证的身份")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("status") != "draft":
        raise ValueError("只能审核草稿")
    day = manifest["business_date"]
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError as exc:
        raise ValueError("无效的业务日期") from exc
    markdown_path = manifest_path.with_name(f"credit-daily-{day}.md")
    content = markdown_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != manifest.get("markdown_sha256"):
        raise ValueError("草稿已被篡改，拒绝发布")
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    publication_dir.mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path, timeout=10) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY, business_date TEXT NOT NULL, version INTEGER NOT NULL,
            draft_manifest_sha256 TEXT NOT NULL UNIQUE, markdown_sha256 TEXT NOT NULL,
            reviewer TEXT NOT NULL, policy_version TEXT NOT NULL, approved_at TEXT NOT NULL,
            published_path TEXT NOT NULL, approval_path TEXT NOT NULL,
            UNIQUE (business_date, version))""")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        published = approval = None
        created_paths = []
        try:
            if conn.execute("SELECT 1 FROM approvals WHERE draft_manifest_sha256=?",
                            (manifest_digest,)).fetchone():
                raise ValueError("该草稿已发布")
            version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM approvals WHERE business_date=?",
                                   (day,)).fetchone()[0]
            stem = f"credit-daily-{day}-v{version}"
            published = publication_dir / f"{stem}.md"
            approval = publication_dir / f"{stem}.approval.json"
            record = {
                "status": "approved", "business_date": day, "version": version,
                "reviewer": reviewer.subject, "policy_version": reviewer.policy_version,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "markdown_sha256": digest, "draft_manifest_sha256": manifest_digest,
                "catalog_version": manifest["catalog_version"], "evidence": manifest["evidence"],
            }
            with published.open("xb") as file:
                created_paths.append(published)
                file.write(content)
            with approval.open("x", encoding="utf-8") as file:
                created_paths.append(approval)
                json.dump(record, file, ensure_ascii=False, indent=2)
                file.write("\n")
            conn.execute("""INSERT INTO approvals
                (business_date, version, draft_manifest_sha256, markdown_sha256,
                 reviewer, policy_version, approved_at, published_path, approval_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (day, version, manifest_digest, digest, reviewer.subject, reviewer.policy_version,
                 record["approved_at"], str(published), str(approval)))
            conn.commit()
        except Exception:
            conn.rollback()
            for path in created_paths:
                if path.exists():
                    path.unlink()
            raise
    return published, approval
