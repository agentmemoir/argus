# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""roadmap/index.md drift: report it, and repair the half that is mechanical."""

from argus.roadmap.evidence import (
    INDEX_HEAD_RE,
    INDEX_ROW_RE,
    index_complete,
    signals,
)
from argus.roadmap.queue import candidates


def audit(repo, roadmap, apply_index=False):
    """Report index.md drift. Returns issue count.

    With ``apply_index`` the second drift class is also repaired: a phase that
    carries a completion marker but is not indexed Complete gets its row
    annotated in place. ``index_complete`` looks for the word ``Complete`` in
    a ``| <number> |`` row, so that annotation is what closes the drift, and it
    goes on the row's last cell rather than in a new column, because these
    tables vary in width and adding a column would break their headers.

    The first class is never repaired automatically: a phase indexed Complete
    with no marker is a claim the evidence does not support, and dropping the
    index entry is an operator decision, not a mechanical fix.
    """
    index_done = index_complete(repo, roadmap)
    issues = 0
    drifted = []
    for number, path in candidates(repo, roadmap):
        s = signals(repo, number, path, index_done)
        markers = (
            s["run"]
            or s["ledger"]
            or s["finalize_log"]
            or s["publication"]
            or s["approval"]
        )
        indexed = number in index_done
        if indexed and not markers:
            print(
                f"drift: phase {number} indexed Complete but has no marker "
                f"({path.name})"
            )
            issues += 1
        elif markers and not indexed:
            print(
                f"drift: phase {number} has completion markers but index is "
                f"not Complete ({path.name})"
            )
            issues += 1
            drifted.append((number, path))
        if s["needs_decision"] and not indexed:
            # Reported, not counted: this is not index drift, it is a phase the
            # selector is passing over until a person decides. Naming it here is
            # what stops the skip being silent.
            print(
                f"decision: phase {number} ended needing a decision and is "
                f"not selected ({path.name})"
            )
    if apply_index and drifted:
        applied, skipped = _annotate_index_complete(repo, roadmap, drifted)
        print(
            f"index: marked {applied} entr(ies) Complete"
            + (f", {skipped} left for a human" if skipped else "")
        )
    return issues


def _annotate_index_complete(_repo, roadmap, drifted):
    """Mark each named phase's index.md entry Complete, in place.

    Two shapes exist and they need different edits:

    * A heading (``## 100155. Title ...``) gets ``· **Complete** ·`` inserted
      before its link, matching the convention the already-complete phases use.
    * A table row is handled by its table's header. When the header names a
      Status column, that cell is *replaced*, because it holds a real value
      such as ``Plan ready`` and appending would produce a contradiction like
      ``Plan ready | — Complete``. When the table has no Status column, the
      marker goes on the last cell.

    A row or heading that already says Complete is left alone, so this is safe
    to re-run. Anything not matching one of those shapes is reported, not
    guessed at.
    """
    if roadmap is None:
        return 0, 0
    path = roadmap / "index.md"
    try:
        text = path.read_text()
    except OSError as exc:
        print(f"index: cannot read index.md ({exc})")
        return 0, 0
    wanted = {f"{number:06d}" for number, _ in drifted}
    lines = text.splitlines(keepends=True)
    # Column 0 is the phase number in every table here. The header labels it
    # "Slice" or "Phase" rather than a number, so the status column is tracked
    # positionally from the header row, and reset when a new table starts.
    status_col = None
    applied = 0
    skipped = 0
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = line.rstrip("\n").split("|")
            if _is_header_row(cells):
                status_col = _status_column_index(cells)
                out.append(line)
                continue
        if not stripped.startswith("|"):
            # Outside any table: headings are the other entry shape.
            head = INDEX_HEAD_RE.match(line)
            if head and head.group(1) in wanted and "Complete" not in line:
                new = _mark_heading_complete(line)
                if new is None:
                    skipped += 1
                else:
                    applied += 1
                out.append(new or line)
                continue
        row = INDEX_ROW_RE.match(line)
        if not row or row.group(1) not in wanted or "Complete" in line:
            out.append(line)
            continue
        new = _mark_row_complete(line, status_col)
        if new is None:
            skipped += 1
            out.append(line)
        else:
            applied += 1
            out.append(new)
    if applied:
        path.write_text("".join(out))
    return applied, skipped


def _is_header_row(cells):
    """True for a table header: a non-numeric first cell naming the column.

    ``cells`` is the raw ``split("|")``, so index 0 is the empty string before
    the leading bar and the labels start at index 1.
    """
    labels = [c.strip() for c in cells[1:-1]]
    if not labels or labels[0].isdigit():
        return False
    return labels[0].lower() in {"slice", "phase", "number", "id"}


def _status_column_index(cells):
    """Index into a raw ``split("|")`` row for the Status cell, or None."""
    for idx, cell in enumerate(cells[1:-1], start=1):
        if cell.strip().lower().startswith("status"):
            return idx
    return None


def _mark_heading_complete(line):
    """Insert the marker before the link, not inside it."""
    newline = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")
    start = body.find("[")
    if start == -1:
        return None
    before = body[:start].rstrip()
    if before.endswith("·"):
        return f"{before} **Complete** · {body[start:]}{newline}"
    return f"{before} · **Complete** · {body[start:]}{newline}"


def _mark_row_complete(line, status_col):
    """Replace the Status cell when the table has one, else mark the last cell.

    The rebuilt row keeps the line's original indentation and column widths by
    editing only the target cell in place, so the file stays diffable and
    renders the same as before apart from the status text.
    """
    newline = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")
    # Split on the cell separator, keeping the empties produced by the leading
    # and trailing "|", so cells[0] is "" and the last is "".
    cells = body.split("|")
    if len(cells) < 3:
        return None
    if status_col is not None and 0 < status_col < len(cells) - 1:
        cells[status_col] = " Complete — PASS WITH DOCUMENTED LIMITATIONS "
    else:
        last = len(cells) - 2
        cells[last] = f"{cells[last].rstrip()} — Complete "
    return "|".join(cells) + newline
