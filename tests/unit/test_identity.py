from uuid import uuid4

import pytest

from boundaryline.contracts import Scenario
from boundaryline.identity import InvalidToken, issue_token, verify_token


def test_signed_token_round_trip() -> None:
    run_id = uuid4()
    token = issue_token(
        subject="patch-agent-lab",
        roles=("monitoring-reader",),
        audience="boundaryline-broker",
        run_id=run_id,
        scenario=Scenario.C,
        secret="test-secret",
        now=1_000,
    )
    claims = verify_token(
        token,
        secret="test-secret",
        audience="boundaryline-broker",
        now=1_001,
    )
    assert claims.sub == "patch-agent-lab"
    assert claims.run_id == run_id
    assert claims.scenario is Scenario.C


def test_token_rejects_tampering() -> None:
    token = issue_token(
        subject="patch-agent-lab",
        roles=("monitoring-reader",),
        audience="boundaryline-broker",
        run_id=uuid4(),
        secret="test-secret",
        now=1_000,
    )
    with pytest.raises(InvalidToken, match="signature"):
        verify_token(
            token + "changed",
            secret="test-secret",
            audience="boundaryline-broker",
            now=1_001,
        )


def test_token_rejects_wrong_audience() -> None:
    token = issue_token(
        subject="patch-agent-lab",
        roles=("monitoring-reader",),
        audience="boundaryline-broker",
        run_id=uuid4(),
        secret="test-secret",
        now=1_000,
    )
    with pytest.raises(InvalidToken, match="audience"):
        verify_token(token, secret="test-secret", audience="other", now=1_001)


def test_token_rejects_expiry() -> None:
    token = issue_token(
        subject="patch-agent-lab",
        roles=("monitoring-reader",),
        audience="boundaryline-broker",
        run_id=uuid4(),
        secret="test-secret",
        ttl_seconds=5,
        now=1_000,
    )
    with pytest.raises(InvalidToken, match="expired"):
        verify_token(token, secret="test-secret", audience="boundaryline-broker", now=1_005)
