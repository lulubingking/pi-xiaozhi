# 实训4：AI共创前端开发

## 一、实训目标

掌握 React/TypeScript/Vite 前端在批改工作流中的页面状态管理、API 调用、表单校验和错误恢复。

## 二、实训内容

- 阅读 `src/App.tsx`、`src/api/client.ts`、`src/utils` 和 `src/styles.css`。
- 实现或维护登录、班级、批次、上传、复核、报告和设置界面。
- 保持接口错误、异步任务和人工确认状态可见。

## 三、实训步骤

### 步骤1：安装依赖并启动开发服务

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
npm install
npm run dev
```

验证方法：打开 Vite 输出的本地地址，观察登录页和浏览器控制台。

成功标志：开发服务启动，页面无阻断性运行时异常。

### 步骤2：检查 API 封装

PowerShell 操作：

```powershell
rg -n "fetch\(|request|credentials|Authorization|/api/v1" '.\src\api\client.ts' '.\src'
```

验证方法：确认请求统一处理 base URL、JSON、错误和 session。

成功标志：页面不直接复制多套请求错误处理逻辑。

### 步骤3：构建生产资源

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
npm run build
Get-ChildItem '.\dist' -Recurse -File | Measure-Object Length -Sum
```

验证方法：检查构建退出码和静态文件产物。

成功标志：`dist` 可交给静态服务器部署，且不包含源码依赖目录。

## 四、验证清单

- 登录失效时回到登录页并保留可理解错误。
- 批次列表、上传、复核和报告页面处理空数据及加载中状态。
- 长任务使用轮询或刷新机制，不阻塞主线程。
- 生产构建不把 API Key、数据库文件或本地上传路径打入 bundle。

## 五、故障排除

- CORS 或请求失败：检查后端地址、浏览器 Network 和后端日志。
- 类型错误：优先修正接口类型和空值分支，不使用无依据的 `any` 扩散。
- 页面卡顿：检查重复请求、巨大表格渲染和不稳定的 effect 依赖。

## 六、参考代码

```powershell
Get-Content -Raw '.\src\api\client.ts'
Get-Content -Raw '.\src\utils\ocr-auto-assignment.ts'
Get-Content -Raw '.\src\utils\rubric-input.ts'
```
