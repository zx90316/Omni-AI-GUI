"""Legacy and persistent OCR response contract tests."""

import unittest

from backend.ocr_contract import build_ocr_stream_payload


class OCRCompatibilityTests(unittest.TestCase):
    def test_completed_extraction_keeps_legacy_top_level_result(self):
        result = {
            "page": 1,
            "total": 1,
            "percent": 100.0,
            "success": True,
            "data": {"發文者": "測試機關", "文號": "A-1"},
            "raw": '{"發文者":"測試機關","文號":"A-1"}',
            "provider": "local",
            "task": "extract",
            "done": True,
        }
        payload = build_ocr_stream_payload(
            task_id=42,
            status="completed",
            progress=100.0,
            progress_message="辨識完成",
            error_message=None,
            result_data={"results": [result], "merged": {"文號": "A-1"}},
        )

        # GovDoc and other legacy clients read these fields from the final SSE
        # event instead of looking inside all_results.
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"], result["data"])
        self.assertEqual(payload["raw"], result["raw"])

        # New clients retain task persistence metadata in the same payload.
        self.assertEqual(payload["task_id"], 42)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["all_results"], [result])
        self.assertEqual(payload["merged"], {"文號": "A-1"})
        self.assertTrue(payload["done"])

    def test_text_recognition_keeps_legacy_raw_fallback(self):
        payload = build_ocr_stream_payload(
            task_id=7,
            status="completed",
            progress=100,
            progress_message="辨識完成",
            error_message=None,
            result_data={
                "results": [
                    {
                        "page": 1,
                        "total": 1,
                        "success": True,
                        "data": None,
                        "raw": "公文全文",
                    }
                ]
            },
        )
        self.assertIsNone(payload["data"])
        self.assertEqual(payload["raw"], "公文全文")

    def test_terminal_failure_matches_legacy_success_false_contract(self):
        payload = build_ocr_stream_payload(
            task_id=9,
            status="failed",
            progress=0,
            progress_message="辨識失敗",
            error_message="模型錯誤",
            result_data={"results": []},
        )
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "模型錯誤")
        self.assertTrue(payload["done"])


if __name__ == "__main__":
    unittest.main()
