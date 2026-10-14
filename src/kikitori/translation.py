"""Context-aware Japanese-to-English translation through a ChatGPT plan."""

import json


TRANSLATION_METHOD = "chatgpt-gpt-6-luna-ja-en-v1"
MODEL = "chatgpt/gpt-6-luna"
MAX_TRANSLATION_ITEMS = 50
MAX_TRANSLATION_REQUEST_BYTES = 128 * 1024
MAX_TRANSLATION_CONTEXT_BYTES = 32 * 1024


def bounded_context(context: str | None) -> str:
    context_bytes = (context or "").encode("utf-8")
    if len(context_bytes) > MAX_TRANSLATION_CONTEXT_BYTES:
        return context_bytes[:MAX_TRANSLATION_CONTEXT_BYTES].decode("utf-8", errors="ignore")
    return context or ""


def translation_request_size(texts: list[str], context: str | None = None) -> int:
    payload = {"context": context or "", "passages": texts}
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class ChatGPTTranslator:
    def __init__(self, completion=None):
        if completion is None:
            from litellm import completion

        self._completion = completion

    def __call__(self, text: str) -> str:
        if not text.strip():
            return ""
        return self.translate_many([text])[0]

    def translate_many(self, texts: list[str], context: str | None = None) -> list[str]:
        """Translate a batch, requiring one English result per Japanese passage."""
        context = bounded_context(context)
        if len(texts) > MAX_TRANSLATION_ITEMS:
            raise ValueError("A translation request cannot contain more than 50 passages")
        if translation_request_size(texts, context) > MAX_TRANSLATION_REQUEST_BYTES:
            raise ValueError("Japanese passages exceed the translation request size limit")

        return self._translate_batch(texts, context)

    def _translate_batch(self, texts: list[str], context: str) -> list[str]:
        # The ChatGPT subscription route can lose text on LiteLLM's non-streaming bridge.
        response = self._completion(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate Japanese podcast transcript passages into natural, faithful English. "
                        "Use the episode context to resolve idioms and references. Translate only the "
                        "passages, without adding information. Return only a JSON array of strings, "
                        "with exactly one translation for each passage in the same order."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"context": context, "passages": texts},
                        ensure_ascii=False,
                    ),
                },
            ],
            stream=True,
        )
        content_parts = []
        for chunk in response:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            content = getattr(choices[0].delta, "content", None)
            if isinstance(content, str):
                content_parts.append(content)
        try:
            translated = json.loads("".join(content_parts))
        except (TypeError, json.JSONDecodeError):
            translated = None
        if isinstance(translated, list) and len(translated) == len(texts) and all(
            isinstance(item, str) and item.strip() for item in translated
        ):
            return [item.strip() for item in translated]

        if len(texts) == 1:
            raise ValueError("ChatGPT could not return one non-empty translation for a passage")

        midpoint = len(texts) // 2
        return self._translate_batch(texts[:midpoint], context) + self._translate_batch(
            texts[midpoint:], context
        )


def ensure_chatgpt_login() -> None:
    """Load or refresh LiteLLM's ChatGPT login before starting worker threads."""
    from litellm.llms.chatgpt.authenticator import Authenticator

    Authenticator().get_access_token()
