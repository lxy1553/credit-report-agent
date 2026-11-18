# 报告生成 Agent

独立于 `Credit_Metric_RAG` 的信贷指标日报生成器。当前首版通过参考服务的认证 API 取数，生成**待人工审核的 Markdown 草稿**和证据清单。审核后可生成带版本号的发布文件及审批记录。

## 首版范围

- 业务日期：单日。
- 指标：申请笔数、申请通过率、贷款余额。
- 数据：仅接受参考服务返回的认证 `FINAL` 数据。
- 校验：指标身份、单日覆盖、数值类型与范围、认证引用、审计 Trace ID、目录版本和发布批次一致性；有未发布分区警告时停止生成。

## 运行

项目可作为本地 Python 包安装，包含 API 与 DOCX/PDF 支持：

```bash
python3 -m pip install -e '.[all]'
report-agent --help
```

也可以只安装对应功能：`python3 -m pip install -e '.[api]'` 安装 API 与 OIDC 依赖，`python3 -m pip install -e '.[documents]'` 安装 DOCX/PDF 依赖。若不希望使用可编辑安装，也可继续从项目根目录运行 `python3 -m report_agent`；`requirements-api.txt` 和 `requirements-documents.txt` 作为分项依赖清单保留。

先启动 `Credit_Metric_RAG` 服务，并使用有权访问上述三个指标的身份。参考项目的演示数据目前只覆盖其 README 指定的样例业务日期，其他日期可能返回无数据。

```bash
export CREDIT_API_URL=http://127.0.0.1:8000
export CREDIT_API_TOKEN=你的服务端令牌
python3 -m report_agent --date 2026-03-02 --output-dir reports/drafts
```

输出：`credit-daily-YYYY-MM-DD.md` 和 `credit-daily-YYYY-MM-DD.evidence.json`。证据清单包含指标口径、批次引用与 Trace ID，供人工审核。访问令牌只从环境变量读取，不写入文件。

草稿默认同时导出 DOCX 和 PDF。安装文档依赖：

```bash
python3 -m pip install -e '.[documents]'
```

若环境未安装中文字体，请设置 `REPORT_CJK_FONT` 指向 ReportLab 支持的中文 TTF 字体文件。可用 `--export-formats docx` 或 `--export-formats pdf` 只生成一种文档格式；Markdown 和证据 JSON 始终保留。

也可以把生成请求写入 SQLite 队列，由独立 worker 执行：

```bash
# 提交任务，保存返回的任务 ID
python3 -m report_agent --enqueue-date 2026-03-02

# 查询任务状态与草稿路径
python3 -m report_agent --task-id <task-id>

# 持续消费任务；在另一个终端查询状态
python3 -m report_agent --worker
```

任务队列默认保存在 `reports/tasks.db`，任务状态为 `queued`、`running`、`completed` 或 `failed`。API 状态响应包含尝试次数和下一次重试时间。瞬时网络故障、限流及数据服务 5xx 会自动按 1/2 秒退避重试，最多尝试三次；认证错误、无效请求和报告核验错误直接失败。worker 会输出包含任务 ID 的失败摘要，不输出访问令牌；普通 API 用户只看到通用失败提示，排查详情需查看 worker 日志或本机任务数据库。每个任务有 30 分钟 worker 租约；worker 重启后用 `python3 -m report_agent --recover-expired` 重新排队过期任务，达到尝试上限的过期任务会标为失败。草稿按任务 ID 分目录保存，避免同一日期的任务互相覆盖。队列适用于单机/单 SQLite 部署，不支持多机共享文件系统下的高可用调度。

人工核对草稿及证据后，审批人使用企业 OIDC 签发的 JWT 执行审批。令牌必须具有 `report_approver` 角色和 `policy_version` 声明；签名、签发方、受众与有效期由 JWKS 验证。先安装审批所需依赖：

```bash
python3 -m pip install 'PyJWT[crypto]>=2.10,<3'
```

