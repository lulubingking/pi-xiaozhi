---
name: training-guide-creator
description: 生成符合统一模板格式的实训指导书markdown文档。用于创建软件开发实训教材，支持前后端分离项目的技术实训文档生成。当用户需要创建实训指导书、技术实训文档、编程实验手册时触发此skill。
---

# 实训指导书生成器

## 使用场景

本skill用于生成结构统一、内容规范的实训指导书markdown文档，适用于：
- 高校计算机课程实训教材编写
- 企业技术培训手册制作
- 编程实验指导文档生成
- 前后端分离项目实训教程

## 生成原则

### 1. 单一职责原则
- 每个实训指导书只聚焦一个具体的技术点或任务
- 避免一个文档涵盖过多内容，保持粒度适中
- 例如：模型定义、数据预处理、训练流程应分别独立成篇

### 2. 前后端分离原则
- 后端实训顺序：数据库→模型层→服务层→API层→部署
- 前端实训顺序：项目初始化→基础组件→高级功能→测试→部署
- 界限清晰，便于理解前后端分离架构

### 3. 模板统一原则
统一使用六段式结构：
- **一、实训目标**：3-5条具体可衡量的目标
- **二、实训内容**：背景知识+项目结构说明
- **三、实训步骤**：详细代码+功能说明+验证步骤
- **四、验证清单**：完成后的验证项表格
- **五、故障排除**：常见问题及解决方案
- **六、参考代码**：完整目录结构+常用命令

### 4. 代码完整性原则
- 每个实训提供可直接运行的代码
- 包含必要的注释（参数、返回值、功能说明）
- 提供完整的文件路径和目录结构

### 5. 渐进式难度原则
- 从基础配置到复杂功能逐步递进
- 前置实训为后续实训打基础
- 避免跳跃性过大的技术跨度

### 6. 实用性原则
- 代码来源于真实项目需求
- 包含错误处理和边界情况
- 提供测试验证方法

### 7. 可验证性原则（重要）
- **每个步骤必须有明确的验证方法**
- 提供具体的验证命令（PowerShell命令）
- 说明预期的成功标志
- 使用户能够确认每一步的正确性

### 8. 无冗余原则
- 不添加emoji符号
- 不添加与实训无关的内容
- 保持文档简洁专业

## 文档结构模板

```markdown
# 实训N 实训标题

## 一、实训目标

1. 掌握xxx技术/方法
2. 学会使用xxx工具/框架
3. 理解xxx概念/原理
4. 能够独立完成xxx功能

## 二、实训内容

### 1. 背景知识

简要介绍相关技术背景，包括：
- 核心概念解释
- 技术选型理由
- 关键组件说明

### 2. 项目结构

展示相关目录结构：

```
project/
├── directory/
│   ├── file.py
│   └── subdir/
└── config.json
```

## 三、实训步骤

### 步骤1：xxx

**操作命令：**
```bash
# 具体的操作命令
cd d:\zhou_dp\Desktop\CN-Digit-Reco\project
code command
```

详细说明和操作代码：

```python
"""
模块功能说明
"""

def function_name(param1, param2):
    """
    函数功能说明
    
    Args:
        param1: 参数1说明
        param2: 参数2说明
        
    Returns:
        返回值说明
        
    Raises:
        Exception: 异常说明
    """
    # 实现代码
    pass
```

**验证方法：**
```bash
# 验证命令（PowerShell）
Test-Path "d:\zhou_dp\Desktop\CN-Digit-Reco\project\file.py"
# 预期输出: True

