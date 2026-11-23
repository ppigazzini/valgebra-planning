from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from valgebra_planning.cli import _address, _verifier, main
from valgebra_planning.servers import connect

PARCELS = str(Path(__file__).parent.parent / "examples" / "parcels")
NAIVE = """\
c1 = find_parcel(tracking=tracking)
c2 = book_return(parcel_id=c1.parcel.parcel_id, state=c1.parcel.state, weight_g=c1.parcel.weight_g)
return c2.label_id
"""


def test_plan_prints_every_task_and_its_links(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", PARCELS, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 8
    assert all(row["program"] is not None for row in rows)
    assert all(link["verdict"] == "proven" for row in rows for link in row["links"])
    weak = next(row for row in rows if row["task"] == "return a parcel")
    assert weak["strong"] is False
    assert weak["uncovered"][0]["examples"] == ["in_transit", "lost", "returned"]


def test_plan_reads_one_task_by_name_as_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", PARCELS, "--task", "customs for a parcel"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## customs for a parcel\n")
    assert "weak; 3 of 3 links proven" in out


def test_an_unknown_task_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", PARCELS, "--task", "no such task"]) == 2
    assert "no task named 'no such task'" in capsys.readouterr().err


def test_a_library_holding_a_key_no_reader_reads_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("tools.json", "tasks.json"):
        (tmp_path / name).write_text((Path(PARCELS) / name).read_text())
    task = {"name": "t", "inputs": {}, "goal": {}, "dept": 9}
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [task]}))
    assert main(["plan", str(tmp_path)]) == 2
    assert "task 't': 'dept' is no key it reads" in capsys.readouterr().err


def test_plan_takes_a_depth_and_says_why_no_program_was_found(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["plan", PARCELS, "--task", "settle a parcel", "--depth", "1"]) == 1
    assert "no program within the bound of 1 calls and tests, which cut" in capsys.readouterr().out
    assert main(["plan", PARCELS, "--task", "settle a parcel"]) == 0