然后配置可信身份源和令牌：

```bash
export REPORT_OIDC_ISSUER='https://你的身份源'
export REPORT_OIDC_AUDIENCE='report-agent'
export REPORT_OIDC_JWKS_URL='https://你的身份源/.well-known/jwks.json'
export REPORT_APPROVAL_TOKEN='审批人的 JWT'
python3 -m report_agent \
  --approve-manifest reports/drafts/credit-daily-2026-03-02.evidence.json
```

发布目录默认是 `reports/published/`，SQLite 审批账本默认是 `reports/approvals.db`。审批会核验草稿的 SHA-256，拒绝被修改或重复发布的草稿，并在 SQLite 事务中分配 `v1`、`v2` 等版本；发布文件及 `.approval.json` 保留审批人 `sub`、策略版本和证据。令牌不会写入报告或账本。

本项目尚未配置真实身份源；OIDC 验证逻辑通过本地测试，但真实 JWT/JWKS 联通仍需在目标环境验收。草稿及证据清单本身仍是本机文件，审核人必须核对内容和来源；哈希只能发现清单生成后对正文的改动，不能证明本机文件从未被替换。

## HTTP 任务 API

服务端 API 可独立接收日报请求并查询提交人的任务状态。安装 API 依赖并设置 OIDC 身份源：

```bash
python3 -m pip install -e '.[api]'
export REPORT_OIDC_ISSUER='https://你的身份源'
export REPORT_OIDC_AUDIENCE='report-agent'
export REPORT_OIDC_JWKS_URL='https://你的身份源/.well-known/jwks.json'
export REPORT_OUTPUT_DIR=reports/drafts
export REPORT_PUBLICATION_DIR=reports/published
export REPORT_APPROVAL_DB=reports/approvals.db
python3 -m report_agent --serve-api
```

服务默认只监听 `127.0.0.1:8081`；需要让其他机器访问时，用反向代理提供 TLS 和网络访问控制，再按部署环境设置 `REPORT_API_HOST`、`REPORT_API_PORT`。每个 API 用户 JWT 必须含 `report_user` 角色、`analyst` 或 `operator` 角色之一、`credit` 数据域及 `policy_version`。提交者身份会保存在任务记录中，查询其他用户的任务返回 404。任务响应提供受所有权校验的报告、证据及已发布文件下载链接，不暴露本地绝对路径或 worker 租约信息。

任务提交、状态查看、审批队列查看、文件下载与审批动作会写入任务数据库的 `report_audit_events` 表，包含操作者 `sub`、策略版本、动作、时间和必要元数据，不包含访问令牌或报告正文。带 `report_auditor` 角色的身份可查审计事件：

```bash
curl 'http://127.0.0.1:8081/v1/audit-events?task_id=<task-id>' \
  -H "Authorization: Bearer $REPORT_AUDITOR_TOKEN"
```

提交任务与查询状态。提交人可以读取自己的草稿；`report_approver` 可通过相同下载接口查看待审草稿与证据，再执行发布：

```bash
curl -X POST http://127.0.0.1:8081/v1/report-tasks \
  -H "Authorization: Bearer $REPORT_USER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"business_date":"2026-03-02"}'
curl http://127.0.0.1:8081/v1/report-tasks/<task-id> \
  -H "Authorization: Bearer $REPORT_USER_TOKEN"
# 审批人列出待审任务，下载草稿与证据
curl 'http://127.0.0.1:8081/v1/report-approvals/pending' \
  -H "Authorization: Bearer $REPORT_APPROVER_TOKEN"
# 审阅后，由具有 report_approver 的身份发布
curl -X POST http://127.0.0.1:8081/v1/report-tasks/<task-id>/approve \
  -H "Authorization: Bearer $REPORT_APPROVER_TOKEN"
curl http://127.0.0.1:8081/v1/report-tasks/<task-id>/artifacts/published \
  -H "Authorization: Bearer $REPORT_USER_TOKEN" -o published-report.md
```

