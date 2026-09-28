# 批小智 AI 自动批改作业 WebApp｜交付版

这是批小智项目的正式交付目录。结构参照 `E:\AI共创面诊分析系统` 的案例资源组织方式，内容按批小智的任务书、PRD、UI/UX、技术文档和实际代码编写。面诊项目的数据库、图片、模型接口与部署命令未复制进本项目。

| 目录 | 内容 |
| --- | --- |
| `01.code/批小智` | 当前前后端源码、数据库迁移、测试及开发启动脚本 |
| `02.data` | 数据类型、隐私和样本准备说明；不含真实学生作业 |
| `03.ppt` | 项目介绍与 AI 共创开发汇报 PPT |
| `04.实训指导书` | 六项实训、三项附录、PRD/UIUX/技术文档和已有验收资料 |
| `05.RAG语料` | 18 项主题语料、助手提示词与索引说明 |
| `部署版code/批小智` | 已构建前端、FastAPI 后端、迁移和 Windows 本地启动入口 |

## 从 GitHub 克隆并运行

当前仓库是私有仓库，需要仓库所有者邀请访问者；未获授权的人无法浏览或克隆。Windows 目标机器需安装 Git、Python 3.12 或 3.13，并能联网安装 Python 依赖。在 PowerShell 中运行：

```powershell
git clone https://github.com/lulubingking/pi-xiaozhi.git
cd pi-xiaozhi/部署版code/批小智
./setup.ps1
./start.ps1
```

打开 `http://127.0.0.1:8000/`，首次使用注册教师账号。另开 PowerShell，在同一目录运行 `./check.ps1` 检查网页、数据库和 API。上述命令只启动单机 WebApp；真实 OCR、PDF 和主观题批改还需按[部署说明](部署版code/批小智/README_prod.md)配置。若 PowerShell 拒绝执行脚本，可在当前终端使用 `Set-ExecutionPolicy -Scope Process Bypass` 后重试。

继续开发请使用 [源码说明](01.code/批小智/README_dev.md)。案例资源的阅读顺序和参考来源见 [交付核对](交付核对.md)。

## 重要边界

- 默认 SQLite 适合单机联调；不要同时运行多个 API/Worker 实例。
- OCR 专用环境及模型、Poppler、模型服务 API Key 需在目标机器另行准备。API Key 只在后端当前进程内存中使用。
- 教师复核前的 AI 建议分不是正式成绩。
- 真实数据库、作业上传、模型缓存与密钥不包含在仓库或交付目录中。
- 本包可用于本地部署与继续验收；不代表真实 OCR 准确率、完整主观题批改或公网生产安全已通过验收。
