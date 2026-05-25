import base64
import json
import os
import socket
import threading
import time
import uuid

from p2p_chat.encryption import MessageEncryptor
from p2p_chat.protocol import (
    ProtocolError,
    discard_buffer,
    recv_json,
    recv_line,
    send_json,
    send_line,
)


MAX_FILE_SIZE = 10 * 1024 * 1024


class PeerServer:
    """
    TCP server embedded in each peer.

    It accepts direct P2P connections from other peers and replies with ACK
    frames so the sender can retry when the destination does not respond.
    """

    def __init__(self, port: int, username: str):
        self.port = port
        self.username = username
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("", port))
        self.server_sock.listen(20)
        self._seen_message_ids: set[str] = set()
        self._seen_lock = threading.Lock()
        self.group_mgr = None

    def start(self) -> None:
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        print(f"[PEER-SERVER] Dang lang nghe ket noi P2P tren cong {self.port}...")

    def stop(self) -> None:
        try:
            self.server_sock.close()
        except Exception:
            pass

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, addr = self.server_sock.accept()
                t = threading.Thread(target=self._handle_incoming, args=(conn, addr), daemon=True)
                t.start()
            except Exception as exc:
                print(f"[PEER-SERVER ERROR] {exc}")
                break

    def _already_seen(self, msg_id: str | None) -> bool:
        if not msg_id:
            return False
        with self._seen_lock:
            if msg_id in self._seen_message_ids:
                return True
            self._seen_message_ids.add(msg_id)
            if len(self._seen_message_ids) > 5000:
                self._seen_message_ids.clear()
            return False

    def _send_ack(self, conn: socket.socket, msg_id: str | None, status: str = "ok", error: str = "") -> None:
        if not msg_id:
            return
        payload = {"type": "ack", "id": msg_id, "status": status}
        if error:
            payload["error"] = error
        try:
            send_json(conn, payload)
        except Exception:
            pass

    def _decrypt_if_needed(self, sender: str, content: str, encrypted: bool) -> tuple[str, str]:
        if not encrypted:
            return content, ""

        if not hasattr(self, "_encryptors"):
            return "[LOI GIAI MA] Peer nhan chua cau hinh khoa.", "[MA HOA] "

        enc = self._encryptors.get(sender)
        if not enc:
            return "[LOI GIAI MA] Chua co shared key cho peer gui.", "[MA HOA] "

        try:
            return enc.decrypt(content), "[MA HOA] "
        except ValueError:
            return "[LOI GIAI MA] Key sai hoac du lieu bi hong.", "[MA HOA] "

    def _save_received_file(self, filename: str, data_b64: str) -> str:
        safe_name = os.path.basename(filename) or "received_file"
        save_dir = os.path.join(os.getcwd(), "received_files")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{int(time.time())}_{safe_name}")
        with open(save_path, "wb") as f:
            f.write(base64.b64decode(data_b64.encode("ascii")))
        return save_path

    def _handle_incoming(self, conn: socket.socket, addr) -> None:
        try:
            while True:
                msg = recv_json(conn)
                if not msg:
                    break

                msg_id = msg.get("id")
                if self._already_seen(msg_id):
                    self._send_ack(conn, msg_id)
                    continue

                msg_type = msg.get("type")
                if msg_type == "chat":
                    sender = msg.get("from", "Unknown")
                    content = msg.get("content", "")
                    group = msg.get("group")
                    is_encrypted = bool(msg.get("encrypted", False))
                    content, prefix = self._decrypt_if_needed(sender, content, is_encrypted)

                    if group:
                        print(f"\n[NHOM {group}] {prefix}{sender}: {content}")
                    else:
                        print(f"\n[TRUC TIEP] {prefix}{sender}: {content}")
                    self._send_ack(conn, msg_id)

                elif msg_type == "file_info":
                    sender = msg.get("from", "Unknown")
                    filename = msg.get("filename", "file")
                    size = msg.get("size", 0)
                    data_b64 = msg.get("data", "")
                    save_path = self._save_received_file(filename, data_b64) if data_b64 else ""
                    print(f"\n[FILE] {sender} gui file: {filename} ({size} bytes) -> {save_path}")
                    self._send_ack(conn, msg_id)

                elif msg_type == "group_invite":
                    sender = msg.get("from", "Unknown")
                    group_name = msg.get("group", "")
                    members = msg.get("members", [])
                    filtered_members = [m for m in members if m != self.username]
                    if self.group_mgr:
                        self.group_mgr.create_group(group_name, filtered_members)
                    print(f"\n[NHOM] Ban da duoc them vao nhom '{group_name}' boi '{sender}'. Thanh vien: {', '.join(members)}")
                    self._send_ack(conn, msg_id)

                else:
                    self._send_ack(conn, msg_id, status="error", error="unknown message type")

        except ProtocolError as exc:
            print(f"\n[PEER-SERVER ERROR] Goi tin khong hop le tu {addr}: {exc}")
        except Exception:
            pass
        finally:
            discard_buffer(conn)
            try:
                conn.close()
            except Exception:
                pass


