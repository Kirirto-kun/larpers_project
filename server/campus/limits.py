import time
from collections import OrderedDict
from threading import Lock


class RequestRates:
    """Bounded token buckets for a coordinator process."""

    def __init__(self, max_keys=8192):
        self.max_keys = max_keys
        self.buckets = OrderedDict()
        self.lock = Lock()

    def take(self, key, capacity, refill):
        current = time.monotonic()
        with self.lock:
            remaining, previous = self.buckets.pop(key, (capacity, current))
            remaining = min(capacity, remaining + (current - previous) * refill)
            allowed = remaining >= 1
            self.buckets[key] = (remaining - int(allowed), current)
            if len(self.buckets) > self.max_keys:
                self.buckets.popitem(last=False)
            return allowed
