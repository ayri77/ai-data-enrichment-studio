"""Print access codes for visitors of a hosted demo.

Run it on your own machine; the codes are made there and shown only to you:

    uv run python -m scripts.make_access_codes --count 10
    uv run python -m scripts.make_access_codes --count 5 --start 11
    uv run python -m scripts.make_access_codes --labels anna,team-b --admin

The first block is the value for `ENRICHMENT_ACCESS_CODES`; to add visitors
later, run it again with `--start` and append the new entries to the value you
already have, separated by a comma. The second block is the same list to hand
out, one visitor per line. `--admin` also prints a value for
`ENRICHMENT_ADMIN_CODE`, which opens the usage summary.
"""

import argparse
import secrets

from ai_data_enrichment.hosted import visitor_codes


def make(labels: list[str]) -> dict[str, str]:
    """A fresh code for every label, in the form the server accepts."""
    codes = {label: secrets.token_urlsafe(12) for label in labels}
    # The server reads its own format back: a label it would refuse fails here.
    assert visitor_codes(",".join(f"{label}:{code}" for label, code in codes.items())) == codes
    return codes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=10, help="how many visitors")
    parser.add_argument("--start", type=int, default=1, help="number of the first visitor")
    parser.add_argument("--prefix", default="guest", help="label before the number")
    parser.add_argument("--labels", help="comma-separated labels instead of numbered ones")
    parser.add_argument("--admin", action="store_true", help="also print an admin code")
    args = parser.parse_args()
    labels = (
        [label.strip() for label in args.labels.split(",") if label.strip()]
        if args.labels
        else [f"{args.prefix}{number:02d}" for number in range(args.start, args.start + args.count)]
    )
    try:
        codes = make(labels)
    except RuntimeError as error:
        parser.error(str(error))
    print("ENRICHMENT_ACCESS_CODES (append to the value you already have, after a comma):\n")
    print(",".join(f"{label}:{code}" for label, code in codes.items()))
    print("\nTo hand out:\n")
    for label, code in codes.items():
        print(f"  {label:<16} {code}")
    if args.admin:
        print(f"\nENRICHMENT_ADMIN_CODE:\n\n{secrets.token_urlsafe(18)}")


if __name__ == "__main__":
    main()
