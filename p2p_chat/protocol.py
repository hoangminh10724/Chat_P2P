"""
Small socket framing helpers used by both the tracker and peers.

TCP is a byte stream, so one send() call is not guaranteed to match one recv()
call. These helpers frame every message as one UTF-8 line. JSON payloads are
encoded with compact separators, so a frame is always a single physical line.
"""
from __future__ import annotations

import json
import socket
import threading
from typing import Any


DEFAULT_MAX_FRAME_SIZE = 20 * 1024 * 1024

_buffers: dict[int, bytearray] = {}
_buffers_lock = threading.Lock()


class ProtocolError(RuntimeError):
    """Raised when a peer sends an invalid or oversized frame."""


def _buffer_for(sock: socket.socket) -> bytearray:
    with _buffers_lock:
        return _buffers.setdefault(id(sock), bytearray())


def discard_buffer(sock: socket.socket) -> None:
    """Drop any buffered bytes for a socket that is about to be closed."""
    with _buffers_lock:
        _buffers.pop(id(sock), None)


def send_line(sock: socket.socket, text: str) -> None:
    """Send one framed UTF-8 text line."""
    if "\n" in text:
        text = text.replace("\r\n", "\\n").replace("\n", "\\n")
    sock.sendall(text.encode("utf-8") + b"\n")


def recv_line(sock: socket.socket, max_bytes: int = DEFAULT_MAX_FRAME_SIZE) -> str:
    """
    Receive one framed UTF-8 text line.

    Returns an empty string only when the remote endpoint closed the connection
    and no buffered data remains.
    """
    buf = _buffer_for(sock)
    while True:
        newline_at = buf.find(b"\n")
        if newline_at >= 0:
            raw = bytes(buf[:newline_at])
            del buf[:newline_at + 1]
            return raw.decode("utf-8")

        chunk = sock.recv(4096)
        if not chunk:
            if buf:
                raw = bytes(buf)
                buf.clear()
                return raw.decode("utf-8")
            return ""

        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise ProtocolError(f"Frame exceeds {max_bytes} bytes")


def send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    """Send a single framed JSON object."""
    send_line(sock, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def recv_json(sock: socket.socket, max_bytes: int = DEFAULT_MAX_FRAME_SIZE) -> dict[str, Any]:
    """Receive a single framed JSON object."""
    line = recv_line(sock, max_bytes=max_bytes)
    if not line:
        return {}
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Invalid JSON frame: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("JSON frame must be an object")
    return value
