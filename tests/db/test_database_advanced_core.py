import pytest

from navig.commands.database_advanced import _validate_sql_identifier

pytestmark = pytest.mark.integration


def test_validate_sql_identifier_allows_legitimate_substring_or():
    assert _validate_sql_identifier("orders", "table") is True


def test_validate_sql_identifier_rejects_exact_reserved_keyword():
    with pytest.raises(ValueError, match="reserved SQL keyword"):
        _validate_sql_identifier("drop", "table")


def test_validate_sql_identifier_still_rejects_invalid_chars():
    with pytest.raises(ValueError, match="Only alphanumeric characters and underscores"):
        _validate_sql_identifier("users;", "table")
