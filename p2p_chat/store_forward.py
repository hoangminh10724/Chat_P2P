"""
In-memory store-and-forward queue.

When a destination peer is offline, the tracker stores the message. When that
peer registers again, the tracker flushes the pending messages to the peer.
"""
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List


@dataclass
class PendingMessage:
    """One message waiting for an offline peer."""

    from_user: str
    to_user: str
    content: str
    timestamp: float = field(default_factory=time.time)

    def formatted(self) -> str:
        t = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return f"[{t}] {self.from_user}: {self.content}"


class MessageQueue:
    """Thread-safe bounded queue of offline messages per username."""

    MAX_QUEUE_SIZE = 100

    def __init__(self):
        self._queues: dict[str, list[PendingMessage]] = defaultdict(list)
        self._lock = threading.Lock()

    def enqueue(self, to_user: str, from_user: str, content: str) -> bool:
        with self._lock:
            queue = self._queues[to_user]
            if len(queue) >= self.MAX_QUEUE_SIZE:
                print(f"[STORE-FWD] Hang doi cua '{to_user}' da day ({self.MAX_QUEUE_SIZE} tin). Bo qua.")
                return False
            queue.append(PendingMessage(from_user=from_user, to_user=to_user, content=content))
            queue_size = len(queue)
        print(f"[STORE-FWD] Luu tin tu '{from_user}' -> '{to_user}' (hang doi: {queue_size})")
        return True

    def flush(self, username: str) -> List[PendingMessage]:
        with self._lock:
            msgs = self._queues.pop(username, [])
        if msgs:
            print(f"[STORE-FWD] Chuyen tiep {len(msgs)} tin nhan ton dong cho '{username}'.")
        return msgs

    def has_pending(self, username: str) -> bool:
        with self._lock:
            return bool(self._queues.get(username))

    def count(self, username: str) -> int:
        with self._lock:
            return len(self._queues.get(username, []))

    def clear(self) -> None:
        with self._lock:
            self._queues.clear()

    def all_queues_summary(self) -> dict:
        with self._lock:
            return {uname: len(msgs) for uname, msgs in self._queues.items() if msgs}
