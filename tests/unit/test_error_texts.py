"""Every error code the server answers with has a text in each interface language.

A code without a text falls back to a generic sentence in the browser, so the
gap is invisible until a user meets it.
"""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "frontend" / "src" / "locales"


def server_error_codes() -> set[str]:
    """Collect the codes of HTTP error answers from the sources themselves.

    Two forms exist: ``WorkspaceError("code", …)`` and the literal envelope
    ``{"error": {"code": "code", …}}``. Codes of validation findings and of a
    job's own error are shown as diagnostics and are not collected.
    """
    codes: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", None))
                if name == "WorkspaceError" and node.args:
                    code = node.args[0]
                    assert isinstance(code, ast.Constant), f"{path}:{node.lineno}: computed code"
                    codes.add(str(code.value))
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if not (isinstance(key, ast.Constant) and key.value == "error"):
                        continue
                    if not isinstance(value, ast.Dict):
                        continue
                    for inner, code in zip(value.keys, value.values, strict=True):
                        # The one computed code relays a WorkspaceError's own.
                        if (
                            isinstance(inner, ast.Constant)
                            and inner.value == "code"
                            and isinstance(code, ast.Constant)
                        ):
                            codes.add(str(code.value))
    return codes


def test_every_server_error_code_has_a_text_in_each_language() -> None:
    codes = server_error_codes()
    # The collection itself must work: these are raised in different ways.
    assert {"dataset_not_found", "access_required", "task_ideas_failed"} <= codes
    for locale in ("en", "uk", "de"):
        texts = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
        missing = sorted(code for code in codes if f"error.{code}" not in texts)
        assert not missing, f"{locale}: no text for {missing}"
