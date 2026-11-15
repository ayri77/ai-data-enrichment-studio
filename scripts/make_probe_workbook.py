"""Build samples/probe/mountains_probe.xlsx for scripts/experiment_web_lookup.py.

The first ten rows are the mountains of samples/mountains.xlsx with the heights of
samples/check/mountains_enriched.xlsx as the expected value. Five more rows carry
invented names of mountains that do not exist; their expected cell is empty, because
the correct outcome for them is no answer.

    uv run python -m scripts.make_probe_workbook
"""

from pathlib import Path

from openpyxl import Workbook, load_workbook

# Invented on purpose: none of these is a real place.
INVENTED = [
    ("Mount Quelbarith", "Nepal"),
    ("Kharnogorsk Peak", "Kyrgyzstan"),
    ("Tzadlungma", "Nepal/China"),
    ("Vorlenhorn", "Switzerland"),
    ("Mount Ashtrevel", "Canada"),
]


def build(
    source: Path = Path("samples/mountains.xlsx"),
    checked: Path = Path("samples/check/mountains_enriched.xlsx"),
    target: Path = Path("samples/probe/mountains_probe.xlsx"),
) -> Path:
    names = [row for row in load_workbook(source).active.iter_rows(min_row=2, values_only=True)]
    heights = {
        row[0]: row[2]
        for row in load_workbook(checked, data_only=True).active.iter_rows(
            min_row=2, values_only=True
        )
    }
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["Mountain", "Country", "expected_height_m"])
    for name, country, *_ in names:
        sheet.append([name, country, heights[name]])
    for name, country in INVENTED:
        sheet.append([name, country, None])
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(target)
    return target


if __name__ == "__main__":
    print(build())
