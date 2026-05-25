"""
gui.py – Giao dien Tkinter cho P2P Chat System
"""
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox, simpledialog
import threading
import queue
import os
import base64
import time

from p2p_chat import spec
from p2p_chat.client import (
    PeerServer, BootstrapClient, DirectChat,
    GroupManager, _find_free_port
)
from p2p_chat.protocol import ProtocolError, discard_buffer, recv_json

# ── Mau sac & Font ────────────────────────────────────────────
BG_DARK      = "#1e1e2e"
BG_PANEL     = "#2a2a3e"
BG_CHAT      = "#13131f"
ACCENT       = "#7c3aed"
ACCENT_LIGHT = "#a78bfa"
TEXT_WHITE   = "#f8fafc"
TEXT_GRAY    = "#94a3b8"
GREEN        = "#22c55e"
ORANGE       = "#fb923c"
RED          = "#ef4444"

FONT_MAIN  = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO  = ("Consolas", 10)


# ── PeerServer tuy chinh: dua message vao queue thay vi print ─
class GUIPeerServer(PeerServer):
    def __init__(self, port, username, msg_queue):
        super().__init__(port, username)
        self.msg_queue = msg_queue

    def _handle_incoming(self, conn, addr):
        try:
            while True:
                msg = recv_json(conn)
                if not msg:
                    break

                msg_id = msg.get("id")
                if self._already_seen(msg_id):
                    self._send_ack(conn, msg_id)
                    continue

                mtype = msg.get("type")
                if mtype == "chat":
                    sender  = msg.get("from", "?")
                    content = msg.get("content", "")
                    is_enc  = msg.get("encrypted", False)
                    if is_enc and hasattr(self, "_encryptors"):
                        enc = self._encryptors.get(sender)
                        if enc:
                            try:
                                content = enc.decrypt(content)
                                is_enc = True
                            except Exception:
                                content = "[LOI GIAI MA]"
                                is_enc = False
                        else:
                            content = "[LOI GIAI MA] Chua co shared key cho peer gui."
                    self.msg_queue.put({
                        "event":     "message",
                        "from":      sender,
                        "content":   content,
                        "group":     msg.get("group"),
                        "encrypted": is_enc,
                    })
                    self._send_ack(conn, msg_id)

                elif mtype == "file_info":
                    self.msg_queue.put({
                        "event":    "file",
                        "from":     msg.get("from"),
                        "filename": msg.get("filename"),
                        "size":     msg.get("size"),
                        "data":     msg.get("data", ""),
                    })
                    self._send_ack(conn, msg_id)
                elif mtype == "group_invite":
                    self.msg_queue.put({
                        "event": "group_invite",
                        "from": msg.get("from", "?"),
                        "group": msg.get("group", ""),
                        "members": msg.get("members", []),
                    })
                    self._send_ack(conn, msg_id)
                else:
                    self._send_ack(conn, msg_id, status="error", error="unknown message type")
        except ProtocolError as exc:
            self.msg_queue.put({
                "event": "system",
                "content": f"[Loi giao thuc] {addr}: {exc}",
            })
        except Exception:
            pass
        finally:
            discard_buffer(conn)
            conn.close()


