# 批小智 WebApp 启动说明

所有命令都从本文件所在的项目根目录执行：

`E:\实训项目\AI自动批改作业智能体`

## 推荐启动

PowerShell：

```powershell
.\start-webapp.ps1
```

也可以双击或在 cmd 中运行：

```bat
start-webapp.cmd
```

启动器会按顺序完成：

1. 检查项目 Python 虚拟环境、Node.js 和前端依赖。
2. 默认执行 `npm run build`，保证预览内容与当前源码一致。
3. 执行 Alembic 迁移并初始化开发教师账号；密码不写入代码、日志或文档。
4. 启动 FastAPI 后端。后端会在同一进程内启动 OCR Worker 和批改 Worker。
5. 启动独立的报告导出 Worker。
6. 启动前端生产预览，并检查首页、后端 ready 和 `/api` 代理。

只有全部检查通过才会显示“批小智已启动”。

访问地址：<http://127.0.0.1:5173/>

如果默认后端端口 `8000` 被其他项目占用，可以让批小智使用备用端口，前端代理会自动跟随：

```powershell
.\start-webapp.ps1 -BackendPort 8001
```

对应检查和安全审计：

```powershell
.\check-webapp.ps1
.\security-audit.ps1 -CheckRuntime -BackendPort 8001
```

已有本地开发教师账号：

```text
邮箱：teacher.demo@pixiaozhi.local
密码：使用你自己设置的本地密码；项目不会显示或保存密码。
```

全新数据库首次启动时，先在当前 PowerShell 会话中临时设置一次密码：

```powershell
$env:DEV_TEACHER_PASSWORD = Read-Host '设置本地开发教师密码'
```

使用后清理当前会话变量：

```powershell
Remove-Item Env:DEV_TEACHER_PASSWORD -ErrorAction SilentlyContinue
```

## 查看状态和停止

```powershell
.\check-webapp.ps1
.\stop-webapp.ps1
```

停止不会删除数据库、上传文件、OCR 结果或日志；日志保留在 `.runtime\logs`。

## 可选参数

跳过前端构建（确认 `dist` 已经是最新时使用）：

```powershell
.\start-webapp.ps1 -SkipBuild
```

打开前端浏览器：

```powershell
.\start-webapp.ps1 -OpenBrowser
```

如果需要诊断 Vite 开发模式，可以显式运行：

```powershell
.\start-webapp.ps1 -FrontendMode dev
```

开发模式必须通过真实首页 HTTP 检查；若只显示 Vite `ready` 但首页超时，启动器会判定失败并清理已启动进程。日常联调建议使用默认的 `preview` 模式。

## 首次环境未创建时

仅在 `backend\.backend-venv` 不存在时执行：

```powershell
python -m venv .\backend\.backend-venv
& .\backend\.backend-venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
npm install
```

然后重新执行 `.\start-webapp.ps1`。

## 故障定位

启动失败时优先查看：

```text
.runtime\logs\backend.stderr.log
.runtime\logs\report-export-worker.stderr.log
.runtime\logs\frontend-preview.stderr.log
```

如果提示端口被未知 PID 占用，启动器不会自动终止该进程。先检查：

```powershell
netstat -ano | findstr ":8000 :5173"
```

不要在 SQLite 上重复启动同类型长期 Worker。
