"""Brace-matching readers for the Terraform root, shared by the infra tests.

Terraform has no test runner in this repository, so the HCL is checked as text.
Matching braces rather than regexing whole blocks is what makes that honest: an
assertion scoped to one `resource` block cannot be satisfied by a coincidence
somewhere else in the file.

Every function takes *source text* rather than a filename, so the same reader
works on a whole file, on one resource body, or on one policy statement.
"""

from __future__ import annotations

import re

_CLOSING = {"{": "}", "[": "]"}


def _end_of_string(source: str, quote: int) -> int:
    """The offset just past the double-quoted string opening at `quote`."""
    offset = quote + 1
    while offset < len(source):
        if source[offset] == "\\":
            offset += 2
            continue
        if source[offset] == '"':
            return offset + 1
        offset += 1
    raise AssertionError(f"unterminated string from offset {quote}")


def _matching(source: str, opening: int) -> int:
    """The offset of the delimiter that closes the one at `opening`.

    Strings and comments are skipped rather than counted. Without that, the
    braces in `"{{ ReleaseSha }}"` and the brackets in `"^[0-9a-f]{40}$"` are
    structure, and a block's apparent end depends on whether its regexes happen
    to be balanced.
    """
    opener = source[opening]
    closer = _CLOSING[opener]
    depth = 0
    offset = opening

    while offset < len(source):
        character = source[offset]
        if character == '"':
            offset = _end_of_string(source, offset)
            continue
        if character == "#" or source.startswith("//", offset):
            newline = source.find("\n", offset)
            offset = len(source) if newline == -1 else newline
            continue
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return offset
        offset += 1

    raise AssertionError(f"unbalanced {opener} from offset {opening}")


def block_body(source: str, opening_brace: int) -> str:
    """The text of an HCL block, from the brace that opens it to its match."""
    return source[opening_brace + 1 : _matching(source, opening_brace)]


def _blanked(text: str) -> str:
    """`text` with every character but its newlines replaced by a space."""
    return "".join("\n" if character == "\n" else " " for character in text)


def top_level(body: str) -> str:
    """`body` with the contents of its nested blocks blanked out.

    Line structure is preserved, and so is every top-level assignment
    including the braces and brackets of its own value, so a list or object on
    the right-hand side survives while a `label { ... }` block does not.

    Telling the two apart is the whole job: a block opens where no assignment
    is in progress, a value's brace opens after an `=`. Without the
    distinction, `name = value` inside a nested block reads as an argument of
    the block that contains it, and an assertion about a resource can be
    satisfied by one of its sub-blocks.
    """
    kept: list[str] = []
    depth = 0
    in_value = False
    offset = 0

    def keep(text: str) -> None:
        kept.append(text if in_value or depth == 0 else _blanked(text))

    while offset < len(body):
        character = body[offset]
        if character == '"':
            end = _end_of_string(body, offset)
            keep(body[offset:end])
            offset = end
            continue
        if character == "#" or body.startswith("//", offset):
            newline = body.find("\n", offset)
            end = len(body) if newline == -1 else newline
            kept.append(_blanked(body[offset:end]))
            offset = end
            continue

        if character in _CLOSING:
            keep(character)
            depth += 1
        elif character in _CLOSING.values():
            depth = max(depth - 1, 0)
            keep(character)
        else:
            if depth == 0:
                if character == "=":
                    in_value = True
                elif character == "\n":
                    in_value = False
            keep(character)
        offset += 1

    return "".join(kept)


def attribute(body: str, name: str) -> str | None:
    """One top-level `name = value` from a block body, whitespace normalised.

    Reading the value rather than matching exact text is what makes an
    assertion about behaviour rather than about `terraform fmt` alignment.
    """
    match = re.search(
        rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", top_level(body), re.MULTILINE
    )
    return match[1] if match else None


def hcl_block(source: str, header: str) -> str:
    """The body of the block introduced by `header`, which omits the brace.

    Leaving the brace out of the header is what lets the same call read a
    resource (`resource "aws_instance" "docs"`) and a nested attribute block
    (`root_block_device`) without the caller knowing how the file is formatted.
    """
    start = source.index(header)
    return block_body(source, source.index("{", start + len(header)))


