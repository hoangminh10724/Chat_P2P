import json
import socket
import threading

from p2p_chat.protocol import discard_buffer, recv_line, send_line
from p2p_chat.store_forward import MessageQueue


# Registry of online peers:
# {username: {"client": socket, "ip": str, "port": int}}
registry: dict[str, dict] = {}
registry_lock = threading.Lock()

# Peers that have registered at least once during this tracker session:
# {username: {"ip": str, "port": int, "online": bool}}
known_peers: dict[str, dict] = {}

# In-memory store-and-forward queue for offline peers.
message_queue = MessageQueue()


def safe_send(client: socket.socket, message: str) -> bool:
    """Send one framed text response to a tracker client."""
    try:
        send_line(client, message)
        return True
    except Exception:
        return False


def safe_close(client: socket.socket | None) -> None:
    if not client:
        return
    try:
        client.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        discard_buffer(client)
        client.close()
    except Exception:
        pass


def _socket_error_text(exc: Exception) -> str:
    winerror = getattr(exc, "winerror", None)
    errno = getattr(exc, "errno", None)
    if winerror in (10053, 10054, 10058) or errno in (10053, 10054, 10058, 32):
        return "client da dong/ngat ket noi"
    return str(exc)


def _valid_username(username: str) -> bool:
    return bool(username) and username.strip() == username and not any(c.isspace() for c in username)


def get_peer_list_json() -> str:
    """Return online peers as a JSON object string."""
    with registry_lock:
        peers = {
            uname: {"ip": info["ip"], "port": info["port"]}
            for uname, info in sorted(registry.items())
        }
    return json.dumps(peers, ensure_ascii=False, separators=(",", ":"))


def get_peer_status_json() -> str:
    """Return all known peers with their current online/offline status."""
    with registry_lock:
        peers = {}
        for uname, info in sorted(known_peers.items()):
            online_info = registry.get(uname)
            if online_info:
                peers[uname] = {
                    "ip": online_info["ip"],
                    "port": online_info["port"],
                    "online": True,
                }
            else:
                peers[uname] = {
                    "ip": info.get("ip", ""),
                    "port": info.get("port", 0),
                    "online": False,
                }
    return json.dumps(peers, ensure_ascii=False, separators=(",", ":"))


def reset_state(close_clients: bool = True) -> None:
    """Clear tracker state so the running server starts from an empty network."""
    with registry_lock:
        online_infos = list(registry.values())
        registry.clear()
        known_peers.clear()
    message_queue.clear()

    if close_clients:
        for info in online_infos:
            safe_close(info.get("client"))
    print("[RESET] Da xoa registry, danh sach peer da tham gia va hang doi offline.")


def remove_peer(username: str, close_socket: bool = False, reason: str | None = None) -> bool:
    """Remove a peer from the online registry."""
    with registry_lock:
        info = registry.pop(username, None)
        if info:
            known_peers[username] = {
                "ip": info["ip"],
                "port": info["port"],
                "online": False,
            }
    if not info:
        return False

    if close_socket:
        safe_close(info.get("client"))

    suffix = f" ({reason})" if reason else ""
    print(f"[LEAVE] '{username}' da roi mang{suffix}.")
    return True


