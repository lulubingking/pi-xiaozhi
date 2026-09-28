# 批小智后端

本目录对应开发技术文档中的 FastAPI 服务层。当前已完成运行配置、SQLite/Alembic 首版模型、统一响应格式、认证会话、班级/学生、作业批次、评分标准、通用任务、文件上传、试卷文件与题干快照、OCR 任务编排接口、OCR Worker、批改前置校验/任务入队接口、评分 Worker、教师复核、报告查询和 PDF/Word 导出接口。OCR 与批改 Worker 已通过 `app/workflows` 使用 LangGraph StateGraph 编排，模型执行仍由已验证的 OCR 专用环境和规则/LLM 适配器完成。

## 启动

在项目根目录创建独立后端环境后安装：

```powershell
python -m venv '.\backend\.backend-venv'
& '.\backend\.backend-venv\Scripts\python.exe' -m pip install -r '.\backend\requirements.txt'
& '.\backend\.backend-venv\Scripts\python.exe' -m alembic -c '.\backend\alembic.ini' upgrade head
& '.\backend\.backend-venv\Scripts\python.exe' '.\backend\scripts\seed_dev_teacher.py'
& '.\backend\.backend-venv\Scripts\python.exe' -m uvicorn app.main:app --app-dir '.\backend' --reload
```

开发环境虚拟教师账号（仅 `APP_ENV=development` 使用）：

```text
邮箱：teacher.demo@pixiaozhi.local
密码：不在文档中保存；使用本地自定义密码。
```

脚本会创建或更新该固定开发账号，并初始化其单教师配置；不会在正式环境启动时自动创建账号。

## 启动 OCR Worker

HTTP 接口只把 OCR 任务写入 `queued` 队列，不在请求中执行 GPU 推理。模型 Worker
需要使用项目中已经核验的三个 OCR 环境，并从项目根目录启动：

```powershell
& '.\backend\.backend-venv\Scripts\python.exe' '.\backend\app\ocr_worker.py' --once
```

`--once` 领取并处理一个任务；持续运行可去掉 `--once`。Worker 默认使用：

- `OCR工具测试\.venv\Scripts\python.exe`：PaddleOCR 印刷体/中文手写和数学公式候选区域提取；
- `OCR工具测试\.venv_trocr\Scripts\python.exe`：TrOCR large 英文手写；
- `OCR工具测试\.conda_texteller\python.exe`：TexTeller 数学手写识别。

Worker 会把页面预处理图、数学候选裁剪、原始 JSON 结果放在配置的 `STORAGE_ROOT`
下的 `ocr/{ocr_run_id}/`，并把可供校对页读取的文本块写入数据库。默认 SQLite
按单 Worker 运行；切换并发 Worker 前需要按开发技术文档迁移到支持行锁的数据库。

