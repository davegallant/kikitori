"""Local Japanese-to-English text translation, preserving passage boundaries."""

MODEL_NAME = "Helsinki-NLP/opus-mt-ja-en"
TRANSLATION_METHOD = "opus-mt-ja-en-v1"


class LocalTranslator:
    def __init__(self):
        import torch
        from transformers import MarianMTModel, MarianTokenizer

        self._torch = torch
        self._tokenizer = MarianTokenizer.from_pretrained(MODEL_NAME)
        self._model = MarianMTModel.from_pretrained(MODEL_NAME).eval()

    def __call__(self, text: str) -> str:
        if not text.strip():
            return ""
        tokens = self._tokenizer.encode(text, add_special_tokens=False)
        translations = []
        # Split oversized passages instead of silently truncating their input.
        # Rejoin their English text into the original passage's single row.
        for offset in range(0, len(tokens), 256):
            inputs = self._tokenizer.prepare_for_model(
                tokens[offset:offset + 256], return_tensors="pt",
                return_attention_mask=True, prepend_batch_axis=True,
            )
            with self._torch.inference_mode():
                output = self._model.generate(**inputs, max_length=512)
            if len(output[0]) >= 512:
                raise ValueError("Translation exceeded the output limit; passage was not saved")
            translated = self._tokenizer.decode(output[0], skip_special_tokens=True).strip()
            if not translated:
                raise ValueError("Translation returned an empty passage")
            translations.append(translated)
        return " ".join(translations)
