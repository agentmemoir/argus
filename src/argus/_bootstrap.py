# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Give direct source commands the package name, regardless of checkout name."""

import importlib.util
import sys
from pathlib import Path

if "argus" not in sys.modules:
    folder = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "argus", folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the argus package from {folder}")
    package = importlib.util.module_from_spec(spec)
    sys.modules["argus"] = package
    spec.loader.exec_module(package)
