# 课迹

大学生多源课程任务理解与自适应规划智能体。

当前为阶段0：项目初始化与开发基础。

## 环境要求

- Windows 10 或 Windows 11
- Python 3.10 或更高版本

## 本地运行

1. 创建虚拟环境：

```powershell
python -m venv .venv
```

2. 激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

3. 安装依赖：

```powershell
python -m pip install -r requirements.txt
```

4. 准备本地环境变量文件：

```powershell
copy .env.example .env
```

5. 启动项目：

```powershell
python run.py
```

如果本机 5000 端口已被占用，可以临时指定其他端口：

```powershell
$env:PORT=5050
python run.py
```

6. 访问地址：

- 首页：http://127.0.0.1:5000/
- 健康检查：http://127.0.0.1:5000/health

## 运行测试

```powershell
python -m pytest
```

## 当前功能

- Flask 应用可启动
- 首页显示项目初始化信息
- `/health` 返回健康检查状态

## 尚未实现

任务管理、AI解析、OCR、文件上传、日历、提醒、用户登录等功能尚未进入开发阶段。
