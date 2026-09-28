---
project: AI自动批改作业智能体-批小智
source_type: guide-security
verified: true
---
# 10 API 密钥申请实训

## 事实内容

模型相关配置入口可查看 `backend/app/core/model_config.py`、`backend/app/core/config.py` 和 `backend/app/scoring/llm_adapter.py`。项目交付包不保存有效密钥；申请和额度管理必须在供应商控制台完成。

## 安全操作

使用运行时环境变量或受控本地配置注入；测试时只输出“是否存在”和脱敏错误，不输出完整变量值。密钥不进入 React bundle、SQLite、日志、截图、Git 或 RAG 向量库。

## 验收边界

连接测试通过只说明当前配置可以调用供应商，不代表 OCR、评分 JSON 解析、教师复核和报告导出全链路通过。真实模型、额度、网络和样本均需要单独记录。

## 证据定位

参考 `实训附录-申请API密钥.md` 与 `提示词.md` 的安全拒答规则。
