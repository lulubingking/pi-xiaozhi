# 实训3：AI共创开发任务文档

## 一、实训目标

学习把 PRD 拆成前端、后端、数据库、异步任务、测试和交付任务，并为每项任务写清依赖、输入输出、验收条件和回滚边界。

## 二、实训内容

- 依据当前代码与文档建立任务树。
- 按“可独立验证的小任务”拆分批次、文件、OCR、评分、复核和报告。
- 对涉及删除、数据迁移、API Key 和模型切换的任务设置保护条件。

## 三、实训步骤

### 步骤1：盘点模块

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
rg --files '.\src' '.\backend\app' '.\backend\tests'
```

验证方法：按前端、API、工作流、持久化、测试五类归档文件。

成功标志：每个主要业务阶段至少有一个代码入口和一个验证入口。

### 步骤2：建立任务依赖

PowerShell 操作：

```powershell
rg -n "include_router|create_task|workflow|Session|select\(" '.\backend\app'
```

验证方法：确认路由、数据库模型、工作流和 worker 的调用方向。

成功标志：不把前端显示改动误拆成后端数据迁移，也不把异步任务当成同步接口。

### 步骤3：执行任务级检查

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体\backend'
python -m compileall app
pytest -q
```

验证方法：记录编译、单元测试和真实模型链路是否分别通过。

成功标志：任务完成记录包含命令、结果、未覆盖范围和后续风险。

## 四、验证清单

- 每个任务有明确文件范围，不修改未授权模块。
- 任务依赖包含数据库迁移和 worker 唯一性约束。
- 任务验收区分自动化检查、API smoke 和人工端到端验收。
- 删除类任务有保留清单和可恢复方案。

## 五、故障排除

- 任务过大：按一条用户路径或一个 API 能力拆分。
- 任务状态不清：补充输入、输出、成功标志和阻塞条件。
- 测试通过但功能异常：检查是否只是 mock 或静态构建，补充真实样本验收。

## 六、参考代码

```powershell
Get-Content -Raw '.\backend\app\main.py'
Get-Content -Raw '.\backend\app\grading_worker.py'
Get-Content -Raw '.\backend\app\ocr_worker.py'
```
