"""The shared request budget spaces every KIS paper request, never sleeping for real."""

from quantpilot.paper.data import LIMITER_INTERVAL, LimitedTransport, RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_calls_by_interval_without_real_sleep():
    clock = FakeClock()
    limiter = RateLimiter(interval=1.05, clock=clock, sleep=clock.sleep)
    limiter.wait()  # first call never waits
    limiter.wait()  # back-to-back call waits the full interval
    clock.now += 0.5
    limiter.wait()  # half the interval already elapsed
    clock.now += 5
    limiter.wait()  # long idle: no wait
    assert clock.slept == [0, 1.05, 0.55, 0]


def test_default_interval_keeps_one_process_under_half_the_paper_limit():
    assert LIMITER_INTERVAL == 1.05
    assert RateLimiter().interval == LIMITER_INTERVAL


def test_limited_transport_waits_before_every_request():
    clock = FakeClock()
    calls = []

    class Transport:
        def request_json(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "ok"

    transport = LimitedTransport(Transport(), RateLimiter(1.05, clock, clock.sleep))
    assert transport.request_json("GET", "/a", headers={}) == "ok"
    assert transport.request_json("GET", "/b", headers={}) == "ok"
    assert [a[1] for a, _ in calls] == ["/a", "/b"]
    assert clock.slept == [0, 1.05]
