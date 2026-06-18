# 课迹

大学生多源课程任务理解与自适应规划智能体。

当前状态：阶段1（响应式页面基础框架）已完成。

下一步：阶段2（SQLite基础任务管理）。

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
- 添加任务：http://127.0.0.1:5000/tasks/new
- AI识别确认：http://127.0.0.1:5000/tasks/confirm
- 任务中心：http://127.0.0.1:5000/tasks
- 智能计划：http://127.0.0.1:5000/plan
- 健康检查：http://127.0.0.1:5000/health

## 运行测试

```powershell
python -m pytest
```

## 当前功能

- Flask 应用可启动
- 首页、添加任务、AI识别确认、任务中心和智能计划页面可切换
- 页面使用示例数据展示响应式APP骨架
- 未接入功能已标注“暂未接入”
- `/health` 返回健康检查状态

## 尚未实现

SQLite任务管理、真实任务保存、AI解析、OCR、文件解析、自动规划、提醒、日历导出和用户登录等功能尚未进入开发阶段。
