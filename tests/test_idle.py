import pytest

from duet.core._idle import IdleTracker


@pytest.fixture
def idle():
    now = [0.0]
    return IdleTracker(60, clock=lambda: now[0]), now


def test_polling_does_not_extend_idle_period(idle):
    tracker, now = idle
    now[0] = 59
    with tracker.hold():
        now[0] = 60
        assert not tracker.expired()
    assert tracker.expired()


def test_concurrent_transfers_restart_idle_period_when_last_transfer_finishes(idle):
    tracker, now = idle
    with tracker.activity():
        with tracker.activity():
            now[0] = 60
        now[0] = 120
        assert not tracker.expired()
    now[0] = 179
    assert not tracker.expired()
    now[0] = 180
    assert tracker.expired()


def test_failed_transfer_releases_hold(idle):
    tracker, now = idle
    with pytest.raises(OSError), tracker.activity():
        now[0] = 50
        raise OSError
    now[0] = 110
    assert tracker.expired()


def test_touch_restarts_idle_period(idle):
    tracker, now = idle
    now[0] = 50
    tracker.touch()
    now[0] = 60
    assert not tracker.expired()
