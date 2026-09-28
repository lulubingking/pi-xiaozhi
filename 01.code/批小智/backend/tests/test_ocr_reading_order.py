import unittest

from backend.app.ocr_reading_order import sort_ocr_blocks_reading_order


class OcrReadingOrderTests(unittest.TestCase):
    def test_reorders_handwritten_lines_by_page_coordinates(self):
        blocks = [
            {"block_index": 13, "text_raw": "第二行续文", "box": [3259, 3263, 5749, 3557]},
            {"block_index": 14, "text_raw": "第一行开头", "box": [3294, 3131, 5778, 3400]},
            {"block_index": 15, "text_raw": "第三行", "box": [3259, 3398, 5795, 3716]},
            {"block_index": 16, "text_raw": "第四行", "box": [3285, 3629, 4361, 3827]},
        ]

        ordered = sort_ocr_blocks_reading_order(blocks)

        self.assertEqual([item["text_raw"] for item in ordered], ["第一行开头", "第二行续文", "第三行", "第四行"])

    def test_keeps_right_aligned_score_with_question_heading_row(self):
        blocks = [
            {"block_index": 1, "text_raw": "（4分）", "box": [800, 105, 900, 195]},
            {"block_index": 2, "text_raw": "18. 分析人物形象", "box": [100, 100, 600, 200]},
            {"block_index": 3, "text_raw": "学生答案", "box": [100, 220, 700, 320]},
        ]

        ordered = sort_ocr_blocks_reading_order(blocks)

        self.assertEqual([item["text_raw"] for item in ordered], ["18. 分析人物形象", "（4分）", "学生答案"])

    def test_reads_clear_two_column_page_column_by_column(self):
        blocks = [
            {"block_index": 1, "text_raw": "页面标题", "box": [0, 0, 1000, 40]},
            {"block_index": 2, "text_raw": "左栏第一行", "box": [50, 100, 400, 130]},
            {"block_index": 3, "text_raw": "右栏第一行", "box": [600, 100, 950, 130]},
            {"block_index": 4, "text_raw": "左栏第二行", "box": [50, 200, 400, 230]},
            {"block_index": 5, "text_raw": "右栏第二行", "box": [600, 200, 950, 230]},
        ]

        ordered = sort_ocr_blocks_reading_order(blocks)

        self.assertEqual([item["text_raw"] for item in ordered], ["页面标题", "左栏第一行", "左栏第二行", "右栏第一行", "右栏第二行"])

    def test_falls_back_to_existing_order_when_any_block_has_no_coordinates(self):
        blocks = [
            {"block_index": 2, "text_raw": "模型顺序第二块", "box": [0, 100, 100, 130]},
            {"block_index": 1, "text_raw": "模型顺序第一块", "box": None},
        ]

        ordered = sort_ocr_blocks_reading_order(blocks)

        self.assertEqual([item["text_raw"] for item in ordered], ["模型顺序第一块", "模型顺序第二块"])


if __name__ == "__main__":
    unittest.main()