class BootstrapClient:
    """Synchronous request/response client for the bootstrap tracker."""

    def __init__(self):
        self.sock: socket.socket | None = None
        self._lock = threading.Lock()
        self.pending_messages: list[dict] = []
        self.last_error = ""
        self.request_timeout = 10

    @staticmethod
    def _friendly_socket_error(exc: Exception) -> str:
        if isinstance(exc, socket.timeout):
            return "Het thoi gian cho phan hoi tu bootstrap server."

        winerror = getattr(exc, "winerror", None)
        errno = getattr(exc, "errno", None)
        if winerror in (10053, 10054, 10058) or errno in (10053, 10054, 10058, 32):
            return "Mat ket noi bootstrap server. Hay khoi dong lai tracker va dang nhap lai."

        return f"Khong the lien lac voi bootstrap server: {exc}"

    def _close_socket(self) -> None:
        sock = self.sock
        self.sock = None
        if not sock:
            return
        try:
            discard_buffer(sock)
            sock.close()
        except Exception:
            pass

    def _mark_disconnected(self, reason: str) -> None:
        self.last_error = reason
        self._close_socket()

    def connect_and_register(self, server_address: tuple, username: str, listen_port: int) -> bool:
        self.last_error = ""
        self.pending_messages = []
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.request_timeout)
            self.sock.connect(server_address)

            prompt = recv_line(self.sock)
            if prompt != "REGISTER":
                self.last_error = f"Phan hoi khong mong doi tu server: {prompt or 'ket noi da dong'}"
                print(f"[ERROR] {self.last_error}")
                self._close_socket()
                return False

            send_line(self.sock, f"{username}:{listen_port}")
            response = recv_line(self.sock)
            if not response.startswith("[OK]"):
                self.last_error = response or "Bootstrap server da dong ket noi khi dang ky."
                print(f"[BOOTSTRAP ERROR] {self.last_error}")
                self._close_socket()
                return False

            pending_response = recv_line(self.sock)
            if pending_response.startswith("OFFLINE_MESSAGES:"):
                raw = pending_response[len("OFFLINE_MESSAGES:"):]
                self.pending_messages = json.loads(raw) if raw else []

            self.sock.settimeout(None)
            print(f"[BOOTSTRAP] {response}")
            self._print_pending_messages()
            return True
        except Exception as exc:
            self.last_error = self._friendly_socket_error(exc)
            print(f"[BOOTSTRAP ERROR] {self.last_error}")
            self._close_socket()
            return False

    def _print_pending_messages(self) -> None:
        if not self.pending_messages:
            return
        print(f"[STORE-FWD] Ban co {len(self.pending_messages)} tin nhan trong luc offline:")
        for msg in self.pending_messages:
            print(f"  [OFFLINE MSG] {msg.get('formatted', msg.get('content', ''))}")

    def _request(self, command: str) -> str:
        if not self.sock:
            return f"[BOOTSTRAP ERROR] {self.last_error or 'Chua ket noi bootstrap server.'}"
        with self._lock:
            try:
                if not self.sock:
                    return f"[BOOTSTRAP ERROR] {self.last_error or 'Chua ket noi bootstrap server.'}"
                self.sock.settimeout(self.request_timeout)
                send_line(self.sock, command)
                response = recv_line(self.sock)
                if not response:
                    self._mark_disconnected("Bootstrap server da dong ket noi.")
                    return f"[BOOTSTRAP ERROR] {self.last_error}"
                self.sock.settimeout(None)
                return response
            except Exception as exc:
                self._mark_disconnected(self._friendly_socket_error(exc))
                return f"[BOOTSTRAP ERROR] {self.last_error}"
            finally:
                if self.sock:
                    try:
                        self.sock.settimeout(None)
                    except Exception:
                        pass

    def get_peer_list(self) -> dict:
        response = self._request("/list")
        if response.startswith("PEER_LIST:"):
            try:
                return json.loads(response[len("PEER_LIST:"):])
            except json.JSONDecodeError:
                print("[ERROR] Du lieu peer list khong hop le.")
                return {}
        print(f"[ERROR] {response}")
        return {}

    def get_peer_statuses(self) -> dict:
        response = self._request("/peers")
        if response.startswith("PEER_STATUS:"):
            try:
                return json.loads(response[len("PEER_STATUS:"):])
            except json.JSONDecodeError:
                print("[ERROR] Du lieu trang thai peer khong hop le.")
                return {}

        if "Lenh khong hop le" in response:
            online_peers = self.get_peer_list()
            return {
                uname: {
                    "ip": info.get("ip", ""),
                    "port": info.get("port", 0),
                    "online": True,
                }
                for uname, info in online_peers.items()
            }

        print(f"[ERROR] {response}")
        return {}

    def get_peer_info(self, target_username: str):
        response = self._request(f"/connect {target_username}")
        if response.startswith("PEER_INFO:"):
            try:
                ip, port = response[len("PEER_INFO:"):].rsplit(":", 1)
                return ip, int(port)
            except ValueError:
                print("[ERROR] Thong tin peer khong hop le.")
                return None
        print(f"[ERROR] {response}")
        return None

    def queue_offline_message(self, target_username: str, content: str) -> dict:
        response = self._request(f"/offline_msg {target_username} {content}")
        if response.startswith("PEER_ONLINE:"):
            ip, port = response[len("PEER_ONLINE:"):].rsplit(":", 1)
            return {"status": "online", "ip": ip, "port": int(port)}
        if response.startswith("[STORE-FWD]"):
            return {"status": "queued", "message": response}
        return {"status": "error", "message": response}

    def report_unreachable(self, target_username: str) -> str:
        return self._request(f"/unreachable {target_username}")

    def queue_info(self) -> str:
        return self._request("/queue_info")

    def reset_server(self) -> str:
        response = self._request("/server_reset")
        self._close_socket()
        return response

    def disconnect(self) -> None:
        with self._lock:
            sock = self.sock
            self.sock = None
            if not sock:
                return
            try:
                sock.settimeout(2)
                send_line(sock, "/exit")
            except Exception:
                pass
            try:
                discard_buffer(sock)
                sock.close()
            except Exception:
                pass


