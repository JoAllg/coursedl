"""The examples from PFERD's CONFIG.md, "Transformation rules"."""

from pathlib import PurePath

import pytest

from coursedl.transform import RuleParseError, Transformer

CASES = [
    ("foo/bar --> baz", "foo/bar", "baz"),
    ("foo/bar --> baz", "foo/bar/file", "baz/file"),
    ("foo/bar --> baz", "foo/baz", "foo/baz"),
    ("foo/bar --> !", "foo/bar/file", None),
    ("foo -name-> bar/baz", "a/foo/b", "a/bar/baz/b"),
    ("foo -name-> !", "a/foo/b", None),
    ("foo/bar -exact-> baz", "foo/bar", "baz"),
    ("foo/bar -exact-> baz", "foo/bar/file", "foo/bar/file"),
    (r"f(oo+)/be?ar -re-> B{g1.upper()}H/fear", "fooooo/bear", "BOOOOOH/fear"),
    (r"f(oo+)/be?ar -re-> B{g1.upper()}H/fear", "foo/bar/x", "BOOH/fear/x"),
    (r"(.*)\.jpeg -name-re-> {g1}.jpg", "a/b.jpeg/c.jpeg", "a/b.jpg/c.jpg"),
    (r"\..+ -name-re-> !", "a/.git/config", None),
    (r"f(oo+)/be?ar -exact-re-> B{g1.upper()}H/fear", "foo/bar/x", "foo/bar/x"),
    # First match wins; ">>" continues with the next rules.
    ("tutorials/tut_02 --> my_tut\ntutorials --> !", "tutorials/tut_02/a", "my_tut/a"),
    ("tutorials/tut_02 --> my_tut\ntutorials --> !", "tutorials/tut_01/a", None),
    ("a -->> b\nb --> c", "a/x", "c/x"),
    # Integer formatting of groups; a target with spaces needs quotes.
    (
        r'Folien/vorlesung-(\d+)\.pdf -re-> "Folien/VL {i1:02}.pdf"',
        "Folien/vorlesung-3.pdf",
        "Folien/VL 03.pdf",
    ),
    ('"Material A" --> B', "Material A/x", "B/x"),
]


@pytest.mark.parametrize(("rules", "path", "expected"), CASES)
def test_transform(rules: str, path: str, expected: str | None) -> None:
    result = Transformer(rules).transform(PurePath(path))
    assert (str(result) if result is not None else None) == expected


def test_parse_error() -> None:
    with pytest.raises(RuleParseError):
        _ = Transformer("foo bar --> baz")
