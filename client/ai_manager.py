import os
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from dotenv import load_dotenv
from utils.logger import setup_logger

logger = setup_logger("AIManager", "ai_manager.log")

load_dotenv()

class AIManager:
    def __init__(self):
        self.deepseek_client = None
        self.ark_client = None
        self.ark_model = os.getenv("ARK_MODEL", "doubao-pro-32k")
        self._init_clients()

    def _init_clients(self):
        # DeepSeek
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if api_key:
            try:
                self.deepseek_client = AsyncOpenAI(
                    api_key=api_key,
                    base_url="https://api.deepseek.com"
                )
                logger.info("DeepSeek client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize DeepSeek client: {e}")

        # Volcengine (Ark) - Using OpenAI-compatible async client
        ark_key = os.getenv("ARK_API_KEY")
        if ark_key:
            try:
                self.ark_client = AsyncOpenAI(
                    api_key=ark_key,
                    base_url="https://ark.cn-beijing.volces.com/api/v3"
                )
                logger.info("Volcengine (Ark) async client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Ark client: {e}")

    def get_preferred_provider(self) -> str:
        if self.deepseek_client:
            return "deepseek"
        if self.ark_client:
            return "volcengine"
        return None

    def get_available_providers(self) -> List[Dict[str, str]]:
        providers = []
        if self.deepseek_client:
            providers.append({"id": "deepseek", "name": "DeepSeek"})
        if self.ark_client:
            providers.append({"id": "volcengine", "name": "豆包 (Volcengine)"})
        return providers

    async def chat(
        self,
        provider: str,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        tools: List[Dict[str, Any]] = None,
        tool_choice: Optional[Any] = None,
    ):
        logger.info(f"Chat request with provider: {provider}, has_tools: {bool(tools)}")
        if tools:
            logger.info(f"Tools provided to AI: {[t['function']['name'] for t in tools]}")
        effective_tool_choice = None
        if tools:
            effective_tool_choice = tool_choice if tool_choice is not None else "auto"
        
        if provider == "deepseek" and self.deepseek_client:
            try:
                response = await self.deepseek_client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "system", "content": system_prompt}] + messages,
                    tools=tools,
                    tool_choice=effective_tool_choice,
                )
                choice = response.choices[0].message
                logger.info(f"AI response content: {choice.content}")
                if choice.tool_calls:
                    logger.info(f"AI requested {len(choice.tool_calls)} tool calls: {[tc.function.name for tc in choice.tool_calls]}")
                return choice.content, choice.tool_calls or []
            except Exception as e:
                logger.error(f"DeepSeek chat error: {e}")
                raise

        elif provider == "volcengine" and self.ark_client:
            try:
                response = await self.ark_client.chat.completions.create(
                    model=self.ark_model,
                    messages=[{"role": "system", "content": system_prompt}] + messages,
                    tools=tools,
                    tool_choice=effective_tool_choice,
                )
                choice = response.choices[0].message
                logger.debug(f"Volcengine response received")
                return choice.content, choice.tool_calls or []
            except Exception as e:
                logger.error(f"Volcengine chat error: {e}")
                raise

        return "Error: Provider not available", []

    async def simple_chat(self, provider: str, messages: List[Dict[str, Any]], system_prompt: str):
        content, _ = await self.chat(provider, messages, system_prompt)
        return content

    async def stream_chat(self, provider: str, messages: List[Dict[str, Any]], system_prompt: str):
        """Streaming chat responses"""
        logger.info(f"Stream chat request with provider: {provider}")
        
        client = None
        model = "deepseek-chat"
        
        if provider == "deepseek":
            client = self.deepseek_client
        elif provider == "volcengine":
            client = self.ark_client
            model = self.ark_model
        
        if client:
            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system_prompt}] + messages,
                    stream=True
                )
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            except Exception as e:
                logger.error(f"{provider} stream error: {e}")
                yield f"Error: {e}"
        else:
            yield "Error: Provider not available for streaming"

ai_manager = AIManager()