class DirectChat:
    """Manage direct P2P sockets and reliable message/file delivery."""

    def __init__(self, my_username: str, peer_server: PeerServer):
        self.my_username = my_username
        self.peer_server = peer_server
        self.connections: dict[str, socket.socket] = {}
        self._send_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._encryptors: dict[str, MessageEncryptor] = {}
        self.ack_timeout = 5
        self.max_retries = 2
        peer_server._encryptors = self._encryptors

    def connect_to_peer(self, username: str, ip: str, port: int) -> bool:
        with self._lock:
            if username in self.connections:
                print(f"[CHAT] Da co ket noi toi '{username}'.")
                return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((ip, port))
            sock.settimeout(None)
            with self._lock:
                self.connections[username] = sock
                self._send_locks[username] = threading.Lock()
            print(f"[CHAT] Ket noi truc tiep P2P toi '{username}' ({ip}:{port}) thanh cong!")
            return True
        except Exception as exc:
            print(f"[CHAT ERROR] Khong the ket noi toi '{username}': {exc}")
            return False

    def set_encryptor(self, username: str, shared_key: str) -> bool:
        try:
            enc = MessageEncryptor(shared_key)
        except ValueError as exc:
            print(f"[MA HOA ERROR] {exc}")
            return False
        self._encryptors[username] = enc
        print(f"[MA HOA] Da bat ma hoa {enc.mode} voi '{username}'.")
        return True

    def remove_encryptor(self, username: str) -> None:
        if username in self._encryptors:
            del self._encryptors[username]
            print(f"[MA HOA] Da tat ma hoa voi '{username}'.")

    def _remove_connection(self, username: str) -> None:
        with self._lock:
            sock = self.connections.pop(username, None)
            self._send_locks.pop(username, None)
        if sock:
            try:
                discard_buffer(sock)
                sock.close()
            except Exception:
                pass

    def _send_payload(self, username: str, payload: dict) -> bool:
        with self._lock:
            sock = self.connections.get(username)
            send_lock = self._send_locks.get(username)
        if not sock or not send_lock:
            print(f"[ERROR] Chua ket noi toi '{username}'. Dung /connect {username} truoc.")
            return False

        payload.setdefault("id", uuid.uuid4().hex)
        last_error = ""
        with send_lock:
            for attempt in range(1, self.max_retries + 2):
                try:
                    sock.settimeout(self.ack_timeout)
                    send_json(sock, payload)
                    ack = recv_json(sock, max_bytes=4096)
                    if ack.get("type") == "ack" and ack.get("id") == payload["id"]:
                        if ack.get("status") == "ok":
                            sock.settimeout(None)
                            return True
                        last_error = ack.get("error", "remote error")
                    else:
                        last_error = "ACK khong khop"
                except Exception as exc:
                    last_error = str(exc)

                if attempt <= self.max_retries:
                    print(f"[CHAT] Thu gui lai '{username}' lan {attempt}/{self.max_retries}...")

            print(f"[CHAT ERROR] Gui toi '{username}' that bai: {last_error}")
            self._remove_connection(username)
            return False

    def send_direct(self, username: str, content: str, group_name: str | None = None) -> bool:
        enc = self._encryptors.get(username)
        payload_content = enc.encrypt(content) if enc else content
        payload = {
            "type": "chat",
            "from": self.my_username,
            "to": username,
            "content": payload_content,
            "encrypted": enc is not None,
        }
        if group_name:
            payload["group"] = group_name
        return self._send_payload(username, payload)

    def send_group_invite(self, username: str, group_name: str, all_members: list) -> bool:
        payload = {
            "type": "group_invite",
            "from": self.my_username,
            "group": group_name,
            "members": all_members,
        }
        return self._send_payload(username, payload)

    def broadcast_to_group(self, usernames: list, content: str, group_name: str = "NHOM", bootstrap=None) -> list[str]:
        failed = []
        for uname in usernames:
            if bootstrap and uname not in self.connections:
                info = bootstrap.get_peer_info(uname)
                if info:
                    self.connect_to_peer(uname, info[0], info[1])
            if not self.send_direct(uname, content, group_name=group_name):
                failed.append(uname)
        if failed:
            print(f"[WARNING] Khong gui duoc toi: {', '.join(failed)}")
        return failed

    def send_file(self, username: str, path: str) -> bool:
        if not os.path.isfile(path):
            print(f"[FILE ERROR] Khong tim thay file: {path}")
            return False
        size = os.path.getsize(path)
        if size > MAX_FILE_SIZE:
            print(f"[FILE ERROR] File qua lon. Gioi han {MAX_FILE_SIZE // (1024 * 1024)}MB.")
            return False
        with open(path, "rb") as f:
            data_b64 = base64.b64encode(f.read()).decode("ascii")
        payload = {
            "type": "file_info",
            "from": self.my_username,
            "filename": os.path.basename(path),
            "size": size,
            "data": data_b64,
        }
        return self._send_payload(username, payload)

    def disconnect_peer(self, username: str) -> None:
        had_connection = username in self.connections
        self._remove_connection(username)
        if had_connection:
            print(f"[CHAT] Da dong ket noi toi '{username}'.")
        else:
            print(f"[WARNING] Khong co ket noi nao toi '{username}'.")

    def list_connections(self) -> list:
        with self._lock:
            return list(self.connections.keys())

    def list_encrypted_connections(self) -> list:
        return list(self._encryptors.keys())


