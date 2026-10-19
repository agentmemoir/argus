# Argus development commands. Packaging is a separate step.
#
# uv is the toolchain for the tooling (see pyproject.toml): `make self-test` and
# the wheel run through `uv run`, which installs the project and the dev group.
# The driver itself still runs through the shell and the Python named in its
# configuration file.
PYTHON ?= python3
UV ?= uv
RUN := $(UV) run
PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
CONFIG ?= config.json
LINE_LIMIT := 350
# Files still above the limit while the migration splits them, one per session.
# Each split removes its file from this list; the list is empty when the
# migration ends. It is empty now: the last file was split.
LINE_EXEMPT :=

.PHONY: install sync lint fmt fmt-check typecheck typecheck-strict lines prose quick check dry-run clear-halt example-check \
        self-test identifiers leakscan noexecscan guard-faults wheel install-check
install:
	install -d "$(DESTDIR)$(BINDIR)"
	ln -sf "$(CURDIR)/bin/argus" "$(DESTDIR)$(BINDIR)/argus"
	@printf 'Installed argus in %s; add this folder to PATH.\n' "$(BINDIR)"

sync:
	$(UV) sync

# Lint and the one formatter (ruff, 88 columns). CI runs lint and fmt-check.
lint:
	$(RUN) ruff check .

fmt:
	$(RUN) ruff format .

fmt-check:
	$(RUN) ruff format --check .

# Type checking at mypy's default level (see pyproject.toml). CI runs it.
typecheck:
	$(RUN) mypy src/argus

# pyright, strict: the checker behind Pylance (see pyright.json). The editor
# uses the same level through .vscode/settings.json.
typecheck-strict:
	$(RUN) pyright --project pyright.json

# No Python file may exceed the line limit, comments and blank lines included.
# Nothing is exempt: a split removes its file here in the same change.
lines:
	@printf 'checking every Python file against the %s-line limit\n' '$(LINE_LIMIT)'
	@files=$$(find src tests -name '*.py' -type f 2>/dev/null); \
	if [ -z "$$files" ]; then printf 'no Python files found\n'; exit 1; fi; \
	printf '%s\n' "$$files" | xargs wc -l | awk -v lim=$(LINE_LIMIT) -v exempt='$(LINE_EXEMPT)' \
		'BEGIN { n = split(exempt, parts, " "); for (i = 1; i <= n; i++) if (parts[i] != "") skip[parts[i]] = 1 } \
		 $$NF == "total" { next } \
		 $$1 > lim { if ($$2 in skip) { printf "exempt for now: %s\n", $$2; next } \
			over = over $$0 "\n"; count++ } \
		 END { if (count) { printf "OVER LIMIT:\n%s", over; \
			printf "%d file(s) exceed the %s-line limit. Split before adding.\n", count, lim; \
			exit 1 } \
			printf "all files within %s lines\n", lim }'

# The prose rule: one paragraph per line, so the reader's viewer does the
# wrapping. A new line is only for a new paragraph, a list item, a heading,
# or a code block. This target fails on indented continuation lines and on
# consecutive prose lines outside fenced code blocks, HTML comments, and YAML
# frontmatter. It scans tracked plus untracked markdown files, so LICENSE
# (not markdown) is never scanned and no directory layout is assumed.
prose: ## Fail on hard-wrapped prose (one paragraph per line)
	@printf 'checking prose for hard-wrapped paragraphs\n'
	@$(RUN) python scripts/check_prose.py

# The static checks, no tests: the fast local gate before a push.
quick: lint fmt-check typecheck typecheck-strict lines prose

# self-test and guard-faults take many minutes, so they run in CI, which sets
# CI. They refuse to start anywhere else unless ARGUS_ALLOW_LOCAL=1 is set.
ci_only = @if [ -z "$$CI" ] && [ "$$ARGUS_ALLOW_LOCAL" != 1 ]; then printf 'make %s runs in CI; set ARGUS_ALLOW_LOCAL=1 to run it here\n' '$@' >&2; exit 1; fi

# A tick reads one configuration file, so the readiness report and the dry run
# read the file named by CONFIG. --self-test reads no file.
check:
	bin/argus --config "$(CONFIG)" --check

dry-run:
	bin/argus --config "$(CONFIG)" --dry-run

# Remove the halt and stall markers. The halt record beside them goes too, but
# only when the phase it names never started, which is the case `--resume`
# refuses by design: no run.json and no resume.json in the run directory. A
# resumable phase keeps its record, so a halt cannot be lost silently. Every
# removal is printed.
clear-halt:
	bin/argus --config "$(CONFIG)" --clear-halt