def hcl_list(source: str, name: str) -> list[str]:
    """The string elements of the `name = [ ... ]` list, unescaped.

    Terraform's own escapes are undone, so a caller reading a shell snippet out
    of an SSM document sees the shell it will actually run.
    """
    match = re.search(rf"\b{re.escape(name)}\s*=\s*\[", source)
    assert match, f"no {name} list here"

    body = source[match.end() - 1 : _matching(source, match.end() - 1) + 1]
    return [
        element.replace('\\"', '"').replace("\\\\", "\\")
        for element in re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    ]


def nested_blocks(body: str, label: str) -> list[str]:
    """Every `label { ... }` block declared directly in `body`."""
    return [
        block_body(body, match.end() - 1)
        for match in re.finditer(rf"(?:^|\n)[ \t]*{re.escape(label)}\s*\{{", body)
    ]


def resource_bodies(source: str, resource_type: str) -> dict[str, str]:
    """Every `resource "<type>" "<name>"` block in `source`, keyed by name.

    Enumerating is what lets a test say "and nothing else". Reading one policy
    attachment and finding it correctly scoped says nothing about a second
    attachment sitting beside it with `AmazonS3FullAccess`.
    """
    header = f'resource "{resource_type}"'
    return {
        match[1]: block_body(source, source.index("{", match.end()))
        for match in re.finditer(rf'{re.escape(header)} "([^"]+)"', source)
    }


def strip_comments(source: str) -> str:
    """HCL with `#`, `//`, and `/* */` comments removed, strings left alone.

    Negative assertions are why this exists. `"iam:PassRole" not in policy`
    passes or fails on prose, which makes it a test of the documentation rather
    than of the configuration, and it fails the moment a comment explains why
    the grant is absent.
    """
    kept: list[str] = []
    offset = 0

    while offset < len(source):
        character = source[offset]
        if character == '"':
            end = _end_of_string(source, offset)
            kept.append(source[offset:end])
            offset = end
            continue
        if character == "#" or source.startswith("//", offset):
            newline = source.find("\n", offset)
            offset = len(source) if newline == -1 else newline
            continue
        if source.startswith("/*", offset):
            end = source.find("*/", offset + 2)
            offset = len(source) if end == -1 else end + 2
            continue
        kept.append(character)
        offset += 1

    return "".join(kept)


def _condition_expression(block: str) -> str:
    """The `condition = ...` expression, however many lines it is written over.

    `attribute` stops at the newline, which is right for a value but wrong for
    a condition: `terraform fmt` spreads a long `contains([...], var.x)` call
    across lines, and reading only the first of them yields `contains(` and an
    evaluator that cannot say anything about the rule.
    """
    match = re.search(r"^[ \t]*condition[ \t]*=[ \t]*", top_level(block), re.MULTILINE)

    assert match, "this validation block declares no condition"

    text = block[match.end() :]
    depth = 0
    offset = 0

    while offset < len(text):
        character = text[offset]
        if character == '"':
            offset = _end_of_string(text, offset)
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == "\n" and depth <= 0:
            break
        offset += 1

    return " ".join(text[:offset].split())


def variable_conditions(source: str, name: str) -> list[str]:
    """Every `validation { condition = ... }` expression one variable declares."""
    body = hcl_block(source, f'variable "{name}"')
    conditions = [
        _condition_expression(block) for block in nested_blocks(body, "validation")
    ]

    assert conditions and all(conditions), f"variable {name} declares no validation"
    return conditions


# The POSIX classes the committed validations use, as the Python `re` equivalent.
# Terraform's `regex` is Go's RE2, which understands `[[:cntrl:]]` inside a
# bracket expression; Python's `re` reads it as a set of punctuation and
# letters, so a translation is the difference between checking the rule and
# checking a typo of it.
_POSIX_CLASSES = {
    "[:cntrl:]": "\\x00-\\x1f\\x7f",
    "[:space:]": " \\t\\n\\r\\f\\v",
}


