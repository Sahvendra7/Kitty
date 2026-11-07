"""The decision chain makes an edit or a gap detectable, by anyone holding the room's text."""
from __future__ import annotations

from kitty import pot as P


def chained(*texts: str) -> list[str]:
    out, prev = [], ""
    for seq, text in enumerate(texts, start=1):
        tagged, prev = P.chain_tag(text, seq, prev)
        out.append(tagged)
    return out


def test_tag_format():
    tagged, h = P.chain_tag("**k1** Asha asked each person for ₹5,000.00.", 1, "")
    assert tagged == f"**k1** Asha asked each person for ₹5,000.00. [d1 {h}]"
    assert len(h) == 12 and h == P.chain_next("", "**k1** Asha asked each person for ₹5,000.00.")


def test_a_clean_chain_passes():
    assert P.chain_check(chained("one", "two", "three")) is None


def test_untagged_lines_are_skipped():
    a, b, c = chained("one", "two", "three")
    assert P.chain_check(["Kitty is open", a, "**c1** an expense", b, "balances", c]) is None


def test_an_edited_line_is_caught():
    lines = chained("one", "two", "three")
    lines[1] = lines[1].replace("two", "TWO")
    assert P.chain_check(lines) == "d2"


def test_a_removed_line_is_caught():
    a, _, c = chained("one", "two", "three")
    assert P.chain_check([a, c]) == "d3"


def test_a_removed_first_line_is_caught():
    _, b, c = chained("one", "two", "three")
    assert P.chain_check([b, c]) == "d2"


def test_an_altered_hash_is_caught():
    lines = chained("one")
    assert P.chain_check([lines[0][:-2] + "0]" if not lines[0].endswith("0]") else lines[0][:-2] + "1]"]) == "d1"
