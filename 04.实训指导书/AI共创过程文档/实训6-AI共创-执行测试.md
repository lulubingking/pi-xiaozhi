# 实训6：AI共创执行测试

## 一、实训目标

建立从静态检查、单元测试、API smoke 到真实 OCR—批改—复核—报告验收的分层测试方法，避免用构建通过代替产品验收。

## 二、实训内容

- 执行前端构建、后端编译和 pytest。
- 检查 API 路由、错误响应、数据库迁移和任务状态。
- 在有真实模型、API Key 和样本时执行人工端到端验收。

## 三、实训步骤

### 步骤1：运行前端检查

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
npm run build
```

验证方法：检查退出码、`dist` 产物和构建日志。

成功标志：前端生产构建成功。

### 步骤2：运行后端检查

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体\backend'
python -m compileall app
pytest -q
```

验证方法：区分编译、自动化测试与服务启动结果。

成功标志：现有自动化检查通过，失败项有可复现命令。

### 步骤3：执行接口 smoke

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体\backend'
python -m uvicorn app.main:app --reload
```

验证方法：另开 PowerShell 请求 `/api/v1/health/live` 或查看项目实际健康路由，并检查未登录访问的 401/403 行为。

成功标志：服务可启动、健康检查可用、认证边界符合预期。

### 步骤4：执行真实链路验收

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
Get-ChildItem '.\验收报告' -Recurse -File | Select-Object FullName
```

验证方法：使用经授权的题目卷、学生作业、模型配置和人工评分基准，逐步记录 OCR、分组、评分、复核、报告和导出结果。

成功标志：每一步都有输入、输出、截图或响应证据；没有条件时明确标记“未验收”。

## 四、验证清单

- 静态检查、单元测试、API smoke、真实端到端分别记录。
- 真实样本覆盖印刷体、手写体、低清晰度和错题分组场景。
- 评分结果有教师复核，不把自动评分结果直接当成真实准确率。
- 导出文件可打开且内容与复核结果一致。

## 五、故障排除

- 服务起不来：检查端口占用、环境变量、数据库路径和依赖。
- OCR 失败：保留原始页面和错误状态，先检查模型配置与样本格式。
- 报告不一致：检查评分任务版本、复核修改和报告生成时间。

## 六、参考代码

```powershell
rg -n "pytest|health|reports|exports|review" '.\backend\tests' '.\backend\app' '.\验收报告'
```
