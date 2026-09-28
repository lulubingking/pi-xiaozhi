# 批小智 WebApp 交接文档

> 写给完全没有上下文的新会话。  
> 项目根目录：E:\实训项目\AI自动批改作业智能体  
> 记录时间：2026-09-20（Asia/Shanghai）。本文件最后的“最新会话交接”优先于前面较早的过程记录。

## 0. 新会话启动顺序

1. 先阅读本文件。
2. 再阅读以下正式依据，不能用聊天记忆替代：
   - E:\实训项目\AI自动批改作业智能体\AI自动批改作业智能体项目任务书V1.0.0.docx
   - E:\实训项目\AI自动批改作业智能体\prototype.html
   - E:\实训项目\AI自动批改作业智能体\批小智_PRD_v1.0.0.md
   - E:\实训项目\AI自动批改作业智能体\批小智_UIUX设计文档_v1.0.1.md
   - E:\实训项目\AI自动批改作业智能体\批小智_开发技术文档_v1.0.0.md
   - E:\实训项目\AI自动批改作业智能体\OCR工具测试\HANDOFF.md
3. 先检查当前文件树、依赖、服务和数据库状态，再修改代码。
4. 继续开发或交付前，必须区分“已验证的受控样例”与“生产级验收”，不要把单样例结果当成准确率或规模化结论。

## 1. 我们在做什么

正在开发“批小智”AI 自动批改作业 WebApp，目标流程是：

教师登录 → 创建班级和学生 → 创建作业批次 → 上传试卷/题目纸及学生作业 → OCR 识别和人工校正 → 配置或绑定题目、分值、参考答案、评分规则 → 自动批改 → 教师复核与确认 → 查看班级/学生报告 → 导出 PDF 或 Word。

本项目必须严格以任务书、产品原型、PRD、UI/UX 文档和开发技术文档为边界。没有在这些文件中确认的功能，不应在首版中自行扩展。

## 2. 已确认的 OCR 模型边界

用户已经确认以下产品识别方案，每一种识别都必须执行：

图片预处理 → 识别区域提取/裁剪 → 对应模型识别。

模型对应关系：

- 印刷体：PaddleOCR
- 中文手写：PaddleOCR PP-OCRv5
- 英文手写：TrOCR
- 数学手写：TexTeller

根产品方案仍然使用 TexTeller 作为数学手写识别配置。OCR工具测试目录里的独立实验（包括后续公式模型实验）不能未经确认就替换产品配置。

用户说明正式验收按现有约定的 85% 指标执行；不要在没有可复现实验边界、样本范围和报告的情况下新增或声称 OCR 准确率。

## 3. 当前实现已经完成的内容

### 3.1 前端基础

- React + Vite 前端已存在于 src。
- 已完成登录态、退出、页面导航和主工作台结构。
- 已有加载中、空状态、错误状态、重试入口。
- 已加入键盘焦点可见样式、跳过链接、响应式断点和减少动画设置。
- 设置页对未接入的后端设置接口会明确提示“本地演示配置”，不会伪造保存成功。
- 全局顶部搜索已是实际表单状态，会跳转到作业批次页并同步过滤。

主要文件：

- E:\实训项目\AI自动批改作业智能体\src\App.tsx
- E:\实训项目\AI自动批改作业智能体\src\styles.css
- E:\实训项目\AI自动批改作业智能体\src\api\client.ts

### 3.2 账号和基础数据

- 登录、会话恢复、退出登录已接入。
- 已提供开发教师虚拟账号：
  - 邮箱：teacher.demo@pixiaozhi.local
   - 密码：不在文档中保存；使用本地自定义密码。
- 该账号只用于开发联调，不要当作生产账号。
- 班级、学生列表、详情、创建、编辑、状态切换已接入。

### 3.3 作业批次和题目

- 作业批次创建、列表、详情、编辑和版本信息已接入。
- 支持题目、分值、参考答案、评分规则绑定。
- 已补充批次分值和题目分值的有限数值、正数、总分一致性校验。
- 批次列表不再展示没有后端依据的假进度、假人数、假异常数，未知值显示“待统计”“待读取”或引导查看详情。
- 待 OCR/校正的批次会进入 OCR 页面，待批改/复核的批次会进入批改页面。

### 3.4 文件上传、试卷和 OCR

- 支持 JPG、PNG、PDF 上传。
- 文件角色区分为 question_paper（试卷/题目纸）和 student_work（学生作业）。
- 已实现上传记录、文件列表、页面列表、预览、重试入口。
- 学生作业支持学生归属、页码顺序和分组。
- 已接入 OCR 任务队列、页面预览、OCR 重试、识别结果编辑。
- 已支持空白确认、答案区域校正、题目提示 OCR 区块绑定及版本概念。
- “查看上传记录”已从原先的假提示改为滚动到真实上传历史，并读取后端文件接口。
- 上传失败重试会复用原 SourceFile，而不是无条件创建新的源文件。
- 学生页码顺序校验为整数且大于等于 1。
- 题目纸页面使用后端 page_index，不再重复加一；OCR block_index 同样遵循后端编号。

PDF 相关修复：

- backend/app/ocr_worker.py 已加入 PDF 渲染依赖探测和明确错误码。
- 支持通过 PDF_POPPLER_PATH 指定 pdftoppm。
- 默认 PDF_OCR_DPI 为 150。
- 支持 PDF_RENDER_TIMEOUT_SECONDS 超时控制。
- PDF 会先渲染为 PNG，再进入后续 OCR 流程。
- backend/README.md 已记录 PDF Worker 的启动和错误边界。

### 3.5 自动批改、复核和报告

- 已有批改预检。
- 客观题支持规则匹配。
- 主观题使用通用模型适配器和严格 JSON 结果校验。
- DeepSeek 是默认示例，但 API 设计允许接入其他 OpenAI-compatible 或兼容适配器；不能把模型供应商写死为 DeepSeek。
- 已有教师复核队列、保存、确认、异常标记、重跑和历史记录。
- 报告区分 reviewed 和 ai_preview，不要把 AI 预览伪装成最终确认结果。
- 已有 PDF/Word 通用导出字段和队列化导出 Worker。
- 之前已验证过一次真实 PDF 导出：
  - task_id：report_export_wHOl7it4x2NAGdv0YiaZUw
  - HTTP 200
  - Content-Type 为 application/pdf
  - 文件约 20290 bytes

### 3.6 后端结构

后端位于：

- E:\实训项目\AI自动批改作业智能体\backend

当前后端已经覆盖：

- FastAPI API
- SQLite 数据库
- Alembic 迁移
- 认证
- 班级和学生
- 作业批次
- 题目、分值、参考答案、评分规则
- 文件和页面
- 试卷
- OCR 编排和 OCR Worker
- 批改预检、批改队列和批改 Worker
- 教师复核
- 报告
- PDF/Word 导出

API 前缀为：

    /api/v1

### 3.7 核心技术已接入正式 Worker

- `backend/requirements.txt` 已加入 `langgraph>=0.6,<0.7`，后端虚拟环境已实际安装并可导入。
- `backend/app/workflows/ocr_graph.py` 使用 LangGraph `StateGraph` 编排：文件加载、格式校验、页面拆分、预处理边界、归属建议、模型执行、原始结果保存、异常检测、页面状态保存、等待教师校对。
- `backend/app/workflows/grading_graph.py` 使用 LangGraph `StateGraph` 编排：批次配置、前置校验、已确认答案、题型分派、客观规则/主观 LLM 节点、结果校验、异常检测、结果保存、学生状态聚合和教师复核入队。
- OCR 图的模型执行节点仍调用已验证的 OCR 专用环境：PaddleOCR + OpenCV/PIL、TrOCR、TexTeller；没有用 LangGraph 替换模型，也没有用静态演示代替识别。
- OCR 页面 `transform_json.workflow` 会保存 LangGraph 节点 trace、当前阶段和等待教师校对状态，便于定位任务停在哪一步。
- `grading_worker.py` 已补齐从项目根目录直接启动时的 Python 路径注入；`grading.py` 已补齐复核审计记录 helper。

