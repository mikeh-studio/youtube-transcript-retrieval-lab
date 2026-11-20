"""Validate regular expressions used to filter feed titles."""

import re
from re import _parser


_REPEATS = {_parser.MAX_REPEAT, _parser.MIN_REPEAT, _parser.POSSESSIVE_REPEAT}


def _first_literals(pattern, flags):
    """Return possible first characters, or None for an indeterminate set."""
    first = set()
    for op, value in pattern:
        if op in {_parser.AT, _parser.ASSERT, _parser.ASSERT_NOT}:
            continue
        if op == _parser.SUBPATTERN:
            _, add_flags, del_flags, body = value
            literals = _first_literals(body, (flags | add_flags) & ~del_flags)
            if literals is None:
                return None
            first.update(literals)
            if body.getwidth()[0] == 0:
                continue
        elif op == _parser.LITERAL and not flags & re.IGNORECASE:
            first.add(value)
        elif op == _parser.IN and not flags & re.IGNORECASE and all(
            kind == _parser.LITERAL for kind, _ in value
        ):
            first.update(char for _, char in value)
        else:
            return None
        break
    return first


def _overlapping_branches(branches, flags):
    seen = set()
    for branch in branches:
        # The parser may factor (a|aa) into a(?:|a), leaving an empty branch.
        first = _first_literals(branch, flags)
        if branch.getwidth()[0] == 0 or first is None or first & seen:
            return True
        seen.update(first)
    return False


def _too_complex(pattern, flags, repeated=False):
    for op, value in pattern:
        if op in _REPEATS:
            if repeated or _too_complex(value[2], flags, repeated=True):
                return True
        elif op == _parser.BRANCH:
            branches = value[1]
            if (repeated and _overlapping_branches(branches, flags)) or any(
                _too_complex(branch, flags, repeated) for branch in branches
            ):
                return True
        elif op == _parser.SUBPATTERN:
            _, add_flags, del_flags, body = value
            if _too_complex(body, (flags | add_flags) & ~del_flags, repeated):
                return True
        elif op in {_parser.ASSERT, _parser.ASSERT_NOT, _parser.ATOMIC_GROUP}:
            body = value if op == _parser.ATOMIC_GROUP else value[1]
            if _too_complex(body, flags, repeated):
                return True
        elif op == _parser.GROUPREF_EXISTS:
            if any(_too_complex(branch, flags, repeated) for branch in value[1:] if branch):
                return True
    return False


def compile_title_pattern(field, pattern):
    """Compile a bounded title filter or raise an error naming its field."""
    if pattern is None:
        return None
    if not isinstance(pattern, str):
        raise ValueError(f"{field} must be a regex string or null.")
    if len(pattern) > 200:
        raise ValueError(f"{field} must be at most 200 characters.")
    try:
        parsed = _parser.parse(pattern, 0)
        if not _too_complex(parsed, parsed.state.flags):
            return re.compile(pattern)
    except (re.error, OverflowError) as exc:
        raise ValueError(f"Invalid {field} regex: {exc}") from exc
    raise ValueError(f"{field} is too complex; avoid nested repetition.")
