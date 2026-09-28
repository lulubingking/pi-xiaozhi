# 实训5：AI共创后端开发

## 一、实训目标

掌握 FastAPI、SQLAlchemy、SQLite、Alembic 和异步 worker 在本地单机批改系统中的组织方式，理解接口幂等、状态流转、权限边界和失败恢复。

## 二、实训内容

- 阅读 `backend/app/main.py`、`backend/app/api`、`backend/app/models.py`、`backend/app/workflows`。
- 理解认证、批次、文件、OCR、评分、复核和报告接口。
- 在不泄露 API Key 的前提下接入模型配置。

## 三、实训步骤

### 步骤1：准备后端环境

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体\backend'
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

验证方法：运行 `python --version` 和 `python -m pip show fastapi sqlalchemy`。

成功标志：依赖安装到项目虚拟环境，不污染交付包。

### 步骤2：检查路由和模型

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
rg -n "include_router|class .*Base|ForeignKey|UniqueConstraint|Index" '.\backend\app'
```

验证方法：确认路由前缀、数据库实体关联、唯一约束和索引。

成功标志：能说明同一批次、文件、题目和评分结果的关系及其生命周期。

### 步骤3：执行静态与测试检查

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体\backend'
python -m compileall app
pytest -q
```

验证方法：保留退出码、测试数量和失败堆栈。

成功标志：编译和现有单元测试通过；未覆盖的 OCR/模型真实链路单独列出。

## 四、验证清单

- 未登录或跨用户访问被拒绝。
- 上传、OCR、评分和导出任务能查询状态并安全重试。
- 数据库迁移可重复执行，单机 worker 不重复启动。
- API Key 只从运行时配置读取，不进入前端、SQLite、日志或交付文档。

## 五、故障排除

- 数据库锁：确认只有一个 worker 写 SQLite，缩短事务并检查连接配置。
- 任务卡住：查询任务状态、worker 日志和输入文件，不直接删除处理中数据。
- 模型调用失败：先检查配置和网络，再区分供应商错误、解析错误和业务校验错误。

## 六、参考代码

```powershell
Get-Content -Raw '.\backend\app\main.py'
Get-Content -Raw '.\backend\app\core\config.py'
Get-Content -Raw '.\backend\app\workflows\ocr_graph.py'
Get-Content -Raw '.\backend\app\workflows\grading_graph.py'
```
