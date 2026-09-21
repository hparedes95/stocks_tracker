"""Las capturas amplias son fronteras deliberadas, nunca olvidos silenciosos."""

from __future__ import annotations

import ast

from stocks_tracker.core.config import project_root


def test_cada_exception_amplia_esta_marcada_para_revision():
    missing = []
    for path in (project_root() / "src").rglob("*.py"):
        lines = path.read_text("utf-8").splitlines()
        tree = ast.parse("\n".join(lines), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            broad = isinstance(node.type, ast.Name) and node.type.id == "Exception"
            if broad and "noqa: BLE001" not in lines[node.lineno - 1]:
                missing.append(f"{path.relative_to(project_root())}:{node.lineno}")
    assert not missing, (
        "capturas amplias sin marcar; acotalas o documenta por que son una "
        f"frontera deliberada: {missing}"
    )