### 3.8 本轮 WebApp 全量审计修复

- 设置页补齐了 `queue_limit`（待处理队列上限）和 `worker_concurrency`（后台并发数）的真实编辑控件，保存仍走 `/settings/storage`，不再只读展示服务端配置。
- 报告页个人成绩单补齐“详情”入口，调用 `/batches/{batch_id}/reports/students/{student_id}`；详情弹窗显示当前统计口径、分项分数、复核状态和缺失题目，并可进入复核页查看逐题证据。
- 个人成绩详情支持关闭按钮、遮罩关闭和 Escape 关闭；报告口径或批次切换时会清理旧详情，避免跨批次/跨口径残留。
- 运行态已统一为项目 `backend\\.backend-venv`：后端 1 个、OCR Worker 1 个、批改 Worker 1 个、报告导出 Worker 1 个。SQLite 单机边界下不启动重复 Worker。
- `vite preview` 使用 `http://127.0.0.1:5173/`，前端 `/api` 代理到 `http://127.0.0.1:8000`；登录、会话、批次、报告详情和设置接口均已通过预览地址实测。

## 4. 已完成的验证证据

以下是本轮最近一次已完成的验证，不代表服务在新会话启动时仍然运行：

前端：

- npm run build 已通过。
- 构建执行了 tsc --noEmit && vite build。
- 最近一次产物包括 dist/index.html、JS 和 CSS。
- 最近验证的预览地址是 http://127.0.0.1:5173/，返回 HTTP 200；通过该地址访问 `/api/v1` 也已验证可代理到后端。
- /assets/ 也返回 HTTP 200。

后端静态检查：

- `& .\backend\.backend-venv\Scripts\python.exe -m compileall -q .\backend\app .\backend\tests` 已通过。
- `& .\backend\.backend-venv\Scripts\python.exe -m unittest discover -s backend/tests -v` 已通过，2 个 LangGraph 工作流冒烟测试通过；不要使用未安装 LangGraph 的系统 Python 替代项目虚拟环境。
- 从项目根目录执行 alembic check 已通过，结果为 No new upgrade operations detected。
- 注意：在 backend 子目录直接使用相对 alembic.ini 曾出现脚本路径错误；那是工作目录错误，不是迁移代码错误。应从项目根目录按 README 命令执行。

API 冒烟：

- GET /health/live：200
- GET /health/ready：200
- 登录和会话接口：200
- 设置接口：200，默认供应商显示为 DeepSeek
- classes：200，已有 1 个班级
- batches：200，已有 1 个批次
- rubrics：200，当前 0 个
- 批次 files：200，已有 1 个文件
- 批次 pages：200，已有 1 个页面
- 批次 validate：200，valid_for_upload=true，valid_for_grading=true
- grading tasks：0
- review queue：0
- reports students：0
- statistics students：0

本轮核心链路回归：

- 真实 PaddleOCR 样例运行返回 `succeeded`、30 个文本块，GPU 设备可用；这不是准确率验收。
- 已完成一条受控 API/Worker 业务回归：PDF 页面栅格化 → OCR Worker → LangGraph OCR 图 → OCR 校对/学生归属 → LangGraph 批改图 → 客观规则建议分 → 教师复核 → `reviewed` 报告 → PDF 导出。
- 受控回归导出文件实际下载成功，大小 23801 bytes，文件头为 `%PDF`。

目前已有联调批次 ID：

    batch_dqTvfDmtNjVhCnEpSZGA

## 5. 当前卡点和未完成验证

### 5.1 浏览器自动化通道不可用

最近多次调用 CUA 浏览器状态，返回：

    apps: []
    browsers: []
    errors: ["Browsers: Error: nodeRepl.fetch request failed"]

这不是已确认的 WebApp 业务错误，而是 Codex 到浏览器的自动化桥接/连接器错误。因此目前不能把真实浏览器点击、上传、拖拽、弹窗、响应式布局检查标记为已完成。

恢复通道时按以下顺序处理：

1. 重启或更新 Codex Desktop。
2. 检查 Computer Use/浏览器插件是否启用。
3. 使用 Codex 内置 @Browser 打开 localhost 或 127.0.0.1。
4. 如使用 Chrome/Edge，重新连接当前浏览器配置文件或扩展。
5. 新建 Codex 任务后再次调用一次浏览器状态。
6. 仍失败时提交 /feedback，附上 nodeRepl.fetch request failed 和时间信息。

不要在同一故障下无限重复 CUA 调用，也不要把自动化桥接故障归因于前端代码。

### 5.2 生产级验收仍未完成

当前已完成一条单学生、单客观题、单页 PDF 的受控真实链路，但这不能替代生产级验收。仍需覆盖：

- PDF 多页渲染和页面顺序。
- question_paper 与 student_work 不混淆。
- OCR 失败重试是否复用源文件。
- 空白页和空白答案的确认状态。
- 学生归属、题号和页码顺序。
- 批改任务失败、重跑和复核确认。
- reviewed 报告和 ai_preview 报告字段差异。
- Word/PDF 导出内容和下载文件实际可打开。
- 中文手写、英文手写和数学手写四种 profile 的真实运行回归；当前本轮只跑通了印刷体 PaddleOCR。
- 主观题 OpenAI-compatible/DeepSeek 真实配置回归；当前未配置外部模型密钥，因此没有把主观题 LLM 结果伪造为通过。
- 受控数据集上的 OCR/评分准确率、并发和 60 秒目标；当前不能从“30 个非空 OCR block”推导准确率。

### 5.3 没有专门的端到端浏览器测试套件

package.json 目前只有：

- dev
- build
- preview

没有 Playwright 或 Cypress。是否增加自动化测试要以项目范围和用户确认的技术文档为准，不能为了测试擅自扩大技术栈。浏览器通道恢复后，也可以先进行手工验收，再决定是否补测试工具。

### 5.4 生产化边界仍需确认

- 当前是开发联调实现，不应宣称已经生产部署。
- API Key 只能通过服务端环境变量或密钥引用读取，禁止写入前端、数据库明文、日志和导出文件。
- 当前本地交付通过 Vite `/api` 代理访问后端，因此 `127.0.0.1:5173` 已完成登录和业务接口验证；若改为前后端分端口直连或更换域名，仍需按实际前端地址调整 CORS，不能把本地代理验证当作生产部署配置已完成。
- SQLite 只适合当前单机开发/小规模联调；在多 Worker 或并发生产环境前，应评估 PostgreSQL、队列和行锁方案。

## 6. 下一步执行计划

### 阶段 A：恢复可观察性

1. 恢复浏览器自动化通道，确认可打开当前前端。
2. 启动后端、前端预览、OCR Worker、grading Worker、report export Worker。
3. 检查健康接口、登录态、控制台错误和网络请求。
4. 若浏览器通道仍不可用，继续用 API 和构建验证，但必须在验收结论中明确“UI 真实点击未验证”。

### 阶段 B：构造完整联调样本

