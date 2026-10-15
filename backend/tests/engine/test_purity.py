"""S01 - engine purity (A§2, A§3.1, A§3.6, CLAUDE.md rule 3).

``tariff_engine`` must stay pure and deterministic: no I/O, database, web, AI, clock, randomness, or
binary floats. This test parses every module of the package (AST, never executed) and fails on any
import outside the A§2 allowlist and on any forbidden name, attribute or float literal. The scanner
itself is checked against known-bad snippets so it cannot silently pass.
"""

from __future__ import annotations

import ast
import pkgutil
from pathlib import Path

import pytest

import tariff_engine

ENGINE_DIR = Path(tariff_engine.__file__).resolve().parent

# A§2: the only top-level modules the engine may import (absolute imports). Relative imports stay
# inside the package and are always allowed. Adding a module here is a contract change.
ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "abc",
        "ast",  # the expression language parses with ast.parse(mode="eval") (A§4.3)
        "bisect",
        "calendar",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",  # date/timedelta only: .now/.today are forbidden attributes below
        "decimal",
        "enum",
        "functools",
        "graphlib",
        "hashlib",
        "heapq",
        "itertools",
        "json",
        "operator",
        "re",
        "types",
        "typing",
        "pydantic",
        "pydantic_core",  # pydantic's exactly-pinned core (PydanticCustomError)
        "tariff_engine",
    }
)
# Modules A§2 names as forbidden; the allowlist must never grow to include one of them.
KNOWN_FORBIDDEN_MODULES = frozenset(
    {
        "tariff_app",
        "sqlalchemy",
        "fastapi",
        "starlette",
        "httpx",
        "pdfplumber",
        "anthropic",
        "time",
        "random",
        "secrets",
        "uuid",
        "zoneinfo",
        "os",
        "sys",
        "io",
        "pathlib",
        "codecs",
        "socket",
        "http",
        "urllib",
        "subprocess",
        "threading",
        "asyncio",
        "sqlite3",
        "logging",
        "importlib",
        "builtins",
        "pickle",
        "math",
        "cmath",
        "statistics",
    }
)
FORBIDDEN_NAMES = frozenset(
    {
        "float",
        "complex",
        "open",
        "print",
        "input",
        "eval",
        "exec",
        "compile",
        "__import__",
        "breakpoint",
        # dynamic access would bypass the attribute and name checks (getattr(datetime, "now"))
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "__builtins__",
    }
)
FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "now",  # datetime.now
        "utcnow",
        "today",  # date.today
        "fromtimestamp",
        "utcfromtimestamp",
        "timestamp",  # datetime -> float
        "from_float",  # Decimal.from_float
        "create_decimal_from_float",
        "__float__",  # Decimal("1.5").__float__() is float() without the name
        "__complex__",
        "literal_eval",  # ast.literal_eval("0.06912") is a float; A§4.3 parses literal source text
        "__globals__",  # function.__globals__["__builtins__"]
        "__builtins__",
    }
)
# json.load/json.loads turn JSON numbers into binary floats unless given parse_float=Decimal.
JSON_PARSERS = frozenset({"load", "loads"})


def _import_allowed(module: str) -> bool:
    return module.split(".")[0] in ALLOWED_IMPORT_ROOTS


