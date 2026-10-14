import json
from types import SimpleNamespace

import pytest

from podcast_transcriber.translation import ChatGPTTranslator


def test_chatgpt_translator_uses_context_and_preserves_passage_order():
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                content='["People in Japan don’t get fired.", '
            ))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                content='"That is called getting fired."]'
            ))]),
            SimpleNamespace(choices=[]),
        ]

    translator = ChatGPTTranslator(completion=complete)

    assert translator.translate_many(
        ["日本には首がない", "首になる"],
        context="The conversation explains that 首 means losing your job.",
    ) == ["People in Japan don’t get fired.", "That is called getting fired."]
    assert calls[0]["model"] == "chatgpt/gpt-6-luna"
    assert calls[0]["stream"] is True
    assert "The conversation explains" in calls[0]["messages"][1]["content"]
    assert json.loads(calls[0]["messages"][1]["content"])["passages"] == [
        "日本には首がない", "首になる"
    ]


def test_chatgpt_translator_splits_batches_when_model_omits_a_translation():
    requests = []

    def complete(**kwargs):
        passages = json.loads(kwargs["messages"][1]["content"])["passages"]
        requests.append(passages)
        results = [f"English: {passage}" for passage in passages]
        if len(results) > 1:
            results.pop()
        content = json.dumps(results, ensure_ascii=False)
        return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])]

    translator = ChatGPTTranslator(completion=complete)

    assert translator.translate_many(["一", "二", "三", "四"]) == [
        "English: 一", "English: 二", "English: 三", "English: 四"
    ]
    assert requests == [
        ["一", "二", "三", "四"],
        ["一", "二"],
        ["一"],
        ["二"],
        ["三", "四"],
        ["三"],
        ["四"],
    ]


@pytest.mark.parametrize("content", [
    '"Only one translation"',
    '["First", " "]',
    '["First", 2]',
])
def test_chatgpt_translator_rejects_unaligned_or_invalid_output(content):
    translator = ChatGPTTranslator(completion=lambda **kwargs: [SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
    )])

    with pytest.raises(ValueError, match="translation"):
        translator.translate_many(["一", "二"])
