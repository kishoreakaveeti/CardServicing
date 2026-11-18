from unittest.mock import Mock
import pytest
from card_servicing.startup import wait_for_dependencies


def test_dependency_transient_failure_recovers():
    probe = Mock(side_effect=[OSError('credentials must not escape'), None])
    wait_for_dependencies(['http://dependency/health'], probe=probe, sleep=lambda _: None)
    assert probe.call_count == 2


def test_dependency_deadline_is_bounded():
    ticks = iter([0, 0, 2])
    with pytest.raises(RuntimeError, match='deadline'):
        wait_for_dependencies(['http://dependency/health'], timeout=1,
                              probe=Mock(side_effect=OSError('secret')),
                              clock=lambda: next(ticks), sleep=lambda _: None)


def test_no_probes_for_default_local_startup():
    probe = Mock()
    wait_for_dependencies([], probe=probe)
    probe.assert_not_called()
