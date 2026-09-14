from labgrid_tui.exec_.runner import client_available, run_capture, run_suspended


async def test_capture_streams_lines_and_exit_code() -> None:
    lines: list[str] = []
    code = await run_capture("sh -c 'echo one; echo two >&2; exit 3'", lines.append)
    assert code == 3
    assert "one" in lines
    assert "two" in lines  # stderr merged


async def test_capture_missing_binary() -> None:
    lines: list[str] = []
    code = await run_capture("definitely-not-a-real-binary-xyz", lines.append)
    assert code == 127
    assert any("not found" in line for line in lines)


def test_run_suspended_exit_code() -> None:
    assert run_suspended("sh -c 'exit 5'") == 5


def test_client_available_returns_bool() -> None:
    assert isinstance(client_available(), bool)
