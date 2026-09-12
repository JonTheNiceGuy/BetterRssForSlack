import threading
import time

from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler


class ThrottledSlackClient:
    def __init__(
        self,
        token: str,
        *,
        min_interval_seconds: float,
        web_client_cls=WebClient,
        sleep_fn=time.sleep,
        clock_fn=time.monotonic,
    ):
        self._web_client = web_client_cls(token)
        if hasattr(self._web_client, "retry_handlers"):
            self._web_client.retry_handlers.append(
                RateLimitErrorRetryHandler(max_retry_count=3)
            )
        self._min_interval = min_interval_seconds
        self._sleep = sleep_fn
        self._clock = clock_fn
        self._lock = threading.Lock()
        self._last_call_at: float | None = None

    def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> dict:
        with self._lock:
            self._throttle()
            kwargs = {"channel": channel, "text": text}
            if thread_ts is not None:
                kwargs["thread_ts"] = thread_ts
            response = self._web_client.chat_postMessage(**kwargs)
            self._last_call_at = self._clock()
            return response

    def _throttle(self):
        if self._last_call_at is None:
            return
        elapsed = self._clock() - self._last_call_at
        remaining = self._min_interval - elapsed
        if remaining > 0:
            self._sleep(remaining)
