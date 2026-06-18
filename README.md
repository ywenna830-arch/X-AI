# 课迹

大学生多源课程任务理解与自适应规划智能体。

当前状态：阶段2（SQLite基础任务管理）已完成；阶段2.5（录入界面重构）已完成。

下一步：阶段3（文字通知AI解析）。

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
- SQLite 持久化保存任务，数据库位于本地 `instance/` 目录
- 支持手动新增、查看、编辑、删除、筛选和状态切换任务
- 首页读取真实任务数据，展示今日任务、即将截止、完成数量和逾期数量
- 添加任务页已改为对话式主入口，并保留真实可用的手动填写入口
- AI识别、文件解析和自动规划等未接入功能已标注“暂未接入”
- `/health` 返回健康检查状态

## 尚未实现

AI解析、OCR、图片/Word/PDF附件解析、自动任务拆解、自动日历规划、提醒、日历导出和用户登录等功能尚未进入开发阶段；添加任务页中的AI发送和附件入口仅作为后续阶段提示。
