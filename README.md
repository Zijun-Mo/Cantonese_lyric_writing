# 粤语填词系统

基于 GLM / DeepSeek 大语言模型的自动粤语填词流水线。输入简谱与普通话语义种子，输出符合粤语声调规律的歌词。

---

## 系统流程

```
普通话种子 → 分词/语义槽提取
简谱       → 字位解析 + 0243 声调模板
                      ↓
           LLM 逐小节批量生成候选（GLM 或 DeepSeek）
                      ↓
           规则评分器排序（协音 × 0.45 + 语义 × 0.20 + 自然度 × 0.15 + 分句 × 0.10 + 韵脚 × 0.10）
                      ↓
           低分小节触发重试 → 必要时升级模型/思考模式
                      ↓
           LLM 润色
                      ↓
           输出 JSON + 流水线日志
```

---

## 目录结构

```
.
├── src/
│   ├── pipeline.py              # 主流水线（入口）
│   ├── preprocess/
│   │   ├── jianpu_parser.py     # 简谱解析，输出字位与节拍信息
│   │   └── mandarin_segmenter.py# 普通话分词 + 语义槽提取
│   ├── generation/
│   │   ├── glm_client.py        # GLM / DeepSeek API 客户端（模型可配置）
│   │   ├── slot_filler.py       # 按语义槽填词（候选生成）
│   │   └── polisher.py          # 歌词润色
│   ├── rules/
│   │   ├── scorer.py            # 多维度评分器
│   │   └── tone_template.py     # 粤语九声 → 0243 模板生成
│   ├── dictionary/
│   │   └── cantonese_db.py      # 粤语字符数据库
│   ├── input/
│   │   └── schema.py            # 输入格式校验（LyricInput）
│   └── frontend/
│       ├── index.html           # 本地前端页面
│       ├── app.js               # 前端交互逻辑
│       ├── styles.css           # 页面样式
│       └── dev_server.py        # 本地前端 + API 服务
├── config/
│   └── settings.yaml            # 模型、评分权重、API 配置
├── scripts/                     # 工具脚本（待补充）
├── tests/                       # 单元测试
├── punie_lyric_input.json       # 示例输入文件
├── requirements.txt
├── PLAN.md                      # 详细设计规范
└── AGENTS.md                    # AI 助手工作规范
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

默认使用 GLM 模式。在项目根目录新建 `APIKey.txt`，写入智谱 AI 的 API Key（单行，无换行）：

```
your_api_key_here
```

> `APIKey.txt` 已被 `.gitignore` 排除，不会提交到版本库。

如需使用 DeepSeek 模式，在项目根目录新建 `DeepSeekAPIKey.txt`，写入 DeepSeek API Key：

```
your_deepseek_api_key_here
```

> `DeepSeekAPIKey.txt` 同样已被 `.gitignore` 排除。前端页面中临时输入的 API Key 会覆盖对应模式的本地 key 文件。

### 3. 修改配置（可选）

编辑 `config/settings.yaml` 调整模型和评分权重：

```yaml
api:
  timeout_seconds: 60
  deepseek_timeout_seconds: 120
  deepseek_thinking_timeout_seconds: 300

models:
  candidate_model: "glm-4-flash"   # 候选生成模型
  polish_model: "glm-4-flash"      # 润色模型
  glm_retry_model: "glm-4-plus"     # GLM 低分重试升级模型
  deepseek_model: "deepseek-v4-pro" # DeepSeek 生成模型
  deepseek_reasoning_effort: "high" # DeepSeek 思考模式强度
  deepseek_thinking_min_tokens: 4096 # DeepSeek 思考模式最小输出 token 预算

generation:
  candidates_per_bar: 10           # 每小节候选数
  temperature: 0.9
```

### 4. 准备输入文件

参考 `punie_lyric_input.json`，格式如下：

```json
{
  "jianpu": "3 3 5 | 6 - - | ...",
  "mandarin_seed": "记忆中的你 | 渐渐远去 | ...",
  "theme_tags": ["怀旧", "爱情"],
  "style_tags": ["抒情"]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `jianpu` | 是 | 简谱，`\|` 分小节 |
| `mandarin_seed` | 是 | 普通话语义种子，`\|` 数量须与 `jianpu` 一致 |
| `theme_tags` | 否 | 主题/情绪标签 |
| `style_tags` | 否 | 风格标签 |

### 5. 运行

```bash
python src/pipeline.py punie_lyric_input.json
```

输出写入 `output.json`，详细日志写入 `pipeline_log.txt`。

### 6. 启动本地前端网页（可选）

如果希望通过网页填写简谱、普通话种子并查看谱面结果，可以启动本地前端服务：

```bash
python src/frontend/dev_server.py
```

浏览器打开：

```
http://127.0.0.1:7860
```

前端服务会调用同一套 `src/pipeline.py` 流水线，可在页面中选择 `GLM` 或 `DeepSeek` 模式。API Key 可在网页中临时输入，也可以继续使用项目根目录的 `APIKey.txt` / `DeepSeekAPIKey.txt`。该服务默认仅监听本机地址，请不要暴露到公网。

更多前端说明见 `src/frontend/README.md`。

---

## 评分机制

| 维度 | 权重 | 说明 |
|------|------|------|
| 协音（tone） | 0.45 | 粤语声调与旋律走向的匹配度 |
| 语义保持 | 0.20 | 与普通话种子的语义一致性 |
| 口语自然度 | 0.15 | 符合粤语口语习惯 |
| 分句匹配 | 0.10 | 句读与小节划分对齐 |
| 韵脚/风格 | 0.10 | 押韵与整体风格统一 |

低于 0.60 的小节会触发重试。GLM 模式多次失败后自动升级至 `glm-4-plus` 重新生成；DeepSeek 模式先使用 `deepseek-v4-pro` 非思考模式，仍低分时切换为 `deepseek-v4-pro` 思考模式（`reasoning_effort=high`）重试。

---

## 粤语声调模板（0243 体系）

系统将粤语九声映射为旋律走向类别，用于约束候选生成：

| 粤语声调 | 类别 | 说明 |
|----------|------|------|
| 阴平（1）、上阴入（7） | 3 | 高平 |
| 阴上（2）、阴去（3）、下阴入（8） | 4 | 高降 |
| 阳去（6） | 2 | 中平 |
| 阳平（4）、阳上（5）、阳入（9） | 0 | 低平/升 |

---

## 依赖

- Python ≥ 3.9
- requests ≥ 2.28
- jieba ≥ 0.42
- pypinyin ≥ 0.49
- pycantonese ≥ 3.4
- pyyaml ≥ 6.0
- 智谱 AI API（[open.bigmodel.cn](https://open.bigmodel.cn)）
- DeepSeek API（可选，[api.deepseek.com](https://api.deepseek.com)）
