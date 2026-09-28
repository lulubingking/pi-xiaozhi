---
project: AI自动批改作业智能体-批小智
source_type: api-code
verified: true
---
# 14 API 接口使用

## 认证

`POST /api/v1/register` 注册，`POST /api/v1/login` 登录，`GET /api/v1/session` 获取当前会话，`POST /api/v1/logout` 登出。具体请求体和响应 schema 以 `backend/app/api/auth.py` 与 `backend/app/schemas.py` 为准。

## 班级与批次

班级路由支持列表、创建、详情、更新、学生列表和学生更新；批次路由支持列表、汇总、创建、详情、更新、删除、校验、题目和题目布局。源文件分别为 `classes.py` 和 `batches.py`。

## OCR、评分和报告

OCR 路由覆盖批次任务、页面、OCR run、重试、答案照片 OCR、分组、修正、确认和历史；评分路由覆盖评分 run、任务、重试、复核队列、复核项、历史和评分结果；报告路由覆盖学生报告、统计、导出任务和下载。源文件分别为 `ocr.py`、`grading.py` 和 `reports.py`。

## 调用原则

先登录并携带会话，再按资源归属调用；202 响应要继续查询状态；错误响应要保留状态码和脱敏 detail。不要根据 URL 猜请求体，优先查看源码 schema 或 OpenAPI。

## 证据定位

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
rg -n "@router\.(get|post|put|patch|delete)" '.\backend\app\api'
```

## 风险

路由存在不等于权限、模型、文件和报告全链路已验收；版本变更后应重新生成接口清单。
