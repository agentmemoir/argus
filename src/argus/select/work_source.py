#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The work source contract, and the registry that resolves a configured name.

A work source is wherever the driver gets its work. The driver asks the active
source what to run next and gets back the same result shape whatever answered,
so claiming, running, notifying, halting and writing results back stay identical
no matter which source supplied the item. That is the whole reason the concept
exists: the phases is one place work can come from, not the place work comes
from.

THE CONTRACT

``name``
    The name a machine configures. Today that is ``phases``.

``select(machine_id, reserve=...)``
    Return the next item to claim, and claim it, as a result dict. The states,
    the JSON shape and the exit codes are the driver's contract and are
    documented in next_phase.py, so they are deliberately not restated here.

WHAT A SOURCE MAY DO BEHIND THAT

List its candidates, filter or rank them, and report which ones are finished.
That is the work, and it belongs to the source: a source that ranks by label
and a source that reads a phase file have nothing in common beyond the two
members above.

WHY AN ABSTRACT CLASS AND NOT A ``typing.Protocol``

A Protocol is enforced by a static type checker and by nothing else, and this
tree has no type checker: no pyproject, no mypy or ruff configuration, no lint
target, no CI workflow. An abstract class is enforced when a source is
instantiated, which is the moment a mistake matters, and it documents the same
two members in code that actually runs. The rule this follows is the typing
specification's own: a protocol "would be used to model duck typing statically,
not explicitly at runtime".

Registration is an explicit table rather than packaging entry points, for the
same reason. These scripts are run by path out of a checkout and are not
installed as a distribution, so there is no metadata for a plugin system to
discover. Adding a source is one line in ``SOURCES``.
"""

import importlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from argus import conventions

#: The source a machine gets when it has configured none. Every machine runs
#: this today, so it is the default rather than a special case.
DEFAULT_SOURCE = "phases"

#: Configured name -> (module, class) implementing it. Held as text and
#: imported on demand because an implementation imports this module for the
#: contract: importing both ways at module scope would be a cycle.
SOURCES = {
    "phases": ("argus.phases.source", "PhasesSource"),
}


class WorkSourceError(RuntimeError):
    """The configured work cannot be served, and saying so is the point.

    An unknown source name, or a source whose work cannot be read, stops that
    machine's tick. Falling back to another source would hide the mistake
    behind progress, so there is no fallback here.
    """


class WorkSource(ABC):
    """What every work source must offer. See the module docstring."""

    name: str

    @abstractmethod
    def select(
        self, machine_id: str | None = None, *, reserve: bool = True
    ) -> dict[str, Any]:
        """Return the next item to claim as a result dict, claiming it.

        ``reserve=False`` reports what it would claim without writing to the
        board, which is what a dry run and a read-only inspection ask for.
        """


def source_names() -> list[str]:
    """Every configured name, sorted, for an error message that is useful."""
    return sorted(SOURCES)


def implementation(name: str) -> Any:
    """The class registered under ``name``, imported on demand."""
    module_name, class_name = SOURCES[name]
    return getattr(importlib.import_module(module_name), class_name)


def resolve(
    name: str | None, repo: str | Path, phases: str | Path | None = None
) -> Any:
    """Build the configured source, or raise WorkSourceError naming the known ones.

    ``name`` is None when a machine has configured nothing, which is the
    phases source and the behaviour every machine has today.
    """
    key = name or DEFAULT_SOURCE
    if key not in SOURCES:
        raise WorkSourceError(
            f"unknown work source {key!r}; this build knows {', '.join(source_names())}"
        )
    factory = implementation(key)
    try:
        return factory(Path(repo), phases)
    except WorkSourceError as exc:
        # A source that cannot read its work stops the tick the same way an
        # unknown name does, carrying the source's own words about why.
        raise WorkSourceError(f"work source {key!r}: {exc}") from exc


def self_test() -> None:
    previous = conventions.configure(
        {
            "repo-root": Path(),
            "pipeline": None,
            "phases.directory": Path(),
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "driver.runs": Path("runs"),
        }
    )
    try:
        _self_test()
    finally:
        conventions.configure(previous)


def _self_test() -> None:
    # Every registered name must be real: its module must import, its class
    # must exist, it must honour the contract, and the name a machine
    # configures must be the name it answers to. A source that is selectable
    # but unnameable in a log line is how a source becomes untraceable.
    for key in source_names():
        factory = implementation(key)
        assert issubclass(factory, WorkSource), (key, factory)
        assert factory.name == key, (key, factory.name)

    resolved = resolve(None, ".")
    assert isinstance(resolved, implementation("phases")), resolved
    assert resolved.name == "phases", resolved.name
    # No named folder means today's behaviour, not an error.
    assert resolve("phases", ".").phases == resolved.phases

    try:
        resolve("unknown-source", ".")
        refused = False
    except WorkSourceError as exc:
        refused = True
        # The message must name what is available, or the operator is left
        # guessing at a name they were never given.
        assert "phases" in str(exc), exc
    assert refused, "an unknown work source must stop the tick, not fall back"

    _self_test_second_source()


class _FixtureSource(WorkSource):
    """A second implementer, existing only so the registry can be proved live.

    At module scope because the registry holds names and imports classes by
    name: a source defined inside a function could never be reached by the table
    that is supposed to reach it. Never in ``SOURCES`` on its own, so no machine
    can configure it; the self-test registers and removes it.
    """

    name = "fixture"

    def __init__(self, repo: str | Path, setting: str | None = None) -> None:
        self.repo = Path(repo)
        self.setting = setting
        self.calls: list[tuple[str | None, bool]] = []

    def select(
        self, machine_id: str | None = None, *, reserve: bool = True
    ) -> dict[str, Any]:
        self.calls.append((machine_id, reserve))
        return {"state": "NO_PHASE", "fixture": True}


def _self_test_second_source():
    """A second registered source must be reachable, or the registry is prose.

    Only the phases source exists today, so every other way of exercising the
    contract — a machine naming something other than the default, a source
    answering from memory rather than reading a folder, the registry resolving
    to something other than the fallback — is untested until a second one lands.
    A fixture source is cheaper than waiting for one, and it is the only way to
    catch a table that lists names it cannot resolve or a contract a second
    implementer cannot meet. Both are stage 2's problem, discovered here
    instead of there.
    """
    SOURCES["fixture"] = ("argus.select.work_source", "_FixtureSource")
    try:
        assert "fixture" in source_names(), source_names()
        chosen = resolve("fixture", ".")
        assert chosen.name == "fixture", chosen.name
        assert chosen.select("host-1", reserve=False)["fixture"] is True, chosen
        assert chosen.calls == [("host-1", False)], chosen.calls
        # The folder argument reaches a source that ignores it, so the registry's
        # construction signature stays one shape for every source.
        assert resolve("fixture", ".", "anywhere").setting == "anywhere"
        # Removing it must make it unnameable again: an entry left behind would
        # be a source a machine could configure and never get.
        del SOURCES["fixture"]
        try:
            resolve("fixture", ".")
            refused = False
        except WorkSourceError as exc:
            refused = True
            assert "phases" in str(exc), exc
        assert refused, "an unregistered source must stop the tick"
    finally:
        SOURCES.pop("fixture", None)
    assert "fixture" not in source_names(), "the fixture source leaked"


__all__ = ["_FixtureSource"]
