# 前端网页（本地运行）

该目录提供一个**精美简洁的本地网页**，通过本地 HTTP 服务调用 `src/pipeline.py` 运行填词流程。

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
- 服务端通过请求头 `Authorization: Bearer <API_KEY>` 接收 key，并以同一个 key 调用模型。
- 请仅在本机使用 `127.0.0.1` 访问，不要把服务暴露到公网。

