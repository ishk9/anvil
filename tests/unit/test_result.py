from domain.models.result import Err, Ok


def test_ok_unwraps_value() -> None:
    result = Ok(42)
    assert result.is_ok()
    assert not result.is_err()
    assert result.unwrap() == 42
    assert result.unwrap_or(0) == 42


def test_ok_maps() -> None:
    assert Ok(2).map(lambda x: x * 3).unwrap() == 6


def test_err_holds_error_and_defaults() -> None:
    result: Err[str] = Err("boom")
    assert result.is_err()
    assert not result.is_ok()
    assert result.error == "boom"
    assert result.unwrap_or(99) == 99


def test_err_map_is_passthrough() -> None:
    result: Err[str] = Err("boom")
    assert result.map(lambda x: x).is_err()