## 健康检查

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health/live'
Invoke-RestMethod 'http://127.0.0.1:8000/health/ready'
```

正式 API 前缀为 `/api/v1`，例如：

```text
POST /api/v1/auth/login
GET  /api/v1/auth/session
GET  /api/v1/classes
GET  /api/v1/batches
GET  /api/v1/rubrics
POST /api/v1/batches/{batch_id}/files
GET  /api/v1/batches/{batch_id}/pages
GET  /api/v1/pages/{page_id}
GET  /api/v1/pages/{page_id}/original
POST /api/v1/batches/{batch_id}/ocr-runs
POST /api/v1/pages/{page_id}/ocr-retry
PATCH /api/v1/pages/{page_id}/grouping
PUT  /api/v1/pages/{page_id}/correction
POST /api/v1/pages/{page_id}/confirm
GET  /api/v1/pages/{page_id}/history
GET  /api/v1/tasks/{task_id}
GET  /api/v1/settings
PATCH /api/v1/settings/profile
PATCH /api/v1/settings/model
POST /api/v1/settings/model/test
PATCH /api/v1/settings/storage
POST /api/v1/batches/{batch_id}/grading-runs
GET  /api/v1/grading-runs/{run_id}
GET  /api/v1/batches/{batch_id}/grading-tasks
POST /api/v1/grading-tasks/{task_id}/retry
POST /api/v1/grading-tasks/{task_id}/rerun
GET  /api/v1/grading-results/{result_id}
GET  /api/v1/batches/{batch_id}/review-queue
GET  /api/v1/review-items/{item_id}
PATCH /api/v1/review-items/{item_id}
POST /api/v1/review-items/{item_id}/confirm
POST /api/v1/review-items/{item_id}/rerun
GET  /api/v1/review-items/{item_id}/history
GET  /api/v1/batches/{batch_id}/reports
GET  /api/v1/batches/{batch_id}/reports/students/{student_id}
GET  /api/v1/batches/{batch_id}/reports/statistics
POST /api/v1/batches/{batch_id}/reports/exports
GET  /api/v1/report-exports/{export_id}
GET  /api/v1/report-exports/{export_id}/download
POST /api/v1/batches/{batch_id}/question-paper
PUT  /api/v1/batches/{batch_id}/questions/{question_id}/prompt
```

应用启动不会自动建表；数据库结构变更必须通过 Alembic 迁移完成。
上传依赖 Pillow 和 pypdf，用于图片可解码性校验、PDF 页数读取和页面记录；不承担 OCR 或模型推理。

OCR 启动请求必须显式传入 `recognition_mode`：`printed`、`chinese_handwriting`、`english_handwriting` 或 `math_handwriting`。任务会持久化模型、预处理和参数快照；HTTP 请求只入队，GPU 推理由 Worker 执行。四种 profile 按已确认的 OCR HANDOFF 固化：印刷体使用 PaddleOCR，中文手写使用 PP-OCRv5，英文手写使用 TrOCR large；数学题面先用 PP-OCRv5 保留中文、英文、数字和普通文本，再把公式候选送入 TexTeller，并按版面坐标合并为同一页结果。数学公式模型不可用时会降级保留普通文本并标明公式未识别，避免整页中文题干丢失。TexTeller 参数为 `device=cuda`、`max_tokens=256`、`num_beams=3`、`no_repeat_ngram_size=0`。PDF 页面在进入同一识别链路前由 Worker 使用 Poppler 的 `pdftoppm` 按页转为 PNG，默认 150 DPI；可通过 `PDF_POPPLER_PATH` 指定工具目录、通过 `OCR_PDF_DPI` 调整栅格化分辨率。PDF 栅格化信息会和 OCR 预处理信息一起写入页面变换快照，渲染缺失、超时或失败会以明确错误码结束任务，不会把 PDF 当作空答案提交。

模型设置使用通用 OpenAI 兼容配置：DeepSeek 是默认值，也可以配置其他服务商、模型和 Base URL。API Key 可直接在网页设置页输入，后端只在当前进程内存中暂存，不写入 SQLite、不返回给前端；已有部署仍兼容 `env:服务端环境变量名` 引用。后端重启后网页端 Key 需要重新输入。连接测试会真实请求配置 Base URL 的 `/models`，未配置或请求失败时返回明确错误，不显示模拟成功。

批改接口当前负责前置条件校验和持久化入队：会检查学生作业文件、OCR 页面、学生归属/页序、已确认答案、主观题试卷题干和评分标准；主观题还会检查网页端运行时 API Key 或兼容的服务端环境变量是否可用。任务进入 `queued` 后由评分 Worker 执行，API 请求不会伪造客观题或主观题分数；API 应用启动时会自动启动同进程评分 Worker，网页端 Key 可以被该 Worker 使用。

主观题适配器位于 `app/scoring/llm_adapter.py`，当前约定 OpenAI 兼容的 `/chat/completions` 接口。它使用统一提示词版本 `subjective-grading-prompt-v3-free-form-rubric`，允许把教师自定义的长文本评分说明原样传给模型，评分标准保存时不再要求评分点分值求和，也不会把题目原始分值当作模型评分上限；模型返回仍需满足评分点结构、分项与总分自洽和 `review_required=true`。适配器只保存输入哈希和响应摘要，不保存原始响应或明文 Key。

评分 Worker 位于 `app/grading_worker.py`，API 应用启动时会自动运行并每 1 秒检查队列；如需独立诊断或手动执行，也可用以下命令处理一个已入队批改运行：

```powershell
& '.\\backend\\.backend-venv\\Scripts\\python.exe' '.\\backend\\app\\grading_worker.py' --once
```

教师复核接口以 `grading_result.id` 作为 `review-item` 标识，携带结果 `version` 进行乐观锁校验；保存待复核、确认、异常处理、单题重跑和历史记录都会写入 `review_records`。只有最新批改运行中所有题目结果均为 `reviewed` 时，批次才会进入正式 `reviewed` 状态。

报告查询支持 `mode=reviewed` 和 `mode=ai_preview`。正式口径只返回整份作业所有题目已复核的学生；未复核学生不会混入正式成绩。

报告导出支持 `pdf` 和 `docx` 两种通用格式。导出创建接口会先冻结本次报告快照并返回 `queued` 任务，Worker 生成完成后再通过状态接口获取 `download_url`。通用报告字段包括：报告标题、批次/班级/学科/满分/生成时间、报告口径、班级统计、成绩分布、常见扣分原因、学生成绩、逐题分数、题型、教师评语、AI 评语、扣分原因和导出状态。`ai_preview` 文件会明确标注“AI 初评参考，非正式成绩”；正式 `reviewed` 文件只纳入整份作业已完成教师复核的学生。

报告 Worker 从项目根目录启动：

```powershell
& '.\backend\.backend-venv\Scripts\python.exe' '.\backend\app\report_export_worker.py' --once
```

持续运行时去掉 `--once`。生成文件使用 `STORAGE_ROOT/reports/{export_id}.pdf` 或 `.docx`，先写临时文件再原子替换；失败会保留任务失败码，不保留不完整文件。运行 PDF/Word 导出前必须安装 `reportlab` 和 `python-docx`，它们已列入 `backend/requirements.txt`。

Worker 当前已执行客观题严格归一化后精确匹配，并把建议分数、证据、规则版本和异常写入数据库；主观题会读取已从上传试卷 OCR 页面确认的 `question_prompt`，调用通用适配器，并将评分点、证据、扣分原因、模型名、提示词版本、输入哈希和响应摘要写入待复核结果。题干缺失时仍会写入 `QUESTION_PROMPT_MISSING` 失败结果并转异常，不会将参考答案冒充题干。

试卷使用独立文件角色 `question_paper`，不会进入学生作业归属。题目可以直接通过批次配置文本录入，也可以上传后使用 OCR 接口的 `file_role=question_paper` 查询参数进行识别；前端按题号和版面顺序自动归属 OCR block。教师核对题目文本和批次共享参考答案后，通过题目内容接口保存快照。参考答案存储在 `BatchQuestion`，所有学生共用，不在学生作业端重复上传。
