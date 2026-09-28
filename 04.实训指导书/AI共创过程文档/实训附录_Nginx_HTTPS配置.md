# 实训附录：Nginx HTTPS 配置

## 一、实训目标

理解如何把前端静态资源与 FastAPI 后端放在同一 HTTPS 入口下，掌握反向代理、上传大小、超时和安全响应头的基本配置。

## 二、实训内容

- 静态资源由 Nginx 提供。
- `/api/` 请求代理到 FastAPI。
- 配置证书、HTTP 到 HTTPS 跳转、上传大小和长任务超时。

## 三、实训步骤

### 步骤1：确认部署产物

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体\AI自动批改作业智能体-批小智\部署版code\批小智'
Get-ChildItem -Recurse -File | Select-Object FullName
```

验证方法：确认存在 `frontend-dist` 和 `backend`，不存在 `node_modules`、数据库和上传数据。

成功标志：Nginx 只接收静态构建和 API 代理目标，不直接暴露源码目录。

### 步骤2：校验配置语法

PowerShell 操作：

```powershell
nginx -t -c 'E:\部署\nginx\conf\nginx.conf'
```

验证方法：在实际 Nginx 安装目录执行，以真实配置文件为准。

成功标志：输出 syntax is ok 和 test is successful。

### 步骤3：验证代理和 HTTPS

PowerShell 操作：

```powershell
Invoke-WebRequest 'https://<域名>/api/v1/health/live' -UseBasicParsing
Invoke-WebRequest 'https://<域名>/' -UseBasicParsing
```

验证方法：检查证书、状态码、前端路由回退和 API 响应。

成功标志：浏览器无混合内容，API 不被错误地代理到静态目录。

## 四、验证清单

- 证书私钥权限最小化且不进入交付包。
- `/api/`、上传大小和长任务超时符合实际需求。
- 静态路由回退到 `index.html`，API 错误保持 JSON。
- 访问日志脱敏，不记录 API Key 或学生作业内容。

## 五、故障排除

- 502：检查 FastAPI 进程、监听地址和代理端口。
- 413：检查 `client_max_body_size` 与后端上传限制。
- 504：检查 OCR/评分长任务设计，不用无限增大 Nginx 超时掩盖 worker 故障。

## 六、参考代码

```nginx
location / {
    root E:/deploy/批小智/frontend-dist;
    try_files $uri $uri/ /index.html;
}

location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_read_timeout 300s;
}
```

示例路径和域名必须替换为实际部署值。
