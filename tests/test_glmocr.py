"""Fast OCR unit tests that do not download model weights."""

from __future__ import annotations

import base64
import importlib.util
import io
import os
import unittest
import sys
import types
from unittest.mock import patch

from PIL import Image

import backend.ocr_engine as engine
from backend.ocr_providers import (
    GenerationOptions,
    InProcessSDKClient,
    OCRProvider,
    TransformersOCRProvider,
)


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeProvider(OCRProvider):
    name = "fake"

    def __init__(self, response: str = "recognized"):
        self.response = response
        self.calls = []

    def recognize(self, image, prompt, options: GenerationOptions) -> str:
        self.calls.append((image.size, prompt, options.model))
        return self.response


class OCRTests(unittest.TestCase):
    def test_transformers_provider_uses_official_image_text_flow(self):
        observed = {}

        class FakeInputs(dict):
            def to(self, device):
                observed["device"] = device
                return self

        class FakeInputIds:
            shape = (1, 2)

        class FakeProcessor:
            def apply_chat_template(self, messages, **kwargs):
                observed["messages"] = messages
                observed["template_kwargs"] = kwargs
                return FakeInputs(input_ids=FakeInputIds(), token_type_ids="remove-me")

            def decode(self, tokens, skip_special_tokens):
                observed["decoded_tokens"] = tokens
                observed["skip_special_tokens"] = skip_special_tokens
                return "recognized locally"

        class FakeAutoProcessor:
            @classmethod
            def from_pretrained(cls, model, **kwargs):
                observed["processor_model"] = model
                observed["processor_load_kwargs"] = kwargs
                return FakeProcessor()

        class FakeModel:
            device = "cpu"

            def eval(self):
                observed["eval"] = True

            def generate(self, **kwargs):
                observed["generation"] = kwargs
                return [[10, 11, 20, 21]]

        class FakeAutoModel:
            @classmethod
            def from_pretrained(cls, model, **kwargs):
                observed["model"] = model
                observed["load_kwargs"] = kwargs
                return FakeModel()

        fake_transformers = types.ModuleType("transformers")
        fake_transformers.AutoProcessor = FakeAutoProcessor
        fake_transformers.AutoModelForImageTextToText = FakeAutoModel
        with (
            patch.dict(sys.modules, {"transformers": fake_transformers}),
            patch.dict(os.environ, {"OCR_DEVICE": "auto"}),
            patch("backend.network_utils.is_model_cached", return_value=True),
        ):
            provider = TransformersOCRProvider()
            output = provider.recognize(
                Image.new("RGB", (8, 8), "white"),
                "Text Recognition:",
                GenerationOptions(model="glm-ocr", max_tokens=64),
            )

        self.assertEqual(output, "recognized locally")
        self.assertEqual(observed["model"], "zai-org/GLM-OCR")
        self.assertEqual(observed["processor_model"], "zai-org/GLM-OCR")
        self.assertTrue(observed["processor_load_kwargs"]["local_files_only"])
        self.assertTrue(observed["load_kwargs"]["local_files_only"])
        self.assertEqual(observed["messages"][0]["content"][1]["text"], "Text Recognition:")
        self.assertTrue(observed["messages"][0]["content"][0]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(observed["generation"]["max_new_tokens"], 64)

    def test_official_task_prompts_and_information_schema(self):
        self.assertEqual(engine.build_ocr_prompt(task="text"), "Text Recognition:")
        self.assertEqual(engine.build_ocr_prompt(task="table"), "Table Recognition:")
        self.assertEqual(engine.build_ocr_prompt(task="formula"), "Formula Recognition:")
        prompt = engine.build_ocr_prompt({"日期": "YYYY-MM-DD", "items": []})
        self.assertTrue(prompt.startswith("请按下列JSON格式输出图中信息:"))
        self.assertIn('"日期": "YYYY-MM-DD"', prompt)

    def test_parse_json_response_handles_fences_and_nested_objects(self):
        text = '說明文字\n```json\n{"person":{"name":"王小明"},"items":[1,2]}\n```\n完成'
        self.assertEqual(
            engine._parse_json_response(text),
            {"person": {"name": "王小明"}, "items": [1, 2]},
        )

    def test_postprocess_result_is_recursive(self):
        with patch.object(engine, "_cc", None), patch.object(
            engine, "_correction_map", {"錯": "對"}
        ):
            value = {"錯字": ["錯", {"nested": "沒有錯嗎"}], "count": 2}
            self.assertEqual(
                engine.postprocess_result(value),
                {"對字": ["對", {"nested": "沒有對嗎"}], "count": 2},
            )

    def test_merge_page_results_handles_nested_values_and_conflicts(self):
        pages = [
            {"id": "A-1", "address": {"city": "台北"}, "note": "first"},
            {"id": "A-1", "address": {"city": "台北"}, "note": "second"},
        ]
        self.assertEqual(
            engine.merge_page_results(pages),
            {"id": "A-1", "address": {"city": "台北"}, "note": ""},
        )

    def test_inprocess_sdk_client_adapts_request_without_http(self):
        provider = FakeProvider("# Markdown")
        client = InProcessSDKClient(provider, GenerationOptions(model="test-model"))
        data_uri = "data:image/png;base64," + base64.b64encode(_image_bytes()).decode()
        response, status = client.process(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Table Recognition:"},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ]
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["choices"][0]["message"]["content"], "# Markdown")
        self.assertEqual(provider.calls, [((8, 8), "Table Recognition:", "test-model")])

    def test_call_glm_ocr_legacy_entrypoint_uses_provider(self):
        provider = FakeProvider("hello")
        encoded = base64.b64encode(_image_bytes()).decode()
        with patch.object(engine, "get_ocr_provider", return_value=provider):
            result = engine._call_glm_ocr(encoded, "OCR", raw_mode=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["raw"], "hello")
        self.assertEqual(provider.calls[0][1], "Text Recognition:")

    def test_process_file_stream_reports_page_contract_without_model(self):
        expected = {
            "success": True,
            "data": None,
            "json": None,
            "markdown": "ok",
            "raw": "ok",
            "error": None,
            "provider": "local",
            "task": "text",
            "layout": False,
        }
        with patch.object(engine, "recognize_image", return_value=expected):
            events = list(
                engine.process_file_stream(
                    _image_bytes(), "sample.png", task="text", provider="local"
                )
            )
        self.assertEqual(
            events,
            [
                {
                    "page": 1,
                    "total": 1,
                    "percent": 100.0,
                    "result": expected,
                    "done": True,
                }
            ],
        )

    def test_document_merge_produces_markdown_and_json_pages(self):
        merged = engine.merge_document_results(
            [
                {"success": True, "markdown": "page 1", "json": [{"content": "one"}]},
                {"success": False, "markdown": "ignored", "json": None},
                {"success": True, "markdown": "page 2", "json": [{"content": "two"}]},
            ]
        )
        self.assertEqual(merged["markdown"], "page 1\n\n---\n\npage 2")
        self.assertEqual(merged["page_count"], 2)
        self.assertEqual(len(merged["json"]), 2)

    def test_capabilities_are_available_without_loading_model_weights(self):
        capabilities = engine.get_ocr_capabilities()
        self.assertEqual(
            [task["id"] for task in capabilities["tasks"]],
            ["document", "text", "table", "formula", "extract"],
        )
        self.assertEqual(capabilities["default_model"], "zai-org/GLM-OCR")
        self.assertEqual(
            [provider["name"] for provider in capabilities["providers"]],
            ["local", "openai", "ollama"],
        )

    @unittest.skipUnless(importlib.util.find_spec("glmocr"), "official glmocr not installed")
    def test_official_sdk_pipeline_accepts_inprocess_provider(self):
        provider = FakeProvider("# Official SDK bridge")
        structured, markdown = engine._run_document_pipeline(
            Image.new("RGB", (24, 24), "white"),
            provider,
            GenerationOptions(model="test-model", max_tokens=32),
            enable_layout=False,
            output_format="both",
        )
        self.assertIn("Official SDK bridge", markdown)
        self.assertIsNotNone(structured)
        self.assertGreaterEqual(len(provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
