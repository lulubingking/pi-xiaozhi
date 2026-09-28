# 实训附录：Nginx 多端口 HTTPS 配置

## 一、实训目标

在前后端分端口或多环境部署时，理解不同监听端口、证书、CORS、健康检查和代理规则之间的关系。

## 二、实训内容

- 为静态站点和 API 分别配置监听端口。
- 明确外部端口与内部 FastAPI 端口的映射。
- 让前端只使用公开 HTTPS API 地址，不暴露内部端口和管理接口。

## 三、实训步骤

### 步骤1：盘点端口

PowerShell 操作：

```powershell
Get-NetTCPConnection -State Listen | Sort-Object LocalPort | Select-Object LocalAddress,LocalPort,OwningProcess
```

验证方法：记录 Nginx、FastAPI 和其他服务实际监听端口。

成功标志：不存在端口重复或把开发端口误当生产端口的情况。

### 步骤2：检查域名和证书映射

PowerShell 操作：

```powershell
Resolve-DnsName '<域名>'
Test-Path 'E:\部署\证书\fullchain.pem'
Test-Path 'E:\部署\证书\privkey.pem'
```

验证方法：确认域名解析、证书 SAN 和私钥路径属于同一环境。

成功标志：证书与端口配置一一对应，私钥不复制到项目目录。

### 步骤3：执行端口级 smoke

PowerShell 操作：

```powershell
Test-NetConnection '<域名>' -Port 443
Invoke-WebRequest 'https://<域名>/api/v1/health/live' -UseBasicParsing
```

验证方法：检查 TLS、代理响应和 CORS/Origin 行为。

成功标志：外部 HTTPS 入口可用，内部管理端口不对公网开放。

## 四、验证清单

- 443、API 外部端口和内部端口职责清晰。
- CORS 只允许实际前端来源。
- 证书续期、日志轮换和进程重启有操作记录。
- 上传、OCR、评分长任务的代理超时已验证。

## 五、故障排除

- TLS 失败：检查证书链、域名和系统时间。
- CORS 失败：检查前端实际 Origin 和后端允许来源。
- 端口可连但页面失败：分开验证静态资源、API、数据库和 worker。

## 六、参考代码

```powershell
nginx -t -c 'E:\部署\nginx\conf\nginx.conf'
Test-NetConnection '127.0.0.1' -Port 8000
```

说明：本项目当前交付包提供部署目录和配置原则，不包含真实证书、域名或生产服务器状态。
