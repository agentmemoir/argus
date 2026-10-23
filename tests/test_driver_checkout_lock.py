# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A second driver config is refused while the first config's phase holds the checkout.

The lock is on the checkout's git common directory, so two configs that name one
repository root share it. The holder stands in for a phase process. The refusal
names the holder's config basename and no host path.
"""

from __future__ import annotations

from pathlib import Path

from driver_checkout_support import make_checkout, run_as, start_holder, stop_holder

REFUSAL = (
    "argus: checkout is in use by driver config config-a.jsonc; "
    "give each driver its own checkout"
)


def test_a_second_config_is_refused_and_named_by_basename(tmp_path: Path) -> None:
    repo = make_checkout(tmp_path / "repo")
    holder = start_holder(repo, tmp_path / "a" / "config-a.jsonc")
    try:
        run = run_as(
            repo, tmp_path / "b" / "config-b.jsonc", "driver_checkout_report\n"
        )
    finally:
        stop_holder(holder)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == REFUSAL, run.stdout
    assert str(tmp_path) not in run.stdout


def test_take_is_refused_while_another_config_holds_the_lock(tmp_path: Path) -> None:
    repo = make_checkout(tmp_path / "repo")
    holder = start_holder(repo, tmp_path / "a" / "config-a.jsonc")
    try:
        run = run_as(
            repo,
            tmp_path / "b" / "config-b.jsonc",
            "driver_checkout_take && echo TOOK || echo REFUSED\n",
        )
    finally:
        stop_holder(holder)
    assert run.stdout.strip() == "REFUSED", run.stdout


def test_the_lock_is_free_once_the_holder_exits(tmp_path: Path) -> None:
    repo = make_checkout(tmp_path / "repo")
    holder = start_holder(repo, tmp_path / "a" / "config-a.jsonc")
    stop_holder(holder)
    run = run_as(
        repo,
        tmp_path / "b" / "config-b.jsonc",
        "driver_checkout_held && echo HELD || echo FREE\n",
    )
    assert run.stdout.strip() == "FREE", run.stdout


def test_the_tick_probe_keeps_nothing(tmp_path: Path) -> None:
    repo = make_checkout(tmp_path / "repo")
    probe = run_as(repo, tmp_path / "a" / "config-a.jsonc", "driver_checkout_held\n")
    assert probe.returncode == 1, probe.stderr
    take = run_as(
        repo,
        tmp_path / "b" / "config-b.jsonc",
        "driver_checkout_take && echo TOOK || echo REFUSED\n",
    )
    assert take.stdout.strip() == "TOOK", take.stdout


def test_the_lock_file_names_the_holder_config_basename(tmp_path: Path) -> None:
    repo = make_checkout(tmp_path / "repo")
    holder = start_holder(repo, tmp_path / "a" / "config-a.jsonc")
    try:
        lock = repo / ".git" / "argus-driver-checkout.lock"
        assert lock.read_text() == "config-a.jsonc\n"
    finally:
        stop_holder(holder)


def test_a_root_that_is_not_a_checkout_has_no_lock(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    run = run_as(
        root,
        tmp_path / "a" / "config-a.jsonc",
        "driver_checkout_held && echo HELD || echo FREE\n"
        "driver_checkout_take && echo TOOK || echo REFUSED\n",
    )
    assert run.stdout.split() == ["FREE", "TOOK"], run.stdout
