# 实训1：AI共创产品需求规格说明书

## 一、实训目标

理解“批小智”的真实业务边界，完成教师从登录、班级管理、批次创建、作业上传、识别、批改、复核到报告导出的端到端需求拆解。明确题目卷与学生作业的角色差异、教师确认节点和失败可恢复路径。

## 二、实训内容

- 阅读 `HANDOFF.md`、`批小智_PRD_v1.0.0.md` 和验收报告。
- 把需求拆成角色、实体、状态、页面、接口和验收条件。
- 标记事实、推理和风险，不把“已有接口”直接等同于“全链路已验收”。

## 三、实训步骤

### 步骤1：定位权威文档

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
Get-Content -Raw '.\HANDOFF.md'
Get-Content -Raw '.\批小智_PRD_v1.0.0.md'
```

验证方法：确认输出包含产品范围、角色、流程和非目标。

成功标志：能说明教师、班级、学生、批次、文件、题目、评分规则、复核项和报告之间的关系。

### 步骤2：建立需求清单

PowerShell 操作：

```powershell
rg -n "登录|班级|学生|批次|上传|OCR|批改|复核|报告|导出|不做|边界" '.\HANDOFF.md' '.\批小智_PRD_v1.0.0.md'
```

验证方法：逐项登记需求来源、实现位置和验收方式。

成功标志：每条核心需求均有文档依据或明确标注为待验证项。

### 步骤3：形成可验收需求

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
Get-ChildItem '.\验收报告' -Recurse -File | Select-Object FullName
```

验证方法：把每个流程节点改写成“前置条件—操作—预期结果—失败处理”。

成功标志：需求可直接映射到页面、API 或人工确认，不依赖未提供的业务规则。

## 四、验证清单

- 是否保留 `question_paper` 与 `student_work` 的区分。
- 是否包含 OCR 低置信度、题目分组错误和评分待复核处理。
- 是否把“接口可调用”与“真实 OCR 到报告全链路成功”分开。
- 是否定义空数据、重复提交、未登录和越权访问的预期。

## 五、故障排除

- 需求冲突：以 `HANDOFF.md` 当前边界为准，并在文档中记录冲突。
- 找不到实现：用 `rg` 搜索路由、组件和状态名，不凭页面名称猜测。
- 验收无法完成：记录缺少的 API Key、模型、样本或人工确认条件。

## 六、参考代码

```powershell
rg -n "question_paper|student_work|review|report|ocr" '.\src' '.\backend\app'
```

参考定位：`src/App.tsx`、`src/api/client.ts`、`backend/app/api`、`backend/app/workflows`。
