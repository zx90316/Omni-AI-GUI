# -*- coding: utf-8 -*-
"""Semantic reranker compatibility tests."""
import unittest

from backend.semantic_engine import (
    _ensure_reranker_tokenizer_compatibility,
    _xlm_roberta_prepare_for_model,
)


class _FakeXLMRobertaTokenizer:
    bos_token_id = 0
    eos_token_id = 2
    truncation_side = "right"
    model_input_names = ["input_ids", "attention_mask"]


class SemanticRerankerCompatibilityTests(unittest.TestCase):
    def test_builds_xlm_roberta_pair_and_truncates_only_passage(self):
        tokenizer = _FakeXLMRobertaTokenizer()

        encoded = _xlm_roberta_prepare_for_model(
            tokenizer,
            [10, 11],
            [20, 21, 22, 23],
            truncation="only_second",
            max_length=8,
            padding=False,
        )

        self.assertEqual(encoded["input_ids"], [0, 10, 11, 2, 2, 20, 21, 2])
        self.assertEqual(encoded["attention_mask"], [1] * 8)

    def test_compatibility_adapter_is_installed_only_when_missing(self):
        tokenizer_class = type(
            "XLMRobertaTokenizer",
            (),
            {
                "bos_token_id": 0,
                "eos_token_id": 2,
                "truncation_side": "right",
                "model_input_names": ["input_ids", "attention_mask"],
            },
        )
        reranker = type("Reranker", (), {"tokenizer": tokenizer_class()})()

        self.assertTrue(_ensure_reranker_tokenizer_compatibility(reranker))
        self.assertFalse(_ensure_reranker_tokenizer_compatibility(reranker))
        self.assertEqual(
            reranker.tokenizer.prepare_for_model([10], [20])["input_ids"],
            [0, 10, 2, 2, 20, 2],
        )


if __name__ == "__main__":
    unittest.main()
