# 批小智 WebApp 部署说明

本目录包含已构建的前端、FastAPI 后端、Alembic 迁移、OCR/批改 Worker 代码与报告导出 Worker。前端与 API 由同一 FastAPI 地址提供；本地使用无需 Node.js 或额外前端代理。

## Windows 本地启动

1. 安装 Python 3.12 或 3.13，确保 `python` 在 PATH 中。首次安装 Python 依赖需要网络。
2. 在本目录打开 PowerShell：

   ```powershell
   .\setup.ps1
   .\start.ps1
   ```

3. 打开 `http://127.0.0.1:8000/`，首次使用在页面注册教师账号。
4. 新开终端执行 `.\check.ps1`，应看到网页、数据库存储和 API 路由三个 `PASS`。
5. 在启动终端按 `Ctrl+C` 停止。数据库与上传目录保留在 `backend/data` 下，须定期备份。

端口冲突时：`.\start.ps1 -Port 8017`，对应检查 `.\check.ps1 -Port 8017`。启动器只监听 `127.0.0.1`，不直接对公网开放。`启动批小智.cmd` 可在安装依赖后双击运行。

## OCR 与模型配置

交付目录不包含约 25 GB 的 OCR 专用 Python 环境与模型。使用真实 OCR 时，先在启动终端设置：

```powershell
$env:OCR_TOOL_ROOT = 'OCR工具测试目录的绝对路径'
```

该目录需提供 PaddleOCR、TrOCR 与 TexTeller 的运行环境和模型；详细默认路径见 `backend/app/ocr_worker.py`。PDF OCR 还需 Poppler 的 `pdftoppm` 在 PATH 中，或设置 `PDF_POPPLER_PATH`。启动脚本在原项目机器上会自动探测相邻的 `OCR工具测试`，移机时必须显式配置并用受控样本验证。

主观题需在网页模型设置中配置服务商、模型、Base URL 和 API Key。Key 只保留在当前后端进程内存，重启后需重新输入；不要写入仓库、SQLite、日志或前端构建。无有效 Key 时主观题不能完成真实模型评分。

## 上线边界

`/health/ready` 只检查数据库与存储目录，不检查 OCR 模型、外部模型或报告版式。公网部署前需完成 HTTPS、访问控制和注册防滥用、密钥管理、数据备份、多实例方案及真实样本全链路验收。SQLite 单机模式不要启动多个 API/Worker 实例。本交付包不宣称已完成生产级性能、准确率或安全验收。