1. 建立开发班级、学生、作业批次。
2. 上传一份题目纸和至少一份学生作业，分别验证文件角色。
3. 使用 PDF 和图片各执行一次。
4. 运行 OCR Worker，检查预处理、区域裁剪、模型选择和识别结果状态。
5. 完成 OCR 校正、空白确认、学生归属、页码排序。
6. 运行批改 Worker，检查客观题、主观题适配器和失败重跑。
7. 进行教师复核和最终确认。
8. 检查学生报告、班级统计、PDF 导出和 Word 导出。

### 阶段 C：缺陷修复和回归

1. 修复真实联调中发现的功能、状态、显示和异常处理问题。
2. 重新运行 npm run build、compileall、alembic check。
3. 重新执行 API 冒烟和完整业务链路。
4. 对关键页面做浏览器手工回归：登录、批次、上传、OCR、批改、复核、报告、设置。
5. 最后再整理开发启动命令、测试账号、已知限制和验收结论。

## 7. 启动命令

从项目根目录执行。

后端环境创建：

    python -m venv .\backend\.backend-venv
    & .\backend\.backend-venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
    & .\backend\.backend-venv\Scripts\python.exe -m alembic -c .\backend\alembic.ini upgrade head
    & .\backend\.backend-venv\Scripts\python.exe .\backend\scripts\seed_dev_teacher.py
    & .\backend\.backend-venv\Scripts\python.exe -m uvicorn app.main:app --app-dir .\backend --reload

OCR Worker：

    & .\backend\.backend-venv\Scripts\python.exe .\backend\app\ocr_worker.py --once

连续运行时去掉 --once。

批改 Worker：

    & .\backend\.backend-venv\Scripts\python.exe .\backend\app\grading_worker.py --once

报告导出 Worker：

    & .\backend\.backend-venv\Scripts\python.exe .\backend\app\report_export_worker.py --once

前端：

    npm run dev

生产构建预览：

    npm run build
    npm run preview

## 8. 绝对不要再踩的坑

1. 不要绕过正式文档自行增加首版功能；不确定时先标记为待确认。
2. 不要把 OCR工具测试 HANDOFF 中的独立实验模型，未经确认替换根产品已经确认的 TexTeller。
3. 不要把 DeepSeek 写死成唯一模型；应保留通用模型适配器、供应商、Base URL、模型名和服务端 API Key 引用。
4. 不要把 API Key 放到前端、SQLite 明文、日志、错误信息或导出文件中。
5. 不要把未完成的后端接口用假 Toast、假成功、假进度、假统计或静态数字遮住。
6. 不要把 ai_preview 当作 reviewed，也不要在教师未确认时显示最终结论。
7. 不要混用 question_paper 和 student_work 文件角色。
8. 不要重复给后端已经从 1 开始的 page_index 加一。
9. 不要在上传失败重试时无条件新建 SourceFile，除非业务明确要求新文件。
10. 不要用逐字符变化触发批量 API 请求；批次 ID 等输入应在回车或失焦后提交。
11. 不要对空值直接做 Number.isFinite、toFixed 或数组操作；先进行显式解析和校验。
12. 不要只验证前端构建就说系统完成；必须覆盖 Worker、数据库、API 和导出。
13. 不要把服务当前是否运行和代码是否实现混为一谈；启动状态要重新检查。
14. 不要在 SQLite 上盲目增加多个 Worker 并发；先解决锁、领取任务和一致性问题。
15. 不要使用破坏性清理命令删除用户原始资料、数据集、模型、环境或原型文件。
16. 不要因浏览器自动化桥接失败就反复修改业务代码；先恢复桥接，再判断 UI 问题。
17. 不要把一次 HTTP 200 当作业务流程正确；要检查响应数据、数据库状态、文件内容和页面交互。
18. 不要把静态检查通过描述成 OCR 或批改准确率通过；准确率只能引用有样本和口径的验收证据。

## 9. 交接结论

项目已经具备可继续联调的前后端骨架和主要业务闭环，前端构建、后端编译、迁移检查、原型回归、前端代理登录和报告详情接口均已通过；开发教师账号和 PDF 导出曾经真实验证过。本轮还修复了设置运行边界不可编辑、报告详情缺入口和重复 Worker 运行态问题。

当前最重要的未完成项不是继续堆页面，而是：

- 恢复浏览器自动化通道；
- 恢复浏览器自动化通道，完成登录、上传、OCR 校对、复核、报告页面的手工/浏览器回归；
- 补齐三种手写/数学 profile 和主观题模型的受控回归；
- 根据多样本结果出具准确率、性能和生产限制报告。

当前结论：核心技术已落到真实 Worker 和受控业务链路中，WebApp 达到可继续交付联调的状态；尚不能宣称生产级验收全部通过。

## 10. 2026-09-19 页面视觉与交互舒适度优化

本轮针对用户提供的工作台、作业管理、OCR 校对、智能批改、评分标准、批改报告和设置页截图，完成了统一的 CSS 体验层优化，未改变现有 API、数据结构、LangGraph、OCR、图像处理、规则批改或报告流程。

- `src/styles.css` 新增 UX polish layer：正文基准调整为 15px，移动端为 16px；辅助文案、状态、表格和表单说明不再使用 9–11px 的难读字号。
- 统一按钮、输入框、选择框、状态标签、卡片圆角、边框、阴影和间距；主操作仍使用橙色，导航和流程状态仍使用蓝色/绿色/警示色。
- 工作台空任务、批改空结果、评分标准空列表、报告空状态统一为明确的图标、标题、说明和操作引导；评分标准空状态横跨整行。
- OCR/校对页面降低无内容时的过度留白，增强流程节点、上传区、文本块、学生归属和答案编辑的层级；报告、设置、班级维护页统一表单可读性。
- 增加/覆盖 1180、900、700、420px 断点规则，移动端表单和按钮触控目标按 44px 以上处理；保留现有 `prefers-reduced-motion`、焦点圈和无横向溢出约束。

本轮验证：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过；后端 LangGraph smoke tests 2 项通过；`compileall` 通过；登录、健康检查和设置接口烟测通过，当前设置接口返回 `model_base_url = null`、队列上限 20、后台并发 4。浏览器桥接当前仍不可用（`apps: []`, `browsers: []`, `nodeRepl.fetch request failed`），因此本轮没有宣称真实浏览器点击验收通过。

## 11. 2026-09-19 OCR 页面自动下载修复

问题：进入 OCR 校对页时，学生作业 PDF 会在页面初始化阶段通过 `iframe` 预览；`backend/app/api/files.py` 的原始页面接口使用了 `FileResponse` 默认的 `Content-Disposition: attachment`，导致浏览器把预览请求当成文件下载。

修复：原始页面接口改为 `content_disposition_type="inline"`，保留账号权限校验和原始文件只读语义；报告导出接口仍保持附件下载。运行中的旧后端进程已替换为项目独立环境 `backend\.backend-venv` 启动的后端，健康检查通过。

证据：同一个真实页面接口现在返回 `Content-Disposition: inline; filename="qa-report_export_bOi53WjwR7a9ZOGKZJUUw.pdf"`；`npm run build`、后端 `compileall`、LangGraph smoke tests 2 项均通过。

## 12. 2026-09-19 参考页面功能化改造

用户提供的 7 张页面截图只作为布局、信息密度和交互入口参考；截图中的姓名、分数、日期、头像和示例批次没有写入产品数据，也没有替代正式 API。

本轮在既有真实业务闭环上增加了功能，而不是只改样式：