def _python_pattern(pattern: str) -> str:
    for posix, replacement in _POSIX_CLASSES.items():
        pattern = pattern.replace(posix, replacement)

    # RE2's `$` means end of text; Python's also matches immediately before a
    # trailing newline. Left untranslated, a fully anchored validation would
    # look like it accepted a value with a newline welded onto the end, which
    # is precisely the value these tests exist to prove is refused.
    if pattern.endswith("$"):
        head = pattern[:-1]
        escaped = (len(head) - len(head.rstrip("\\"))) % 2 == 1
        if not escaped:
            pattern = head + r"\Z"

    return pattern


def _unquote(literal: str) -> str:
    assert literal.startswith('"') and literal.endswith('"'), literal
    return literal[1:-1].replace('\\"', '"').replace("\\\\", "\\")


def _split_conjunction(condition: str) -> list[str]:
    """`condition` split on its top-level `&&`, strings left intact."""
    terms: list[str] = []
    start = 0
    offset = 0

    while offset < len(condition):
        if condition[offset] == '"':
            offset = _end_of_string(condition, offset)
            continue
        if condition.startswith("&&", offset):
            terms.append(condition[start:offset])
            offset += 2
            start = offset
            continue
        offset += 1

    terms.append(condition[start:])
    return [term.strip() for term in terms]


def _term_accepts(term: str, value: str) -> bool:
    negated = term.startswith("!")
    term = term.removeprefix("!").strip()

    if match := re.fullmatch(r"can\(regex\((\".*\"), var\.\w+\)\)", term):
        held = re.search(_python_pattern(_unquote(match[1])), value) is not None
    elif match := re.fullmatch(
        r"length\(regexall\((\".*\"), var\.\w+\)\)\s*==\s*0", term
    ):
        held = re.search(_python_pattern(_unquote(match[1])), value) is None
    elif match := re.fullmatch(r"startswith\(var\.\w+, (\".*\")\)", term):
        held = value.startswith(_unquote(match[1]))
    elif match := re.fullmatch(r"endswith\(var\.\w+, (\".*\")\)", term):
        held = value.endswith(_unquote(match[1]))
    elif match := re.fullmatch(r"length\(var\.\w+\)\s*<=\s*(\d+)", term):
        held = len(value) <= int(match[1])
    elif match := re.fullmatch(r"trimspace\(var\.\w+\)\s*!=\s*\"\"", term):
        held = value.strip() != ""
    elif match := re.fullmatch(r"contains\(\s*\[(.*)\],\s*var\.\w+\s*\)", term):
        allowed = [
            _unquote(literal)
            for literal in re.findall(r'"(?:[^"\\]|\\.)*"', match[1])
        ]
        held = value in allowed
    else:
        raise AssertionError(f"this reader cannot evaluate the condition {term!r}")

    return not held if negated else held


def variable_accepts(source: str, name: str, value: str) -> bool:
    """Whether every `validation` on one string variable holds for `value`.

    Terraform evaluates these at plan time against real inputs, which needs
    credentials and a provider no test here has. Reading the committed
    expression and applying it is what makes the accepted and refused values
    assertable: the pattern under test is the one in `variables.tf`, so
    loosening it fails these tests rather than quietly passing them.
    """
    return all(
        all(_term_accepts(term, value) for term in _split_conjunction(condition))
        for condition in variable_conditions(source, name)
    )


def statements(document_body: str) -> list[str]:
    """Every `statement` block in an `aws_iam_policy_document` body."""
    return nested_blocks(document_body, "statement")


def actions(document_body: str) -> list[str]:
    """Every action granted by every statement in the body, in order.

    Read from the `actions` lists themselves, so an action named only in a
    comment neither satisfies nor breaks an assertion about the grant.
    """
    return [
        action
        for statement in statements(strip_comments(document_body))
        for action in hcl_list(statement, "actions")
    ]


def statement_with_action(document_body: str, action: str) -> str:
    """The one statement granting `action`.

    Insisting on exactly one is the point: a second statement granting the same
    action is how a scoped grant quietly becomes an unscoped one.
    """
    matches = [block for block in statements(document_body) if f'"{action}"' in block]
    assert len(matches) == 1, (
        f"expected exactly one {action} statement, found {len(matches)}"
    )
    return matches[0]
