from __future__ import annotations

import json
from pathlib import Path

import pytest

from snoc_agent.cli.commands import replay_directory
from snoc_agent.config import Settings

FIXTURES = Path("tests/fixtures/emails")


@pytest.mark.parametrize(
    ("directory", "scenario", "expected_steps", "expected_last_detail"),
    [
        ("scenario_g_mixed_reply", None, 2, ""),
        ("scenario_h_corrections", "before-execution", 2, ""),
        ("scenario_h_corrections", "after-execution", 2, ""),
        ("scenario_i_correlation_markers", None, 4, "correlation conflict"),
    ],
)
def test_manifest_scenarios_seed_their_own_state(
    capsys,
    tmp_path,
    directory: str,
    scenario: str | None,
    expected_steps: int,
    expected_last_detail: str,
) -> None:
    replay_directory(
        Settings(raw_eml_directory=tmp_path / directory),
        FIXTURES / directory,
        scenario=scenario,
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["dry_run"] is True
    assert len(payload["results"]) == expected_steps
    assert payload["results"][-1]["detail"] == expected_last_detail
    assert all(result["seeded_state"] for result in payload["results"])
