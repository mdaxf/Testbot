from __future__ import annotations

import base64
import os

from framework import tlsconfig  # trusts the OS store + company root certificates + proxy (see tlsconfig.py)


class VisionProviderError(Exception):
    pass


def _require_env(name: str, provider: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise VisionProviderError(f"{name} is not set -- required for AI_VISION_PROVIDER={provider!r}")
    return value


def _call_anthropic(image_bytes: bytes, prompt: str) -> str:
    from anthropic import Anthropic

    api_key = _require_env("ANTHROPIC_API_KEY", "anthropic")
    model = os.environ.get("AI_VISION_MODEL", "claude-sonnet-5")
    client = Anthropic(api_key=api_key, http_client=tlsconfig.http_client(sdk="anthropic"))
    response = client.messages.create(
        model=model,
        max_tokens=20,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return response.content[0].text.strip()


def _call_openai_compatible(image_bytes: bytes, prompt: str, client, model: str) -> str:
    """Shared chat-completions call shape used by both plain OpenAI and Azure OpenAI."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    response = client.chat.completions.create(
        model=model,
        max_tokens=20,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _call_openai(image_bytes: bytes, prompt: str) -> str:
    from openai import OpenAI

    api_key = _require_env("OPENAI_API_KEY", "openai")
    model = os.environ.get("AI_VISION_MODEL", "gpt-4o")
    client = OpenAI(api_key=api_key, http_client=tlsconfig.http_client(sdk="openai"))
    return _call_openai_compatible(image_bytes, prompt, client, model)


def _call_azure_openai(image_bytes: bytes, prompt: str) -> str:
    from openai import AzureOpenAI

    api_key = _require_env("AZURE_OPENAI_API_KEY", "azure_openai")
    endpoint = _require_env("AZURE_OPENAI_ENDPOINT", "azure_openai")
    deployment = _require_env("AZURE_OPENAI_DEPLOYMENT", "azure_openai")  # the deployment name IS the model
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
    client = AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version, http_client=tlsconfig.http_client(sdk="openai"))
    return _call_openai_compatible(image_bytes, prompt, client, deployment)


def _call_gemini(image_bytes: bytes, prompt: str) -> str:
    from google import genai
    from google.genai import types

    api_key = _require_env("GEMINI_API_KEY", "gemini")
    model = os.environ.get("AI_VISION_MODEL", "gemini-2.0-flash")
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(client_args=tlsconfig.client_args()))
    response = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
    )
    return (response.text or "").strip()


_PROVIDERS = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "azure_openai": _call_azure_openai,
    "gemini": _call_gemini,
}


def call_vision_model(image_bytes: bytes, prompt: str) -> str:
    """Dispatches to whichever vision-capable LLM AI_VISION_PROVIDER names (default
    "anthropic"). All providers take the same (image_bytes, prompt) -> answer-text shape,
    so the caller (vision_fallback.py) doesn't need to know which one is active.
    """
    provider = os.environ.get("AI_VISION_PROVIDER", "anthropic").strip().lower()
    call = _PROVIDERS.get(provider)
    if call is None:
        raise VisionProviderError(f"Unknown AI_VISION_PROVIDER '{provider}'. Supported: {', '.join(sorted(_PROVIDERS))}.")
    return call(image_bytes, prompt)
