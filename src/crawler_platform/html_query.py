from __future__ import annotations

from html.parser import HTMLParser
from typing import Iterable


class Node:
    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: "Node | None" = None):
        self.tag = tag.lower()
        self.attrs = attrs or {}
        self.parent = parent
        self.children: list[Node] = []
        self.text_parts: list[str] = []

    @property
    def text(self) -> str:
        parts: list[str] = []
        self._collect_text(parts)
        return " ".join(" ".join(parts).split())

    def _collect_text(self, parts: list[str]) -> None:
        parts.extend(part.strip() for part in self.text_parts if part.strip())
        for child in self.children:
            child._collect_text(parts)

    def to_html(self) -> str:
        attrs = "".join(f' {key}="{value}"' for key, value in self.attrs.items())
        inner = "".join(child.to_html() for child in self.children)
        text = "".join(self.text_parts)
        return f"<{self.tag}{attrs}>{text}{inner}</{self.tag}>"

    def descendants(self) -> Iterable["Node"]:
        for child in self.children:
            yield child
            yield from child.descendants()


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {key: value or "" for key, value in attrs}, self.current)
        self.current.children.append(node)
        if tag.lower() not in {"br", "img", "input", "meta", "link"}:
            self.current = node

    def handle_endtag(self, tag: str) -> None:
        cursor = self.current
        while cursor.parent is not None:
            if cursor.tag == tag.lower():
                self.current = cursor.parent
                return
            cursor = cursor.parent

    def handle_data(self, data: str) -> None:
        if data:
            self.current.text_parts.append(data)


class HTMLDocument:
    def __init__(self, html: str | Node):
        if isinstance(html, Node):
            self.root = html
        else:
            parser = _TreeBuilder()
            parser.feed(html)
            self.root = parser.root

    def select(self, selector: str | None) -> list[Node]:
        if not selector:
            return [self.root]
        current = [self.root]
        for token in selector.strip().split():
            next_nodes: list[Node] = []
            for node in current:
                next_nodes.extend(desc for desc in node.descendants() if _matches(desc, token))
            current = next_nodes
        return current

    def xpath(self, expression: str) -> list[str | Node]:
        expr = expression.strip()
        if expr.startswith("string(") and expr.endswith(")"):
            values = self.xpath(expr[7:-1])
            first = values[0] if values else ""
            return [first.text if isinstance(first, Node) else str(first)]
        if "/@" in expr:
            node_expr, attr = expr.rsplit("/@", 1)
            return [node.attrs.get(attr, "") for node in self.xpath(node_expr) if isinstance(node, Node)]
        if expr.startswith("//"):
            selector = _xpath_to_selector(expr[2:])
            return self.select(selector)
        return []


def _matches(node: Node, token: str) -> bool:
    tag = None
    id_value = None
    classes: list[str] = []
    attr_name = None
    attr_value = None

    rest = token
    if "[" in rest and rest.endswith("]"):
        rest, attr_part = rest[:-1].split("[", 1)
        if "=" in attr_part:
            attr_name, attr_value = attr_part.split("=", 1)
            attr_value = attr_value.strip("\"'")
        else:
            attr_name = attr_part
    if "#" in rest:
        rest, id_value = rest.split("#", 1)
    if "." in rest:
        parts = rest.split(".")
        rest = parts[0]
        classes = [part for part in parts[1:] if part]
    if rest:
        tag = rest.lower()

    if tag and node.tag != tag:
        return False
    if id_value and node.attrs.get("id") != id_value:
        return False
    class_attr = set(node.attrs.get("class", "").split())
    if classes and not all(item in class_attr for item in classes):
        return False
    if attr_name and attr_name not in node.attrs:
        return False
    if attr_name and attr_value is not None and node.attrs.get(attr_name) != attr_value:
        return False
    return True


def _xpath_to_selector(expr: str) -> str:
    if expr.startswith("*[@id="):
        id_value = expr.split("=", 1)[1].strip("[]'\"")
        return f"#{id_value}"
    if "[contains(@class," in expr:
        tag, rest = expr.split("[", 1)
        class_value = rest.split(",", 1)[1].split(")", 1)[0].strip("'\" ")
        return f"{tag}.{class_value}"
    if "[@id=" in expr:
        tag, rest = expr.split("[", 1)
        id_value = rest.split("=", 1)[1].strip("[]'\"")
        return f"{tag}#{id_value}"
    return expr