- `src/App.tsx`：工作台标题使用真实教师名；任务行根据真实批次状态跳转 OCR、智能批改或作业管理；增加创建批次、作业管理、评分标准、报告快捷入口；增加基于真实加载结果的会话状态和首次使用引导。
- 作业管理：批次搜索纳入批次 ID、班级、学科和批次名称；增加学科筛选、结果计数和清除筛选；保留班级、学生、文件上传、失败重试等正式接口。
- 智能批改：复核队列增加学生/学号/题号搜索、异常过滤、排序，以及上一条/下一条结果切换；展示内容仍来自 `review-queue`、复核详情、历史和保存/确认接口。
- 评分标准：增加学科、状态和名称/编号筛选；增加真实版本历史读取弹窗；编辑仍通过版本递增接口，停用/启用仍通过正式接口。
- `src/styles.css`：新增上述交互区域的响应式布局、状态层级、触控尺寸和窄屏折叠规则。

本轮验证：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过；前端预览入口 `http://127.0.0.1:5173/` 返回 HTTP 200。浏览器桥接仍不可用，因此没有把真实浏览器点击截图验收写成已完成；后续应在桥接恢复后按工作台、作业管理、OCR 校对、智能批改、评分标准、批改报告、设置逐页回归。

## 13. 2026-09-19 页面视觉系统重构

针对用户反馈“样式也要跟着改变”，在不改动真实 API、数据结构和业务流程的前提下，对 `src/styles.css` 增加了最终视觉覆盖层：

- 蓝白教师工作台品牌基调：侧边栏、顶部栏、页面头部、内容背景和状态色统一。
- 页面头部增加轻量几何背景层，统一标题、说明和主操作的视觉层级；没有引入截图中的虚构插画或演示数据。
- 卡片、指标、表格、工具栏、上传区、流程区、复核区、报告区和设置区统一圆角、阴影、边框、间距和状态反馈。
- 按钮、输入框、状态标签和移动端控件提升到更适合操作的尺寸；保留 375、700、900、1180 等响应式断点和无横向溢出约束。
- 仍使用 Phosphor SVG 图标，没有用 emoji 或临时图片代替结构化图标。

验证：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过。浏览器桥接仍不可用，视觉验收目前以代码、构建、响应式审计和已有原型回归为依据；桥接恢复后还需要逐页截图对照验收。

## 14. 2026-09-19 内置浏览器逐页截图验收

本轮使用内置浏览器 IAB，在 `http://127.0.0.1:5173/` 以开发教师账号登录，桌面视口 1440×900 逐页点击验收：

- 工作台：真实教师名、统计卡片、待处理任务、处理流程和快捷入口正常显示。
- 作业管理：批次/班级/学生管理、搜索筛选、批量上传区域正常显示；输入不存在的批次后可进入真实空状态，再清除筛选恢复。
- OCR 校对：试卷 OCR、PaddleOCR/PP-OCRv5/TrOCR/TexTeller 识别模式、学生作业 OCR 校对和保存/确认入口正常显示；再次从导航进入时未出现自动下载控件或下载提示。
- 智能批改：批次选择、结果筛选、复核队列、学生/题号搜索和“仅异常”筛选正常显示；“仅异常”已实际勾选并恢复。
- 评分标准：搜索、学科/状态筛选、空状态、版本与校验规则、版本历史区域正常显示。
- 批改报告：批次范围、PDF/Word 选择、教师已复核与 AI 初评参考口径切换、成绩分布和个人成绩单正常显示；已实际切换到“AI 初评参考”。导出按钮未触发，避免产生额外下载文件。
- 设置：模型 API、文件与数据、队列上限、后台并发、保存配置和真实连接测试入口正常显示；未触发真实连接或保存，避免外部副作用。

验收结论：逐页进入、DOM 结构、页面截图和上述低风险交互均通过；未发现阻断性页面错误。上一节“浏览器桥接仍不可用”的描述仅代表视觉重构完成时的状态，已由本节内置浏览器验收结果补充更新。

## 15. 2026-09-19 参考图样式二次对齐

用户反馈上一版“统一美化”与参考截图仍有明显差异。本轮按参考截图重新调整页面构图，而不是只换颜色：

- 侧栏从 266px 调整为约 228px，主内容改为全宽伸展，避免高分辨率下右侧空白和信息密度不足。
- 页面头部改为参考图中的蓝白渐变 Hero 结构：标题、说明、页面主操作、装饰层和页面主题视觉分区明确。
- 工作台、作业管理、OCR、智能批改、评分标准、批改报告、设置分别显示对应的任务图形和主题文案。
- 参考图中的教师人物没有可复用的原始资源，本轮使用内嵌 SVG 绘制轻量教师插画，避免引入外部图片或把用户截图直接作为产品资源。
- 保留实际批次、统计、上传、OCR、批改、报告和设置数据；新增视觉层不替换后端数据，也不改变 API 或核心技术流程。

验证：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过；内置浏览器新标签逐页复看工作台、作业管理、OCR 校对、智能批改、评分标准，页面主题图形、布局和按钮层级已生效。

## 16. 2026-09-19 frontend-design 技能配置与视觉系统重设

用户要求配置并使用 `frontend-design` 技能重设批小智页面。OpenAI curated 技能列表没有同名技能，已从公开 `anthropics/skills` 仓库安装到项目目录：

    .agents/skills/frontend-design/SKILL.md

本轮先按技能要求完成设计方向和自检，再实现代码：保留参考图的侧栏、顶部导航、Hero 页面头部、流程分区和真实数据入口；将产品视觉收敛为“纸张与批注工作台”，用蓝色表达可进入/处理中、橙色表达需要教师动作、绿色表达已确认，避免所有区域继续使用同一种卡片、阴影和渐变。

- `src/styles.css`：移除外部 Google 字体依赖，改用本地可用字体栈；重新定义颜色、背景、容器、按钮、指标卡、表格、上传区、OCR 校对区、复核区、报告区和设置区的视觉层级。
- 保留并强化现有参考图构图：工作台为任务/流程/快捷操作三列；作业管理为批次与归属主列加批量上传侧列；OCR 为上传、识别设置、识别说明和第 4 步文本校对；智能批改、评分标准、批改报告、设置统一使用页面主题色和上边界强调。
- 保留原有 API、SQLite、LangGraph、PaddleOCR、OpenCV/PIL、规则引擎、FastAPI 和真实数据流；没有写入参考截图里的姓名、分数、日期或演示批次。
- 增加 `prefers-reduced-motion` 覆盖，继续保留键盘焦点、移动端断点和无横向溢出约束。

验证：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过；内置浏览器 1440×900 逐页复核工作台、OCR 校对、智能批改、评分标准、批改报告和设置页，页面截图可见新的纸张/批注视觉系统，导航和真实状态入口正常；未触发真实上传、导出、模型连接或配置保存等外部副作用。
## 17. 2026-09-19 frontend-design 全新视觉重设（不参考图片）

用户明确取消参考图片，本轮完全按 `.agents/skills/frontend-design/SKILL.md` 重设页面视觉。

- `PageHeaderArt` 移除人物 SVG，改为批注纸张、评分标记、蓝橙规则线组成的 CSS 视觉元素。
- 侧栏改为墨蓝工作台目录，主区改为冷纸白工作面和浅网格背景。
- 采用本地字体栈，移除 Google Fonts 外部依赖，避免本地启动时受网络影响。
- 面板不再全部使用相同卡片样式，以蓝、橙、绿上边界表达任务、流程、状态层级。
- 工作台右侧常用操作改为单列，修复窄列下中文标题竖向换行的问题。
- 不改变 API、真实数据、LangGraph、PaddleOCR、OpenCV/PIL、Python 规则引擎、FastAPI、React、SQLite 等功能基础。

