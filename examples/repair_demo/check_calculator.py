"""使用标准库检查加法行为，直接执行本文件即可，不依赖 pytest。"""

import unittest

from calculator import add


class AdditionTests(unittest.TestCase):
    """固定需求：正数、负数和零的加法；修复时保持这些检查不变。"""

    def test_positive_numbers(self) -> None:
        """无需 fixture，检查两个正数相加；结果不是 5 时失败，无返回值。"""
        self.assertEqual(add(2, 3), 5)

    def test_opposite_numbers(self) -> None:
        """无需 fixture，检查正负数相抵；结果不是 0 时失败，无返回值。"""
        self.assertEqual(add(-1, 1), 0)

    def test_zero(self) -> None:
        """无需 fixture，检查加零保持原值；结果不是 4 时失败，无返回值。"""
        self.assertEqual(add(4, 0), 4)


if __name__ == "__main__":
    unittest.main()