class GroupManager:
    """Local group membership used for P2P fan-out."""

    def __init__(self):
        self.groups: dict[str, set[str]] = {}
        self.group_join_history: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def create_group(self, group_name: str, members: list) -> None:
        with self._lock:
            self.groups[group_name] = set(members)
            self.group_join_history[group_name] = sorted(list(set(self.group_join_history.get(group_name, []) + members)))
        print(f"[NHOM] Da tao nhom '{group_name}' voi {len(members)} thanh vien: {', '.join(members)}")

    def delete_group(self, group_name: str) -> None:
        with self._lock:
            self.groups.pop(group_name, None)
        print(f"[NHOM] Da xoa nhom '{group_name}'.")

    def get_history(self, group_name: str) -> list:
        with self._lock:
            return sorted(self.group_join_history.get(group_name, []))

    def add_member(self, group_name: str, username: str) -> None:
        with self._lock:
            self.groups.setdefault(group_name, set()).add(username)
        print(f"[NHOM] Da them '{username}' vao nhom '{group_name}'.")

    def get_members(self, group_name: str) -> list:
        with self._lock:
            return sorted(self.groups.get(group_name, []))

    def list_groups(self) -> list:
        with self._lock:
            return sorted(self.groups.keys())


def _find_free_port(base_port: int) -> int:
    port = base_port
    while port < base_port + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("Khong tim duoc cong trong trong pham vi 8000-8100.")