验证结果：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过；已使用内置浏览器复核工作台新视觉，未触发上传、导出或配置保存等有副作用操作。

## 18. 2026-09-19 班级入口移至顶部并拆分弹窗

用户要求将原作业管理页面中的班级操作改为顶部栏入口：

- 顶部新增“创建班级”和“管理班级”两个按钮。
- “创建班级”打开独立创建窗口，保留真实班级 API、批量粘贴学生名单、格式校验和创建后自动关闭提示。
- “管理班级”打开独立管理窗口，保留班级列表、服务端详情、启停、改名、添加学生、编辑学生和学生启停功能。
- 作业管理主体不再重复嵌入大块班级维护面板，批次列表和上传区域保持原有功能。
- 两个弹窗使用 `frontend-design` 的墨蓝工作台、纸面白、橙色动作按钮和冷色边界视觉；移动端缩减为图标入口并保留可访问名称。

验证：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过；内置浏览器已实际打开并关闭创建班级、管理班级两个窗口，管理窗口读取到当前服务端班级与学生数据；未提交新增班级或修改学生等有副作用操作。

## 19. 2026-09-19 主观题答案照片 OCR

用户要求在主观题答案编辑区域增加“上传照片并识别答案”能力。本轮采用题目级独立输入，不把照片伪装成新的学生作业页，避免破坏原有学生页序、归属和整页 OCR 结果。

- `backend/app/models.py` 新增 `AnswerPhoto`，保存答案照片原图、预处理图、原始 OCR 结果键、识别模式、识别文本、置信度、LangGraph 工作流快照和失败信息；`AnswerVersion` 增加可选 `answer_photo_id`，使最终保存的答案版本能够回溯到照片输入。
- `backend/migrations/versions/9d2e7f8a1b3c_add_answer_photo_ocr.py` 新增 SQLite 迁移；已将本地 `backend/data/pixiaozhi.db` 从 `f0d4e5a6b7c8` 升级到 `9d2e7f8a1b3c`。迁移采用 SQLite batch mode，兼容 SQLite 不支持直接增加外键约束的限制。
- `backend/app/api/ocr.py` 新增 `POST /api/v1/pages/{page_id}/answer-photo-ocr`：校验学生归属、题目必须为主观题、照片内容和格式，原图落盘后创建持久化任务，不在 HTTP 请求中伪造识别文字。
- `backend/app/ocr_worker.py` 扩展 `answer_photo_ocr` 任务：复用现有 LangGraph `ocr_pipeline`，继续经过 OpenCV/PIL 预处理和已配置的 PaddleOCR、TrOCR 或 TexTeller 运行时；识别结果只有在真实 Worker 完成后才回填。
- `src/api/client.ts` 增加照片 OCR 上传接口和任务结果类型；`src/App.tsx` 主观题编辑器增加照片选择、预览、识别任务轮询、成功回填、失败提示和 `answer_photo_id` 传递。识别完成后仍需教师保存校对草稿或确认本页，不自动生成正式成绩。
- 页面详情现在返回当前学生作业对象的已保存答案版本，刷新后可恢复照片识别文本、来源类型和照片追溯 ID；保存照片识别结果不会因整页 OCR block 为空而被清空。
- `src/styles.css` 增加题目级照片识别条的视觉层级、状态标签、预览和窄屏布局；客观题不显示该入口。

验证：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过；`backend\\.backend-venv\\Scripts\\python.exe -m compileall -q backend/app backend/migrations` 通过；`alembic check` 报告无新增迁移；LangGraph smoke tests 2 项通过；FastAPI OpenAPI 已包含 `/api/v1/pages/{page_id}/answer-photo-ocr`。本轮内置浏览器桥接调用再次返回 `nodeRepl.fetch request failed`，因此本轮未宣称真实照片上传和 Worker 推理点击验收；需启动后端 OCR Worker，并使用真实 JPG/PNG 做一次受控联调。

## 20. 2026-09-19 侧栏底部卡片越界修复

用户反馈窄侧栏状态下，“教师复核保护”和教师账号卡片向右突出到主内容区。问题是侧栏底部网格和卡片缺少可收缩的最小宽度约束，邮箱等长文本会撑大卡片。

- 为 `.sidebar-bottom`、`.privacy-card`、`.profile-card` 增加 `width: 100%`、`min-width: 0` 和 `max-width: 100%`，使卡片跟随固定侧栏宽度收缩。
- 为底部文字容器增加可收缩和省略号规则，避免长邮箱或标题再次撑开布局。
- 为侧栏增加横向溢出保护；未修改业务逻辑、API、OCR 或批改流程。

验证待执行：`npm run build`、`node prototype.audit.test.cjs`。

## 21. 2026-09-19 题目数量与题型一键识别

用户要求在 OCR 校对页增加“上传图片后一键识别题目数量、客观题和主观题”的能力。

- OCR 校对页的试卷上传卡新增“一键识别题型”按钮，仅接受 JPG、JPEG、PNG 图片；当前批次仍必须先选择。
- 点击后复用现有 question_paper 上传接口和真实 OCR 队列，等待 OCR Worker 完成后读取本次图片的 OCR 文本块。
- `src/utils/question-structure.ts` 使用题号切分、题型标题、选择项格式、解答/过程关键词进行规则判定，展示总题数、客观题数、主观题数、待确认数、逐题置信度和判定依据。
- 识别结果只作为教师确认建议，不会自动修改批次题目配置；PDF 仍走普通试卷 OCR，避免把多页文档误当作单张图片。

验证：`npm run build` 通过；`node prototype.audit.test.cjs` 全部通过。尚未进行真实图片上传和 OCR Worker 点击验收，需在后端与 OCR 专用运行环境启动后用受控 JPG/PNG 验证。

## 22. 2026-09-19 教师注册功能

用户要求在登录页增加教师注册功能。本轮基于现有会话认证实现，没有新增数据库表或绕过服务端认证。

- `backend/app/schemas.py` 新增 `RegisterRequest`：邮箱标准化、教师姓名非空、密码至少 8 位。
- `backend/app/api/auth.py` 新增 `POST /api/v1/auth/register`：检查邮箱唯一性，使用现有 PBKDF2 密码哈希写入 `users`，创建教师账号和服务端 HttpOnly 会话 Cookie，并记录注册审计事件。
- `src/api/client.ts` 新增注册请求方法；`src/App.tsx` 登录页增加“立即注册/返回登录”切换、教师姓名、确认密码和注册提交状态。
- 注册成功后直接进入工作台；已有邮箱返回明确错误，不会覆盖原账号。
- 当前为本地/内部系统注册，没有加入邮箱验证码、找回密码和公开注册限流；如果部署到公网，这些属于上线前必须补充的安全边界。

验证：`npm run build` 通过；`backend\\.backend-venv\\Scripts\\python.exe -m compileall -q backend/app backend/migrations` 通过；`node prototype.audit.test.cjs` 全部通过；OpenAPI 已包含 `POST /api/v1/auth/register`。

## 23. 2026-09-19 已保存作业图片删除

用户更正需求：作业管理的“已保存学生作业”区域需要删除已上传作业图片，而不是下载输出。

- 移除上一轮误加的下载接口、前端下载方法和“输出图片”按钮。
- `backend/app/api/files.py` 新增 `DELETE /api/v1/files/{file_id}`，复用当前账号与批次归属校验，只允许删除学生作业文件；会同步删除尚未进入后续流程的页面记录、上传任务和磁盘原文件。
- 如果文件已产生 OCR、学生归属、答案照片、异常或批改关联，接口返回明确阻止信息，不物理删除历史链路。
- `src/App.tsx` 在“已保存学生作业”每行增加“删除图片/删除文件”，删除前弹出确认，成功后从列表移除并刷新状态。
- `src/styles.css` 增加危险操作按钮样式；没有改变上传、OCR、归属和批改核心流程。

