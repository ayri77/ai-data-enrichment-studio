"""Unit tests for dataset profile domain objects."""

import json

import pytest
from pydantic import ValidationError

from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)


@pytest.fixture
def sample_column_profile() -> ColumnProfile:
    """Provide a valid column profile for unit tests."""
    return ColumnProfile(
        name="Height",
        inferred_type=PrimitiveType.NUMBER,
        null_count=2,
        sample_values=[8848.86, 4808.73],
        warnings=["Type inferred from sampled values"],
    )


@pytest.fixture
def sample_dataset_profile(
    sample_column_profile: ColumnProfile,
) -> DatasetProfile:
    """Provide a valid dataset profile for unit tests."""
    return DatasetProfile(
        dataset_id="mountains",
        name="Mountain Elevations",
        row_count=100,
        columns=[sample_column_profile],
        sample_records=[
            {"Height": 8848.86},
            {"Height": 4808.73},
        ],
        schema_warnings=["Only a subset of rows was sampled"],
    )


@pytest.mark.parametrize(
    ("source_type", "inferred_type"),
    [
        (None, PrimitiveType.NUMBER),
        ("FLOAT", PrimitiveType.NUMBER),
        ("VARCHAR(255)", PrimitiveType.TEXT),
        ("INTEGER", PrimitiveType.INTEGER),
        ("VARCHAR(255)", PrimitiveType.MIXED),
    ],
)
def test_column_profile_preserves_source_and_inferred_types(
    source_type: str | None,
    inferred_type: PrimitiveType,
) -> None:
    """ColumnProfile should distinguish source and inferred types."""
    profile = ColumnProfile(
        name="height",
        source_type=source_type,
        inferred_type=inferred_type,
        nullable=None,
        null_count=2,
        sample_values=[8848.86, 4808.73],
        warnings=[],
    )
    assert profile.name == "height"
    assert profile.source_type == source_type
    assert profile.inferred_type == inferred_type
    assert profile.nullable is None
    assert profile.null_count == 2
    assert profile.sample_values == [8848.86, 4808.73]
    assert profile.warnings == []


def test_column_profile_rejects_negative_null_count() -> None:
    """ColumnProfile should reject negative null counts."""
    with pytest.raises(ValidationError) as exc_info:
        ColumnProfile(
            name="height",
            source_type=None,
            inferred_type=PrimitiveType.NUMBER,
            nullable=None,
            null_count=-1,
            sample_values=[8848.86, 4808.73],
            warnings=[],
        )
    assert exc_info.value.errors()[0]["loc"] == ("null_count",)


def test_column_profile_uses_default_values_for_optional_fields() -> None:
    """ColumnProfile should use default values for optional fields."""
    profile = ColumnProfile(
        name="height",
        inferred_type=PrimitiveType.NUMBER,
        null_count=0,
    )
    assert profile.source_type is None
    assert profile.nullable is None
    assert profile.sample_values == []
    assert profile.warnings == []


def test_column_profile_default_lists_are_independent() -> None:
    """Each ColumnProfile should have independent mutable default lists."""
    profile1 = ColumnProfile(name="height", inferred_type=PrimitiveType.NUMBER, null_count=0)
    profile2 = ColumnProfile(name="height", inferred_type=PrimitiveType.NUMBER, null_count=0)
    profile1.sample_values.append(8848.86)
    profile1.warnings.append("warning 1")
    assert profile1.sample_values == [8848.86]
    assert profile1.warnings == ["warning 1"]
    assert profile2.sample_values == []
    assert profile2.warnings == []


@pytest.mark.parametrize("name", ["", "   "])
def test_column_profile_rejects_empty_name(name: str) -> None:
    """ColumnProfile should reject empty or whitespace-only names."""
    with pytest.raises(ValidationError) as exc_info:
        ColumnProfile(name=name, inferred_type=PrimitiveType.NUMBER, null_count=0)
    assert exc_info.value.errors()[0]["loc"] == ("name",)


def test_column_profile_preserves_original_name() -> None:
    """ColumnProfile should preserve whitespace in valid column names."""
    profile = ColumnProfile(name=" Mountain Name ", inferred_type=PrimitiveType.TEXT, null_count=0)
    assert profile.name == " Mountain Name "


def test_dataset_profile_preserves_dataset_metadata() -> None:
    """DatasetProfile should preserve logical dataset metadata."""
    columns = [
        ColumnProfile(name="Mountain Name", inferred_type=PrimitiveType.TEXT, null_count=0),
        ColumnProfile(name="Height", inferred_type=PrimitiveType.NUMBER, null_count=0),
    ]
    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountain Elevations",
        row_count=100,
        columns=columns,
        sample_records=[],
        schema_warnings=[],
    )
    assert profile.dataset_id == "mountains"
    assert profile.name == "Mountain Elevations"
    assert profile.row_count == 100
    assert profile.columns == columns
    assert profile.sample_records == []
    assert profile.schema_warnings == []


def test_dataset_profile_rejects_negative_row_count() -> None:
    """DatasetProfile should reject negative row counts."""
    with pytest.raises(ValidationError) as exc_info:
        DatasetProfile(
            dataset_id="mountains",
            name="Mountain Elevations",
            row_count=-1,
            columns=[],
            sample_records=[],
            schema_warnings=[],
        )
    assert exc_info.value.errors()[0]["loc"] == ("row_count",)


@pytest.mark.parametrize("row_count", [None, 0, 100])
def test_dataset_profile_accepts_valid_row_counts(row_count: int | None) -> None:
    """DatasetProfile should accept unknown and non-negative row counts."""
    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountain Elevations",
        row_count=row_count,
        columns=[],
        sample_records=[],
        schema_warnings=[],
    )
    assert profile.row_count == row_count


