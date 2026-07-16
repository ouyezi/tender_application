# 标书诊断 Demo

本地可运行的标书诊断演示系统：上传招标文件与标书后发起诊断任务，管理端配置诊断项并监控任务进度。诊断引擎默认为 Mock 实现，不调用真实 LLM，也不包含登录鉴权。

## 启动后端

```bash
.venv/bin/uvicorn app.main:app --reload --app-dir backend --port 8000
```

首次启动时会自动写入 3 条示例诊断配置（企业资质核验、目录完整性、偏差表响应）。

## 启动前端

> 前端脚手架见 Task 9，当前为占位说明。

```bash
cd frontend && npm install && npm run dev
```

前端默认运行在 `http://localhost:5173`，通过 Vite 代理访问后端 `/api`。

## 说明

- **无鉴权**：所有接口公开可用，仅用于本地演示。
- **Mock 引擎**：诊断结果为模拟数据，不解析上传文件正文。
- **存储**：SQLite 数据库 + 本地 `uploads/`、`reports/` 目录。
