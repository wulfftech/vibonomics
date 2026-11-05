"""The Hard suite's tool and chat tasks are graded without a sandbox: check that a right answer scores and the
plausible wrong ones do not. (Its coding tasks are proven by ``test_suites.py``, which runs their reference
solutions.)"""

from __future__ import annotations

import pytest

from vibonomics.evals.graders import grade_answer, grade_tool_calls
from vibonomics.evals.suites import SUITES

# Tasks are taken from the suite: the task modules import ``suites`` themselves, so importing one first is a cycle.
_HARD = {task.id: task for task in SUITES["hard"].tasks}
HARD_TOOL_CHAIN = _HARD["tool-chain-ids"]
HARD_TOOL_SCHEDULE = _HARD["tool-schedule"]
HARD_BULLETS = _HARD["instr-bullets"]
HARD_REVISIONS = _HARD["acc-revisions"]
HARD_TRAIN = _HARD["reason-train"]
HARD_MODULAR = _HARD["reason-modular"]


def call(name: str, **arguments):
    return {"name": name, "arguments": arguments, "error": None}


CHAIN = [
    call("find_user", email="dana@example.com"),
    call("list_orders", user_id="u_482"),
    call("get_order", order_id="o_93"),
]


def test_the_chain_needs_every_id_to_come_from_the_previous_result():
    assert grade_tool_calls(CHAIN, "It was $184.20.", HARD_TOOL_CHAIN.check)[0] == 100
    # The first order in the list instead of the most recent one.
    wrong_order = [*CHAIN[:2], call("get_order", order_id="o_91")]
    score, _ = grade_tool_calls(wrong_order, "", HARD_TOOL_CHAIN.check)
    assert score == pytest.approx(66.7)
    # An id the model made up rather than read from find_user.
    invented = [CHAIN[0], call("list_orders", user_id="dana"), CHAIN[2]]
    assert grade_tool_calls(invented, "", HARD_TOOL_CHAIN.check)[0] == pytest.approx(66.7)
    assert grade_tool_calls([CHAIN[0]], "", HARD_TOOL_CHAIN.check)[0] == 0


def test_the_chain_answer_must_carry_the_total():
    assert grade_answer("184.20 AUD", HARD_TOOL_CHAIN.check)[0] == 100
    assert grade_answer("I could not find it.", HARD_TOOL_CHAIN.check)[0] == 0


SCHEDULE = call(
    "create_event",
    title="Q1 planning",
    start="2026-11-10T14:30",
    duration_minutes=45,
    attendees=["sam@example.com", "lee@example.com"],
)


@pytest.mark.parametrize(
    ("change", "score"),
    [
        ({}, 100),
        ({"start": "2026-11-11T14:30"}, 0),  # Wednesday: next Tuesday is the 10th
        ({"start": "2026-11-03T14:30"}, 0),  # the Tuesday just gone
        ({"start": "2026-11-10T14:30:00"}, 0),  # seconds were ruled out
        ({"start": "2026-11-10T02:30"}, 0),
        ({"duration_minutes": "45"}, 0),
        ({"attendees": ["sam@example.com"]}, 0),
    ],
)
def test_the_event_arguments_must_match_exactly(change, score):
    event = {**SCHEDULE, "arguments": {**SCHEDULE["arguments"], **change}}
    assert grade_tool_calls([event], "Booked.", HARD_TOOL_SCHEDULE.check)[0] == score


def test_an_email_instead_of_the_event_scores_nothing():
    email = call("send_email", to="sam@example.com", subject="Q1 planning")
    assert grade_tool_calls([email], "", HARD_TOOL_SCHEDULE.check)[0] == 0
    assert grade_tool_calls([], "Done.", HARD_TOOL_SCHEDULE.check)[0] == 0


ONE = "- Backups protect work from sudden loss"
TWO = "- Copies stored elsewhere still survive local disasters"
THREE = "- Teams that practise recovery can quickly restore."


def bullets(*lines: str) -> str:
    return "\n".join(lines)


@pytest.mark.parametrize(
    ("answer", "score"),
    [
        (bullets(ONE, TWO, THREE), 100),
        (bullets(ONE, "", TWO, THREE, ""), 100),  # blank lines are not bullets
        (bullets(ONE, TWO, THREE.rstrip(".")), 0),
        (bullets(ONE, TWO, THREE, "- Extra bullet that makes four now"), 0),
        (bullets(ONE.replace("work", "data"), TWO, THREE), 0),
        (bullets(ONE, TWO.replace("local", "lazy zebra"), THREE), 0),
        (bullets("- Backups protect work", TWO, THREE), 0),
        (bullets(ONE, TWO, "- Teams that practise recovery can quickly and calmly restore it all."), 0),
        (bullets(*(line.replace("- ", "* ") for line in (ONE, TWO, THREE))), 0),
    ],
    ids=[
        "good",
        "blank-lines",
        "no-full-stop",
        "four-bullets",
        "the-word-data",
        "the-letter-z",
        "too-short",
        "wrong-ending-and-too-long",
        "wrong-marker",
    ],
)
def test_bullet_rules(answer, score):
    assert grade_answer(answer, HARD_BULLETS.check)[0] == score


@pytest.mark.parametrize(
    ("answer", "score"),
    [
        ("They assemble at the entrance to the north car park on 16 October.", 100),
        ("On the 16th of October, at the north car park entrance.", 100),
        ("October 16, at the north car park.", 100),
        ("They assemble on the south lawn on 16 October.", 0),  # the update's general rule, not the exception
        ("They assemble at the north car park on 14 October.", 0),  # the original date
        ("They assemble at the north car park.", 0),  # no date
    ],
)
def test_the_memo_answer_needs_the_latest_date_and_the_exception(answer, score):
    assert grade_answer(answer, HARD_REVISIONS.check)[0] == score


@pytest.mark.parametrize(
    ("answer", "score"), [("12:52", 100), ("It arrives at 12:52.", 100), ("12:40", 0), ("13:52", 0)]
)
def test_the_train_arrival(answer, score):
    assert grade_answer(answer, HARD_TRAIN.check)[0] == score


@pytest.mark.parametrize(
    ("answer", "score"), [("4", 100), ("4.", 100), ("**4**", 100), ("2", 0), ("The remainder is 4", 0), ("14", 0)]
)
def test_the_remainder(answer, score):
    assert grade_answer(answer, HARD_MODULAR.check)[0] == score
