import re
from typing import Any


def adf_to_text(node: dict[str, Any] | None) -> str:
    if not node:
        return ""
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        attrs = node.get("attrs", {})
        return str(attrs.get("text") or attrs.get("displayName") or "")
    text = "".join(adf_to_text(child) for child in node.get("content", []))
    if kind == "listItem":
        return "- " + text.rstrip("\n") + "\n"
    if kind in ("paragraph", "heading", "codeBlock"):
        return text + "\n"
    return text


def wiki_to_text(text: str | None) -> str:
    # shortcut: common Jira wiki markup only; use a full parser if richer markup is needed.
    text = re.sub(r"\{(?:code(?::[^}]*)?|noformat|color(?::[^}]*)?)\}", "", text or "")
    text = re.sub(r"(?m)^h[1-6]\.\s*", "", text)
    text = re.sub(r"\[([^\]|]+)\|[^\]]+\]", r"\1", text)
    text = re.sub(r"([*_])([^\n]+?)\1", r"\2", text)
    return text.strip()
