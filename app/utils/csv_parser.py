import csv
import io

from app.schemas.bulk import CsvRowError
from app.schemas.hospital import HospitalInput

REQUIRED_COLUMNS = {"name", "address", "phone"}


class CsvValidationError(Exception):
    def __init__(self, errors: list[CsvRowError]):
        self.errors = errors
        super().__init__("; ".join(e.error for e in errors))


def parse_and_validate_csv(raw: bytes, max_rows: int) -> list[HospitalInput]:
    errors: list[CsvRowError] = []

    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        raise CsvValidationError([CsvRowError(error="CSV file is empty")])

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise CsvValidationError([CsvRowError(error="CSV file has no header row")])

    header = {h.strip() for h in reader.fieldnames}
    missing = REQUIRED_COLUMNS - header
    extra = header - REQUIRED_COLUMNS
    if missing:
        raise CsvValidationError(
            [CsvRowError(error=f"CSV header is missing required column(s): {', '.join(sorted(missing))}")]
        )
    if extra:
        raise CsvValidationError(
            [CsvRowError(error=f"CSV header contains unsupported column(s): {', '.join(sorted(extra))}")]
        )

    rows: list[HospitalInput] = []
    row_num = 0
    for raw_row in reader:
        row_num += 1

        if row_num > max_rows:
            raise CsvValidationError(
                [CsvRowError(error=f"CSV contains more than the maximum allowed {max_rows} hospital rows")]
            )

        name = (raw_row.get("name") or "").strip()
        address = (raw_row.get("address") or "").strip()
        phone = (raw_row.get("phone") or "").strip() or None

        if not name:
            errors.append(CsvRowError(row=row_num, error="'name' is required and cannot be blank"))
        if not address:
            errors.append(CsvRowError(row=row_num, error="'address' is required and cannot be blank"))

        rows.append(HospitalInput(row=row_num, name=name, address=address, phone=phone))

    if row_num == 0:
        raise CsvValidationError([CsvRowError(error="CSV file contains no hospital data rows")])

    if errors:
        raise CsvValidationError(errors)

    return rows
