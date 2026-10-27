"""All provider adapters, keyed by ``spec.type``. Add new adapters here."""

from __future__ import annotations

from .anthropic import AnthropicAdapter
from .base import ProviderAdapter, ProviderSpec
from .cerebras import CerebrasAdapter
from .chutes import ChutesAdapter
from .custom_openai import CustomOpenAIAdapter
from .deepseek import DeepSeekAdapter
from .fireworks import FireworksAdapter
from .google import GoogleAdapter
from .groq import GroqAdapter
from .mistral import MistralAdapter
from .moonshot import MoonshotAdapter
from .nvidia import NvidiaAdapter
from .openai import OpenAIAdapter
from .opencode import OpenCodeAdapter, OpenCodeGoAdapter
from .openrouter import OpenRouterAdapter
from .requesty import RequestyAdapter
from .together import TogetherAdapter
from .xai import XaiAdapter
from .zai import ZaiAdapter

ADAPTERS: dict[str, type[ProviderAdapter]] = {
    cls.spec.type: cls
    for cls in (
        OpenRouterAdapter,
        OpenCodeAdapter,
        OpenCodeGoAdapter,
        AnthropicAdapter,
        OpenAIAdapter,
        GoogleAdapter,
        DeepSeekAdapter,
        XaiAdapter,
        MistralAdapter,
        GroqAdapter,
        MoonshotAdapter,
        ZaiAdapter,
        TogetherAdapter,
        FireworksAdapter,
        CerebrasAdapter,
        RequestyAdapter,
        ChutesAdapter,
        NvidiaAdapter,
        CustomOpenAIAdapter,
    )
}


def get_adapter_class(provider_type: str) -> type[ProviderAdapter] | None:
    return ADAPTERS.get(provider_type)


def all_specs() -> list[ProviderSpec]:
    return [cls.spec for cls in ADAPTERS.values()]