验证待执行：前端构建、原型审计、后端编译、删除路由 OpenAPI 检查和真实删除接口受控验收。

## 24. 2026-09-19 OCR 后文件撤销识别并彻底删除

用户说明误上传的作业图片已经加入 OCR，需要增加彻底删除能力。

- 普通“删除图片”仍只处理未进入 OCR 的文件；删除被 `FILE_DELETE_LOCKED` 拦截后，界面显示“撤销识别并彻底删除”。
- `backend/app/api/files.py` 新增 `POST /api/v1/files/{file_id}/purge`，要求回显原始文件名确认；仅允许当前教师所属的学生作业文件，且批次未进入批改/复核阶段。
- OCR 阶段清理会同步删除页面、OCR 运行、OCR 文本块、异常、答案照片及其缓存、上传任务，并从批量 OCR 任务快照中移除对应运行记录；原图、预处理图和 OCR 原始输出文件一并清理。
- 已绑定学生/页序或已保存答案的文件会明确拒绝彻底删除，避免删除后破坏成绩历史；正在运行的 OCR 任务也必须等待结束后再清理。
- 前端使用二次确认，成功后从已保存作业列表移除并刷新批次状态；没有新增数据库表或改变 LangGraph/OCR 推理流程。

验证：前端构建、原型审计、后端编译和 OpenAPI 检查通过；使用真实登录、临时上传图片调用 purge，接口返回 200、`purged=true`，并确认该文件不再出现在批次文件列表。尚未对“已有学生归属/已保存答案”文件执行破坏性测试，仅保留代码级阻断规则。

## 25. 2026-09-19 当前会话最终交接总览

本节写给一个完全没有本会话上下文的新会话，优先级高于旧的过程性记录；如果与旧记录的“待验证”状态冲突，以本节列出的最新验证证据为准。

### 25.1 我们正在做什么

项目是“批小智”AI 自动批改作业 WebApp，目标是交付一个可运行、可联调、可继续验收的教师工作台。核心链路必须是真实实现：教师登录/注册、班级与学生管理、批次与作业文件上传、OpenCV/PIL 图像预处理、PaddleOCR/TrOCR/TexTeller 识别、OCR 校对与题目结构化、LangGraph 多步骤批改编排、Python 规则评分、OpenAI 兼容模型生成主观题评价、教师复核、正式成绩和报告导出。

技术边界不能被“只做页面”替代：Python 3.12、FastAPI、React/Vite、SQLite、LangGraph、PaddleOCR、OpenCV/PIL、规则引擎和 OpenAI 兼容 LLM 适配器均属于项目核心技术约束。页面视觉目前采用已安装的 `frontend-design` skill 指导的纸张/批注工作台方向，但文档规定的功能和数据边界仍优先于视觉发挥。

### 25.2 已完成的主要工作

1. 已建立前后端可运行工程：前端入口为 `src/App.tsx`，接口封装为 `src/api/client.ts`，样式为 `src/styles.css`；后端位于 `backend/app/`，使用 SQLite 与 Alembic。
2. 已实现并联通主要页面与交互：工作台、作业/批次管理、OCR 校对、智能批改、教师复核、评分标准、批改报告、设置、登录/注册，以及班级创建/管理弹窗等。
3. 已保留真实 OCR/批改链路及其边界：不能把演示数据或前端状态当成生产识别结果；OCR、图像预处理、LangGraph、规则评分、LLM 评价和报告任务要通过后端流程执行。
4. 已完成误上传文件的分级删除：未进入下游流程的文件用普通 DELETE；已进入 OCR 但仍允许撤销的文件用 purge；已保存答案、页码/学生归属、正在运行 OCR、批改中、教师复核、已归档等危险状态会被后端拒绝。
5. purge 会清理数据库关联记录以及原图、处理图、OCR 原始结果、答案照片等存储文件；报告导出下载接口是另一项明确功能，不要误删。
6. 已增加答案照片 OCR 入口、题目识别/客观题和主观题分类相关能力，并保持“先识别、再校对、再进入批改”的流程约束。
7. 已完成 UI 视觉重构和多轮交互修复；学生作业展示不应触发自动下载，只有用户明确点击导出报告时才触发报告下载。

### 25.3 当前运行状态与卡点

- 当前检查时前端 `http://127.0.0.1:5173/` 返回 HTTP 200。
- 当前检查时后端 `http://127.0.0.1:8000/health/ready` 返回 `{"status":"ready","database":true,"storage":true}`。
- 当前后端 PID 为 35796，前端 PID 为 25220；新会话不能假设 PID 或服务仍然存在，必须重新检查端口。
- 项目根目录不是 Git 仓库；`git status` 会失败，不要据此声称代码“无未提交修改”或给出 Git diff 结论。
- 当前真正未完成的不是本次删除接口代码，而是完整生产级验收：需要真实题卷/学生作业样本跑通 PaddleOCR、预处理、OCR 校对、LangGraph 批改、教师复核和报告导出，并按项目协议记录结果。当前 smoke test 没有证明 OCR 准确率，也没有覆盖“已完成 OCR 产物全部清理”的破坏性场景。
- 对已经绑定学生、已经保存答案、正在批改或已进入教师复核的文件，当前实现会阻止强制删除。如果用户以后要求强制删除，必须先确认新的数据保留/成绩一致性策略，不能直接放开接口。

### 25.4 新会话第一步必须做什么

1. 先读本文件完整内容，尤其是第 0、1、2、17–25 节；不要跳过用户已经确认的核心技术和删除安全边界。
2. 检查目录、依赖、端口、数据库迁移状态和当前服务；不要重复启动占用同一端口的服务。
3. 先执行后端编译、前端构建和接口/OpenAPI 检查，再决定是否做浏览器逐页验收。
4. 如果继续改动，先定位现有实现和对应文档/页面，不要用临时前端 mock 替代后端能力；每次改动后至少构建、运行接口和检查关键交互。
5. 若要做删除相关测试，只能创建临时、未关联学生答案的测试文件；测试后确认数据库记录、文件列表和磁盘文件均已清理。不要拿真实学生作业或已保存答案做删除测试。

### 25.5 启动与验证命令

在项目根目录 `E:\实训项目\AI自动批改作业智能体` 使用项目虚拟环境。后端迁移和种子数据：

```powershell
& '.\backend\.backend-venv\Scripts\python.exe' -m alembic -c '.\backend\alembic.ini' upgrade head
& '.\backend\.backend-venv\Scripts\python.exe' '.\backend\scripts\seed_dev_teacher.py'
```

后端和前端分别在两个终端启动：

```powershell
& '.\backend\.backend-venv\Scripts\python.exe' -m uvicorn app.main:app --app-dir '.\backend' --host 127.0.0.1 --port 8000
npm run dev -- --host 127.0.0.1 --port 5173
```

需要单次执行后台任务时使用：

```powershell
& '.\backend\.backend-venv\Scripts\python.exe' '.\backend\app\ocr_worker.py' --once
& '.\backend\.backend-venv\Scripts\python.exe' '.\backend\app\grading_worker.py' --once
& '.\backend\.backend-venv\Scripts\python.exe' '.\backend\app\report_export_worker.py' --once
```

不要在 SQLite 上未经确认就并发启动多个长期 worker。先检查队列、锁和当前运行实例。