@pytest.mark.parametrize("dataset_id", ["", "   ", "\t"])
def test_dataset_profile_rejects_empty_id(dataset_id: str) -> None:
    """DatasetProfile should reject empty or whitespace-only IDs."""
    with pytest.raises(ValidationError) as exc_info:
        DatasetProfile(
            dataset_id=dataset_id,
            name="Mountain Elevations",
            row_count=100,
            columns=[],
            sample_records=[],
            schema_warnings=[],
        )
    assert exc_info.value.errors()[0]["loc"] == ("dataset_id",)


@pytest.mark.parametrize("name", ["", "   "])
def test_dataset_profile_rejects_empty_name(name: str) -> None:
    """DatasetProfile should reject empty or whitespace-only names."""
    with pytest.raises(ValidationError) as exc_info:
        DatasetProfile(
            dataset_id="mountains",
            name=name,
            row_count=100,
            columns=[],
            sample_records=[],
            schema_warnings=[],
        )
    assert exc_info.value.errors()[0]["loc"] == ("name",)


def test_dataset_profile_uses_default_values_for_optional_fields() -> None:
    """DatasetProfile should use default values for optional fields."""
    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountain Elevations",
    )

    assert profile.row_count is None
    assert profile.columns == []
    assert profile.sample_records == []
    assert profile.schema_warnings == []


def test_dataset_profile_default_lists_are_independent() -> None:
    """Each DatasetProfile should have independent mutable default lists."""
    profile1 = DatasetProfile(dataset_id="mountains", name="Mountain Elevations")
    profile2 = DatasetProfile(dataset_id="mountains", name="Mountain Elevations")
    profile1.columns.append(
        ColumnProfile(name="Height", inferred_type=PrimitiveType.NUMBER, null_count=0)
    )
    profile1.sample_records.append({"height": 8848.86})
    profile1.schema_warnings.append("warning 1")
    assert profile1.columns == [
        ColumnProfile(name="Height", inferred_type=PrimitiveType.NUMBER, null_count=0)
    ]
    assert profile1.sample_records == [{"height": 8848.86}]
    assert profile1.schema_warnings == ["warning 1"]
    assert profile2.columns == []
    assert profile2.sample_records == []
    assert profile2.schema_warnings == []


def test_dataset_profile_round_trips_json_data(
    sample_dataset_profile: DatasetProfile,
) -> None:
    """DatasetProfile should survive JSON-compatible serialization."""
    json_data = sample_dataset_profile.model_dump_json()
    restored = DatasetProfile.model_validate_json(json_data)

    assert restored == sample_dataset_profile
    assert json.loads(json_data)["columns"][0]["inferred_type"] == "Number"


def test_dataset_profile_rejects_invalid_nested_column_type(
    sample_dataset_profile: DatasetProfile,
) -> None:
    """DatasetProfile should reject invalid types in nested columns."""
    dict_data = sample_dataset_profile.model_dump(mode="json")
    dict_data["columns"][0]["inferred_type"] = "InvalidType"

    with pytest.raises(ValidationError) as exc_info:
        DatasetProfile.model_validate(dict_data)

    assert exc_info.value.errors()[0]["loc"] == (
        "columns",
        0,
        "inferred_type",
    )


def test_dataset_profile_rejects_non_json_sample_values() -> None:
    """DatasetProfile should reject non-JSON values in sample records."""
    with pytest.raises(ValidationError) as exc_info:
        DatasetProfile.model_validate(
            {
                "dataset_id": "mountains",
                "name": "Mountain Elevations",
                "sample_records": [{"height": object()}],
            }
        )

    assert exc_info.value.errors()[0]["loc"] == (
        "sample_records",
        0,
        "height",
    )


def test_column_profile_rejects_non_json_sample_values() -> None:
    """ColumnProfile should reject non-JSON sample values."""
    with pytest.raises(ValidationError) as exc_info:
        ColumnProfile.model_validate(
            {
                "name": "Height",
                "inferred_type": "Number",
                "null_count": 0,
                "sample_values": [object()],
            }
        )

    assert exc_info.value.errors()[0]["loc"] == (
        "sample_values",
        0,
    )


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
)
def test_column_profile_rejects_non_finite_sample_values(
    value: float,
) -> None:
    """ColumnProfile should reject non-finite numeric samples."""
    with pytest.raises(ValidationError) as exc_info:
        ColumnProfile(
            name="Height",
            inferred_type=PrimitiveType.NUMBER,
            null_count=0,
            sample_values=[value],
        )

    assert exc_info.value.errors()[0]["loc"][:2] == (
        "sample_values",
        0,
    )


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
)
def test_dataset_profile_rejects_non_finite_sample_values(
    value: float,
) -> None:
    """DatasetProfile should reject non-finite numeric samples."""
    with pytest.raises(ValidationError) as exc_info:
        DatasetProfile(
            dataset_id="mountains",
            name="Mountain Elevations",
            sample_records=[{"height": value}],
        )

    assert exc_info.value.errors()[0]["loc"][:3] == (
        "sample_records",
        0,
        "height",
    )


def test_dataset_profile_accepts_nested_json_values() -> None:
    """DatasetProfile should preserve valid nested JSON sample values."""
    expected_records = [
        {
            "Mountain": "Everest",
            "Height": 8848.86,
            "Verified": True,
            "Comment": None,
            "Aliases": ["Sagarmatha", "Chomolungma"],
            "Coordinates": {
                "latitude": 27.9881,
                "longitude": 86.9250,
            },
        }
    ]

    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountain Elevations",
        sample_records=expected_records,
    )

    assert profile.model_dump(mode="json")["sample_records"] == expected_records