HTTP 服务与 worker 分开运行；worker 继续使用 `CREDIT_API_URL` 和 `CREDIT_API_TOKEN` 消费 SQLite 队列，并且 `--output-dir` 必须与 API 的 `REPORT_OUTPUT_DIR` 指向同一目录。该服务令牌应由部署方配置为报告所需的最小权限，报告代码本身仍只请求首版固定的三个指标。当前队列是单机 SQLite，API 多进程或多主机部署需要共享可靠数据库/队列后再扩展。发布的首版文件为版本化 Markdown；DOCX/PDF 下载目前是草稿阶段文件。

API、worker 和 CLI 共用以下环境变量约定：`REPORT_TASK_DB`、`REPORT_OUTPUT_DIR`、`REPORT_PUBLICATION_DIR`、`REPORT_APPROVAL_DB`。CLI 显式传入的 `--task-db`、`--output-dir`、`--publication-dir` 和 `--approval-db` 会覆盖对应环境变量；API 与 worker 应使用相同的任务数据库及报告目录。

## 本地合成数据演示

没有真实 OIDC 和 Credit_Metric_RAG 环境时，可以用内置演示服务走通提交、生成、审核、发布、下载和审计流程。演示服务固定只监听 `127.0.0.1`，使用静态演示令牌和虚构指标，不能用于真实数据或生产部署。

终端一启动模拟数据 API 和演示身份：

```bash
python3 -m pip install -e '.[all]'
report-agent --serve-demo
```

终端二启动 worker（DOCX 可直接生成；生成 PDF 还需设置可用的中文 TTF 字体）：

```bash
export CREDIT_API_URL=http://127.0.0.1:8081
export CREDIT_API_TOKEN=demo-credit-service-token
report-agent --worker --export-formats docx
```

终端三提交日报并查看任务。可使用任意合法日期；各日期返回相同的合成数值：

```bash
curl -X POST http://127.0.0.1:8081/v1/report-tasks \
  -H 'Authorization: Bearer demo-report-user-token' \
  -H 'Content-Type: application/json' \
  -d '{"business_date":"2026-03-02"}'
curl http://127.0.0.1:8081/v1/report-tasks/<task-id> \
  -H 'Authorization: Bearer demo-report-user-token'
curl http://127.0.0.1:8081/v1/report-tasks/<task-id>/artifacts/markdown \
  -H 'Authorization: Bearer demo-report-user-token' -o draft.md
```

审核人使用 `demo-approver-token` 查询待办、下载证据并发布；审计身份使用 `demo-auditor-token` 查询日志：

```bash
curl http://127.0.0.1:8081/v1/report-approvals/pending \
  -H 'Authorization: Bearer demo-approver-token'
curl http://127.0.0.1:8081/v1/report-tasks/<task-id>/artifacts/evidence \
  -H 'Authorization: Bearer demo-approver-token' -o evidence.json
curl -X POST http://127.0.0.1:8081/v1/report-tasks/<task-id>/approve \
  -H 'Authorization: Bearer demo-approver-token'
curl 'http://127.0.0.1:8081/v1/audit-events?task_id=<task-id>' \
  -H 'Authorization: Bearer demo-auditor-token'
```

演示令牌及合成数据的固定值见 `report_agent/demo.py`。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

## 当前边界与下一步

当前使用固定模板和确定性文字，不调用生成式模型，因此不会编造原因分析。参考项目合成数据的本地 API 联调已通过：2026-03-02 的三个指标均来自同一认证版本及发布批次。已实现 SQLite 报告任务队列、提交人隔离的 HTTP 任务 API、报告/证据下载、审批人 API 发布、worker 租约恢复、DOCX/PDF 渲染、OIDC 审批适配与 SQLite 审批账本。仍需在目标环境配置真实身份源并联通验收；队列仅适合单机部署，尚未实现定时调度。

架构与目标流程见[报告生成 agent 流程图.md](报告生成%20agent%20流程图.md)。
