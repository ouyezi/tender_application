# 标书诊断 Demo

本地可运行的标书诊断演示系统：上传招标文件与标书后发起诊断任务，管理端配置诊断项并监控任务进度。诊断引擎默认为 Mock 实现，不调用真实 LLM，也不包含登录鉴权。

## 环境准备

需要 Python 3.11+ 与 Node.js 18+。

### 后端依赖

在项目根目录创建虚拟环境并安装依赖（首次或依赖变更时）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

### 前端依赖

```bash
cd frontend && npm install
```

## 一键启动（推荐）

在项目根目录执行：

```bash
.venv/bin/python startup.py
```

脚本会：

1. 检查虚拟环境与前端依赖（缺 `node_modules` 时自动 `npm install`）
2. 启动后端（`:8000`）与前端（`:5173`）
3. 等待服务就绪后打开浏览器
4. 在 macOS 上会弹出两个 Terminal 窗口分别跑前后端；其他系统在当前终端托管，`Ctrl+C` 停止

## 分别启动

### 后端

```bash
.venv/bin/uvicorn app.main:app --reload --app-dir backend --port 8000
```

- API 文档：`http://localhost:8000/docs`
- 健康检查：`GET /api/health` → `{"ok": true}`
- 首次启动若配置表为空，会自动写入 3 条示例诊断配置（企业资质核验、目录完整性、偏差表响应）

### 前端

```bash
cd frontend && npm install && npm run dev
```

前端默认运行在 `http://localhost:5173`，通过 Vite 代理访问后端 `/api`。

| 页面 | 路径 |
|---|---|
| 任务列表 / 创建诊断 | `/` |
| 任务详情（报告预览） | `/tasks/:id` |
| 管理端 · 诊断配置 | `/admin/configs` |
| 管理端 · 任务监控 | `/admin/tasks` |

## 验收清单

按以下场景手动验收（与设计文档 §11 一致）：

1. **配置 CRUD**：在 `/admin/configs` 新增、编辑、删除诊断配置；列表即时刷新。
2. **创建 → 完成 → 下载**：在 `/` 创建任务（上传 PDF/DOCX），进入详情见进度增长至 `completed`，可预览 Markdown 报告并下载 DOCX。
3. **暂停 / 继续**：任务 `running` 时在管理端暂停，进度停止；继续后直至完成。
4. **停止后不可下载**：`stop` 后状态为 `stopped`，不可 resume；正式报告下载返回 404。
5. **非法扩展名**：上传非 PDF/DOCX（如 `.txt`）被拒绝。

## 说明

- **无鉴权**：所有接口公开可用，仅用于本地演示。
- **Mock 引擎**：诊断结果为模拟数据，不解析上传文件正文。
- **存储**：SQLite 数据库 + 本地 `uploads/`、`reports/` 目录（均已加入 `.gitignore`）。
- **测试**：`cd backend && ../.venv/bin/python -m pytest`
