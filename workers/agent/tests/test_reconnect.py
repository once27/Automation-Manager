from browserfleet_worker.reconnect import ExponentialBackoff


def test_backoff_is_bounded_and_injectable() -> None:
    backoff = ExponentialBackoff(
        initial_seconds=2, maximum_seconds=5, jitter=lambda: 0.5
    )

    assert backoff.delay(1) == 1
    assert backoff.delay(2) == 2
    assert backoff.delay(3) == 2.5