def _ensure_direct_connection(bootstrap: BootstrapClient, direct_chat: DirectChat, username: str) -> bool:
    if username in direct_chat.list_connections():
        return True
    info = bootstrap.get_peer_info(username)
    if not info:
        return False
    return direct_chat.connect_to_peer(username, info[0], info[1])


def _send_or_store(bootstrap: BootstrapClient, direct_chat: DirectChat, target: str, content: str) -> None:
    if not _ensure_direct_connection(bootstrap, direct_chat, target):
        result = bootstrap.queue_offline_message(target, content)
        print(f"  {result.get('message', '[ERROR] Khong the gui hoac luu tin nhan.')}")
        return

    if direct_chat.send_direct(target, content):
        print(f"  [Ban -> {target}] {content}")
        return

    print(bootstrap.report_unreachable(target))
    result = bootstrap.queue_offline_message(target, content)
    print(f"  {result.get('message', '[ERROR] Khong the luu tin nhan offline.')}")


def run_client(server_address: tuple, username: str, base_port: int) -> None:
    listen_port = _find_free_port(base_port)
    peer_server = PeerServer(listen_port, username)
    peer_server.start()

    bootstrap = BootstrapClient()
    if not bootstrap.connect_and_register(server_address, username, listen_port):
        print("[ERROR] Dang ky that bai. Thoat chuong trinh.")
        return

    direct_chat = DirectChat(username, peer_server)
    group_mgr = GroupManager()
    peer_server.group_mgr = group_mgr

    _print_usage()
    print(f"\n[READY] Chao '{username}'! Hay bat dau chat.\n")

    try:
        while True:
            raw = input(">>> ").strip()
            if not raw:
                continue

            if raw == "/exit":
                bootstrap.disconnect()
                print("[BYE] Da ngat ket noi. Tam biet!")
                break

            if raw == "/list":
                peers = bootstrap.get_peer_list()
                if not peers:
                    print("[INFO] Khong co ai online.\n")
                    continue
                print("\n[ONLINE PEERS]")
                for uname, info in peers.items():
                    tag = " (ban)" if uname == username else ""
                    print(f"  - {uname}{tag} -> {info['ip']}:{info['port']}")
                print()
                continue

            if raw == "/peers":
                peers = bootstrap.get_peer_statuses()
                if not peers:
                    print("[INFO] Chua co peer nao da tham gia.\n")
                    continue
                print("\n[PEERS DA THAM GIA]")
                for uname, info in peers.items():
                    tag = " (ban)" if uname == username else ""
                    state = "online" if info.get("online") else "offline"
                    ip = info.get("ip") or "-"
                    port = info.get("port") or "-"
                    print(f"  - [{state}] {uname}{tag} -> {ip}:{port}")
                print()
                continue

            if raw.startswith("/connect "):
                target = raw.split(" ", 1)[1].strip()
                if target == username:
                    print("[ERROR] Khong the ket noi toi chinh ban.")
                    continue
                _ensure_direct_connection(bootstrap, direct_chat, target)
                continue

            if raw.startswith("/msg "):
                parts = raw.split(" ", 2)
                if len(parts) < 3:
                    print("[USAGE] /msg <username> <noi dung>")
                    continue
                targets = [t.strip() for t in parts[1].split(",") if t.strip()]
                for target in targets:
                    if target == username:
                        print("[ERROR] Khong the gui tin nhan cho chinh ban.")
                        continue
                    _send_or_store(bootstrap, direct_chat, target, parts[2])
                continue

            if raw.startswith("/offline_msg "):
                parts = raw.split(" ", 2)
                if len(parts) < 3:
                    print("[USAGE] /offline_msg <username> <noi dung>")
                    continue
                target, content = parts[1], parts[2]
                result = bootstrap.queue_offline_message(target, content)
                if result["status"] == "online":
                    direct_chat.connect_to_peer(target, result["ip"], result["port"])
                    _send_or_store(bootstrap, direct_chat, target, content)
                else:
                    print(f"  {result.get('message')}")
                continue

            if raw == "/queue_info":
                print(f"  {bootstrap.queue_info()}")
                continue

            if raw == "/server_reset":
                print(f"  {bootstrap.reset_server()}")
                print("[INFO] Server da reset, client hien tai se thoat. Dang nhap lai de tiep tuc.")
                break

            if raw.startswith("/sendfile "):
                parts = raw.split(" ", 2)
                if len(parts) < 3:
                    print("[USAGE] /sendfile <username> <duong_dan_file>")
                    continue
                target, path = parts[1], parts[2].strip('"')
                if _ensure_direct_connection(bootstrap, direct_chat, target):
                    ok = direct_chat.send_file(target, path)
                    if ok:
                        print(f"  [FILE] Da gui '{path}' toi {target}.")
                continue

            if raw.startswith("/encrypt "):
                parts = raw.split(" ", 2)
                if len(parts) < 3:
                    print("[USAGE] /encrypt <username> <shared_key>")
                    continue
                direct_chat.set_encryptor(parts[1], parts[2])
                continue

            if raw.startswith("/unencrypt "):
                direct_chat.remove_encryptor(raw.split(" ", 1)[1].strip())
                continue

            if raw == "/enc_status":
                enc_list = direct_chat.list_encrypted_connections()
                if enc_list:
                    print(f"\n[MA HOA] Ket noi dang ma hoa: {', '.join(enc_list)}\n")
                else:
                    print("[MA HOA] Khong co ket noi nao dang ma hoa.\n")
                continue

            if raw.startswith("/group create "):
                parts = raw.split()[2:]
                if len(parts) < 2:
                    print("[USAGE] /group create <ten_nhom> <u1> <u2> ...")
                    continue
                group_name = parts[0]
                members = [m for m in parts[1:] if m != username]
                for member in members:
                    _ensure_direct_connection(bootstrap, direct_chat, member)
                group_mgr.create_group(group_name, members)
                
                # Send P2P group invites to all members
                all_members = [username] + members
                for member in members:
                    direct_chat.send_group_invite(member, group_name, all_members)
                continue

            if raw.startswith("/broadcast "):
                parts = raw.split(" ", 2)
                if len(parts) < 3:
                    print("[USAGE] /broadcast <ten_nhom> <noi dung>")
                    continue
                group_name, content = parts[1], parts[2]
                members = group_mgr.get_members(group_name)
                if not members:
                    print(f"[ERROR] Nhom '{group_name}' khong ton tai hoac khong co thanh vien.")
                    continue
                failed = direct_chat.broadcast_to_group(members, content, group_name, bootstrap=bootstrap)
                print(f"  [Ban -> NHOM {group_name}] {content}")
                for member in failed:
                    print(bootstrap.report_unreachable(member))
                    result = bootstrap.queue_offline_message(member, f"[NHOM {group_name}] {content}")
                    print(f"  {result.get('message')}")
                continue

            if raw.startswith("/group delete "):
                target_group = raw.split(" ", 2)[2].strip()
                if target_group in group_mgr.list_groups():
                    group_mgr.delete_group(target_group)
                else:
                    print(f"[ERROR] Nhom '{target_group}' khong ton tai.")
                continue

            if raw.startswith("/group history "):
                target_group = raw.split(" ", 2)[2].strip()
                hist = group_mgr.get_history(target_group)
                if hist:
                    print(f"\n[NHOM {target_group} - LICH SU THAM GIA] {', '.join(hist)}\n")
                else:
                    print(f"[ERROR] Khong tim thay lich su cho nhom '{target_group}'.")
                continue

            if raw == "/connections":
                conns = direct_chat.list_connections()
                if conns:
                    print(f"\n[KET NOI P2P TRUC TIEP] {', '.join(conns)}\n")
                else:
                    print("[INFO] Chua co ket noi truc tiep nao.\n")
                continue

            if raw.startswith("/disconnect "):
                direct_chat.disconnect_peer(raw.split(" ", 1)[1].strip())
                continue

            if raw == "/groups":
                groups = group_mgr.list_groups()
                if groups:
                    print(f"\n[NHOM HIEN TAI] {', '.join(groups)}\n")
                else:
                    print("[INFO] Chua co nhom nao.\n")
                continue

            if raw == "/help":
                _print_usage()
                continue

            print("[ERROR] Lenh khong hop le. Go /help de xem huong dan.")

    except KeyboardInterrupt:
        bootstrap.disconnect()
        print("\n[BYE] Da thoat chuong trinh.")