def _pending_messages_payload(username: str) -> str:
    pending = message_queue.flush(username)
    payload = [
        {
            "from": msg.from_user,
            "to": msg.to_user,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "formatted": msg.formatted(),
        }
        for msg in pending
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _handle_offline_message(username: str, client: socket.socket, data: str) -> None:
    parts = data.split(" ", 2)
    if len(parts) < 3:
        safe_send(client, "[ERROR] Cu phap: /offline_msg <username> <noi dung>")
        return

    target_user = parts[1].strip()
    content = parts[2].strip()
    with registry_lock:
        info = registry.get(target_user)

    if info:
        safe_send(client, f"PEER_ONLINE:{info['ip']}:{info['port']}")
        return

    ok = message_queue.enqueue(to_user=target_user, from_user=username, content=content)
    if ok:
        safe_send(client, f"[STORE-FWD] Tin nhan se duoc gui toi '{target_user}' khi ho online.")
    else:
        safe_send(client, f"[ERROR] Hang doi cua '{target_user}' da day.")


def handle(username: str) -> None:
    """
    Handle commands from a registered peer.

    Supported commands:
      /list
      /peers
      /server_reset
      /connect <username>
      /offline_msg <username> <content>
      /queue_info
      /unreachable <username>
      /exit
    """
    with registry_lock:
        info = registry.get(username)
    if not info:
        return

    client = info["client"]
    try:
        while True:
            data = recv_line(client).strip()
            if not data:
                break

            if data == "/exit":
                safe_send(client, "[SERVER] Da ngat ket noi. Tam biet!")
                break

            if data == "/list":
                safe_send(client, f"PEER_LIST:{get_peer_list_json()}")
                continue

            if data == "/peers":
                safe_send(client, f"PEER_STATUS:{get_peer_status_json()}")
                continue

            if data == "/server_reset":
                safe_send(client, "[OK] Server da reset. Tat ca peer can dang nhap lai.")
                reset_state()
                break

            if data.startswith("/connect "):
                target = data.split(" ", 1)[1].strip()
                with registry_lock:
                    target_info = registry.get(target)
                if target_info:
                    safe_send(client, f"PEER_INFO:{target_info['ip']}:{target_info['port']}")
                    print(
                        f"[CONNECT] '{username}' yeu cau ket noi toi "
                        f"'{target}' ({target_info['ip']}:{target_info['port']})"
                    )
                else:
                    safe_send(client, f"[ERROR] User '{target}' khong online hoac khong ton tai.")
                continue

            if data.startswith("/offline_msg "):
                _handle_offline_message(username, client, data)
                continue

            if data == "/queue_info":
                count = message_queue.count(username)
                if count:
                    safe_send(client, f"[QUEUE] Ban co {count} tin nhan dang cho trong hang doi.")
                else:
                    safe_send(client, "[QUEUE] Khong co tin nhan nao dang cho ban.")
                continue

            if data.startswith("/unreachable "):
                target = data.split(" ", 1)[1].strip()
                if target == username:
                    safe_send(client, "[ERROR] Khong the danh dau chinh ban la unreachable.")
                    continue
                removed = remove_peer(target, close_socket=True, reason=f"reported unreachable by {username}")
                if removed:
                    safe_send(client, f"[OK] Da cap nhat '{target}' thanh offline.")
                else:
                    safe_send(client, f"[OK] '{target}' hien khong online trong registry.")
                continue

            safe_send(client, "[ERROR] Lenh khong hop le.")

    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as exc:
        print(f"[LEAVE] '{username}' mat ket noi: {_socket_error_text(exc)}")
    except Exception as exc:
        print(f"[ERROR] Xu ly '{username}': {exc}")
    finally:
        remove_peer(username)
        safe_close(client)


def receive(server: socket.socket) -> None:
    """
    Main tracker accept loop.

    Registration protocol:
      tracker -> peer: REGISTER
      peer -> tracker: username:listen_port
      tracker -> peer: [OK] ... or [ERROR] ...
      tracker -> peer: OFFLINE_MESSAGES:[...]
    """
    print("[BOOTSTRAP] Server dang lang nghe ket noi...")
    while True:
        client = None
        address = None
        try:
            client, address = server.accept()
            ip = address[0]
            print(f"[CONNECTION] Ket noi moi tu {address}")

            if not safe_send(client, "REGISTER"):
                safe_close(client)
                continue

            reg_data = recv_line(client).strip()
            parts = reg_data.split(":", 1)
            if len(parts) != 2:
                safe_send(client, "[ERROR] Dinh dang dang ky sai. Can: username:listen_port")
                safe_close(client)
                continue

            username = parts[0].strip()
            listen_port_str = parts[1].strip()
            if not _valid_username(username):
                safe_send(client, "[ERROR] Username khong duoc rong hoac chua khoang trang.")
                safe_close(client)
                continue

            try:
                listen_port = int(listen_port_str)
            except ValueError:
                safe_send(client, "[ERROR] listen_port phai la so nguyen.")
                safe_close(client)
                continue

            with registry_lock:
                if username in registry:
                    safe_send(client, f"[ERROR] Username '{username}' da duoc su dung.")
                    safe_close(client)
                    continue
                registry[username] = {"client": client, "ip": ip, "port": listen_port}
                known_peers[username] = {"ip": ip, "port": listen_port, "online": True}

            print(f"[REGISTER] '{username}' dang ky tai {ip}:{listen_port}")
            safe_send(client, f"[OK] Dang ky thanh cong voi ten '{username}'. Ban dang online!")
            safe_send(client, f"OFFLINE_MESSAGES:{_pending_messages_payload(username)}")

            thread = threading.Thread(target=handle, args=(username,), daemon=True)
            thread.start()

        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as exc:
            source = f" tu {address}" if address else ""
            print(f"[CONNECTION] Dang ky that bai{source}: {_socket_error_text(exc)}")
            safe_close(client)
        except Exception as exc:
            print(f"[ERROR] Vong lap accept: {exc}")
            safe_close(client)