### 25.6 关键文件与接口定位

- 页面与交互：`src/App.tsx`
- API 客户端：`src/api/client.ts`
- 全局样式：`src/styles.css`
- 文件接口：`backend/app/api/files.py`
- 请求/响应模型：`backend/app/schemas.py`
- 后端入口：`backend/app/main.py`
- 迁移：`backend/migrations/`
- 后端启动说明：`backend/README.md`
- OCR 工具测试和优化边界：`OCR工具测试/HANDOFF.md`
- 正式任务书、PRD、UI/UX、开发技术文档：见本文件第 0 节列出的路径。

删除接口当前应存在：

- `DELETE /api/v1/files/{file_id}`：普通删除，适用于尚未产生下游 OCR/批改引用的文件。
- `POST /api/v1/files/{file_id}/purge`：撤销 OCR 并彻底删除，body 为 `{"confirm_name":"原始文件名"}`，只允许当前安全状态。

### 25.7 最新验证证据

- `npm run build` 已通过；Vite 成功转换 4572 个模块并生成 `dist/`。
- `node prototype.audit.test.cjs` 已通过：包含多页面、375/768/1024/1440 宽度检查及浏览器运行时无未捕获脚本错误。
- `python -m compileall -q backend\app backend\migrations` 已通过。
- OpenAPI 已确认普通删除和 purge 路由存在：`DELETE_ROUTE True`、`PURGE_ROUTE True`。
- 真实登录后的临时 PNG 上传/清理 smoke test 已通过：上传 202、purge 200、`purged:true`、文件列表不再出现；临时文件未保留。
- 另一次普通删除临时 PNG smoke test 已通过：上传 202、删除 200、`deleted:true`、文件列表不再出现。
- 以上证据是受控测试，不等于生产 OCR 准确率或所有批改场景已验收；尤其没有对真实学生关联文件做破坏性测试。

### 25.8 绝对不要再踩的坑

1. 不要把“页面能打开”说成“核心技术已实现”；必须区分 UI、接口、worker、真实模型和端到端证据。
2. 不要删除或绕过 `OCR工具测试/HANDOFF.md` 里已验证的 OCR 预处理、区域提取、模型识别和输出约定。
3. 不要把学生作业预览、OCR 文本展示或页面进入行为绑定成自动下载；只有用户明确点击导出报告时才触发报告下载。
4. 不要用前端假数据掩盖后端失败；接口未连接、任务失败、无数据和真实成功必须显示不同状态。
5. 不要把普通删除和 OCR 阶段 purge 混为一谈；不要为了“彻底删除”直接级联删除已绑定学生答案、评分或教师复核数据。
6. 不要只删数据库记录而留下原图、处理图、OCR raw、答案照片或缓存文件，也不要只删磁盘文件而留下数据库孤儿记录。
7. 不要在未确认端口和现有进程前重复启动服务；浏览器桥接曾出现 `nodeRepl.fetch request failed`，桥接不可用时只能把内置浏览器作为辅助验收工具，不能据此声称完成完整自动化验收。
8. 不要把演示批次、受控临时文件和生产数据混用；测试完成后必须检查批次、文件列表、数据库记录和磁盘。
9. 不要未经用户明确确认就修改正式任务书、PRD、UI/UX 或开发技术文档；代码实现若发现文档冲突，先记录冲突并向用户说明。
10. 不要使用破坏性 Git 命令、递归删除宽泛目录或覆盖用户原始资料；本项目根目录也不是 Git 仓库，先核实路径和状态。

## 26. 2026-09-20 OCR 页误上传试卷删除

用户要求在 OCR 校对页删除上传错误的题目文件；本轮已完成前后端联动，删除对象限定为 `question_paper`，不会把入口混到学生作业列表。

- `src/App.tsx` 的 `QuestionPaperWorkflow` 会加载当前批次的已上传试卷列表，提供“删除文件”入口；本地尚未上传的文件提供“清除当前待上传文件”。
- 删除前使用页面内确认弹窗，要求输入“删除”；确认后调用现有 `POST /api/v1/files/{file_id}/purge`，成功后刷新批次、页面和文件列表。
- `backend/app/api/files.py` 的 purge 逻辑现在同时支持 `student_work` 和 `question_paper`：会清理原图、页面、OCR 运行、文本块、任务记录和处理缓存。
- 已进入批改/复核/归档的批次、正在运行的 OCR、已绑定题干 OCR 文本块、已绑定学生或已保存答案的文件仍会被后端拒绝，避免破坏题干来源和成绩历史。
- 文件仍处于 `pending`/`validating` 保存阶段时只显示“保存处理中，完成后可删除”，避免在文件尚未落盘时执行清理。

验证：`npm run build`、`node prototype.audit.test.cjs`、`python -m compileall -q backend\app backend\migrations` 和 `backend\.backend-venv\Scripts\python.exe -m unittest discover -s backend/tests -v` 均通过；运行中的后端健康检查为 ready，OpenAPI purge 路由存在。本轮未对真实批次执行破坏性删除联调。

## 27. 2026-09-20 OCR 识别进度展示

用户反馈 OCR 校对页只能看到“等待 OCR”，无法看到识别进度。本轮补齐了任务状态读取和页面展示。

- `backend/app/api/ocr.py` 新增 `GET /api/v1/batches/{batch_id}/ocr-task?file_role=question_paper`，按当前教师权限读取指定文件类型最近一次 OCR 任务，支持页面刷新后恢复进度。
- `src/api/client.ts` 增加 `api.batches.ocrTask`；`src/App.tsx` 在提交试卷 OCR 后每 1.2 秒轮询任务状态，并展示排队中、处理中、已完成、部分失败、失败、当前阶段、已处理页数和百分比。
- OCR 任务完成或失败后会自动刷新试卷页面和文本块；手动“刷新试卷”仍保留作为兜底。
- 只读任务进度不会修改 OCR 数据；失败时保留后端错误信息，部分失败时明确提示处理失败页面。

验证：`npm run build`、`node prototype.audit.test.cjs`、`python -m compileall -q backend\app backend\migrations` 和 `backend\.backend-venv\Scripts\python.exe -m unittest discover -s backend/tests -v` 均通过；运行中的后端健康检查为 ready，OpenAPI 已包含 OCR 任务查询路由，未登录访问按预期返回 `AUTH_REQUIRED`。

## 28. 2026-09-20 最新会话交接：批改前置条件矛盾已定位并修复

### 28.1 任务目标

本项目继续开发和验收“批小智”AI 自动批改作业 WebApp。当前重点是保证完整链路真实可用：

    班级/批次 → 题干与参考答案上传或文本录入 → 题目 OCR/题目位置模板 → 学生作业多页 OCR → 文本人工校对 → AI/规则批改 → 教师复核 → 评分报告/导出

题干和参考答案应按批次上传一次，供同一批次多名学生共用；学生每页先按题目模板定位，再识别答案；同一学生的多页答案必须按 `page_sequence` 顺序合并。主观题评分标准由教师自定义，作为模型输入约束；模型输出必须是结构化批改结果和评语，分数不得超过题目满分。不同学科和不同题型均应走同一套可扩展识别/批改流程，不能只为数学或语文写死。

### 28.2 本次用户反馈与事实结论

用户在“学生批改”页看到以下互相矛盾的状态：

- 蓝色提示：`批改前置条件已满足：题干、评分标准、学生归属和答案覆盖均已确认。`
- 红色提示：`当前批次尚未满足发起批改的前置条件。`
- 队列任务数为 0。

核查的开发联调批次：

    batch_UL7J46QDatfiEv84K38QtQ