def _print_usage() -> None:
    print("\n" + "=" * 65)
    print("  HUONG DAN SU DUNG - TRUE P2P CHAT SYSTEM")
    print("=" * 65)
    print("  KET NOI & KHAM PHA")
    print("    /list                            Xem danh sach peers online")
    print("    /peers                           Xem tat ca peer da tham gia va trang thai")
    print("    /connect <user>                  Ket noi P2P truc tiep toi peer")
    print("    /connections                     Xem cac ket noi P2P hien tai")
    print("    /disconnect <user>               Dong ket noi P2P voi peer do")
    print()
    print("  NHAN TIN TRUC TIEP (P2P)")
    print("    /msg <user> <noi_dung>           Gui tin nhan truc tiep; offline se luu hang doi")
    print("    /offline_msg <user> <noi_dung>   Chu dong luu/giai quyet tin offline")
    print("    /queue_info                      Xem so tin dang cho ban tren tracker")
    print("    /server_reset                    Reset tracker va xoa toan bo trang thai")
    print()
    print("  CHAT NHOM")
    print("    /group create <ten> <u1> <u2>    Tao nhom chat P2P")
    print("    /group delete <ten>              Xoa nhom chat")
    print("    /group history <ten>             Xem lich su peer da tung vao nhom")
    print("    /groups                          Xem danh sach nhom")
    print("    /broadcast <ten_nhom> <noi_dung> Gui tin toi ca nhom")
    print()
    print("  FILE & MA HOA")
    print("    /sendfile <user> <path>          Gui file truc tiep toi peer")
    print("    /encrypt <user> <shared_key>     Bat ma hoa voi peer do")
    print("    /unencrypt <user>                Tat ma hoa voi peer do")
    print("    /enc_status                      Xem trang thai ma hoa")
    print()
    print("  HE THONG")
    print("    /help                            Hien thi huong dan nay")
    print("    /exit                            Thoat khoi mang P2P")
    print("=" * 65 + "\n")
