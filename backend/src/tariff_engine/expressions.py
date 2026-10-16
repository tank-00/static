"""Safe formula language for tariff rules (A§4.3).

A formula is parsed with ``ast.parse(mode="eval")`` and converted, through a strict whitelist, into
a small typed tree (:data:`Node`, wrapped in :class:`Expr`). Python's ``eval`` is never used: the
tree is interpreted by :func:`evaluate` over exact :class:`~tariff_engine.units.Quantity` values.

Grammar (everything else is rejected with an :class:`ExpressionError` carrying its position):

* numbers -- decimal literals read from their **source text** (``0.1`` is exactly
  ``Decimal("0.1")``; ``_`` digit separators are stripped; hex/octal/binary/complex literals are
  refused). A ``-`` written directly before a number literal is part of the literal (``-1.5`` is
  one :class:`Constant`, negated exactly, never rounded). Literals are dimensionless, except that
  a literal whose value is zero (``0``, ``0.00``) is the unit-polymorphic zero
  (:meth:`Quantity.zero_literal`). Units come from ``q(200, "h")`` or ``q("6.912", "cents/kWh")``;
  text is allowed only there and in ``round_to``'s mode, as one plain quoted literal.
* names -- ASCII ``[A-Za-z][A-Za-z0-9_]*`` (no dots, no leading ``_``). The built-ins
  (:data:`BUILTIN_NAMES`) are looked up like any other name; :func:`builtin_values` builds them.
* operators -- ``+ - * /``, unary ``-``, comparisons ``< <= > >= == !=`` (chains allowed),
  ``and or not`` and ``a if cond else b``.
* functions -- ``min``/``max`` (2+ args), ``abs``, ``round_to(x, places[, "MODE"])``, ``ceil``,
  ``floor``, ``sqrt``, ``q(number, "unit")``, ``in_months(m1, ...)``, ``power_factor(p, q)``,
  ``apparent(p, q)``, ``as_reactive_equiv(x)``. Literal-only arguments (``q``'s number and unit,
  ``round_to``'s places and mode, ``in_months``'s months) are validated at parse time.
* limits -- at most :data:`MAX_SOURCE_LENGTH` characters and :data:`MAX_DEPTH` levels of nested
  expression nodes; comments (``#``), backslashes and control characters are refused anywhere.

Parsing and evaluation never depend on the ambient decimal context: literals are read exactly and
all arithmetic runs in :func:`~tariff_engine.decimals.engine_context` (A§4.1).

Values are numbers (:class:`Quantity`) or conditions (``bool``); mixing them is an
``ExpressionError`` -- at parse time when the kinds are evident, otherwise when evaluated.

Missing values (A§3.2: missing is never zero): a name that is absent from the environment or bound
to :class:`MissingValue` makes the sub-expression *unknown*. Unknowns propagate through arithmetic
and functions; a conditional whose condition is unknown is unknown (its branches are not
evaluated); ``and``/``or`` and comparison chains use three-valued logic, so a definite ``False``
operand decides ``and`` (and ``True`` decides ``or``) even when another operand is unknown. An
error in an operand that is reached only after an unknown one (``has_meter and kwh / kw >
q(200, "h")`` with ``has_meter`` missing and ``kw = 0``) makes the result unknown too: whether that
operand is evaluated at all depends on the missing input. If the final result is unknown,
:func:`evaluate` raises :class:`MissingInputError` naming every missing name the result depends on,
in evaluation order.

Errors carry positions: :class:`ExpressionError` always, and a ``UnitError``/``CalcError`` raised
while evaluating gets the span of the innermost node whose own operation failed
(:meth:`~tariff_engine.errors.EngineError.locate`).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from enum import Enum, StrEnum
from functools import lru_cache
from typing import Final, assert_never

from tariff_engine.dates import MAX_BILLING_DAYS, month_days, parse_month
from tariff_engine.decimals import ENGINE_EMAX, ENGINE_ETINY, D, RoundingMode, engine_context
from tariff_engine.errors import (
    CalcError,
    DateError,
    EngineError,
    ExpressionError,
    MissingInputError,
    UnitError,
)
from tariff_engine.units import Quantity, Unit, qmax, qmin

__all__ = [
    "BUILTIN_NAMES",
    "FUNCTION_NAMES",
    "MAX_DEPTH",
    "MAX_SOURCE_LENGTH",
    "MISSING",
    "Binary",
    "BinaryOperator",
    "BoolOp",
    "BoolOperator",
    "Call",
    "Compare",
    "CompareOperator",
    "Conditional",
    "Constant",
    "Conversion",
    "EnvValue",
    "Evaluation",
    "Expr",
    "ExpressionError",
    "Function",
    "InMonths",
    "MissingInputError",
    "MissingValue",
    "Name",
    "Node",
    "RoundTo",
    "Span",
    "Substitution",
    "Unary",
    "UnaryOperator",
    "Value",
    "builtin_values",
    "children",
    "evaluate",
    "evaluate_traced",
    "parse_expression",
    "referenced_names",
    "walk",
]

MAX_SOURCE_LENGTH: Final = 2_000
"""Longest accepted formula text, in characters."""

MAX_DEPTH: Final = 50
"""Deepest accepted nesting of expression nodes (a name or literal is 1; each operator, function
call or conditional adds 1 above its deepest operand, so ``a + b + c`` has depth 3)."""

BUILTIN_NAMES: Final = frozenset(
    {"billing_month", "billing_days", "days_in_billing_month", "period_year"}
)
"""Names supplied from the calculation context (see :func:`builtin_values`)."""


class Function(StrEnum):
    """Functions whose arguments are ordinary (evaluated) number expressions."""

    MIN = "min"
    MAX = "max"
    ABS = "abs"
    CEIL = "ceil"
    FLOOR = "floor"
    SQRT = "sqrt"
    POWER_FACTOR = "power_factor"
    APPARENT = "apparent"
    AS_REACTIVE_EQUIV = "as_reactive_equiv"


FUNCTION_NAMES: Final = frozenset(
    {*(function.value for function in Function), "q", "round_to", "in_months"}
)
"""Every callable name; none of them may be used as a plain name."""

# (minimum, maximum) argument counts; None = no maximum.
_ARITY: Final[dict[Function, tuple[int, int | None]]] = {
    Function.MIN: (2, None),
    Function.MAX: (2, None),
    Function.ABS: (1, 1),
    Function.CEIL: (1, 1),
    Function.FLOOR: (1, 1),
    Function.SQRT: (1, 1),
    Function.POWER_FACTOR: (2, 2),
    Function.APPARENT: (2, 2),
    Function.AS_REACTIVE_EQUIV: (1, 1),
}


# ---------------------------------------------------------------------------------------- the tree


@dataclass(frozen=True, slots=True)
class Span:
    """Where a node's text lies in the formula: ``source[start:end]``.

    ``start``/``end`` are 0-based character offsets; ``line``/``column`` (of ``start``) are 1-based
    and count characters, not bytes.
    """

    start: int
    end: int
    line: int
    column: int


class UnaryOperator(StrEnum):
    NEG = "-"
    NOT = "not"


class BinaryOperator(StrEnum):
    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"


class BoolOperator(StrEnum):
    AND = "and"
    OR = "or"


class CompareOperator(StrEnum):
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    EQ = "=="
    NE = "!="


@dataclass(frozen=True, slots=True)
class Constant:
    """A signed number literal (dimensionless, or the polymorphic zero) or a ``q(number, "unit")``.

    ``text`` is the literal's source text, sign included (``-1.5``, ``q(6.912, "cents/kWh")``).
    """

    value: Quantity
    text: str
    span: Span


@dataclass(frozen=True, slots=True)
class Name:
    """A reference resolved through the environment (a built-in or a package name)."""

    id: str
    span: Span


@dataclass(frozen=True, slots=True)
class Unary:
    op: UnaryOperator
    operand: Node
    span: Span


@dataclass(frozen=True, slots=True)
class Binary:
    op: BinaryOperator
    left: Node
    right: Node
    span: Span


@dataclass(frozen=True, slots=True)
class BoolOp:
    op: BoolOperator
    operands: tuple[Node, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class Compare:
    """``left ops[0] comparators[0] ops[1] comparators[1] ...`` (a chain is a conjunction)."""

    left: Node
    ops: tuple[CompareOperator, ...]
    comparators: tuple[Node, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class Conditional:
    """``if_true if condition else if_false``; only the selected branch is evaluated."""

    condition: Node
    if_true: Node
    if_false: Node
    span: Span


@dataclass(frozen=True, slots=True)
class Call:
    function: Function
    args: tuple[Node, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class RoundTo:
    """``round_to(operand, places, "MODE")``: rounds the canonical-unit magnitude (A§4.1)."""

    operand: Node
    places: int
    mode: RoundingMode
    span: Span


@dataclass(frozen=True, slots=True)
class InMonths:
    """``in_months(m1, ...)``: true iff the billing month number is one of ``months``."""

    months: tuple[int, ...]
    span: Span


Node = Constant | Name | Unary | Binary | BoolOp | Compare | Conditional | Call | RoundTo | InMonths
"""Any node of a parsed formula."""


@dataclass(frozen=True, slots=True)
class Expr:
    """A parsed formula: its exact source text and the root of its tree."""

    source: str
    body: Node

    def __str__(self) -> str:
        return self.source


def children(node: Node) -> tuple[Node, ...]:
    """The direct sub-expressions of ``node``, in source order."""
    match node:
        case Constant() | Name() | InMonths():
            return ()
        case Unary() | RoundTo():
            return (node.operand,)
        case Binary():
            return (node.left, node.right)
        case BoolOp():
            return node.operands
        case Compare():
            return (node.left, *node.comparators)
        case Conditional():  # source order "x if c else y"; evaluation starts at the condition
            return (node.if_true, node.condition, node.if_false)
        case Call():
            return node.args
    assert_never(node)


def walk(expr: Expr | Node) -> Iterator[Node]:
    """Every node of the tree, pre-order, in source order (deterministic)."""
    stack: list[Node] = [expr.body if isinstance(expr, Expr) else expr]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(children(node)))


def referenced_names(expr: Expr | Node) -> tuple[str, ...]:
    """Sorted, distinct names the formula refers to -- excluding function names and built-ins.

    This is the formula's contribution to the dependency graph (A§4.6); ``in_months`` reads the
    built-in ``billing_month`` and adds nothing here.
    """
    return tuple(
        sorted(
            {
                node.id
                for node in walk(expr)
                if isinstance(node, Name) and node.id not in BUILTIN_NAMES
            }
        )
    )


# ------------------------------------------------------------------------------------------ parser

# Rejected anywhere in the text: comments, backslashes (escapes, line continuations), control and
# line-separator characters, and lone surrogates (not encodable, so ast.parse could not read them).
_FORBIDDEN_CHARACTER_RE: Final = re.compile(r"[#\\\x00-\x08\x0b-\x1f\x7f-\x9f  \ud800-\udfff]")
_NAME_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# Python decimal number literals (underscores are validated by Python and stripped here).
_NUMBER_TEXT_RE: Final = re.compile(
    r"(?:[0-9][0-9_]*(?:\.(?:[0-9][0-9_]*)?)?|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9][0-9_]*)?"
)
_WHOLE_NUMBER_TEXT_RE: Final = re.compile(r"[0-9][0-9_]*")
# One plain quoted text literal: no prefix (u, r, b, f), no triple quotes, no implicit concatenation
# ("kW" "h" is one ast.Constant "kWh" whose source would hide the effective unit or mode).
_TEXT_LITERAL_RE: Final = re.compile(
    r"'[^'\n]*'"  # 'kWh'
    r'|"[^"\n]*"'  # "kWh"
)
# Lexemes for the pre-scan that runs before ast.parse, mirroring Python's tokenizer: text literals
# (skipped), words (identifiers and keywords, which absorb their digits), number tokens and any
# other character. Backslashes and "#" are refused before this runs, so strings have no escapes.
_LEXEME_RE: Final = re.compile(
    r"(?P<text>'''.*?(?:'''|\Z)"  # triple-quoted (may span lines, may be unterminated)
    r'|""".*?(?:"""|\Z)'
    r"|'[^'\n]*'?"  # one line (the closing quote may be missing)
    r'|"[^"\n]*"?)'
    r"|(?P<word>[^\W\d]\w*)"
    r"|(?P<number>0[xX](?:_?[0-9a-fA-F])+|0[oO](?:_?[0-7])+|0[bB](?:_?[01])+"
    r"|(?:[0-9](?:_?[0-9])*(?:\.(?:[0-9](?:_?[0-9])*)?)?|\.[0-9](?:_?[0-9])*)"
    r"(?:[eE][+-]?[0-9](?:_?[0-9])*)?[jJ]?)"
    r"|(?P<other>.)",
    re.DOTALL,
)
_WORD_CHARACTER_RE: Final = re.compile(r"[^\W\d]")
# String prefixes whose literal contains code (f-strings; t-strings from Python 3.14).
_TEMPLATE_PREFIXES: Final = frozenset({"f", "fr", "rf", "t", "tr", "rt"})

_DISALLOWED: Final[dict[type[ast.AST], str]] = {
    ast.Attribute: "attribute access (dotted names)",
    ast.Subscript: "a subscript",
    ast.Slice: "a slice",
    ast.Lambda: "a lambda",
    ast.ListComp: "a comprehension",
    ast.SetComp: "a comprehension",
    ast.DictComp: "a comprehension",
    ast.GeneratorExp: "a comprehension",
    ast.JoinedStr: "an f-string",
    ast.FormattedValue: "an f-string",
    ast.Starred: "a starred argument",
    ast.NamedExpr: "an assignment expression (:=)",
    ast.List: "a list",
    ast.Tuple: "a tuple",
    ast.Set: "a set",
    ast.Dict: "a dict",
    ast.Await: "await",
    ast.Yield: "yield",
    ast.YieldFrom: "yield",
}
_BINARY_OPERATORS: Final[dict[type[ast.operator], BinaryOperator]] = {
    ast.Add: BinaryOperator.ADD,
    ast.Sub: BinaryOperator.SUB,
    ast.Mult: BinaryOperator.MUL,
    ast.Div: BinaryOperator.DIV,
}
_DISALLOWED_BINARY_TEXT: Final[dict[type[ast.operator], str]] = {
    ast.Pow: "**",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.MatMult: "@",
    ast.LShift: "<<",
    ast.RShift: ">>",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.BitAnd: "&",
}
_COMPARE_OPERATORS: Final[dict[type[ast.cmpop], CompareOperator]] = {
    ast.Lt: CompareOperator.LT,
    ast.LtE: CompareOperator.LE,
    ast.Gt: CompareOperator.GT,
    ast.GtE: CompareOperator.GE,
    ast.Eq: CompareOperator.EQ,
    ast.NotEq: CompareOperator.NE,
}
_DISALLOWED_COMPARE_TEXT: Final[dict[type[ast.cmpop], str]] = {
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}


def _negated(number: Decimal) -> Decimal:
    """``-number`` exactly, whatever the ambient decimal context (never ``-0``).

    Python's unary minus on a Decimal rounds to the *current* context's precision, which would make
    a parsed literal (cached by text) depend on whichever context was active when it was first
    parsed (A§3.6, A§4.1). ``copy_negate`` is exact and context-free.
    """
    return number if number.is_zero() else number.copy_negate()


class _Kind(Enum):
    """What a sub-expression is known to produce at parse time."""

    NUMBER = "a number"
    CONDITION = "a condition (true/false)"
    ANY = "any value"  # a name: only its environment value tells


class _Source:
    """The formula text plus conversions from ``ast`` positions to character offsets.

    Leading spaces, tabs and newlines are skipped before parsing (``ast.parse`` would call them an
    indent); every reported offset still refers to the original text.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.stripped = text.lstrip(" \t\n")
        self.lead = len(text) - len(self.stripped)
        self.lines = self.stripped.split("\n")
        self.line_starts: list[int] = []
        start = 0
        for line in self.lines:
            self.line_starts.append(start)
            start += len(line) + 1

    def error_at(self, message: str, offset: int) -> ExpressionError:
        offset = max(0, min(offset, len(self.text)))
        line = self.text.count("\n", 0, offset) + 1
        column = offset - (self.text.rfind("\n", 0, offset) + 1) + 1
        return ExpressionError(message, offset=offset, line=line, column=column)

    def error(self, message: str, node: ast.expr | ast.keyword) -> ExpressionError:
        return self.error_at(message, self._offset(node.lineno, node.col_offset))

    def _offset(self, lineno: int, byte_column: int) -> int:
        index = max(0, min(lineno, len(self.lines)) - 1)
        line = self.lines[index].encode("utf-8")[:byte_column].decode("utf-8", errors="ignore")
        return self.lead + self.line_starts[index] + len(line)

    def span(self, node: ast.expr) -> Span:
        start = self._offset(node.lineno, node.col_offset)
        end_line = node.end_lineno if node.end_lineno is not None else node.lineno
        end_column = node.end_col_offset if node.end_col_offset is not None else node.col_offset
        end = self._offset(end_line, end_column)
        line = self.text.count("\n", 0, start) + 1
        column = start - (self.text.rfind("\n", 0, start) + 1) + 1
        return Span(start, end, line, column)

    def segment(self, node: ast.expr) -> str:
        span = self.span(node)
        return self.text[span.start : span.end]

    def operator_offset(self, after: ast.expr, before: ast.expr) -> int:
        """Offset of the operator token written between two operands (for error positions)."""
        start, stop = self.span(after).end, self.span(before).start
        for index in range(start, stop):
            if self.text[index] not in " \t\n()":
                return index
        return start

    def check_characters(self) -> None:
        match = _FORBIDDEN_CHARACTER_RE.search(self.text)
        if match is None:
            return
        character = match.group()
        if character == "#":
            message = "comments (#) are not allowed"
        elif character == "\\":
            message = "backslashes are not allowed"
        else:
            message = f"character U+{ord(character):04X} is not allowed"
        raise self.error_at(message, match.start())

    def check_lexemes(self) -> None:
        """Refuse, before ``ast.parse`` sees the text, what Python would only warn about.

        Python reads ``1if x else 2`` as ``1 if x else 2`` and emits a ``SyntaxWarning`` (printed
        to stderr, or an error under ``-W error``). The engine does no I/O and its messages must not
        depend on warning filters, so a number glued to the word after it is refused here, as are
        f-strings (their replacement fields are code that the tokenizer would scan the same way).
        """
        text = self.text
        for match in _LEXEME_RE.finditer(text):
            kind, end = match.lastgroup, match.end()
            if kind == "number" and _WORD_CHARACTER_RE.match(text, end) is not None:
                raise self.error_at("a number must be separated from the word after it", end)
            if kind == "word" and text[end : end + 1] in ("'", '"'):
                prefix = match.group().lower()
                if prefix in _TEMPLATE_PREFIXES:
                    what = "an f-string" if "f" in prefix else "a t-string"
                    raise self.error_at(f"{what} is not allowed", match.start())

    def parse(self) -> ast.expr:
        if not self.stripped.strip():
            raise self.error_at("empty expression", len(self.text))
        try:
            tree = ast.parse(self.stripped, mode="eval")
        except SyntaxError as exc:
            raise self._syntax_error(exc) from None
        except (RecursionError, MemoryError):  # the parser's own nesting limits
            raise self.error_at(
                f"expression is nested more than {MAX_DEPTH} levels deep", self.lead
            ) from None
        return tree.body

    def _syntax_error(self, exc: SyntaxError) -> ExpressionError:
        message = f"syntax error: {exc.msg or 'invalid syntax'}"
        if exc.lineno is None or exc.lineno < 1:
            return self.error_at(message, self.lead)
        index = min(exc.lineno, len(self.lines)) - 1
        line = self.lines[index]
        # SyntaxError.offset counts characters (1-based); 0/None means "at the end of the line".
        column = len(line) if exc.offset is None or exc.offset < 1 else exc.offset - 1
        return self.error_at(message, self.lead + self.line_starts[index] + min(column, len(line)))

    def check_tree(self, root: ast.expr) -> None:
        """Iteratively enforce :data:`MAX_DEPTH` before any recursive processing."""
        stack: list[tuple[ast.AST, int]] = [(root, 1)]
        while stack:
            node, depth = stack.pop()
            if depth > MAX_DEPTH and isinstance(node, ast.expr):
                raise self.error(f"expression is nested more than {MAX_DEPTH} levels deep", node)
            for child in ast.iter_child_nodes(node):
                stack.append((child, depth + 1 if isinstance(child, ast.expr) else depth))


class _Converter:
    """Whitelist conversion of a Python ``ast`` expression into :data:`Node` (with kind checks)."""

    def __init__(self, source: _Source) -> None:
        self.src = source

    def convert(self, node: ast.expr) -> tuple[Node, _Kind]:
        span = self.src.span(node)
        if isinstance(node, ast.Constant):
            return self._constant(node, span), _Kind.NUMBER
        if isinstance(node, ast.Name):
            return Name(self._name(node), span), _Kind.ANY
        if isinstance(node, ast.UnaryOp):
            return self._unary(node, span)
        if isinstance(node, ast.BinOp):
            return self._binary(node, span), _Kind.NUMBER
        if isinstance(node, ast.BoolOp):
            op = BoolOperator.AND if isinstance(node.op, ast.And) else BoolOperator.OR
            operands = tuple(self._expect(value, _Kind.CONDITION) for value in node.values)
            return BoolOp(op, operands, span), _Kind.CONDITION
        if isinstance(node, ast.Compare):
            return self._compare(node, span), _Kind.CONDITION
        if isinstance(node, ast.IfExp):
            return self._conditional(node, span)
        if isinstance(node, ast.Call):
            return self._call(node, span)
        description = _DISALLOWED.get(type(node), type(node).__name__)
        raise self.src.error(f"{description} is not allowed", node)

    # -- helpers -------------------------------------------------------------------------------

    def _expect(self, node: ast.expr, wanted: _Kind) -> Node:
        converted, kind = self.convert(node)
        if kind is not _Kind.ANY and kind is not wanted:
            raise self.src.error(f"expected {wanted.value}, found {kind.value}", node)
        return converted

    def _name(self, node: ast.Name) -> str:
        # Python NFKC-normalizes identifiers ("ﬁx" -> "fix"), so the text must equal the id.
        text = self.src.segment(node)
        if text != node.id or _NAME_RE.fullmatch(node.id) is None:
            raise self.src.error(
                f"invalid name {text!r}: use ASCII letters, digits and '_', starting with a letter",
                node,
            )
        if node.id in FUNCTION_NAMES:
            raise self.src.error(f"function {node.id!r} must be called with arguments", node)
        return node.id

    def _function_name(self, node: ast.Name) -> str:
        text = self.src.segment(node)
        if text != node.id or node.id not in FUNCTION_NAMES:
            raise self.src.error(
                f"unknown function {text!r}; allowed: {', '.join(sorted(FUNCTION_NAMES))}", node
            )
        return node.id

    def _number_text(self, node: ast.Constant, text: str) -> Decimal:
        value = node.value
        if isinstance(value, str):
            raise self.src.error(
                "text is only allowed as the unit of q(...) or the mode of round_to(...)", node
            )
        if isinstance(value, bool) or value is None or value is Ellipsis:
            raise self.src.error(f"{text} is not allowed", node)
        if isinstance(value, bytes):
            raise self.src.error("bytes literals are not allowed", node)
        if _NUMBER_TEXT_RE.fullmatch(text) is None:
            raise self.src.error(
                f"{text!r} is not allowed: numbers must be decimal literals such as 12.5", node
            )
        try:
            return D(text.replace("_", ""))
        except ValueError:
            raise self.src.error("number is outside the engine range", node) from None

    def _constant(self, node: ast.Constant, span: Span) -> Constant:
        text = self.src.text[span.start : span.end]
        number = self._number_text(node, text)
        value = Quantity.zero_literal(number) if number.is_zero() else Quantity(number)
        return Constant(value, text, span)

    def _whole_number(self, node: ast.expr, what: str, *, allow_negative: bool) -> int:
        sign, literal = 1, node
        if allow_negative and isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            sign, literal = -1, node.operand
        if not isinstance(literal, ast.Constant) or isinstance(literal.value, (bool, str)):
            raise self.src.error(f"{what} must be a whole-number literal", node)
        text = self.src.segment(literal)
        if _WHOLE_NUMBER_TEXT_RE.fullmatch(text) is None:
            raise self.src.error(f"{what} must be a whole-number literal", node)
        return sign * int(text.replace("_", ""))

    def _text(self, node: ast.expr) -> str | None:
        """The value of ``node`` if it is one plain quoted text literal, else ``None``.

        A prefix (``u"kW"``) or implicit concatenation (``"kW" "h"``, which Python joins into one
        constant) is refused: the reviewed formula text must show the effective unit or mode.
        """
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            return None
        if _TEXT_LITERAL_RE.fullmatch(self.src.segment(node)) is None:
            return None
        return node.value

    # -- operators -------------------------------------------------------------------------------

    def _unary(self, node: ast.UnaryOp, span: Span) -> tuple[Node, _Kind]:
        if isinstance(node.op, ast.USub):
            operand = self._expect(node.operand, _Kind.NUMBER)
            if isinstance(operand, Constant) and not isinstance(node.operand, ast.Call):
                # A sign written before a number literal is part of the entered value: fold it in
                # exactly (as arithmetic, unary minus would round past 34 digits). ``-q(...)``
                # stays an operation on the quantity.
                value = operand.value
                magnitude = _negated(value.value)
                signed = (
                    Quantity.zero_literal(magnitude)
                    if value.is_zero_literal
                    else Quantity(magnitude)
                )
                return Constant(signed, self.src.text[span.start : span.end], span), _Kind.NUMBER
            return Unary(UnaryOperator.NEG, operand, span), _Kind.NUMBER
        if isinstance(node.op, ast.Not):
            operand = self._expect(node.operand, _Kind.CONDITION)
            return Unary(UnaryOperator.NOT, operand, span), _Kind.CONDITION
        text = "+" if isinstance(node.op, ast.UAdd) else "~"
        raise self.src.error(f"unary {text} is not allowed", node)

    def _binary(self, node: ast.BinOp, span: Span) -> Binary:
        op = _BINARY_OPERATORS.get(type(node.op))
        if op is None:
            text = _DISALLOWED_BINARY_TEXT.get(type(node.op), type(node.op).__name__)
            offset = self.src.operator_offset(node.left, node.right)
            raise self.src.error_at(f"operator {text} is not allowed", offset)
        left = self._expect(node.left, _Kind.NUMBER)
        right = self._expect(node.right, _Kind.NUMBER)
        return Binary(op, left, right, span)

    def _compare(self, node: ast.Compare, span: Span) -> Compare:
        operands = [node.left, *node.comparators]
        ops: list[CompareOperator] = []
        for index, cmpop in enumerate(node.ops):
            op = _COMPARE_OPERATORS.get(type(cmpop))
            if op is None:
                text = _DISALLOWED_COMPARE_TEXT.get(type(cmpop), type(cmpop).__name__)
                offset = self.src.operator_offset(operands[index], operands[index + 1])
                raise self.src.error_at(f"operator {text!r} is not allowed", offset)
            ops.append(op)
        converted = [self.convert(operand) for operand in operands]
        for index, op in enumerate(ops):
            (_, left_kind), (_, right_kind) = converted[index], converted[index + 1]
            right_node = operands[index + 1]
            if op in (CompareOperator.EQ, CompareOperator.NE):
                if _Kind.ANY not in (left_kind, right_kind) and left_kind is not right_kind:
                    raise self.src.error(
                        f"cannot compare {left_kind.value} with {right_kind.value}", right_node
                    )
            else:
                for operand, kind in ((operands[index], left_kind), (right_node, right_kind)):
                    if kind is _Kind.CONDITION:
                        raise self.src.error(
                            f"operator {op.value} needs numbers, found {kind.value}", operand
                        )
        return Compare(converted[0][0], tuple(ops), tuple(item[0] for item in converted[1:]), span)

    def _conditional(self, node: ast.IfExp, span: Span) -> tuple[Node, _Kind]:
        condition = self._expect(node.test, _Kind.CONDITION)
        if_true, true_kind = self.convert(node.body)
        if_false, false_kind = self.convert(node.orelse)
        if _Kind.ANY not in (true_kind, false_kind) and true_kind is not false_kind:
            raise self.src.error(
                "both branches of a conditional must be numbers, or both conditions", node.orelse
            )
        kind = true_kind if true_kind is false_kind else _Kind.ANY
        return Conditional(condition, if_true, if_false, span), kind

    # -- calls -----------------------------------------------------------------------------------

    def _call(self, node: ast.Call, span: Span) -> tuple[Node, _Kind]:
        if not isinstance(node.func, ast.Name):
            raise self.src.error(
                f"only these functions can be called: {', '.join(sorted(FUNCTION_NAMES))}",
                node.func,
            )
        name = self._function_name(node.func)
        if node.keywords:
            raise self.src.error("keyword arguments are not allowed", node.keywords[0])
        for argument in node.args:
            if isinstance(argument, ast.Starred):
                raise self.src.error("starred arguments are not allowed", argument)
        if name == "q":
            return self._q(node, span), _Kind.NUMBER
        if name == "round_to":
            return self._round_to(node, span), _Kind.NUMBER
        if name == "in_months":
            return self._in_months(node, span), _Kind.CONDITION
        function = Function(name)
        least, most = _ARITY[function]
        given = len(node.args)
        if given < least or (most is not None and given > most):
            if most is None:
                expected = f"at least {least} arguments"
            else:
                expected = f"exactly {least} argument{'s' if least != 1 else ''}"
            raise self.src.error(f"{name}() takes {expected} ({given} given)", node)
        args = tuple(self._expect(argument, _Kind.NUMBER) for argument in node.args)
        return Call(function, args, span), _Kind.NUMBER

    def _q(self, node: ast.Call, span: Span) -> Constant:
        if len(node.args) != 2:
            raise self.src.error('q() takes a number and a unit text, e.g. q(200, "h")', node)
        amount_node, unit_node = node.args
        amount = self._q_amount(amount_node)
        unit = self._text(unit_node)
        if unit is None:
            raise self.src.error(
                'the unit of q() must be one quoted text literal such as "kWh"', unit_node
            )
        try:
            value = Quantity(amount, unit)
        except UnitError as exc:
            raise self.src.error(f"invalid unit: {exc}", unit_node) from None
        except CalcError as exc:
            raise self.src.error(str(exc), amount_node) from None
        return Constant(value, self.src.text[span.start : span.end], span)

    def _q_amount(self, node: ast.expr) -> Decimal:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = self._text(node)
            if text is None:
                raise self.src.error(
                    "the amount of q() must be a number literal or one quoted decimal, e.g. "
                    'q("6.912", "cents/kWh")',
                    node,
                )
            try:
                return D(text)
            except ValueError:
                raise self.src.error(
                    f"the amount of q() is not a decimal number: {text[:40]!r}", node
                ) from None
        negative, literal = False, node
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            negative, literal = True, node.operand
        if not isinstance(literal, ast.Constant) or isinstance(literal.value, str):
            raise self.src.error(
                'the amount of q() must be a number literal, e.g. q(200, "h")', node
            )
        number = self._number_text(literal, self.src.segment(literal))
        return _negated(number) if negative else number

    def _round_to(self, node: ast.Call, span: Span) -> RoundTo:
        if not 2 <= len(node.args) <= 3:
            raise self.src.error(
                "round_to() takes a value, a number of places and an optional mode, e.g. "
                'round_to(x, 2, "HALF_UP")',
                node,
            )
        operand = self._expect(node.args[0], _Kind.NUMBER)
        places = self._whole_number(node.args[1], "the places of round_to()", allow_negative=True)
        if not ENGINE_ETINY <= -places <= ENGINE_EMAX:
            raise self.src.error(
                f"round_to() places must lie between {-ENGINE_EMAX} and {-ENGINE_ETINY}",
                node.args[1],
            )
        mode = RoundingMode.HALF_UP
        if len(node.args) == 3:
            mode_node = node.args[2]
            mode_text = self._text(mode_node)
            if mode_text is None:
                raise self.src.error(
                    'the mode of round_to() must be one quoted text literal such as "DOWN"',
                    mode_node,
                )
            try:
                mode = RoundingMode(mode_text)
            except ValueError:
                raise self.src.error(
                    f"unknown rounding mode {mode_text[:40]!r}; use one of "
                    f"{', '.join(member.value for member in RoundingMode)}",
                    mode_node,
                ) from None
        return RoundTo(operand, places, mode, span)

    def _in_months(self, node: ast.Call, span: Span) -> InMonths:
        if not node.args:
            raise self.src.error("in_months() needs at least one month number (1-12)", node)
        months: list[int] = []
        for argument in node.args:
            month = self._whole_number(argument, "a month of in_months()", allow_negative=False)
            if not 1 <= month <= 12:
                raise self.src.error(f"month {month} is not between 1 and 12", argument)
            if month in months:
                raise self.src.error(f"month {month} is listed twice", argument)
            months.append(month)
        return InMonths(tuple(months), span)


def parse_expression(source: str) -> Expr:
    """Parse and validate formula text; raises :class:`ExpressionError` with its position.

    Results are cached by text (the tree is immutable), so parsing the same formula is cheap.
    """
    if not isinstance(source, str):
        raise TypeError(f"expression source must be str, not {type(source).__name__}")
    if len(source) > MAX_SOURCE_LENGTH:
        raise _Source(source[: MAX_SOURCE_LENGTH + 1]).error_at(
            f"expression is longer than {MAX_SOURCE_LENGTH} characters", MAX_SOURCE_LENGTH
        )
    return _parse(source)


@lru_cache(maxsize=4096)
def _parse(source: str) -> Expr:
    src = _Source(source)
    src.check_characters()
    src.check_lexemes()
    root = src.parse()
    src.check_tree(root)
    body, _ = _Converter(src).convert(root)
    return Expr(source, body)


# --------------------------------------------------------------------------------------- evaluation


@dataclass(frozen=True, slots=True)
class MissingValue:
    """Binds a name whose value is known to be missing (null) -- never treated as zero."""

    reason: str | None = None


MISSING: Final = MissingValue()

EnvValue = Quantity | bool | int | MissingValue
"""What an environment maps names to; an ``int`` (e.g. ``billing_month``) is dimensionless."""

Value = Quantity | bool
"""What a formula evaluates to: a number or a condition."""


@dataclass(frozen=True, slots=True)
class Substitution:
    """A name and the value it was evaluated with (``int`` environment values as quantities)."""

    name: str
    value: Value


@dataclass(frozen=True, slots=True)
class Conversion:
    """A traced dimension change: ``power_factor``, ``apparent`` or ``as_reactive_equiv``."""

    function: Function
    arguments: tuple[Quantity, ...]
    result: Quantity
    span: Span


@dataclass(frozen=True, slots=True)
class Evaluation:
    """A result with its trace: substitutions in first-use order and conversions in call order."""

    value: Value
    substitutions: tuple[Substitution, ...]
    conversions: tuple[Conversion, ...]


@dataclass(frozen=True, slots=True)
class _Unknown:
    """A sub-result that depends on missing names."""

    names: tuple[str, ...]


def _unknown(*parts: object) -> _Unknown:
    names: dict[str, None] = {}
    for part in parts:
        if isinstance(part, _Unknown):
            names.update(dict.fromkeys(part.names))
    return _Unknown(tuple(names))


def _error(span: Span, message: str) -> ExpressionError:
    return ExpressionError(message, offset=span.start, line=span.line, column=span.column)


def _decimal(operation: Callable[[], Decimal], what: str) -> Decimal:
    with engine_context():
        try:
            return operation()
        except DecimalException as exc:
            raise CalcError(f"{what} failed ({type(exc).__name__})") from exc


_KW: Final = Unit({"kW": 1})
_KWH: Final = Unit({"kW": 1, "h": 1})
_REACTIVE_OF: Final = {_KW: Unit({"kVAR": 1}), _KWH: Unit({"kVAR": 1, "h": 1})}
_APPARENT_OF: Final = {_KW: Unit({"kVA": 1}), _KWH: Unit({"kVA": 1, "h": 1})}
_MONTH_NUMBERS: Final = frozenset(Decimal(month) for month in range(1, 13))


def _sqrt(value: Quantity) -> Quantity:
    exponents = value.unit.exponents
    if any(exponent % 2 for exponent in exponents.values()):
        raise UnitError(f"cannot take the square root of {value.unit}: an exponent is odd")
    if value.value.is_signed() and not value.value.is_zero():
        raise CalcError(f"square root of a negative value: {value}")
    root = _decimal(value.value.copy_abs().sqrt, f"square root of {value}")
    return Quantity(root, Unit({symbol: power // 2 for symbol, power in exponents.items()}))


def _hypotenuse(function: Function, real: Quantity, reactive: Quantity) -> Decimal:
    if real.unit not in _REACTIVE_OF or reactive.unit != _REACTIVE_OF[real.unit]:
        raise UnitError(
            f"{function}() needs real power in kW (or energy in kWh) and reactive power in kVAR "
            f"(or kVARh) on the same time basis, not {real.unit} and {reactive.unit}"
        )
    p, q = real.value, reactive.value
    return _decimal(lambda: (p * p + q * q).sqrt(), f"{function}() of {real} and {reactive}")


def _power_factor(real: Quantity, reactive: Quantity) -> Quantity:
    """``p / sqrt(p^2 + q^2)``, dimensionless."""
    apparent = _hypotenuse(Function.POWER_FACTOR, real, reactive)
    if apparent.is_zero():
        raise CalcError("power factor is undefined when real and reactive power are both zero")
    return Quantity(_decimal(lambda: real.value / apparent, "power factor"))


def _apparent(real: Quantity, reactive: Quantity) -> Quantity:
    """``sqrt(p^2 + q^2)`` in kVA (or kVAh)."""
    return Quantity(_hypotenuse(Function.APPARENT, real, reactive), _APPARENT_OF[real.unit])


def _as_reactive_equiv(value: Quantity) -> Quantity:
    """kW -> kVAR, kWh -> kVARh with the same magnitude (an explicit, traced conversion)."""
    target = _REACTIVE_OF.get(value.unit)
    if target is None:
        raise UnitError(f"as_reactive_equiv() converts kW or kWh, not {value.unit}")
    return Quantity(value.value, target)


class _Evaluator:
    def __init__(self, env: Mapping[str, EnvValue]) -> None:
        self._env = env
        self.substitutions: dict[str, Value] = {}
        self.conversions: list[Conversion] = []

    def value(self, node: Node) -> Value | _Unknown:
        try:
            return self._value(node)
        except EngineError as exc:
            if exc.offset is None:  # raised by this node's own operation, not a sub-expression's
                exc.locate(node.span.start, node.span.line, node.span.column)
            raise

    def _value(self, node: Node) -> Value | _Unknown:
        match node:
            case Constant():
                return node.value
            case Name():
                return self._lookup(node.id)
            case Unary():
                return self._unary(node)
            case Binary():
                return self._binary(node)
            case BoolOp():
                return self._bool_op(node)
            case Compare():
                return self._compare(node)
            case Conditional():
                condition = self._condition(node.condition)
                if isinstance(condition, _Unknown):
                    return condition
                return self.value(node.if_true if condition else node.if_false)
            case Call():
                return self._call(node)
            case RoundTo():
                operand = self._number(node.operand)
                if isinstance(operand, _Unknown):
                    return operand
                return operand.quantize(node.places, node.mode)
            case InMonths():
                return self._in_months(node)
        assert_never(node)

    def _lookup(self, name: str) -> Value | _Unknown:
        if name in self.substitutions:
            return self.substitutions[name]
        if name not in self._env:
            return _Unknown((name,))
        raw = self._env[name]
        value: Value
        if isinstance(raw, MissingValue):
            return _Unknown((name,))
        if isinstance(raw, (Quantity, bool)):
            value = raw
        elif isinstance(raw, int):
            try:
                value = Quantity(raw)
            except ValueError:  # more than 6,145 digits: not an engine decimal (A§4.1)
                raise CalcError(
                    f"environment value for {name!r} is outside the engine range"
                ) from None
        else:
            raise TypeError(
                f"environment value for {name!r} must be a Quantity, bool, int or MissingValue, "
                f"not {type(raw).__name__}"
            )
        self.substitutions[name] = value
        return value

    def _number(self, node: Node) -> Quantity | _Unknown:
        result = self.value(node)
        if isinstance(result, bool):
            raise _error(node.span, "expected a number, found a condition (true/false)")
        return result

    def _condition(self, node: Node) -> bool | _Unknown:
        result = self.value(node)
        if isinstance(result, Quantity):
            raise _error(node.span, "expected a condition (true/false), found a number")
        return result

    def _unary(self, node: Unary) -> Value | _Unknown:
        if node.op is UnaryOperator.NOT:
            condition = self._condition(node.operand)
            return condition if isinstance(condition, _Unknown) else not condition
        number = self._number(node.operand)
        return number if isinstance(number, _Unknown) else -number

    def _binary(self, node: Binary) -> Quantity | _Unknown:
        left, right = self._number(node.left), self._number(node.right)
        if isinstance(left, _Unknown) or isinstance(right, _Unknown):
            return _unknown(left, right)
        match node.op:
            case BinaryOperator.ADD:
                return left + right
            case BinaryOperator.SUB:
                return left - right
            case BinaryOperator.MUL:
                return left * right
            case BinaryOperator.DIV:
                return left / right
        assert_never(node.op)

    def _bool_op(self, node: BoolOp) -> bool | _Unknown:
        decisive = node.op is BoolOperator.OR  # True decides "or"; False decides "and"
        unknowns: list[_Unknown] = []
        for operand in node.operands:
            try:
                result = self._condition(operand)
            except EngineError:
                if not unknowns:
                    raise
                # Left to right, this operand is reached only if every unknown operand before it
                # turns out non-decisive, so whether the error happens depends on missing inputs.
                # Later operands cannot decide: in that case evaluation would stop at the error.
                return _unknown(*unknowns)
            if isinstance(result, _Unknown):
                unknowns.append(result)
            elif result is decisive:
                return decisive
        return _unknown(*unknowns) if unknowns else not decisive

    def _compare(self, node: Compare) -> bool | _Unknown:
        left = self.value(node.left)
        unknowns: list[_Unknown] = []
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            try:
                right = self.value(right_node)
                if isinstance(left, _Unknown) or isinstance(right, _Unknown):
                    unknowns.append(_unknown(left, right))
                elif not _compare_values(op, left, right, node.span):
                    return False  # a definite False pair decides the chain
            except EngineError:
                if not unknowns:
                    raise
                return _unknown(*unknowns)  # reached only if the unknown pairs before it hold
            left = right
        return _unknown(*unknowns) if unknowns else True

    def _call(self, node: Call) -> Quantity | _Unknown:
        args = [self._number(argument) for argument in node.args]
        numbers = [argument for argument in args if isinstance(argument, Quantity)]
        if len(numbers) != len(args):
            return _unknown(*args)
        match node.function:
            case Function.MIN:
                return qmin(numbers[0], *numbers[1:])
            case Function.MAX:
                return qmax(numbers[0], *numbers[1:])
            case Function.ABS:
                return abs(numbers[0])
            case Function.CEIL:
                return numbers[0].quantize(0, RoundingMode.CEILING)
            case Function.FLOOR:
                return numbers[0].quantize(0, RoundingMode.FLOOR)
            case Function.SQRT:
                return _sqrt(numbers[0])
            case Function.POWER_FACTOR:
                result = _power_factor(numbers[0], numbers[1])
            case Function.APPARENT:
                result = _apparent(numbers[0], numbers[1])
            case Function.AS_REACTIVE_EQUIV:
                result = _as_reactive_equiv(numbers[0])
            case _:
                assert_never(node.function)
        self.conversions.append(Conversion(node.function, tuple(numbers), result, node.span))
        return result

    def _in_months(self, node: InMonths) -> bool | _Unknown:
        month = self._lookup("billing_month")
        if isinstance(month, _Unknown):
            return month
        if (
            isinstance(month, bool)
            or not month.unit.is_dimensionless
            or month.value not in _MONTH_NUMBERS
        ):
            raise _error(
                node.span, f"in_months() needs billing_month as a month number 1-12, not {month}"
            )
        return int(month.value) in node.months


def _compare_values(op: CompareOperator, left: Value, right: Value, span: Span) -> bool:
    if isinstance(left, bool) and isinstance(right, bool):
        if op is CompareOperator.EQ:
            return left == right
        if op is CompareOperator.NE:
            return left != right
        raise _error(span, f"operator {op.value} needs numbers; conditions only support == and !=")
    if isinstance(left, bool) or isinstance(right, bool):
        raise _error(span, "cannot compare a number with a condition")
    order = left.compare(right)  # unit-checked; the zero literal is unit-polymorphic
    match op:
        case CompareOperator.LT:
            return order < 0
        case CompareOperator.LE:
            return order <= 0
        case CompareOperator.GT:
            return order > 0
        case CompareOperator.GE:
            return order >= 0
        case CompareOperator.EQ:
            return order == 0
        case CompareOperator.NE:
            return order != 0
    assert_never(op)


def evaluate_traced(expr: Expr, env: Mapping[str, EnvValue]) -> Evaluation:
    """Evaluate ``expr`` and return the value with its trace (substitutions, conversions).

    Raises :class:`MissingInputError` when the result depends on absent/missing names,
    :class:`ExpressionError` for a value of the wrong kind, ``UnitError`` for incompatible units
    and ``CalcError`` for arithmetic failures (division by zero, overflow, ...). The result may be
    the polymorphic zero (e.g. the formula ``0``); callers place it in their expected unit.
    """
    if not isinstance(expr, Expr):
        raise TypeError(f"evaluate() needs a parsed Expr, not {type(expr).__name__}")
    evaluator = _Evaluator(env)
    result = evaluator.value(expr.body)
    if isinstance(result, _Unknown):
        raise MissingInputError(*result.names)
    substitutions = tuple(
        Substitution(name, value) for name, value in evaluator.substitutions.items()
    )
    return Evaluation(result, substitutions, tuple(evaluator.conversions))


def evaluate(expr: Expr, env: Mapping[str, EnvValue]) -> Value:
    """Evaluate ``expr`` over ``env`` (see :func:`evaluate_traced`)."""
    return evaluate_traced(expr, env).value


def builtin_values(billing_month: str, billing_days: int) -> dict[str, EnvValue]:
    """Environment entries for the built-in names (A§4.3), from the calculation context.

    ``billing_month`` is the month number (1-12) and ``period_year`` the year of the utility
    billing month (``"YYYY-MM"``, A§4.0); ``billing_days`` (the resolved day count of the service
    period, A§4.0) and ``days_in_billing_month`` (calendar days of the billing month) are in
    ``day``.
    """
    year, month = parse_month(billing_month)
    if isinstance(billing_days, bool) or not isinstance(billing_days, int):
        raise TypeError(f"billing_days must be an int, not {type(billing_days).__name__}")
    if not 1 <= billing_days <= MAX_BILLING_DAYS:
        raise DateError(
            f"billing_days must be between 1 and {MAX_BILLING_DAYS}, got {billing_days}"
        )
    return {
        "billing_month": month,
        "billing_days": Quantity(billing_days, "day"),
        "days_in_billing_month": Quantity(month_days(billing_month), "day"),
        "period_year": year,
    }
