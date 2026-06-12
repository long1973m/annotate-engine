"""
API 客户端 — 多模型适配、重试、限速

支持 OpenAI 兼容格式的 API（包括 agnes、openai、ollama 等）
内置指数退避重试、429 限速处理
"""
import time
import json
import asyncio
import aiohttp
from typing import Optional
from pathlib import Path


class APIClient:
    """OpenAI 兼容格式的 LLM API 客户端"""
    
    def __init__(self, provider_cfg: dict, model_cfg: dict):
        self.base_url = provider_cfg["base_url"].rstrip("/")
        self.api_key = provider_cfg["api_key"]
        self.model_name = model_cfg["name"]
        self.max_tokens = model_cfg.get("max_tokens", 4096)
        self.temperature = model_cfg.get("temperature", 0.7)
        self.thinking_mode = model_cfg.get("thinking_mode", {})
        self.thinking_enabled = self.thinking_mode.get("enabled", False)
        self.thinking_budget = self.thinking_mode.get("budget_tokens", 2048)
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0
        self._min_interval = 0.1  # 最小请求间隔（秒）
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=120)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _rate_limit(self):
        """速率限制"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()
    
    async def call(self, prompt: str, system_prompt: str = "", 
                   response_format: Optional[dict] = None) -> str:
        """
        调用 LLM API
        
        Args:
            prompt: 用户消息（包含数据和模板）
            system_prompt: 系统消息
            response_format: {"type": "json_object"} 强制 JSON 输出
        
        Returns:
            模型回复文本
        """
        await self._rate_limit()
        
        session = await self._get_session()
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        body = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        
        # 强制 JSON 输出
        if response_format:
            body["response_format"] = response_format
        
        # Thinking mode
        if self.thinking_enabled and self.thinking_budget:
            body["thinking"] = {"enabled": True, "budget_tokens": self.thinking_budget}
        
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        # 指数退避重试（最多 5 次）
        max_retries = 5
        for attempt in range(max_retries):
            try:
                async with session.post(url, json=body, headers=headers) as resp:
                    if resp.status == 429:
                        wait = min(2 ** attempt * 2, 30)
                        await asyncio.sleep(wait)
                        continue
                    
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise RuntimeError(f"HTTP {resp.status}: {error_text[:200]}")
                    
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return content
                    
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < max_retries - 1:
                    wait = min(2 ** attempt * 2, 30)
                    await asyncio.sleep(wait)
                else:
                    raise RuntimeError(f"API 调用失败（{max_retries} 次重试后）: {e}")
        
        raise RuntimeError("API 调用失败：未知错误")
    
    async def call_batch(self, requests: list, workers: int = 5) -> list:
        """
        并发批量调用
        
        Args:
            requests: [{"prompt": "...", "system_prompt": "..."}] 列表
            workers: 并发数
        
        Returns:
            [content, content, ...] 列表，失败的项返回 None
        """
        results = [None] * len(requests)
        semaphore = asyncio.Semaphore(workers)
        
        async def _limited_call(idx, req):
            async with semaphore:
                try:
                    content = await self.call(
                        req["prompt"],
                        req.get("system_prompt", ""),
                        req.get("response_format")
                    )
                    results[idx] = content
                except Exception as e:
                    results[idx] = f"ERROR: {e}"
        
        tasks = [
            asyncio.create_task(_limited_call(i, req))
            for i, req in enumerate(requests)
        ]
        await asyncio.gather(*tasks)
        return results