# ── Man hinh dang nhap ────────────────────────────────────────
class LoginScreen(tk.Frame):
    def __init__(self, master, on_login):
        super().__init__(master, bg=BG_DARK)
        self.on_login = on_login
        self._build()

    def _build(self):
        center = tk.Frame(self, bg=BG_DARK)
        center.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(center, text="🌐 P2P Chat System",
                 font=("Segoe UI", 22, "bold"), fg=ACCENT_LIGHT, bg=BG_DARK).pack(pady=(0, 4))
        tk.Label(center, text="He thong chat ngang hang phan tan",
                 font=FONT_SMALL, fg=TEXT_GRAY, bg=BG_DARK).pack(pady=(0, 24))

        card = tk.Frame(center, bg=BG_PANEL, padx=32, pady=28)
        card.pack()

        def field(lbl, default):
            tk.Label(card, text=lbl, font=FONT_BOLD, fg=TEXT_GRAY,
                     bg=BG_PANEL, anchor="w").pack(fill="x")
            e = tk.Entry(card, font=FONT_MAIN, bg="#3a3a55", fg=TEXT_WHITE,
                         insertbackground=TEXT_WHITE, relief="flat",
                         bd=6, highlightthickness=1, highlightcolor=ACCENT)
            e.insert(0, default)
            e.pack(fill="x", pady=(2, 12))
            return e

        self.e_user   = field("Ten nguoi dung", "Alice")
        self.e_server = field("Bootstrap Server IP", spec.HOST)
        self.e_port   = field("Bootstrap Server Port", str(spec.PORT))

        self.btn = tk.Button(card, text="  Tham gia mang P2P  ",
                             font=FONT_BOLD, fg="white", bg=ACCENT,
                             relief="flat", padx=20, pady=10,
                             cursor="hand2", command=self._submit)
        self.btn.pack(fill="x", pady=(4, 0))

        self.status = tk.Label(center, text="", font=FONT_SMALL,
                               fg=RED, bg=BG_DARK)
        self.status.pack(pady=(8, 0))
        self.e_user.bind("<Return>", lambda _: self._submit())

    def _submit(self):
        user = self.e_user.get().strip()
        srv  = self.e_server.get().strip()
        try:
            port = int(self.e_port.get().strip())
        except ValueError:
            self.status.config(text="Cong khong hop le!")
            return
        if not user:
            self.status.config(text="Vui long nhap ten nguoi dung!")
            return
        self.status.config(text="Dang ket noi...", fg=TEXT_GRAY)
        self.btn.config(state="disabled")
        self.update()
        self.on_login(user, srv, port, self._on_error)

    def _on_error(self, msg):
        self.status.config(text=msg, fg=RED)
        self.btn.config(state="normal")