# 检查文件内容
Get-Content "d:\zhou_dp\Desktop\CN-Digit-Reco\project\file.py" | Select-String "keyword"
# 预期输出: 包含特定内容
```

**成功标志：**
- 文件存在
- 包含特定内容
- 命令执行成功

### 步骤2：xxx
...

## 四、验证清单

完成所有步骤后，请确认以下验证项：

| 验证项 | 验证方法 | 预期结果 |
|--------|----------|----------|
| 文件存在 | `Test-Path` | True |
| 依赖安装 | `pip show package` | 显示版本信息 |
| 服务运行 | `Invoke-RestMethod` | 返回预期响应 |
| 功能正常 | 浏览器/命令测试 | 符合预期行为 |

## 五、故障排除

### 问题1：xxx错误

**现象：** 描述错误现象

**解决方案：**
```bash
# 解决命令
solution command
```

### 问题2：xxx失败

**现象：** 描述失败现象

**解决方案：**
```bash
# 解决命令
solution command
```

## 六、参考代码

### 完整目录结构

```
project/
├── dir/
│   └── file.py
└── config.json
```

### 常用命令

```bash
# 命令1
command1

# 命令2
command2
```

### 关键配置说明

| 参数 | 说明 | 示例 |
|------|------|------|
| param1 | 说明1 | value1 |
| param2 | 说明2 | value2 |
```

## 技术栈规范

### 后端技术栈（默认）
- 语言：Python 3.8+
- 框架：FastAPI
- ORM：SQLAlchemy
- 数据库：SQLite
- 深度学习：PyTorch
- 测试：pytest

### 前端技术栈（默认）
- 框架：React 18
- 路由：React Router
- 构建工具：Vite
- 样式：CSS Modules / 普通CSS

## 验证步骤编写规范

### 1. 每个步骤必须包含三部分

#### 操作命令
- 使用PowerShell命令（Windows系统）
- 包含完整路径
- 命令应可直接复制执行

#### 验证方法
- 使用Test-Path检查文件存在
- 使用Get-Content检查文件内容
- 使用Select-String查找特定内容
- 使用Invoke-RestMethod测试API
- 使用pip show检查包安装
- 使用python -c执行验证代码

#### 成功标志
- 明确的预期输出
- 具体的成功条件
- 可观察的结果

### 2. 验证命令示例

**检查文件存在：**
```powershell
Test-Path "d:\zhou_dp\Desktop\CN-Digit-Reco\project\file.py"
# 预期输出: True
```

**检查文件内容：**
```powershell
Get-Content "d:\zhou_dp\Desktop\CN-Digit-Reco\project\file.py" | Select-String "def function_name"
# 预期输出: 包含函数定义
```

**检查Python包：**
```powershell
pip show fastapi
# 预期输出: 显示版本信息
```

**测试API端点：**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health" -Method GET
# 预期输出: {"status":"ok"}
```

**检查进程运行：**
```powershell
Get-Process -Name python | Select-Object -First 1
# 预期输出: 显示进程信息
```

**检查端口占用：**
```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
# 预期输出: 显示连接信息或无输出（未占用）
```

### 3. 验证清单表格格式

```markdown
| 验证项 | 验证方法 | 预期结果 |
|--------|----------|----------|
| 文件存在 | `Test-Path` | True |
| 依赖安装 | `pip show package` | 显示版本信息 |
| 服务运行 | `Invoke-RestMethod` | 返回预期响应 |
| 功能正常 | 浏览器/命令测试 | 符合预期行为 |
```

## 生成流程

1. **理解需求**：明确实训的技术点、难度级别、前置依赖
2. **确定结构**：根据技术点选择合适的代码结构和示例
3. **编写内容**：按照六段式模板填充内容
4. **添加验证**：为每个步骤编写验证方法和成功标志
5. **编写故障排除**：列出常见问题和解决方案
6. **代码审查**：确保代码可运行、注释完整
7. **格式检查**：确保符合模板规范，无emoji等冗余内容

## 注意事项

1. 代码注释使用中文，遵循Google Python Style Guide
2. 函数必须包含docstring（参数、返回值、异常说明）
3. 文件路径使用绝对路径（Windows格式：d:\path\to\file）
4. 避免使用过于复杂的代码，保持示例简洁易懂
5. 每个实训步骤应该有明确的输入和输出
6. **验证步骤必须具体可操作**，不能模糊
7. **成功标志必须可观察**，不能主观判断
8. 故障排除应覆盖该实训最常见的3-5个问题
