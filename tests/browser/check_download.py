"""Check actual browser-downloaded artifacts, not a second API execution."""

import sys
from copy import copy
from math import acos, cos, radians, sin
from typing import cast

from openpyxl import load_workbook

name, result_path = sys.argv[1:]
source = load_workbook(f"samples/{name}.xlsx")
result = load_workbook(result_path)
try:
    for sheet in source:
        for row in sheet:
            for cell in row:
                actual = result[sheet.title][cell.coordinate]
                if cell.value is not None:
                    assert actual.value == cell.value
                assert actual.number_format == cell.number_format
                assert actual.font == copy(cell.font)
                assert actual.fill == copy(cell.fill)
    sheet = result["Sheet1"]
    if name == "capitals":
        a, b = radians(51.50853), radians(41.89193)
        delta = radians(-0.12574 - 12.51133)
        distance = 6371 * acos(sin(a) * sin(b) + cos(a) * cos(b) * cos(delta))
        assert abs(cast(float, sheet["E3"].value) - distance) < 0.01
        assert sheet["E2"].value is None
        assert sheet["C11"].value == "Istanbul"
        assert sheet["D11"].value == "Turkey"
    else:
        assert sheet["C2"].value == 8848.86
        assert sheet["C11"].value is None
finally:
    source.close()
    result.close()