def test_critique_refuses_the_naive_chain_and_accepts_the_planned_program(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    naive = tmp_path / "naive.py"
    naive.write_text(NAIVE, encoding="utf-8")
    assert main(["critique", PARCELS, "--task", "return a parcel", str(naive)]) == 1
    out = capsys.readouterr().out
    assert "refuted: c1.parcel.state <= book_return.state" in out
    assert main(["plan", PARCELS, "--task", "notify a parcel's contact", "--json"]) == 0
    planned = tmp_path / "planned.py"
    planned.write_text(json.loads(capsys.readouterr().out)[0]["program"], encoding="utf-8")
    assert main(["critique", PARCELS, "--task", "notify a parcel's contact", str(planned)]) == 0
    assert capsys.readouterr().out.endswith("accepted\n")


def test_a_program_the_reader_refuses_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import os\n", encoding="utf-8")
    assert main(["critique", PARCELS, "--task", "return a parcel", str(bad)]) == 2
    assert str(bad) in capsys.readouterr().err


# `--server` is split as a POSIX shell splits it, so the interpreter's path is quoted.
SERVER = shlex.join([sys.executable, str(Path(__file__).parent / "parcel_server.py")])


def _command(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run the command line in a process of its own, as a user does, its server a child of it."""
    entry = "from valgebra_planning.cli import main; raise SystemExit(main())"
    return subprocess.run(  # noqa: S603 - this interpreter, this package
        [sys.executable, "-c", entry, *argv], capture_output=True, text=True, check=False
    )


def test_run_plans_and_runs_a_task_against_a_live_server() -> None:
    inputs = '{"tracking": "AB123456789IT"}'
    argv = ["run", PARCELS, "--task", "settle a parcel", "--inputs", inputs, "--server", SERVER]
    ran = _command(*argv, "--json")
    assert ran.returncode == 0, ran.stderr
    row = json.loads(ran.stdout)
    assert row["strong"] is True
    assert row["refused"] is None
    assert row["broken"] == []
    assert row["value"].startswith("lbl_")


def test_plan_reads_the_tools_of_a_live_server(capsys: pytest.CaptureFixture[str]) -> None:
    live = _command("plan", PARCELS, "--task", "return a parcel", "--server", SERVER, "--json")
    assert live.returncode == 0, live.stderr
    assert main(["plan", PARCELS, "--task", "return a parcel", "--json"]) == 0
    assert json.loads(live.stdout) == json.loads(capsys.readouterr().out)


def test_check_finds_no_vacuous_specification_in_the_parcels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check", PARCELS]) == 0
    assert capsys.readouterr().out == "no specification holds vacuously\n"


def test_check_notes_a_contract_wider_than_its_keywords_and_exits_on_vacuity_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    library = tmp_path / "library"
    shutil.copytree(PARCELS, library)
    listing = json.loads((library / "tools.json").read_text())
    for tool in listing["tools"]:
        if tool["name"] == "book_return":
            tool["inputSchema"]["properties"]["weight_g"].pop("maximum")
    (library / "tools.json").write_text(json.dumps(listing), encoding="utf-8")
    assert main(["check", str(library)]) == 0
    assert capsys.readouterr().out == (
        "no specification holds vacuously\n"
        "note: tool book_return: book_return.weight_g has no upper bound\n"
    )
    assert main(["check", str(library), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "where": "tool book_return",
            "finding": "book_return.weight_g has no upper bound",
            "certainty": "proven",
            "kind": "wide",
        }
    ]


def test_privileges_exits_1_on_an_expansion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps({"send": [[1, 0, {"to": {"const": "ann"}}, 0]]}), encoding="utf-8")
    new.write_text(json.dumps({"send": [[1, 0, {}, 0]]}), encoding="utf-8")
    assert main(["privileges", str(new), str(old)]) == 0
    assert capsys.readouterr().out == "send: narrowing\n"
    assert main(["privileges", str(old), str(new)]) == 1
    assert capsys.readouterr().out.startswith("send: expansion, allowing ")


def test_stubs_prints_python_a_model_writes_against(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stubs", PARCELS]) == 0
    out = capsys.readouterr().out
    assert "def find_parcel(*, tracking: str) -> dict[str, Any]:" in out


def test_a_live_server_s_results_are_read_closed_where_the_library_says_so(tmp_path: Path) -> None:
    for name in ("tools.json", "tasks.json", "policies.json", "flows.json"):
        (tmp_path / name).write_text((Path(PARCELS) / name).read_text())
    listing = json.loads((tmp_path / "tools.json").read_text())
    (tmp_path / "tools.json").write_text(json.dumps({**listing, "closedResults": True}))
    live = _command(
        "plan", str(tmp_path), "--task", "return a parcel", "--server", SERVER, "--json"
    )
    assert live.returncode == 0, live.stderr
    assert "from the output of find_parcel read closed" in live.stdout
    inputs = '{"tracking": "AB123456789IT"}'
    argv = ["run", str(tmp_path), "--task", "settle a parcel", "--inputs", inputs]
    ran = _command(*argv, "--server", SERVER, "--json")
    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout)["refused"] is None


@pytest.mark.parametrize("closing", [False, True], ids=["as-declared", "read-closed"])
def test_the_proxy_reads_its_server_s_tools_as_its_library_does(
    tmp_path: Path, *, closing: bool
) -> None:
    listing = json.loads((Path(PARCELS) / "tools.json").read_text())
    (tmp_path / "tools.json").write_text(json.dumps({**listing, "closedResults": closing}))
    noting = shlex.join([sys.executable, str(Path(__file__).parent / "parcel_server.py"), "--note"])
    entry = "from valgebra_planning.cli import main; raise SystemExit(main())"
    command = [sys.executable, "-c", entry, "proxy", "--server", noting, "--library", str(tmp_path)]

    async def called() -> Any:
        async with connect(shlex.join(command)) as client:
            return await client.call_tool("find_parcel", {"tracking": "AB123456789IT"})

    result = asyncio.run(called())
    assert result.is_error is closing


@pytest.mark.parametrize("spelling", [False, True], ids=["as-published", "spelled-out"])
def test_the_proxy_lists_its_server_s_tools_spelled_out_on_request(*, spelling: bool) -> None:
    entry = "from valgebra_planning.cli import main; raise SystemExit(main())"
    argv = ["proxy", "--server", SERVER, *(["--spell-out"] if spelling else [])]

    async def listed() -> Any:
        async with connect(shlex.join([sys.executable, "-c", entry, *argv])) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}

    state = asyncio.run(listed())["open_claim"].input_schema["properties"]["state"]
    assert ("enum" in state) is spelling


@pytest.mark.parametrize(
    "argv",
    [
        ["--http", "127.0.0.1:8000"],
        ["--http", "127.0.0.1:8000", "--resource", "http://127.0.0.1:8000/mcp"],
        ["--resource", "http://127.0.0.1:8000/mcp"],
        ["--origin", "http://localhost:3000"],
    ],
    ids=["http-alone", "http-without-issuer", "resource-without-http", "origin-without-http"],
)
def test_the_proxy_serves_http_to_authenticated_clients_alone(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["proxy", "--server", "unused", *argv])
    assert stopped.value.code == 2
    assert "--http serves authenticated clients alone" in capsys.readouterr().err


def test_an_address_and_a_verifier_are_read_as_named() -> None:
    assert _address("127.0.0.1:8000") == ("127.0.0.1", 8000)
    assert _address("[::1]:8000") == ("::1", 8000)
    with pytest.raises(ValueError, match="names `HOST:PORT`"):
        _address("8000")
    with pytest.raises(ValueError, match="names `module:name`"):
        _verifier("tokens")
    with pytest.raises(TypeError, match="is no token verifier"):
        _verifier("json:loads")


def test_the_proxy_serves_http_to_a_client_holding_a_token_issued_for_it() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    resource = f"http://127.0.0.1:{port}/mcp"
    entry = "from valgebra_planning.cli import main; raise SystemExit(main())"
    argv = ["proxy", "--server", SERVER, "--http", f"127.0.0.1:{port}", "--resource", resource]
    argv += ["--issuer", "https://auth.example", "--verifier", "tokens:VERIFIER"]

    async def session() -> tuple[int, Any]:
        served = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            entry,
            *argv,
            cwd=Path(__file__).parent,
            env={**os.environ, "PROXY_RESOURCE": resource},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with httpx2.AsyncClient() as bare:
                for _ in range(200):
                    try:
                        status = (await bare.post(resource, json={})).status_code
                        break
                    except httpx2.TransportError:
                        await asyncio.sleep(0.1)
            held = {"Authorization": "Bearer agent"}
            async with (
                httpx2.AsyncClient(headers=held) as http,
                Client(streamable_http_client(resource, http_client=http)) as client,
            ):
                found = await client.call_tool("find_parcel", {"tracking": "AB123456789IT"})
        finally:
            served.terminate()
            await served.wait()
        return status, found

    status, found = asyncio.run(session())
    assert status == 401
    assert not found.is_error
