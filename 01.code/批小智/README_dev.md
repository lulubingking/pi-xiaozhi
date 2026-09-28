# 批小智源码开发说明

本目录是 React/Vite 前端与 FastAPI 后端源码。当前运行流程以 `HANDOFF.md` 和项目任务书为准；不要把开发库、真实上传文件或模型缓存提交到仓库。

## 首次准备

在本目录执行：

```powershell
python -m venv .\backend\.backend-venv
& .\backend\.backend-venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
npm ci
```

建议使用项目文档指定的 Python 3.12。此目录不含虚拟环境与 `node_modules`。

## 本机运行

```powershell
.\start-webapp.ps1
.\check-webapp.ps1
```

启动器默认构建前端、执行 Alembic 迁移并运行 API、OCR/批改 Worker、报告 Worker 和前端预览。首次本地演示账号需在当前 PowerShell 临时设置 `DEV_TEACHER_PASSWORD`；也可在页面注册账号。详情见 `STARTUP.md`。停止使用 `.\stop-webapp.ps1`。

## 工程验证

```powershell
npm run build
npm run test:ocr-structure
& .\backend\.backend-venv\Scripts\python.exe -m unittest discover -s backend\tests -p 'test_*.py' -v
```

构建和单元测试不等于真实 OCR、外部模型和完整教师流程的验收。正式交付说明见 `../../部署版code/批小智/README_prod.md`。
