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


def block_body(source: str, opening_brace: int) -> str:
    """The text of an HCL block, from the brace that opens it to its match."""
    depth = 0
    for offset, character in enumerate(source[opening_brace:], start=opening_brace):
        depth += {"{": 1, "}": -1}.get(character, 0)
        if depth == 0:
            return source[opening_brace + 1 : offset]
    raise AssertionError(f"unbalanced braces from offset {opening_brace}")


def hcl_block(source: str, header: str) -> str:
    """The body of the block introduced by `header`, which omits the brace.

    Leaving the brace out of the header is what lets the same call read a
    resource (`resource "aws_instance" "docs"`) and a nested attribute block
    (`root_block_device`) without the caller knowing how the file is formatted.
    """
    start = source.index(header)
    return block_body(source, source.index("{", start + len(header)))


def statements(document_body: str) -> list[str]:
    """Every `statement` block in an `aws_iam_policy_document` body."""
    return [
        block_body(document_body, match.end() - 1)
        for match in re.finditer(r"\n\s*statement\s*\{", document_body)
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
