import unittest

from backend.scripts.ocr_runtime import objective_choice_candidate_windows


class ObjectiveChoiceCropTests(unittest.TestCase):
    def test_crop_after_open_parenthesis_uses_answer_line_geometry(self):
        windows = objective_choice_candidate_windows(
            [{"text_raw": "一项是（", "box": [0, 150, 263, 254]}],
            image_width=678,
            image_height=400,
        )

        self.assertEqual(windows, [{"box": [255, 110, 560, 320], "anchor_text": "一项是（"}])

    def test_non_answer_line_does_not_create_a_choice_crop(self):
        windows = objective_choice_candidate_windows(
            [{"text_raw": "一项是", "box": [0, 150, 220, 254]}],
            image_width=678,
            image_height=400,
        )

        self.assertEqual(windows, [])

    def test_candidate_crop_is_clamped_to_processed_image_bounds(self):
        windows = objective_choice_candidate_windows(
            [{"text_raw": "答案（", "box": [0, 20, 80, 60]}],
            image_width=120,
            image_height=80,
        )

        self.assertEqual(windows[0]["box"], [77, 5, 120, 80])


if __name__ == "__main__":
    unittest.main()
