import pytest

from acme_app.api.routes_evidence import split_evidence_ref


def test_split_evidence_ref_parses_kind_and_identifier():
    assert split_evidence_ref('issue:ISS-102') == ('issue', 'ISS-102')


def test_split_evidence_ref_rejects_invalid_value():
    with pytest.raises(ValueError):
        split_evidence_ref('ISS-102')
