---
project: AI自动批改作业智能体-批小智
source_type: guide-design
verified: true
---
# 06 UI/UX 设计实训

## 设计原则

界面以教师批改效率、证据可见和失败可恢复为优先。批次列表用于筛选和进入详情；详情页需要展示文件、识别、评分、复核和报告状态；复核界面需要同时看到学生答案、参考答案、评分依据和修改入口。

## 必要状态

异步流程至少覆盖 loading、empty、success、error、retry、disabled 和处理中不可删除。长任务不应阻塞页面主线程，返回列表后应保留筛选和上下文。

## 证据定位

查看 `批小智_UIUX设计文档_v1.0.1.md`、`design-system/pi-xiaozhi/MASTER.md`、`src/App.tsx` 和 `src/styles.css`。

## 验证与风险

构建成功只能说明资源可打包，不能证明窄屏布局、键盘操作、真实网络异常和教师理解成本已通过人工验收；这些需要浏览器和代表性流程检查。
