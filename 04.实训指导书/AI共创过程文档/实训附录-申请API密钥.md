# 实训附录：申请并配置 API 密钥

## 一、实训目标

理解模型供应商密钥的申请、最小权限配置、运行时注入和轮换方法，确保密钥不进入前端、数据库、日志、Git 或交付包。

## 二、实训内容

- 选择与后端适配器兼容的模型供应商。
- 在供应商控制台申请密钥并设置额度、权限和来源限制。
- 通过本地环境变量或受控配置注入，不在代码中硬编码。

## 三、实训步骤

### 步骤1：确认适配器配置

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体'
Get-Content -Raw '.\backend\app\core\model_config.py'
Get-Content -Raw '.\backend\app\scoring\llm_adapter.py'
```

验证方法：确认所需 provider、base URL、模型名和密钥变量名。

成功标志：不凭空填写供应商参数，变量名与代码一致。

### 步骤2：在供应商控制台申请密钥

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体\backend'
Get-ChildItem Env: | Where-Object Name -Match 'API|KEY|MODEL|BASE'
```

验证方法：在供应商网页完成账号、额度和权限配置；本地只检查变量名是否存在，不输出密钥值。

成功标志：密钥已配置为运行时变量，终端输出不包含密钥内容。

### 步骤3：执行安全连接测试

PowerShell 操作：

```powershell
Set-Location 'E:\实训项目\AI自动批改作业智能体\backend'
python -m compileall app
```

验证方法：在应用设置页或受控测试入口执行模型连接测试，检查响应状态与脱敏日志。

成功标志：连接测试只验证可用性，不把密钥写入 SQLite、浏览器存储、日志或截图。

## 四、验证清单

- 密钥权限最小化且可撤销。
- 密钥不出现在源码、`.env.example` 的真实值、前端 bundle、数据库和报告中。
- 额度、超时、重试和供应商错误有边界。
- 交付包不包含任何有效密钥。

## 五、故障排除

- 401/403：检查变量名、权限、额度和供应商账户状态，不打印完整密钥。
- 超时：检查 base URL、网络、模型负载和超时配置。
- 解析失败：查看脱敏后的响应结构，确认适配器 schema，不直接把原始响应写入用户数据。

## 六、参考代码

```powershell
rg -n "API_KEY|api_key|provider|base_url|model" '.\backend\app' '.\README.md'
```

说明：本交付包不包含有效 API Key，也不声称已完成真实供应商链路验收。
