from app.slack.client import ThrottledSlackClient


class FakeWebClient:
    def __init__(self, token):
        self.token = token
        self.calls = []

    def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "ts": f"ts-{len(self.calls)}"}


def make_client(min_interval=1.0):
    times = {"now": 0.0}
    sleeps = []

    def fake_clock():
        return times["now"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        times["now"] += seconds

    client = ThrottledSlackClient(
        "xoxb-fake",
        min_interval_seconds=min_interval,
        web_client_cls=FakeWebClient,
        sleep_fn=fake_sleep,
        clock_fn=fake_clock,
    )
    return client, sleeps, times


def test_post_message_forwards_to_slack_sdk():
    client, sleeps, _ = make_client()
    resp = client.post_message("C123", "hello")
    assert resp["ts"] == "ts-1"
    assert client._web_client.calls[0] == {"channel": "C123", "text": "hello"}


def test_post_message_includes_thread_ts_when_given():
    client, sleeps, _ = make_client()
    client.post_message("C123", "reply", thread_ts="ts-1")
    assert client._web_client.calls[0]["thread_ts"] == "ts-1"


def test_second_call_within_interval_sleeps_remaining_gap():
    client, sleeps, times = make_client(min_interval=1.0)
    client.post_message("C123", "first")
    times["now"] = 0.4  # only 0.4s elapsed
    client.post_message("C123", "second")
    assert sleeps == [0.6]


def test_call_after_interval_elapsed_does_not_sleep():
    client, sleeps, times = make_client(min_interval=1.0)
    client.post_message("C123", "first")
    times["now"] = 2.0
    client.post_message("C123", "second")
    assert sleeps == []