数据库和 API 事实：

- 该批次包含 1 道主观题，题干为 `作文《我学会了——》`，已绑定语文评分标准版本；题干和评分标准不是当前阻断项。
- 4 个学生作业对象的学生归属状态均为 `assigned`。
- 4 条题目答案记录的 `coverage_status` 均为 `reviewed`，答案覆盖和 OCR 校对不是当前阻断项。
- 页面 OCR、学生页序和分组确认也已存在；当前没有评分任务是因为批改运行尚未成功入队。
- 真正的入队接口 `POST /api/v1/batches/{batch_id}/grading-runs` 返回的唯一阻断项是：

      MODEL_KEY_UNAVAILABLE
      当前后端进程中没有可用的模型 API Key，暂不能入队主观题批改。

根因是模型 API Key 的设计为“网页提交到当前后端进程内存，不写入 SQLite”。后端进程重启后，运行时 Key 会被清空；本次为了加载代码重启后端时，数据库中的 `api_key_ref` 仍为空，所以需要重新在“模型配置”页面输入并保存 Key。这不是学生答案校对失效。

### 28.3 已完成的代码修复

1. 后端前置校验统一

文件：`backend/app/api/batches.py`

- `POST /api/v1/batches/{batch_id}/validate` 以前只检查批次题目、总分、题干、评分标准基础配置和是否存在学生文件。
- 真正创建批改运行时，`backend/app/api/grading.py::_validate_scope_and_inputs` 还会检查学生归属、页序、OCR 状态、答案覆盖、答案版本、评分标准有效性、主观题题干和模型 API Key。
- 现在批次 validate 接口会调用同一套严格入队校验，并返回一致的 `grading_errors` 与 `valid_for_grading`。页面不会再同时显示“已满足”和“不能发起批改”。
- 为避免 `batches.py` 与 `grading.py` 顶层循环导入，校验函数采用了 endpoint 内局部导入；不要随意改成顶层互相 import。

2. 前端错误信息增强

文件：`src/App.tsx`

- `startGrading` 捕获 `ApiClientError` 时现在会展示后端 `details` 中的具体原因，而不是只显示笼统的“批改运行提交失败”。
- 当错误码为 `MODEL_KEY_UNAVAILABLE` 时，会明确提示到“模型配置”填写并保存 API Key。
- 不要把这个错误改成自动跳过 Key 检查；主观题没有模型 Key 就不能真实批改。

3. 当前服务已重新加载修复

- 后端已使用项目虚拟环境重启，服务地址为 `http://127.0.0.1:8000`。
- 前端开发服务当前地址为 `http://127.0.0.1:4175/`，不是旧记录中常见的 5173；新会话必须先检查端口，不要重复启动。
- 本次重启没有改动数据库、上传文件、OCR 结果或学生答案。

### 28.4 本次验证证据

已完成以下验证：

- `& '.\backend\.backend-venv\Scripts\python.exe' -m compileall -q backend/app` 通过。
- `npm run build` 通过：TypeScript 检查和 Vite 构建均通过，仅保留已有的大 chunk warning。
- `GET http://127.0.0.1:8000/health/ready` 返回 `status=ready`、`database=true`、`storage=true`。
- 后端无运行时 Key 时，`POST /api/v1/batches/{batch_id}/validate` 返回 `valid_for_grading=false` 和 `MODEL_KEY_UNAVAILABLE`。
- 通过网页设置接口临时注入测试 Key 后，validate 返回 `valid_for_grading=true`；测试完成后已经清除测试 Key，没有留下假 Key。
- 无 Key 时直接调用真正的 `POST /api/v1/batches/{batch_id}/grading-runs`，返回 HTTP 422 和同一个 `MODEL_KEY_UNAVAILABLE`，没有创建伪造批改运行。

这些验证证明“前置条件显示与入队条件一致”已经修复；尚未使用用户真实 API Key 发起主观题真实模型调用，也不能据此声称外部模型连接、批改耗时或评分质量已验收。

### 28.5 新会话下一步计划

按以下顺序继续，不要从头重做已完成的 OCR/多页合并修复：

1. 启动前检查端口和进程：

       Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
         Where-Object { $_.LocalPort -in 8000,4175,5173 }

   若 8000 或 4175 已有项目服务，不要重复启动。

2. 用浏览器打开 `http://127.0.0.1:4175/`；如果浏览器桥接仍报 `nodeRepl.fetch request failed`，使用 API 验收并在结论中明确没有完成真实点击验收。

3. 进入“模型配置”，输入用户自己的模型服务商、模型名、Base URL 和 API Key，点击“保存配置”。Key 不要写入本文件、日志、截图、数据库或聊天。

4. 返回“学生批改”，刷新队列。确认前置提示为“已满足”，再点击“发起批改”。此时应看到真实批改运行状态、任务总数和进度；不能只看按钮点击成功。

5. 若仍失败，先查看：

       GET /api/v1/batches/{batch_id}/validate
       GET /api/v1/batches/{batch_id}/grading-runs/latest
       GET /api/v1/batches/{batch_id}/grading-tasks

   重点记录 `error.code`、`error.details`、运行状态、任务状态和后端日志，不要用刷新页面掩盖失败。

6. 使用真实 Key 时只做最小范围受控验收。当前批次是 4 名学生、1 道主观题；若模型调用产生费用或限流，先只选择一个学生/题目的 scope（前提是 UI/接口明确支持该范围），不要无确认地反复重跑整批。

7. 批改完成后检查：结构化评分字段、AI 评语、题目满分上限、教师复核、复核后报告和导出文件。模型输出失败时应进入失败/待重跑状态，不能生成假分数。

### 28.6 当前明确卡点

- 当前后端运行时没有用户真实 API Key；因此主观题批改还没有真实入队，也没有可以展示的真实评分队列。
- API Key 只保存在当前后端进程内存。后端再次重启后必须在“模型配置”重新保存；这是当前安全策略，不要把 Key 改为明文落库来绕过。
- 浏览器自动化桥接此前反复出现 `nodeRepl.fetch request failed`，尚未证明内置浏览器可以完成完整上传/OCR/批改/复核点击链路。
- 当前 build 和 API 预检已通过，但尚未用用户真实模型 Key 完成主观题端到端批改验收。

### 28.7 绝对不要再踩的坑

1. 不要把批次基础 validate 当成真正的批改入队校验；以后新增前置条件必须同时更新页面 validate 和 `grading-runs` 入队逻辑，最好复用同一函数。
2. 不要看到页面“校对已确认”就断言可以批改；主观题还需要有效的模型名、Base URL 和当前后端进程可解析的 API Key。
3. 不要把网页临时 Key 写入 `app_settings.api_key_ref` 的明文、SQLite、日志、HANDOFF 或前端静态代码；当前设计是运行时内存，重启后重新输入。
4. 不要用测试字符串当真实模型 Key 发起批改；本次测试字符串只用于验证 validate 分支，已经清除。
5. 不要在没有确认当前运行实例的情况下重启/重复启动后端；SQLite 和 Worker 可能因此造成队列、锁或状态混乱。
6. 不要把 HTTP 202/入队成功当作批改完成；必须轮询 run/task 状态并检查真实结果。
7. 不要把 `ai_preview`、模型建议分或任务入队状态当作教师确认后的正式成绩。
8. 不要因为浏览器桥接失败就声称完成了全流程 UI 验收；应区分 API/build 验证和人工浏览器验收。
9. 不要为验证 Key 缺失而修改真实学生作业、评分标准、页序或批改历史；本次数据均已保留。
