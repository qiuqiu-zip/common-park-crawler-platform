from __future__ import annotations

from dataclasses import dataclass
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
        for token in _tokenize_selector(selector):
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


@dataclass(slots=True)
class _AttrSelector:
    name: str
    operator: str | None = None
    value: str | None = None


def _matches(node: Node, token: str) -> bool:
    tag, id_value, classes, attrs = _parse_simple_selector(token)

    if tag and node.tag != tag:
        return False
    if id_value and node.attrs.get("id") != id_value:
        return False
    class_attr = set(node.attrs.get("class", "").split())
    if classes and not all(item in class_attr for item in classes):
        return False
    for attr in attrs:
        if not _matches_attr(node, attr):
            return False
    return True


def _matches_attr(node: Node, selector: _AttrSelector) -> bool:
    value = node.attrs.get(selector.name)
    if value is None:
        return False
    if selector.operator is None:
        return True
    expected = selector.value or ""
    if selector.operator == "=":
        return value == expected
    if selector.operator == "*=":
        return expected in value
    if selector.operator == "^=":
        return value.startswith(expected)
    if selector.operator == "$=":
        return value.endswith(expected)
    if selector.operator == "~=":
        return expected in value.split()
    if selector.operator == "|=":
        return value == expected or value.startswith(f"{expected}-")
    return False


def _tokenize_selector(selector: str) -> list[str]:
    tokens: list[str] = []
    buffer: list[str] = []
    bracket_depth = 0
    quote: str | None = None
    for char in selector.strip():
        if quote is not None:
            buffer.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'} and bracket_depth > 0:
            quote = char
            buffer.append(char)
            continue
        if char == "[":
            bracket_depth += 1
            buffer.append(char)
            continue
        if char == "]":
            bracket_depth = max(0, bracket_depth - 1)
            buffer.append(char)
            continue
        if char.isspace() and bracket_depth == 0:
            if buffer:
                tokens.append("".join(buffer))
                buffer = []
            continue
        buffer.append(char)
    if buffer:
        tokens.append("".join(buffer))
    return tokens


def _parse_simple_selector(token: str) -> tuple[str | None, str | None, list[str], list[_AttrSelector]]:
    attrs: list[_AttrSelector] = []
    rest: list[str] = []
    index = 0
    while index < len(token):
        if token[index] == "[":
            end = _find_attr_end(token, index)
            if end == -1:
                rest.append(token[index:])
                break
            attrs.append(_parse_attr_selector(token[index + 1 : end]))
            index = end + 1
            continue
        rest.append(token[index])
        index += 1

    selector = "".join(rest)
    tag, id_value, classes = _parse_tag_id_classes(selector)
    return tag, id_value, classes, attrs


def _find_attr_end(token: str, start: int) -> int:
    quote: str | None = None
    for index in range(start + 1, len(token)):
        char = token[index]
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "]":
            return index
    return -1


def _parse_attr_selector(part: str) -> _AttrSelector:
    for operator in ("~=", "|=", "^=", "$=", "*=", "="):
        if operator in part:
            name, value = part.split(operator, 1)
            return _AttrSelector(name=name.strip(), operator=operator, value=value.strip().strip("\"'"))
    return _AttrSelector(name=part.strip())


def _parse_tag_id_classes(selector: str) -> tuple[str | None, str | None, list[str]]:
    tag: str | None = None
    id_value: str | None = None
    classes: list[str] = []
    index = 0
    if selector and selector[0] not in "#.":
        start = 0
        while index < len(selector) and selector[index] not in "#.":
            index += 1
        tag = selector[start:index].lower()
    while index < len(selector):
        prefix = selector[index]
        index += 1
        start = index
        while index < len(selector) and selector[index] not in "#.":
            index += 1
        value = selector[start:index]
        if prefix == "#" and value:
            id_value = value
        elif prefix == "." and value:
            classes.append(value)
    return tag, id_value, classes


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

