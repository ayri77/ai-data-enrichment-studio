"""Build a larger test workbook in the layout of the two bundled samples.

    uv run python -m scripts.scale_workbook --case capitals --rows 500
    uv run python -m scripts.scale_workbook --case mountains --rows 100 --output my.xlsx

The file goes to the ignored `artifacts/scale/` unless `--output` is given. Its
columns and sheet match `samples/<case>.xlsx`, so the example instruction and
the prepared plan apply unchanged. Names come from `scripts.scale_names`.

Capitals rows are pairs of different capitals. `--distinct` limits how many
different capitals take part, which sets the number of different lookups: 500
rows over 40 capitals ask the source about 40 places, 500 rows over 190 about
190. Mountains rows are one name each, so the list length is the ceiling;
`--repeat` cycles the list beyond it, and repeated names are answered from the
run's own memory rather than by the source.

Entries whose country name the application does not recognize ("Russia",
"Ivory Coast", ...) are left out by default, because one such row currently
fails the whole run before any request is made. `--keep-unrecognized` puts them
back to reproduce that.
"""

from __future__ import annotations

import argparse
import random
from itertools import combinations
from pathlib import Path

from openpyxl import Workbook
from scripts.scale_names import CAPITALS, MOUNTAINS

from ai_data_enrichment.resolution.geography import normalize_country_code

HEADERS = {
    "capitals": ("Capital_From", "Country_From", "Capital_To", "Country_To", "distance"),
    "mountains": ("Mountain", "Country", "height"),
}
SEED = 20261112


def recognized(country: str) -> bool:
    """Whether the application accepts every country named in the cell."""
    try:
        for part in country.split("/"):
            normalize_country_code(part)
    except LookupError:
        return False
    return True


def names(case: str, keep_unrecognized: bool = False) -> tuple[tuple[str, str], ...]:
    entries = CAPITALS if case == "capitals" else MOUNTAINS
    return tuple(entry for entry in entries if keep_unrecognized or recognized(entry[1]))


def left_out(case: str) -> list[str]:
    """Country cells of the entries `names` drops by default."""
    entries = CAPITALS if case == "capitals" else MOUNTAINS
    return sorted({country for _, country in entries if not recognized(country)})


def capital_rows(
    rows: int, distinct: int | None, pool: tuple[tuple[str, str], ...]
) -> list[tuple[str, str, str, str]]:
    pool = pool[: distinct or len(pool)]
    if len(pool) < 2:
        raise ValueError("Pairs need at least two capitals.")
    pairs = list(combinations(pool, 2))
    if rows > len(pairs):
        raise ValueError(
            f"{len(pool)} capitals give {len(pairs)} different pairs; {rows} rows were asked for."
        )
    # A fixed shuffle spreads every capital over the table and keeps runs comparable.
    random.Random(SEED).shuffle(pairs)
    return [(a[0], a[1], b[0], b[1]) for a, b in pairs[:rows]]


def mountain_rows(
    rows: int, repeat: bool, pool: tuple[tuple[str, str], ...]
) -> list[tuple[str, str]]:
    if rows > len(pool) and not repeat:
        raise ValueError(
            f"The list has {len(pool)} mountains; pass --repeat to cycle it for {rows} rows."
        )
    return [pool[index % len(pool)] for index in range(rows)]


def build_rows(
    case: str,
    rows: int,
    *,
    distinct: int | None = None,
    repeat: bool = False,
    keep_unrecognized: bool = False,
) -> list[tuple[str, ...]]:
    if rows < 1:
        raise ValueError("At least one row is needed.")
    if case not in HEADERS:
        raise ValueError(f"Unknown case: {case}")
    pool = names(case, keep_unrecognized)
    if case == "capitals":
        return list(capital_rows(rows, distinct, pool))
    return list(mountain_rows(rows, repeat, pool))


def distinct_lookups(case: str, rows: list[tuple[str, ...]]) -> int:
    """How many different places the source will be asked about."""
    if case == "capitals":
        return len({row[:2] for row in rows} | {row[2:4] for row in rows})
    return len(set(rows))


def write_workbook(path: Path, case: str, rows: list[tuple[str, ...]]) -> str:
    """Write the table and return its range, e.g. ``A1:E501``."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Sheet1"
    sheet.append(HEADERS[case])
    for row in rows:
        sheet.append((*row, None))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()
    last_column = "ABCDEFG"[len(HEADERS[case]) - 1]
    return f"A1:{last_column}{len(rows) + 1}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--case", choices=sorted(HEADERS), required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--distinct", type=int, help="capitals only: how many capitals take part")
    parser.add_argument("--repeat", action="store_true", help="mountains only: cycle the list")
    parser.add_argument(
        "--keep-unrecognized",
        action="store_true",
        help="keep entries whose country name the application does not recognize",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        rows = build_rows(
            args.case,
            args.rows,
            distinct=args.distinct,
            repeat=args.repeat,
            keep_unrecognized=args.keep_unrecognized,
        )
    except ValueError as error:
        parser.error(str(error))
    path = args.output or Path(f"artifacts/scale/{args.case}-{args.rows}.xlsx")
    range_ref = write_workbook(path, args.case, rows)
    print(f"{path}: {len(rows)} rows, range {range_ref},")
    print(f"{distinct_lookups(args.case, rows)} different places to look up.")
    if not args.keep_unrecognized and left_out(args.case):
        print(f"Left out (country not recognized): {', '.join(left_out(args.case))}")


if __name__ == "__main__":
    main()
