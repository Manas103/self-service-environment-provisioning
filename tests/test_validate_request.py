"""
14 of 14 non-conforming requests must be rejected, and plan() must never be
called for a rejected request (checked with a Mock, not just a return-value
assertion). 0 false rejects over the conforming fixtures.
"""
import glob
import os
from unittest.mock import Mock

import pytest

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from validate_request import validate, load_request, validate_and_maybe_plan  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
NON_CONFORMING = sorted(glob.glob(os.path.join(FIXTURES, "non_conforming", "*.yaml")))
CONFORMING = sorted(glob.glob(os.path.join(FIXTURES, "conforming", "*.yaml")))


def test_fourteen_non_conforming_fixtures_exist():
    assert len(NON_CONFORMING) == 14


@pytest.mark.parametrize("path", NON_CONFORMING)
def test_non_conforming_request_rejected(path):
    request = load_request(path)
    verdict = validate(request)
    assert verdict.ok is False, f"{path} should have been rejected but was not"
    assert len(verdict.violations) >= 1


@pytest.mark.parametrize("path", NON_CONFORMING)
def test_non_conforming_request_never_reaches_plan(path):
    plan_fn = Mock()
    validate_and_maybe_plan(path, plan_fn)
    plan_fn.assert_not_called()


@pytest.mark.parametrize("path", CONFORMING)
def test_conforming_request_accepted(path):
    request = load_request(path)
    verdict = validate(request)
    assert verdict.ok is True, f"{path} should have passed but was rejected: {verdict.violations}"


@pytest.mark.parametrize("path", CONFORMING)
def test_conforming_request_reaches_plan(path):
    plan_fn = Mock()
    validate_and_maybe_plan(path, plan_fn)
    plan_fn.assert_called_once()
