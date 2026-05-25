# True P2P Chat System

Đồ án mô phỏng hệ thống chat ngang hàng trong hệ phân tán. Bootstrap/tracker chỉ dùng để đăng ký, khám phá peer, theo dõi online/offline và lưu tin offline. Tin nhắn, chat nhóm và file được truyền trực tiếp giữa các peer qua TCP socket.

## Tính năng

- Peer vừa là client vừa là TCP server để gửi và nhận đồng thời.
- Bootstrap server cung cấp peer discovery và danh sách online.
- Chat trực tiếp P2P với ACK, timeout và retry.
- Chat nhóm bằng cách fan-out trực tiếp tới từng peer trong nhóm.
- Store-and-forward: tin gửi tới peer offline được tracker giữ trong hàng đợi và trả khi peer online lại.
- Mã hóa tin nhắn theo shared key. Nếu có `cryptography`, dùng Fernet/AES-256; nếu chưa cài, dùng fallback minh họa.
- GUI Tkinter, gửi file trực tiếp, tự cập nhật danh sách online.

## Cài đặt

```powershell
cd p2p-chat
pip install .
```

Tùy chọn mã hóa AES-256 đầy đủ:

```powershell
pip install ".[crypto]"
```

## Chạy trên cùng máy

Terminal 1:

```powershell
p2p_demo run server --host 127.0.0.1 --port 55555
```

Terminal 2:

```powershell
p2p_demo run client --username Alice --server-host 127.0.0.1 --server-port 55555
```

Terminal 3:

```powershell
p2p_demo run client --username Bob --server-host 127.0.0.1 --server-port 55555
```

GUI:

```powershell
p2p_demo run gui
```

## Chạy trong mạng LAN

Máy chạy tracker:

```powershell
p2p_demo run server --host 0.0.0.0 --port 55555
```

Các peer kết nối tới IP LAN của máy tracker:

```powershell
p2p_demo run client --username Alice --server-host <IP_LAN_TRACKER> --server-port 55555
```

## Lệnh console

- `/list`: xem peer online.
- `/connect <user>`: tạo kết nối TCP trực tiếp tới peer.
- `/msg <user> <noi_dung>`: gửi tin trực tiếp; nếu peer offline hoặc không phản hồi thì chuyển sang store-and-forward.
- `/offline_msg <user> <noi_dung>`: chủ động lưu tin offline.
- `/queue_info`: xem số tin đang chờ trên tracker.
- `/group create <ten> <u1> <u2>`: tạo nhóm cục bộ.
- `/broadcast <ten_nhom> <noi_dung>`: gửi tin tới toàn bộ thành viên nhóm.
- `/sendfile <user> <path>`: gửi file trực tiếp, tối đa 10MB.
- `/encrypt <user> <shared_key>` và `/unencrypt <user>`: bật/tắt mã hóa.
- `/connections`, `/disconnect <user>`, `/help`, `/exit`.

## Kiểm tra nhanh

```powershell
python -m compileall p2p_chat
```

Có thể kiểm thử thủ công bằng 3 terminal: chạy tracker, chạy Alice, chạy Bob; Alice `/connect Bob`, `/msg Bob Xin chao`, tạo nhóm, gửi broadcast, tắt Bob rồi thử `/msg Bob Tin offline`.

## Cấu trúc chính

- `p2p_chat/server.py`: bootstrap/tracker, registry online/offline, store-and-forward.
- `p2p_chat/client.py`: PeerServer, BootstrapClient, DirectChat, GroupManager và CLI.
- `p2p_chat/protocol.py`: framing cho TCP socket bằng newline-delimited text/JSON.
- `p2p_chat/encryption.py`: mã hóa tin nhắn theo shared key.
- `p2p_chat/gui.py`: giao diện Tkinter.
- `p2p_chat/store_forward.py`: hàng đợi tin nhắn offline.
- `BAO_CAO.md`: báo cáo kiến trúc, giao thức, peer discovery, xử lý lỗi và kiểm thử.
