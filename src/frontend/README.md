# 前端网页（本地运行）

该目录提供一个**精美简洁的本地网页**，通过本地 HTTP 服务调用 `src/pipeline.py` 运行填词流程。

页面支持两种模型服务：

- `GLM`：默认模式，使用项目根目录的 `APIKey.txt`
- `DeepSeek`：使用 `DeepSeekAPIKey.txt`，常规生成走 `deepseek-v4-pro` 非思考模式；低分重试升级为思考模式

## 1. 启动方式（Windows / PowerShell）

在项目根目录执行：

```powershell
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python .\src\frontend\dev_server.py
```

浏览器打开：

`http://127.0.0.1:7860`

## 2. API Key 安全说明

- 网页端 API Key 只在浏览器内存中存在，不会写入磁盘。
- 服务端通过请求头 `Authorization: Bearer <API_KEY>` 接收 key，并按页面选择的模式调用对应模型。
- 如果不在网页输入 key，服务端会按模式读取本地 `APIKey.txt` 或 `DeepSeekAPIKey.txt`。
- 请仅在本机使用 `127.0.0.1` 访问，不要把服务暴露到公网。
