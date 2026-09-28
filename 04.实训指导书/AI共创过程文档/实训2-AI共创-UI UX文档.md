# 实训2：AI共创 UI/UX 文档

## 一、实训目标

把批改工作流转化为可操作、可恢复、可审计的教师界面，重点处理批次状态、识别结果、评分依据和人工复核，而不是只追求页面视觉效果。

## 二、实训内容

- 阅读 `批小智_UIUX设计文档_v1.0.1.md` 与 `design-system/pi-xiaozhi/MASTER.md`。
- 检查布局、色彩、字体、表格、抽屉、确认弹窗和响应式规则。
- 设计 loading、empty、error、retry 和 disabled 状态。

## 三、实训步骤

### 步骤1：读取设计依据

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
Get-Content -Raw '.\批小智_UIUX设计文档_v1.0.1.md'
Get-Content -Raw '.\design-system\pi-xiaozhi\MASTER.md'
```

验证方法：记录页面层级、设计 token 和关键交互。

成功标志：能解释“列表—详情—复核—返回列表”的状态保持方式。

### 步骤2：检查前端入口和页面状态

PowerShell 操作：

```powershell
rg -n "loading|error|empty|review|batch|upload|drawer|dialog" '.\src'
```

验证方法：将每个异步请求对应到可见反馈和可重试动作。

成功标志：失败时不会静默停留，成功后不会丢失批次筛选或复核上下文。

### 步骤3：执行前端构建

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
npm run build
```

验证方法：检查命令退出码与 `dist/index.html`。

成功标志：构建退出码为 0，并产生可部署的静态资源。

## 四、验证清单

- 页面在桌面与窄屏下没有关键按钮被遮挡。
- 上传、OCR、评分和导出都有处理中、成功和失败反馈。
- 复核项展示学生答案、参考答案、评分依据和编辑入口。
- 颜色和图标不承担唯一语义，键盘焦点可见。

## 五、故障排除

- 构建失败：先检查 Node 版本、`package-lock.json` 和 TypeScript 错误。
- 样式漂移：核对 `src/styles.css` 与设计 token，不在单个组件里重复定义全局色值。
- 交互不连贯：沿 `App.tsx` 的视图状态和 `src/api/client.ts` 的请求状态追踪。

## 六、参考代码

```powershell
Get-Content -Raw '.\src\App.tsx'
Get-Content -Raw '.\src\styles.css'
Get-Content -Raw '.\src\api\client.ts'
```
