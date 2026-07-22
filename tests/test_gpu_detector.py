import unittest
from unittest.mock import patch

from manager import gpu_detector


LEGACY_NVIDIA_SMI = """
| NVIDIA-SMI 572.83       Driver Version: 572.83       CUDA Version: 12.8 |
"""

WINDOWS_610_NVIDIA_SMI = """
| NVIDIA-SMI 610.62       KMD Version: 610.62       CUDA UMD Version: 13.3 |
"""


class GpuDetectorTests(unittest.TestCase):
    def test_parses_legacy_nvidia_smi_labels(self):
        self.assertEqual(gpu_detector._parse_cuda_version(LEGACY_NVIDIA_SMI), "12.8")
        self.assertEqual(gpu_detector._parse_driver_version(LEGACY_NVIDIA_SMI), "572.83")

    def test_parses_windows_610_kmd_and_cuda_umd_labels(self):
        self.assertEqual(gpu_detector._parse_cuda_version(WINDOWS_610_NVIDIA_SMI), "13.3")
        self.assertEqual(gpu_detector._parse_driver_version(WINDOWS_610_NVIDIA_SMI), "610.62")

    @patch.object(
        gpu_detector,
        "_query_gpu_identity",
        return_value=("NVIDIA GeForce RTX 5090 Laptop GPU", "610.62"),
    )
    @patch.object(gpu_detector, "_run_nvidia_smi", return_value=WINDOWS_610_NVIDIA_SMI)
    def test_detects_rtx_5090_with_cuda_umd_output(self, _run_smi, _query_identity):
        result = gpu_detector.detect_gpu_info()

        self.assertTrue(result["has_nvidia"])
        self.assertEqual(result["gpu_name"], "NVIDIA GeForce RTX 5090 Laptop GPU")
        self.assertEqual(result["driver_version"], "610.62")
        self.assertEqual(result["cuda_version"], "13.3")
        self.assertEqual(result["compute_platform"], "cu128")
        self.assertEqual(result["pytorch_index_url"], "https://download.pytorch.org/whl/cu128")

    @patch.object(
        gpu_detector,
        "_query_gpu_identity",
        return_value=("NVIDIA Test GPU", "999.1"),
    )
    @patch.object(gpu_detector, "_run_nvidia_smi", return_value="NVIDIA-SMI custom output")
    def test_reports_gpu_present_when_cuda_label_is_unknown(self, _run_smi, _query_identity):
        result = gpu_detector.detect_gpu_info()

        self.assertTrue(result["has_nvidia"])
        self.assertEqual(result["gpu_name"], "NVIDIA Test GPU")
        self.assertEqual(result["cuda_version"], "N/A")
        self.assertEqual(result["compute_platform"], "cpu")


if __name__ == "__main__":
    unittest.main()
