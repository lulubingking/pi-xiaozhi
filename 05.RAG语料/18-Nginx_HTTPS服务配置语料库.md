---
project: AI自动批改作业智能体-批小智
source_type: deployment-security
verified: true
---
# 18 Nginx 与 HTTPS 服务配置

## 推荐拓扑

Nginx 提供 `frontend-dist`，将 `/api/` 反向代理到 FastAPI，使用 HTTPS 证书保护外部连接。静态路由回退到 `index.html`，API 错误保持 JSON；内部 FastAPI 端口不直接暴露公网。

## 关键配置

根据真实文件大小设置 `client_max_body_size`；根据 OCR、评分和导出任务设置代理读取超时；CORS 只允许实际前端来源；访问日志脱敏，不记录 API Key、学生作业原图或完整答案。

## 验证命令

```powershell
nginx -t -c 'E:\部署\nginx\conf\nginx.conf'
Test-NetConnection '<域名>' -Port 443
Invoke-WebRequest 'https://<域名>/api/v1/health/live' -UseBasicParsing
```

命令中的域名、证书和配置路径是占位符，必须替换为真实部署环境。

## 证据定位

参考 `实训附录_Nginx_HTTPS配置.md`、`实训附录_Nginx多端口HTTPS配置.md` 和 `部署版code/批小智/README_prod.md`。

## 风险

证书有效不代表 API、worker、数据库和长任务可用；应分别验证 TLS、静态资源、健康检查、认证、上传、任务状态和报告下载。