# ── Cua so chat chinh ─────────────────────────────────────────
class ChatWindow(tk.Frame):
    def __init__(self, master, app, username, bootstrap, direct_chat, group_mgr, msg_queue):
        super().__init__(master, bg=BG_DARK)
        self.app         = app
        self.username    = username
        self.bootstrap   = bootstrap
        self.direct_chat = direct_chat
        self.group_mgr   = group_mgr
        self.msg_queue   = msg_queue
        self.current_chat = None   # peer or group name
        self.history      = {}     # {name: [(tag, text)]}
        self.peer_statuses = {}
        self.peer_rows = {}
        self._build()
        self._poll()
        self._auto_refresh_peers()

    # ── Build UI ──────────────────────────────────────────────
    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=ACCENT, height=46)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="🌐  P2P Chat System",
                 font=FONT_BOLD, fg="white", bg=ACCENT).pack(side="left", padx=14)
        tk.Button(hdr, text="🚪 Đăng xuất", font=FONT_SMALL, fg="white",
                  bg=RED, relief="flat", padx=8, pady=3,
                  cursor="hand2", command=self._logout).pack(side="right", padx=14)
        tk.Button(hdr, text="Reset server", font=FONT_SMALL, fg="white",
                  bg="#334155", relief="flat", padx=8, pady=3,
                  cursor="hand2", command=self._reset_server).pack(side="right", padx=(0, 6))
        tk.Label(hdr, text=f"👤 {self.username}",
                 font=FONT_BOLD, fg=ACCENT_LIGHT, bg=ACCENT).pack(side="right", padx=14)

        # Body
        body = tk.Frame(self, bg=BG_DARK)
        body.pack(fill="both", expand=True)

        self._build_left(body)
        tk.Frame(body, bg="#3a3a55", width=1).pack(side="left", fill="y")
        self._build_right(body)

    def _build_left(self, parent):
        left = tk.Frame(parent, bg=BG_PANEL, width=210)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        # Peers
        tk.Label(left, text="PEERS", font=("Segoe UI", 8, "bold"),
                 fg=TEXT_GRAY, bg=BG_PANEL).pack(anchor="w", padx=12, pady=(12, 3))

        self.peer_lb = tk.Listbox(left, bg=BG_PANEL, fg=TEXT_WHITE,
                                  font=FONT_MAIN, height=10,
                                  selectbackground=ACCENT, relief="flat",
                                  bd=0, highlightthickness=0, activestyle="none")
        self.peer_lb.pack(fill="x", padx=8)
        self.peer_lb.bind("<Double-Button-1>", self._peer_dbl_click)

        row1 = tk.Frame(left, bg=BG_PANEL)
        row1.pack(fill="x", padx=8, pady=4)
        for txt, cmd, col in [
            ("🔄", self._refresh_peers, "#334155"),
            ("Chat", self._connect_selected, ACCENT),
        ]:
            tk.Button(row1, text=txt, font=FONT_SMALL, fg="white",
                      bg=col, relief="flat", padx=6, pady=3,
                      cursor="hand2", command=cmd).pack(side="left", padx=2)

        tk.Button(left, text="✉️ Gửi nhiều người cùng lúc", font=FONT_SMALL, fg="white",
                  bg="#0f766e", relief="flat", padx=8, pady=4,
                  cursor="hand2", command=self._send_multiple_dialog
                  ).pack(fill="x", padx=8, pady=4)

        # Groups
        tk.Label(left, text="NHOM CHAT", font=("Segoe UI", 8, "bold"),
                 fg=TEXT_GRAY, bg=BG_PANEL).pack(anchor="w", padx=12, pady=(14, 3))

        self.group_lb = tk.Listbox(left, bg=BG_PANEL, fg=TEXT_WHITE,
                                   font=FONT_MAIN, height=5,
                                   selectbackground="#0f766e", relief="flat",
                                   bd=0, highlightthickness=0, activestyle="none")
        self.group_lb.pack(fill="x", padx=8)
        self.group_lb.bind("<Double-Button-1>", self._group_dbl_click)

        row_grp = tk.Frame(left, bg=BG_PANEL)
        row_grp.pack(fill="x", padx=8, pady=4)
        
        tk.Button(row_grp, text="➕ Tạo nhóm", font=FONT_SMALL, fg="white",
                  bg="#0f766e", relief="flat", padx=4, pady=3,
                  cursor="hand2", command=self._create_group_dialog).pack(side="left", padx=2)
                  
        tk.Button(row_grp, text="❌ Xóa", font=FONT_SMALL, fg="white",
                  bg=RED, relief="flat", padx=4, pady=3,
                  cursor="hand2", command=self._delete_group_selected).pack(side="left", padx=2)
                  
        tk.Button(row_grp, text="📜 Lịch sử", font=FONT_SMALL, fg="white",
                  bg="#334155", relief="flat", padx=4, pady=3,
                  cursor="hand2", command=self._show_group_history_selected).pack(side="left", padx=2)

        self.status_lbl = tk.Label(left, text="🟢 Online",
                                   font=FONT_SMALL, fg=GREEN, bg=BG_PANEL)
        self.status_lbl.pack(side="bottom", pady=8)

    def _build_right(self, parent):
        right = tk.Frame(parent, bg=BG_DARK)
        right.pack(fill="both", expand=True)

        # Chat header
        self.chat_hdr = tk.Frame(right, bg=BG_PANEL, height=40)
        self.chat_hdr.pack(fill="x")
        self.chat_hdr.pack_propagate(False)
        self.chat_title = tk.Label(self.chat_hdr,
                                   text="Chon mot peer de bat dau chat...",
                                   font=FONT_BOLD, fg=TEXT_WHITE, bg=BG_PANEL)
        self.chat_title.pack(side="left", padx=14, pady=8)
        self.enc_status = tk.Label(self.chat_hdr, text="",
                                   font=FONT_SMALL, fg=TEXT_GRAY, bg=BG_PANEL)
        self.enc_status.pack(side="right", padx=14)

        # Chat display
        self.display = scrolledtext.ScrolledText(
            right, font=FONT_MONO, bg=BG_CHAT, fg=TEXT_WHITE,
            relief="flat", bd=0, padx=12, pady=10,
            wrap="word", state="disabled", highlightthickness=0)
        self.display.pack(fill="both", expand=True)
        for tag, color, fnt in [
            ("sent",   ACCENT_LIGHT, FONT_MONO),
            ("recv",   "#60a5fa",    FONT_MONO),
            ("sys",    TEXT_GRAY,    FONT_SMALL),
            ("enc",    GREEN,        FONT_MONO),
            ("file",   ORANGE,       FONT_MONO),
            ("time",   "#475569",    ("Consolas", 8)),
        ]:
            self.display.tag_config(tag, foreground=color, font=fnt)

        # Encryption bar
        enc_bar = tk.Frame(right, bg="#1e293b", height=32)
        enc_bar.pack(fill="x")
        enc_bar.pack_propagate(False)
        tk.Label(enc_bar, text="🔒 Ma hoa AES-256:",
                 font=FONT_SMALL, fg=TEXT_GRAY, bg="#1e293b").pack(side="left", padx=(10, 4), pady=5)
        self.enc_key = tk.Entry(enc_bar, font=FONT_SMALL, bg="#334155",
                                fg=TEXT_WHITE, relief="flat", bd=4, width=16,
                                insertbackground=TEXT_WHITE, show="*")
        self.enc_key.insert(0, "mykey123")
        self.enc_key.pack(side="left", padx=4, pady=5)
        self.enc_var = tk.BooleanVar()
        tk.Checkbutton(enc_bar, text="Bat", variable=self.enc_var,
                       font=FONT_SMALL, fg=TEXT_WHITE, bg="#1e293b",
                       selectcolor="#1e293b", activebackground="#1e293b",
                       command=self._toggle_enc).pack(side="left")

        # Input bar
        inp = tk.Frame(right, bg="#1e293b", height=52)
        inp.pack(fill="x")
        inp.pack_propagate(False)
        tk.Button(inp, text="📎", font=("Segoe UI", 13), fg=TEXT_WHITE,
                  bg="#334155", relief="flat", padx=8, pady=6,
                  cursor="hand2", command=self._send_file
                  ).pack(side="left", padx=(8, 4), pady=8)
        self.entry = tk.Entry(inp, font=FONT_MAIN, bg="#334155", fg=TEXT_WHITE,
                              relief="flat", bd=8, insertbackground=TEXT_WHITE,
                              highlightthickness=1, highlightcolor=ACCENT)
        self.entry.pack(side="left", fill="both", expand=True, pady=10)
        self.entry.bind("<Return>", lambda _: self._send_msg())
        send_btn = tk.Button(inp, text="Gui ➤", font=FONT_BOLD, fg="white",
                             bg=ACCENT, relief="flat", padx=14, pady=8,
                             cursor="hand2", command=self._send_msg)
        send_btn.pack(side="right", padx=8, pady=8)
        send_btn.bind("<Enter>", lambda _: send_btn.config(bg="#6d28d9"))
        send_btn.bind("<Leave>", lambda _: send_btn.config(bg=ACCENT))

    # ── Helpers ───────────────────────────────────────────────
    def _append(self, tag, text, store=True):
        t = time.strftime("%H:%M:%S")
        self.display.config(state="normal")
        self.display.insert("end", f"[{t}] ", "time")
        self.display.insert("end", text + "\n", tag)
        self.display.see("end")
        self.display.config(state="disabled")
        if store and self.current_chat:
            self.history.setdefault(self.current_chat, []).append((tag, text))

    def _switch_chat(self, name):
        self.current_chat = name
        self.chat_title.config(text=f"💬  {name}")
        # Reload history
        self.display.config(state="normal")
        self.display.delete("1.0", "end")
        for tag, txt in self.history.get(name, []):
            self.display.insert("end", txt + "\n", tag)
        self.display.see("end")
        self.display.config(state="disabled")
        self._update_chat_status_indicator(name)

    def _update_chat_status_indicator(self, name):
        if name not in self.group_mgr.list_groups() and self._is_known_offline_peer(name):
            self.enc_status.config(text="Offline", fg=ORANGE)
            self.enc_var.set(False)
            return
        enc = name in self.direct_chat.list_encrypted_connections()
        self.enc_status.config(text="🔒 Ma hoa" if enc else "🔓 Thuong",
                               fg=GREEN if enc else TEXT_GRAY)
        self.enc_var.set(enc)

    def _is_known_offline_peer(self, username):
        info = self.peer_statuses.get(username)
        return info is not None and not bool(info.get("online"))

    def _open_peer_chat(self, peer):
        self._switch_chat(peer)
        if self._is_known_offline_peer(peer):
            self._append(
                "sys",
                f"[He thong] '{peer}' dang offline. Tin nhan van ban se duoc luu va gui khi peer online lai.",
                store=False,
            )

    def _queue_offline_text(self, peer, text):
        result = self.bootstrap.queue_offline_message(peer, text)
        if result.get("status") == "queued":
            self._append("sent", f"[Ban -> {peer} | offline] {text}")
            self._append("sys", result.get("message", "Tin nhan da duoc luu."), store=False)
            return True

        if result.get("status") == "online":
            ip, port = result["ip"], result["port"]
            self.peer_statuses[peer] = {"ip": ip, "port": port, "online": True}
            if self.direct_chat.connect_to_peer(peer, ip, port) and self.direct_chat.send_direct(peer, text):
                self._append("sent", f"[Ban -> {peer}] {text}")
                return True

            self.bootstrap.report_unreachable(peer)
            retry = self.bootstrap.queue_offline_message(peer, text)
            if retry.get("status") == "queued":
                self._append("sent", f"[Ban -> {peer} | offline] {text}")
                self._append("sys", retry.get("message", "Tin nhan da duoc luu."), store=False)
                return True
            self._append("sys", retry.get("message", "[Loi] Khong the luu tin nhan offline."), store=False)
            return False

        self._append("sys", result.get("message", "[Loi] Khong the luu tin nhan offline."), store=False)
        return False

    # ── Peer actions ─────────────────────────────────────────
    def _refresh_peers(self):
        def do():
            peers = self.bootstrap.get_peer_statuses()
            self.after(0, lambda: self._update_peer_list(peers))
        threading.Thread(target=do, daemon=True).start()

    def _auto_refresh_peers(self):
        self._refresh_peers()
        self.after(5000, self._auto_refresh_peers)

    def _update_peer_list(self, peers: dict):
        self.peer_lb.delete(0, "end")
        incoming = peers or {}
        merged = {
            uname: {
                "ip": info.get("ip", ""),
                "port": info.get("port", 0),
                "online": False,
            }
            for uname, info in self.peer_statuses.items()
        }
        for uname, info in incoming.items():
            merged[uname] = {
                "ip": info.get("ip", merged.get(uname, {}).get("ip", "")),
                "port": info.get("port", merged.get(uname, {}).get("port", 0)),
                "online": bool(info.get("online")),
            }
        self.peer_statuses = merged
        self.peer_rows = {}
        sorted_peers = sorted(
            self.peer_statuses.items(),
            key=lambda item: (not bool(item[1].get("online")), item[0].lower()),
        )
        for u, info in sorted_peers:
            online = bool(info.get("online"))
            tag = " (ban)" if u == self.username else ""
            ip = info.get("ip") or "-"
            port = info.get("port") or "-"
            prefix = "[ON] " if online else "[OFF]"
            self.peer_lb.insert("end", f"{prefix} {u}{tag}  {ip}:{port}")
            index = self.peer_lb.size() - 1
            self.peer_rows[index] = {"username": u, "online": online}
            self.peer_lb.itemconfig(index, fg=TEXT_WHITE if online else TEXT_GRAY)
        online_count = sum(1 for info in self.peer_statuses.values() if info.get("online"))
        offline_count = len(self.peer_statuses) - online_count
        self.status_lbl.config(text=f"{online_count} online / {offline_count} offline", fg=GREEN)
        if self.current_chat and self.current_chat not in self.group_mgr.list_groups():
            self._update_chat_status_indicator(self.current_chat)

    def _get_selected_peer_row(self):
        sel = self.peer_lb.curselection()
        if not sel:
            return None
        return self.peer_rows.get(sel[0])

    def _get_selected_peer(self):
        row = self._get_selected_peer_row()
        if not row:
            return None
        return row["username"]

    def _peer_dbl_click(self, _):
        row = self._get_selected_peer_row()
        if not row:
            return
        peer = row["username"]
        if peer != self.username and row["online"]:
            self._connect_to(peer)
        elif peer != self.username:
            self._open_peer_chat(peer)

    def _connect_selected(self):
        row = self._get_selected_peer_row()
        if not row:
            messagebox.showinfo("P2P Chat", "Chon mot peer!")
            return
        peer = row["username"]
        if peer != self.username:
            if not row["online"]:
                self._open_peer_chat(peer)
                return
            self._connect_to(peer)

    def _connect_to(self, peer):
        def do():
            info = self.bootstrap.get_peer_info(peer)
            if not info:
                self.peer_statuses.setdefault(peer, {})["online"] = False
                self.after(0, lambda: self._open_peer_chat(peer))
                return
            ip, port = info
            ok = self.direct_chat.connect_to_peer(peer, ip, port)
            if ok:
                def ui():
                    self._switch_chat(peer)
                    self._append("sys", f"[He thong] Da ket noi P2P voi '{peer}' ({ip}:{port}) ✓")
                self.after(0, ui)
            else:
                self.bootstrap.report_unreachable(peer)
                self.peer_statuses.setdefault(peer, {})["online"] = False
                self.after(0, lambda: self._open_peer_chat(peer))
                self._refresh_peers()
        threading.Thread(target=do, daemon=True).start()

    # ── Group actions ─────────────────────────────────────────
    def _create_group_dialog(self):
        name = simpledialog.askstring("Tao nhom", "Ten nhom:", parent=self)
        if not name:
            return
        members_str = simpledialog.askstring(
            "Thanh vien", "Nhap ten cac peer (cach nhau dau phay):", parent=self)
        if not members_str:
            return
        members = [m.strip() for m in members_str.split(",") if m.strip()]
        def do():
            for m in members:
                if m not in self.direct_chat.list_connections():
                    info = self.bootstrap.get_peer_info(m)
                    if info:
                        self.direct_chat.connect_to_peer(m, info[0], info[1])
            self.group_mgr.create_group(name, members)
            
            # Send P2P group invites to all members
            all_members = [self.username] + members
            for m in members:
                self.direct_chat.send_group_invite(m, name, all_members)
                
            def ui():
                self.group_lb.insert("end", name)
                self._switch_chat(name)
                self._append("sys", f"[He thong] Da tao nhom '{name}' voi: {', '.join(members)}")
            self.after(0, ui)
        threading.Thread(target=do, daemon=True).start()

    def _delete_group_selected(self):
        sel = self.group_lb.curselection()
        if not sel:
            messagebox.showinfo("Nhóm", "Vui lòng chọn một nhóm để xóa!")
            return
        group_name = self.group_lb.get(sel[0])
        if messagebox.askyesno("Xóa nhóm", f"Bạn có chắc muốn xóa nhóm '{group_name}'?"):
            self.group_mgr.delete_group(group_name)
            self.group_lb.delete(sel[0])
            if self.current_chat == group_name:
                self.current_chat = None
                self.chat_title.config(text="Chọn một peer để bắt đầu chat...")
                self.display.config(state="normal")
                self.display.delete("1.0", "end")
                self.display.config(state="disabled")
                
    def _show_group_history_selected(self):
        sel = self.group_lb.curselection()
        if not sel:
            messagebox.showinfo("Nhóm", "Vui lòng chọn một nhóm!")
            return
        group_name = self.group_lb.get(sel[0])
        hist = self.group_mgr.get_history(group_name)
        if not hist:
            messagebox.showinfo(f"Lịch sử nhóm {group_name}", "Chua co peer nao trong lich su nhom.")
            return
        rows = []
        for member in hist:
            info = self.peer_statuses.get(member, {})
            state = "online" if info.get("online") else "offline"
            rows.append(f"- [{state}] {member}")
        messagebox.showinfo(f"Lịch sử nhóm {group_name}", "Cac peer da tung tham gia nhom nay:\n\n" + "\n".join(rows))

    def _logout(self):
        self.bootstrap.disconnect()
        if hasattr(self.direct_chat, "peer_server") and self.direct_chat.peer_server:
            self.direct_chat.peer_server.stop()
        for peer in list(self.direct_chat.connections.keys()):
            self.direct_chat._remove_connection(peer)
        self.app._show_login_from_chat(self)

    def _reset_server(self):
        if not messagebox.askyesno("Reset server", "Reset server se xoa danh sach peer va tat ca tin offline dang cho. Tiep tuc?"):
            return

        def do():
            response = self.bootstrap.reset_server()
            if hasattr(self.direct_chat, "peer_server") and self.direct_chat.peer_server:
                self.direct_chat.peer_server.stop()
            for peer in list(self.direct_chat.connections.keys()):
                self.direct_chat._remove_connection(peer)

            def ui():
                messagebox.showinfo("Reset server", response)
                self.app._show_login_from_chat(self)

            self.after(0, ui)

        threading.Thread(target=do, daemon=True).start()

    def _send_multiple_dialog(self):
        targets_str = simpledialog.askstring(
            "Gửi nhiều người", "Nhập tên các peer (cách nhau bởi dấu phẩy):", parent=self)
        if not targets_str:
            return
        text = simpledialog.askstring(
            "Nội dung", "Nhập nội dung tin nhắn cần gửi:", parent=self)
        if not text:
            return
        targets = [t.strip() for t in targets_str.split(",") if t.strip()]
        
        def do():
            for target in targets:
                if target == self.username:
                    continue
                if target not in self.direct_chat.list_connections():
                    info = self.bootstrap.get_peer_info(target)
                    if info:
                        self.direct_chat.connect_to_peer(target, info[0], info[1])
                
                if target in self.direct_chat.list_connections() and self.direct_chat.send_direct(target, text):
                    self.after(0, lambda t=target: self._append("sent", f"[Bạn → {t}] {text}", store=True))
                else:
                    self.bootstrap.report_unreachable(target)
                    result = self.bootstrap.queue_offline_message(target, text)
                    self.after(0, lambda t=target, r=result: self._append("sys", f"[Ngoại tuyến] {r.get('message')}", store=False))
        threading.Thread(target=do, daemon=True).start()

    def _group_dbl_click(self, _):
        sel = self.group_lb.curselection()
        if sel:
            self._switch_chat(self.group_lb.get(sel[0]))

    # ── Send message ──────────────────────────────────────────
    def _send_msg(self):
        text = self.entry.get().strip()
        if not text or not self.current_chat:
            return
        self.entry.delete(0, "end")
        groups = self.group_mgr.list_groups()
        if self.current_chat in groups:
            members = self.group_mgr.get_members(self.current_chat)
            failed = self.direct_chat.broadcast_to_group(members, text, self.current_chat, bootstrap=self.bootstrap)
            self._append("sent", f"[Ban → NHOM {self.current_chat}] {text}")
            for peer in failed:
                self.bootstrap.report_unreachable(peer)
                result = self.bootstrap.queue_offline_message(peer, f"[NHOM {self.current_chat}] {text}")
                self._append("sys", result.get("message", f"[Loi] Khong gui duoc toi {peer}."))
        else:
            peer = self.current_chat
            if self._is_known_offline_peer(peer) and peer not in self.direct_chat.list_connections():
                self._queue_offline_text(peer, text)
                self._refresh_peers()
                return

            ok = self.direct_chat.send_direct(peer, text)
            if ok:
                enc = peer in self.direct_chat.list_encrypted_connections()
                tag = "enc" if enc else "sent"
                lock = "🔒 " if enc else ""
                self._append(tag, f"{lock}[Ban → {peer}] {text}")
            else:
                self.bootstrap.report_unreachable(peer)
                self._queue_offline_text(peer, text)
                self._refresh_peers()

    # ── Send file ─────────────────────────────────────────────
    def _send_file(self):
        if not self.current_chat:
            messagebox.showinfo("P2P Chat", "Chon mot peer truoc!")
            return
        if self.current_chat not in self.group_mgr.list_groups() and self._is_known_offline_peer(self.current_chat):
            messagebox.showinfo("P2P Chat", "Peer dang offline. Store-and-forward chi ho tro tin nhan van ban.")
            return
        path = filedialog.askopenfilename(title="Chon file de gui")
        if not path:
            return
        size = os.path.getsize(path)
        if size > 10 * 1024 * 1024:
            messagebox.showerror("Loi", "File qua lon! Gioi han 10MB.")
            return
        def do():
            groups = self.group_mgr.list_groups()
            targets = self.group_mgr.get_members(self.current_chat) if self.current_chat in groups else [self.current_chat]
            sent = []
            failed = []
            for target in targets:
                if self.direct_chat.send_file(target, path):
                    sent.append(target)
                else:
                    failed.append(target)

            def ui():
                if sent:
                    self._append("file", f"[File] Ban → {', '.join(sent)}: {os.path.basename(path)} ({size} bytes)")
                if failed:
                    self._append("sys", f"[Loi] Khong gui file duoc toi: {', '.join(failed)}")
            self.after(0, ui)
        threading.Thread(target=do, daemon=True).start()

    # ── Encryption toggle ─────────────────────────────────────
    def _toggle_enc(self):
        if not self.current_chat:
            return
        if self.current_chat in self.group_mgr.list_groups():
            messagebox.showwarning("Ma hoa", "Hay chon mot peer truc tiep de bat/tat ma hoa.")
            self.enc_var.set(False)
            return
        key = self.enc_key.get().strip()
        if not key:
            messagebox.showwarning("Ma hoa", "Nhap shared key truoc!")
            self.enc_var.set(False)
            return
        if self.enc_var.get():
            if self.direct_chat.set_encryptor(self.current_chat, key):
                self.enc_status.config(text="🔒 Ma hoa", fg=GREEN)
                self._append("sys", f"[He thong] Da bat ma hoa AES-256 voi {self.current_chat}")
            else:
                self.enc_var.set(False)
        else:
            self.direct_chat.remove_encryptor(self.current_chat)
            self.enc_status.config(text="🔓 Thuong", fg=TEXT_GRAY)
            self._append("sys", f"[He thong] Da tat ma hoa voi {self.current_chat}")

    # ── Queue polling: cap nhat UI tu network thread ──────────
    def _poll(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                event = item.get("event")

                if event == "message":
                    sender  = item["from"]
                    content = item["content"]
                    group   = item.get("group")
                    is_enc  = item.get("encrypted", False)
                    lock    = "🔒 " if is_enc else ""
                    tag     = "enc" if is_enc else "recv"
                    label   = f"NHOM {group}" if group else sender
                    chat_key = group if group else sender

                    line = f"{lock}[{label}] {content}"
                    if self.current_chat == chat_key:
                        self._append(tag, line)
                    else:
                        self.history.setdefault(chat_key, []).append((tag, line))

                elif event == "file":
                    sender   = item["from"]
                    filename = item["filename"]
                    size     = item["size"]
                    data_b64 = item.get("data", "")
                    # Tu dong luu file vao thu muc hien tai
                    if data_b64:
                        try:
                            save_path = os.path.join(os.getcwd(), f"recv_{filename}")
                            with open(save_path, "wb") as f:
                                f.write(base64.b64decode(data_b64))
                            txt = f"[File] {sender} gui: {filename} ({size} bytes) → Luu: {save_path}"
                        except Exception as e:
                            txt = f"[File] {sender} gui {filename} – Loi luu: {e}"
                    else:
                        txt = f"[File] {sender} gui: {filename} ({size} bytes)"

                    if self.current_chat == sender:
                        self._append("file", txt)
                    else:
                        self.history.setdefault(sender, []).append(("file", txt))

                elif event == "group_invite":
                    sender = item["from"]
                    group = item["group"]
                    members = item["members"]
                    filtered_members = [m for m in members if m != self.username]
                    self.group_mgr.create_group(group, filtered_members)
                    if group not in self.group_lb.get(0, tk.END):
                        self.group_lb.insert(tk.END, group)
                    self._append("sys", f"[He thong] Bạn đã được thêm vào nhóm '{group}' bởi {sender}. Thành viên: {', '.join(members)}")

                elif event == "system":
                    self._append("sys", item["content"])

        except Exception:
            pass
        self.after(150, self._poll)


# ── App chinh ─────────────────────────────────────────────────
class P2PChatApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("P2P Chat System")
        self.root.geometry("920x660")
        self.root.configure(bg=BG_DARK)
        self.root.resizable(True, True)

        self.msg_queue = queue.Queue()
        self._show_login()
        self.root.mainloop()

    def _show_login(self):
        self.login = LoginScreen(self.root, self._do_login)
        self.login.pack(fill="both", expand=True)

    def _do_login(self, username, server, port, on_error):
        def connect():
            try:
                listen_port = _find_free_port(spec.P2P_BASE_PORT)
                peer_srv    = GUIPeerServer(listen_port, username, self.msg_queue)
                peer_srv.start()

                bootstrap = BootstrapClient()
                ok = bootstrap.connect_and_register((server, port), username, listen_port)
                if not ok:
                    peer_srv.stop()
                    self.root.after(0, lambda: on_error(bootstrap.last_error or "Ket noi that bai! Kiem tra server."))
                    return
                for msg in bootstrap.pending_messages:
                    self.msg_queue.put({
                        "event": "message",
                        "from": msg.get("from", "offline"),
                        "content": f"[Offline] {msg.get('content', '')}",
                        "group": None,
                        "encrypted": False,
                    })

                direct_chat = DirectChat(username, peer_srv)
                group_mgr   = GroupManager()
                peer_srv.group_mgr = group_mgr

                def show_main():
                    self.login.destroy()
                    self.root.title(f"P2P Chat – {username}")
                    chat_win = ChatWindow(
                        self.root, self, username, bootstrap,
                        direct_chat, group_mgr, self.msg_queue)
                    chat_win.pack(fill="both", expand=True)

                self.root.after(0, show_main)
            except Exception as ex:
                self.root.after(0, lambda: on_error(f"Loi: {ex}"))

        threading.Thread(target=connect, daemon=True).start()

    def _show_login_from_chat(self, chat_win):
        chat_win.destroy()
        self.root.title("P2P Chat System")
        self._show_login()


def launch():
    P2PChatApp()


if __name__ == "__main__":
    launch()
