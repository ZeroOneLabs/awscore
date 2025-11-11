# ***REMOVED***/file_processing/validator/file_validator.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Optional
import csv
import re
import xml.etree.ElementTree as ET
from ***REMOVED*** import AutoLogger


class FileValidator(AutoLogger):
    """
    Validate CSV or XML files against a schema.

    Schema format (example):
    ```python
    {
        "root": "Report",
        "record": "Row",
        "columns": [
            {"name": "respondent_id", "required": True, "type": "str", "regex": "^R\\d+$"},
            {"name": "value", "required": True, "type": "int", "min": 0, "max": 1000}
        ]
    }
    ```
    """

    def __init__(self, schema: Dict[str, Any]):
        """
        Initialize validator with schema.

        Args:
            schema: Validation rules for CSV/XML.
        """
        super().__init__()
        self.schema = schema

    # --------------------------------------------------------------------- #
    # CSV Validation
    # --------------------------------------------------------------------- #

    def validate_csv(self, path: Path) -> List[str]:
        """
        Validate a CSV file against the schema.

        Args:
            path: Path to the CSV file.

        Returns:
            List of error messages (empty if valid).
        """
        errors: List[str] = []
        expected_cols = {c["name"] for c in self.schema.get("columns", [])}

        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not expected_cols.issubset(reader.fieldnames or []):
                missing = expected_cols - set(reader.fieldnames or [])
                errors.append(f"Header missing columns: {missing}")
                return errors  # Can't validate rows

            for row_num, row in enumerate(reader, start=2):
                errors.extend(self._validate_row(row, row_num))

        self.log.info("CSV validation complete", extra={"path": str(path), "errors": len(errors)})
        return errors

    # --------------------------------------------------------------------- #
    # XML Validation
    # --------------------------------------------------------------------- #

    def validate_xml(self, path: Path) -> List[str]:
        """
        Validate an XML file against the schema.

        Schema must define:
            - ``root``: Root element name
            - ``record``: Repeating record element
            - ``columns``: List of field definitions

        Args:
            path: Path to the XML file.

        Returns:
            List of error messages (empty if valid).

        Example:
            >>> schema = {
            ...     "root": "Reports",
            ...     "record": "Report",
            ...     "columns": [
            ...         {"name": "respondent_id", "required": True, "regex": "^R\\d+$"},
            ...         {"name": "value", "type": "int", "min": 0}
            ...     ]
            ... }
            >>> validator = FileValidator(schema)
            >>> errors = validator.validate_xml(Path("report.xml"))
        """
        errors: List[str] = []

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except ET.ParseError as e:
            errors.append(f"XML parse error: {e}")
            self.log.error("XML parsing failed", extra={"path": str(path), "error": str(e)})
            return errors

        schema_root = self.schema.get("root")
        schema_record = self.schema.get("record")

        if schema_root and root.tag != schema_root:
            errors.append(f"Root element must be <{schema_root}>, got <{root.tag}>")
            return errors

        if not schema_record:
            errors.append("Schema missing 'record' element name")
            return errors

        record_elements = root.findall(schema_record)
        if not record_elements:
            errors.append(f"No <{schema_record}> records found")
            return errors

        for idx, elem in enumerate(record_elements, start=1):
            row_data: Dict[str, str] = {}
            for child in elem:
                row_data[child.tag] = (child.text or "").strip()

            errors.extend(self._validate_row(row_data, idx, source="XML"))

        self.log.info("XML validation complete", extra={"path": str(path), "records": len(record_elements), "errors": len(errors)})
        return errors

    # --------------------------------------------------------------------- #
    # Shared Row Validation
    # --------------------------------------------------------------------- #

    def _validate_row(self, row: Dict[str, str], row_num: int, source: str = "CSV") -> List[str]:
        """
        Validate a single row (CSV or XML record) against schema.

        Args:
            row: Dictionary of field_name -> value.
            row_num: Row/record number (for error reporting).
            source: "CSV" or "XML".

        Returns:
            List of error messages for this row.
        """
        errors: List[str] = []

        for col in self.schema.get("columns", []):
            name = col["name"]
            value = row.get(name)

            # Required field
            if col.get("required") and (value is None or value == ""):
                errors.append(f"{source} Row {row_num}: '{name}' is required")
                continue

            if value is None or value == "":
                continue  # Skip further checks if empty and not required

            # Type validation
            type_map = {
                "str": lambda v: isinstance(v, str),
                "int": lambda v: v.isdigit(),
                "float": lambda v: self._is_float(v),
            }
            col_type = col.get("type", "str")
            if col_type in type_map and not type_map[col_type](value):
                errors.append(f"{source} Row {row_num}: '{name}' must be {col_type}")

            # Regex
            if regex := col.get("regex"):
                if not re.fullmatch(regex, value):
                    errors.append(f"{source} Row {row_num}: '{name}' fails regex '{regex}'")

            # Numeric thresholds
            if col_type in ("int", "float"):
                try:
                    num_val = int(value) if col_type == "int" else float(value)
                    if "min" in col and num_val < col["min"]:
                        errors.append(f"{source} Row {row_num}: '{name}' below min {col['min']}")
                    if "max" in col and num_val > col["max"]:
                        errors.append(f"{source} Row {row_num}: '{name}' above max {col['max']}")
                except ValueError:
                    pass  # Already caught by type check

        return errors

    @staticmethod
    def _is_float(value: str) -> bool:
        """Check if string is valid float."""
        try:
            float(value)
            return True
        except ValueError:
            return False