"""LLM API 封装：调用 GLM / DeepSeek 对话补全接口"""

import os
import json
import time
import logging
import requests
import yaml
from typing import Optional, List, Dict, Any

logger = logging.getLogger("src.generation.llm_client")

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'settings.yaml')
with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
    _CONFIG = yaml.safe_load(f)

_API_ENDPOINT = _CONFIG['api']['endpoint']
_DEEPSEEK_ENDPOINT = _CONFIG['api'].get(
    'deepseek_endpoint',
    'https://api.deepseek.com/chat/completions',
)


def _normalize_provider(provider: str) -> str:
    normalized = (provider or "glm").strip().lower()
    if normalized not in {"glm", "deepseek"}:
        raise ValueError(f"unsupported provider: {provider}")
    return normalized


def _load_api_key(provider: str = "glm") -> str:
    """从配置文件指定的密钥文件中加载 API Key"""
    provider = _normalize_provider(provider)
    if provider == "deepseek":
        key_file = _CONFIG['api'].get('deepseek_key_file', 'DeepSeekAPIKey.txt')
    else:
        key_file = _CONFIG['api']['key_file']
    # 相对于项目根目录
    root = os.path.join(os.path.dirname(__file__), '..', '..')
    key_path = os.path.join(root, key_file)
    with open(key_path, 'r', encoding='utf-8') as f:
        return f.read().strip()


def _get_int_config(section: str, key: str, default: int) -> int:
    try:
        return int(_CONFIG.get(section, {}).get(key, default))
    except (TypeError, ValueError):
        return default


def _retry_wait_seconds(attempt: int) -> int:
    return min(2 ** attempt, 8)


class GLMClient:
    """GLM / DeepSeek API 客户端。

    类名暂时保留为 GLMClient，避免大范围改动现有调用点。
    """

    def __init__(
        self,
        model: Optional[str] = None,
        max_retries: int = 3,
        api_key: Optional[str] = None,
        provider: str = "glm",
        thinking: bool = False,
        reasoning_effort: Optional[str] = None,
    ):
        self.provider = _normalize_provider(provider)
        self.api_key = (api_key or "").strip() or _load_api_key(self.provider)
        self.max_retries = max_retries
        self.thinking = bool(thinking) if self.provider == "deepseek" else False
        self.reasoning_effort = reasoning_effort or _CONFIG['models'].get(
            'deepseek_reasoning_effort',
            'high',
        )

        if self.provider == "deepseek":
            self.model = model or _CONFIG['models'].get('deepseek_model', 'deepseek-v4-pro')
            self.endpoint = _DEEPSEEK_ENDPOINT
        else:
            self.model = model or _CONFIG['models']['candidate_model']
            self.endpoint = _API_ENDPOINT
        self.timeout_seconds = self._resolve_timeout_seconds()

    def _resolve_timeout_seconds(self) -> int:
        if self.provider == "deepseek" and self.thinking:
            return _get_int_config('api', 'deepseek_thinking_timeout_seconds', 300)
        if self.provider == "deepseek":
            return _get_int_config('api', 'deepseek_timeout_seconds', 120)
        return _get_int_config('api', 'timeout_seconds', 60)

    def mode_label(self) -> str:
        """返回日志可读的模型/模式标签。"""
        if self.provider == "deepseek":
            mode = "thinking" if self.thinking else "non-thinking"
            return f"{self.model} ({mode})"
        return self.model

    def for_quality_retry(self) -> "GLMClient":
        """返回低分重试使用的升级客户端。"""
        if self.provider == "deepseek":
            return GLMClient(
                provider="deepseek",
                api_key=self.api_key,
                model=self.model,
                max_retries=self.max_retries,
                thinking=True,
                reasoning_effort=self.reasoning_effort,
            )

        return GLMClient(
            provider="glm",
            api_key=self.api_key,
            model=_CONFIG['models'].get('glm_retry_model', 'glm-4-plus'),
            max_retries=self.max_retries,
        )

    def _build_payload(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: Optional[Dict],
    ) -> Dict[str, Any]:
        output_tokens = max_tokens
        if self.provider == "deepseek" and self.thinking:
            output_tokens = max(
                max_tokens,
                _get_int_config('models', 'deepseek_thinking_min_tokens', 4096),
            )

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": output_tokens,
        }

        if self.provider == "deepseek":
            payload["thinking"] = {
                "type": "enabled" if self.thinking else "disabled",
            }
            if self.thinking:
                payload["reasoning_effort"] = self.reasoning_effort
            else:
                payload["temperature"] = temperature
        else:
            payload["temperature"] = temperature

        if response_format:
            payload["response_format"] = response_format

        return payload

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.9,
        max_tokens: int = 2048,
        response_format: Optional[Dict] = None,
    ) -> Optional[str]:
        """发送对话请求

        Args:
            messages: 对话消息列表
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            response_format: 响应格式，如 {"type": "json_object"}

        Returns:
            模型回复文本，失败返回 None
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = self._build_payload(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )

                if resp.status_code == 429:
                    wait = _retry_wait_seconds(attempt)
                    logger.warning(f"限流，等待 {wait}s 后重试")
                    time.sleep(wait)
                    continue

                if resp.status_code != 200:
                    logger.error(
                        f"{self.provider} API 返回错误 {resp.status_code}: {resp.text}"
                    )
                    if attempt < self.max_retries - 1:
                        time.sleep(1)
                        continue
                    return None

                data = resp.json()
                choice = data['choices'][0]
                message = choice.get('message') or {}
                content = message.get('content') or ""
                if content:
                    return content

                finish_reason = choice.get('finish_reason')
                reasoning_len = len(str(message.get('reasoning_content') or ""))
                logger.warning(
                    f"{self.mode_label()} 返回空 content "
                    f"(finish_reason={finish_reason}, "
                    f"reasoning_len={reasoning_len}, "
                    f"max_tokens={payload.get('max_tokens')})"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                    continue
                return None

            except requests.exceptions.Timeout:
                wait = _retry_wait_seconds(attempt)
                logger.warning(
                    f"{self.mode_label()} 请求超时 "
                    f"(timeout={self.timeout_seconds}s, 第 {attempt+1}/{self.max_retries} 次)"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(wait)
                continue
            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as e:
                wait = _retry_wait_seconds(attempt)
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"{self.mode_label()} 连接中断，等待 {wait}s 后重试 "
                        f"(第 {attempt+1}/{self.max_retries} 次): {e}"
                    )
                    time.sleep(wait)
                    continue
                logger.error(f"{self.mode_label()} 连接中断，多次重试仍失败: {e}")
                return None
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logger.error(f"解析响应失败: {e}")
                return None
            except requests.exceptions.RequestException as e:
                wait = _retry_wait_seconds(attempt)
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"{self.mode_label()} 请求异常，等待 {wait}s 后重试 "
                        f"(第 {attempt+1}/{self.max_retries} 次): {e}"
                    )
                    time.sleep(wait)
                    continue
                logger.error(f"{self.mode_label()} 请求异常，多次重试仍失败: {e}")
                return None

        return None

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.9,
        max_tokens: int = 2048,
    ) -> Optional[Dict]:
        """发送对话请求并解析 JSON 响应"""
        content = self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        if content is None:
            return None

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 JSON
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1:
                try:
                    return json.loads(content[start:end+1])
                except json.JSONDecodeError:
                    pass
            logger.error(f"{self.mode_label()} 无法解析 JSON 响应: {content[:200]!r}")
            return None
