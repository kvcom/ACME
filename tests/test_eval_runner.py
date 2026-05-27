from acme_app.evaluation.eval_cases import EVAL_CASES


def test_case_count() -> None:
    assert len(EVAL_CASES) == 13