# The example must load and pass the readiness report on any machine. A real
# project names its own Glide command; `examples/check-bin` puts a stand-in
# first on PATH, because --check only asks whether the command resolves and a
# machine with no Glide installed must still be able to check the example. The
# runs folder is created here: it is git-ignored, and the driver needs the
# configured run-state folder to exist before it starts.
#
# The phases repository is a separate checkout, so this target builds one at
# examples/project/phases: a Git directory with a remote of its own, holding the
# example's committed work item. Only that generated Git state is git-ignored.
# The last step runs the code's own separation proof, so the example cannot ship
# a phases destination that matches the public repo-root or the board.
example-check:
	@mkdir -p examples/runs
	@if [ ! -d examples/project/phases/.git ]; then \
		git init -q --bare examples/runs/phases.git; \
		git init -q -b main examples/project/phases; \
		git -C examples/project/phases remote add origin "$(CURDIR)/examples/runs/phases.git"; \
	fi
	@git -C examples/project/phases add -A
	@git -C examples/project/phases rev-parse -q --verify HEAD >/dev/null 2>&1 \
		|| git -C examples/project/phases -c user.email=argus@example.invalid \
			-c user.name=argus commit -q -m 'example phases checkout'
	@PATH="$(CURDIR)/examples/check-bin:$$PATH" $(RUN) bin/argus --config examples/argus-config.jsonc --check
	@$(RUN) python -c 'from pathlib import Path; from argus import conventions; from argus.board.git import assert_public_separate; conventions.configure_from_file("examples/argus-config.jsonc"); board = conventions.board_repo(); privates = {"phases": conventions.phases_repo()}; privates.update({"board": board} if board else {}); assert_public_separate(Path(conventions.setting("repo-root")), privates); print("example destinations are separate: " + ", ".join(sorted(privates)))'

self-test:
	$(ci_only)
	$(RUN) bin/argus --self-test
	$(RUN) python tests/test_config.py
	$(RUN) python tests/test_entrypoint.py
	$(RUN) python tests/test_conventions.py
	$(RUN) python tests/test_phase_notifications.py
	$(RUN) python tests/test_identifiers.py
	$(RUN) python -m argus.select.group --self-test
	$(RUN) python -m argus.gitsync.main --self-test
	$(RUN) pytest -q tests/test_driver_maintenance.py tests/test_driver_maintenance_cli.py tests/test_driver_saved_publication.py tests/test_driver_stop_contract.py tests/test_board_branch.py tests/test_board_lease_policy.py tests/test_selftest_git_isolation.py tests/test_selftest_recovery.py tests/test_selftest_teardown.py
	$(RUN) pytest -q tests/test_config_overlay.py tests/test_idle_watcher.py tests/test_idle_tick_log.py tests/test_checkpoint_empty_stage.py tests/test_notify_target.py tests/test_selection_blocked.py tests/test_estimate.py

# The published folder must carry no identifier: no home path, no numeric
# account or channel id, and no notification default or literal that is not a
# documented placeholder. It runs inside self-test too, so a new identifier
# cannot be added to the package quietly.
identifiers:
	$(RUN) python tests/test_identifiers.py

# The published tree must carry no host path, no credential assignment, and no
# run state. The tests own the patterns; this target is how a push check and CI
# run them.
leakscan:
	$(RUN) pytest -q tests/test_no_host_leak.py tests/test_leak_patterns.py

# The noexec rule: a hook or stub CLI must run from a directory the host proved
# it executes, never a bare temp directory or a `tmp_path` file marked
# executable. The test is the rulebook; this target is how a push check and CI
# run it.
noexecscan:
	$(RUN) pytest -q tests/test_noexec_policy.py

guard-faults:
	$(ci_only)
	$(RUN) python tests/guard_faults.py

# The package: a wheel whose console script is `argus`, for `pip install .` and
# `uv tool install .`. `install` above stays the development link, so an edit is
# live without reinstalling. A wrong mapping produces a wheel with no files in
# it without an error, so this target checks the wheel it just built against
# the package folder.
wheel:
	$(UV) build --wheel
	@$(RUN) python scripts/check_wheel.py "$$(ls -1t dist/*.whl | head -1)"

# The wheel must install and run where nothing of this checkout is on PATH and
# no Glide is installed: a clean venv installs the built wheel, every shipped
# module imports, the driver ships beside the package, and the installed
# console script passes the example readiness report.
install-check: wheel
	@$(RUN) python scripts/check_install.py "$$(ls -1t dist/*.whl | head -1)"
