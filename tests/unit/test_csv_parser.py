import pytest

from app.utils.csv_parser import CsvValidationError, parse_and_validate_csv
from tests.conftest import make_csv


def test_valid_csv_parses_all_rows():
    csv_bytes = make_csv(
        [("General Hospital", "123 Main St", "555-1234"), ("City Hospital", "456 Oak St", "")]
    )
    rows = parse_and_validate_csv(csv_bytes, max_rows=20)
    assert len(rows) == 2
    assert rows[0].row == 1
    assert rows[0].name == "General Hospital"
    assert rows[1].phone is None


def test_empty_file_is_invalid():
    with pytest.raises(CsvValidationError):
        parse_and_validate_csv(b"", max_rows=20)


def test_header_only_csv_is_invalid():
    with pytest.raises(CsvValidationError):
        parse_and_validate_csv(b"name,address,phone\n", max_rows=20)


def test_missing_required_column_is_invalid():
    csv_bytes = b"name,phone\nGeneral Hospital,555-1234\n"
    with pytest.raises(CsvValidationError) as exc_info:
        parse_and_validate_csv(csv_bytes, max_rows=20)
    assert "address" in exc_info.value.errors[0].error


def test_extra_column_is_rejected():
    csv_bytes = b"name,address,phone,extra\nGeneral Hospital,123 Main St,555-1234,oops\n"
    with pytest.raises(CsvValidationError) as exc_info:
        parse_and_validate_csv(csv_bytes, max_rows=20)
    assert "extra" in exc_info.value.errors[0].error


def test_blank_required_field_is_invalid():
    csv_bytes = b"name,address,phone\n,123 Main St,555-1234\n"
    with pytest.raises(CsvValidationError) as exc_info:
        parse_and_validate_csv(csv_bytes, max_rows=20)
    assert exc_info.value.errors[0].row == 1
    assert "name" in exc_info.value.errors[0].error


def test_more_than_max_rows_is_invalid():
    rows = [(f"Hospital {i}", f"{i} Main St", "") for i in range(21)]
    csv_bytes = make_csv(rows)
    with pytest.raises(CsvValidationError) as exc_info:
        parse_and_validate_csv(csv_bytes, max_rows=20)
    assert "maximum" in exc_info.value.errors[0].error.lower()


def test_exactly_max_rows_is_valid():
    rows = [(f"Hospital {i}", f"{i} Main St", "") for i in range(20)]
    csv_bytes = make_csv(rows)
    result = parse_and_validate_csv(csv_bytes, max_rows=20)
    assert len(result) == 20


def test_quoted_comma_in_address_is_handled():
    csv_bytes = b'name,address,phone\n"General Hospital","123 Main St, Suite 4",555-1234\n'
    rows = parse_and_validate_csv(csv_bytes, max_rows=20)
    assert rows[0].address == "123 Main St, Suite 4"


def test_surrounding_whitespace_is_stripped():
    csv_bytes = b"name,address,phone\n  General Hospital  ,  123 Main St  ,  555-1234  \n"
    rows = parse_and_validate_csv(csv_bytes, max_rows=20)
    assert rows[0].name == "General Hospital"
    assert rows[0].address == "123 Main St"