def _is_json_module(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "json"


def _parses_floats_as_decimal(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "parse_float":
            value = keyword.value
            return (isinstance(value, ast.Name) and value.id == "Decimal") or (
                isinstance(value, ast.Attribute) and value.attr == "Decimal"
            )
    return False


def scan_source(source: str, filename: str = "<snippet>") -> list[str]:
    """Return one message per forbidden construct found in ``source``."""
    violations: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        where = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            violations.extend(
                f"{where}: import {alias.name} (not in the A§2 allowlist)"
                for alias in node.names
                if not _import_allowed(alias.name)
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and not _import_allowed(module):
                violations.append(f"{where}: from {module} import ... (not in the A§2 allowlist)")
            for alias in node.names:
                # importing a forbidden name directly would bypass the attribute/name checks
                if alias.name in FORBIDDEN_ATTRIBUTES or alias.name in FORBIDDEN_NAMES:
                    violations.append(f"{where}: from {module} import {alias.name}")
                elif module == "json" and alias.name in JSON_PARSERS:
                    violations.append(f"{where}: from json import {alias.name} (use json.loads)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, (float, complex)):
            violations.append(f"{where}: float literal {node.value!r}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append(f"{where}: forbidden name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            violations.append(f"{where}: forbidden attribute .{node.attr}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in JSON_PARSERS
            and _is_json_module(node.func.value)
            and not _parses_floats_as_decimal(node)
        ):
            violations.append(f"{where}: json.{node.func.attr} without parse_float=Decimal")
    return violations


def engine_source_files() -> list[Path]:
    return sorted(ENGINE_DIR.rglob("*.py"))


@pytest.mark.req("R05-DETERMINISM")
def test_every_engine_module_is_pure() -> None:
    violations: list[str] = []
    for path in engine_source_files():
        relative = path.relative_to(ENGINE_DIR.parent).as_posix()
        violations.extend(scan_source(path.read_text(encoding="utf-8"), relative))
    assert violations == []


def test_the_scan_covers_every_engine_module() -> None:
    scanned = {
        ".".join(path.relative_to(ENGINE_DIR.parent).with_suffix("").parts)
        for path in engine_source_files()
    }
    importable = {
        name
        for _, name, _ in pkgutil.walk_packages(tariff_engine.__path__, prefix="tariff_engine.")
    }
    assert importable <= scanned
    assert {
        "tariff_engine.__init__",
        "tariff_engine.dates",
        "tariff_engine.decimals",
        "tariff_engine.errors",
        "tariff_engine.units",
    } <= scanned


@pytest.mark.parametrize(
    "snippet",
    [
        "x = 1.5",
        "x = 1e3",
        "x = 2j",
        "y = float('1')",
        "def f(x: float) -> None: ...",
        "import random",
        "import uuid",
        "import time",
        "from time import monotonic",
        "import secrets",
        "import os",
        "from pathlib import Path",
        "import socket",
        "import math",
        "import logging",
        "import sqlalchemy.orm",
        "from fastapi import FastAPI",
        "import pdfplumber",
        "import anthropic",
        "import tariff_app",
        "from tariff_app.config import Settings",
        "from datetime import datetime\nstamp = datetime.now()",
        "from datetime import date\nday = date.today()",
        "import datetime\nstamp = datetime.datetime.utcnow()",
        "from decimal import Decimal\nx = Decimal.from_float(1)",
        "handle = open('rates.csv')",
        "print('debug')",
        "value = eval('1')",
        "module = __import__('random')",
        # outside the allowlist: C backends, other I/O, network, AI/DB/document libraries
        "import _random\nx = _random.Random(1).random()",
        "import posix\nnames = posix.listdir('.')",
        "import nt",
        "import _thread",
        "import _socket",
        "import _io\nf = _io.open('x')",
        "import codecs\nf = codecs.open('rates.csv')",
        "import gzip\nf = gzip.open('rates.gz')",
        "import ftplib",
        "import imaplib",
        "import xmlrpc.client",
        "import urllib3",
        "import aiohttp",
        "import openai",
        "import psycopg2",
        "import pypdf",
        "import pickle\nx = pickle.load(handle)",
        "import sched",
        "import locale",
        "import tariff_engine_extras",  # a root that merely starts with the package name
        # dynamic access that would bypass the name/attribute checks
        "from datetime import datetime\nstamp = getattr(datetime, 'now')()",
        "f = __builtins__['open']",
        "f = globals()['__builtins__']",
        "names = vars()",
        "def f(): ...\ng = f.__globals__",
        # float back doors that name neither `float` nor a float literal
        "from decimal import Decimal\nx = Decimal('1.5').__float__()",
        "from decimal import Decimal\nx = Decimal('1.5').__complex__()",
        "import ast\nx = ast.literal_eval('0.06912')",
        "from ast import literal_eval",
        "import json\nx = json.loads('{\"rate\": 0.06912}')",
        "import json\nx = json.load(handle)",
        "import json\nx = json.loads(text, parse_float=str)",
        "from json import loads",
    ],
)
def test_scanner_flags_forbidden_constructs(snippet: str) -> None:
    assert scan_source(snippet)


def test_the_allowlist_contains_no_forbidden_module() -> None:
    assert ALLOWED_IMPORT_ROOTS.isdisjoint(KNOWN_FORBIDDEN_MODULES)
    for module in sorted(KNOWN_FORBIDDEN_MODULES):
        assert scan_source(f"import {module}"), module
        assert scan_source(f"from {module} import x"), module


def test_scanner_accepts_pure_code() -> None:
    pure = (
        "from __future__ import annotations\n"
        "import hashlib, json, re\n"
        "from datetime import date, timedelta\n"
        "from decimal import Decimal, localcontext\n"
        "from pydantic import BaseModel\n"
        "from tariff_engine.decimals import D\n"
        "from pydantic_core import PydanticCustomError\n"
        "from contextlib import AbstractContextManager\n"
        "import ast, calendar, collections.abc, dataclasses, enum, functools, operator, typing\n"
        "from . import units\n"
        "from .units import Quantity\n"
        "start = date(2026, 1, 1) + timedelta(days=1)\n"
        "total = Decimal('0.1') + D('0.2') * 3\n"
        "digest = hashlib.sha256(json.dumps({'a': 1}).encode()).hexdigest()\n"
        "data = json.loads('{\"rate\": 0.06912}', parse_float=Decimal)\n"
        "same = json.loads(text, parse_float=decimal.Decimal)\n"
    )
    assert scan_source(pure) == []
