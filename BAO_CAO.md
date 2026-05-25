# Báo cáo đồ án: True P2P Chat System

## 1. Kiến trúc hệ thống

Hệ thống gồm hai loại tiến trình:

- Bootstrap/Tracker server: lưu registry các peer online theo `username -> ip:port`, trả danh sách peer, hỗ trợ tìm địa chỉ peer, lưu tin nhắn offline.
- Peer node: mỗi peer đồng thời chạy một TCP server riêng để nhận tin trực tiếp và một client kết nối tracker để đăng ký/khám phá peer.

Tracker không relay tin nhắn chat thông thường. Sau khi biết địa chỉ nhau, các peer gửi tin, broadcast nhóm và file trực tiếp qua socket P2P.

## 2. Giao thức trao đổi thông điệp

TCP là byte stream nên hệ thống dùng framing trong `p2p_chat/protocol.py`. Mỗi thông điệp là một dòng UTF-8 kết thúc bằng `\n`.

Giao thức tracker dạng text frame:

- Tracker -> peer: `REGISTER`
- Peer -> tracker: `username:listen_port`
- Tracker -> peer: `[OK] ...` hoặc `[ERROR] ...`
- Tracker -> peer: `OFFLINE_MESSAGES:[...]`
- Peer -> tracker: `/list`, `/connect <user>`, `/offline_msg <user> <content>`, `/queue_info`, `/unreachable <user>`, `/exit`

Giao thức P2P dạng JSON frame:

```json
{"type":"chat","id":"...","from":"Alice","to":"Bob","content":"...","encrypted":false}
```

Mỗi tin trực tiếp có `id`. Peer nhận phản hồi:

```json
{"type":"ack","id":"...","status":"ok"}
```

Nếu không nhận ACK trong thời gian chờ, peer gửi lại. Peer nhận có bộ nhớ `seen_message_ids` để ACK lại nhưng không hiển thị trùng.

## 3. Peer discovery

Khi khởi động, peer tìm cổng trống cho PeerServer rồi đăng ký `username:listen_port` với tracker. Tracker lấy IP từ socket kết nối đến và lưu vào registry.

Peer dùng `/list` để lấy toàn bộ peer online hoặc `/connect <user>` để lấy `ip:port` của một peer cụ thể. Sau đó peer mở TCP socket trực tiếp đến PeerServer của peer đích.

## 4. Online/offline

Tracker cập nhật trạng thái online khi peer đăng ký thành công. Khi peer gửi `/exit`, đóng socket, lỗi socket hoặc bị peer khác báo unreachable sau lỗi gửi trực tiếp, tracker xóa peer khỏi registry. GUI tự làm mới danh sách online định kỳ 5 giây.

## 5. Chat trực tiếp và chat nhóm

Chat trực tiếp dùng socket P2P giữa hai peer, không đi qua tracker. Chat nhóm dùng mô hình fan-out: peer gửi lặp lại cùng nội dung tới từng thành viên trong nhóm bằng kết nối trực tiếp. Nếu một thành viên không phản hồi, hệ thống báo lỗi, cập nhật trạng thái offline và lưu tin qua store-and-forward.

## 6. Truyền tin đáng tin cậy và xử lý lỗi

Hệ thống có các cơ chế:

- `sendall()` để tránh gửi thiếu byte.
- Framing để tránh lỗi nhận dính/tách gói TCP.
- ACK theo `message_id`.
- Retry khi không nhận ACK.
- Timeout khi kết nối hoặc chờ ACK.
- Xóa kết nối lỗi khỏi danh sách P2P.
- Báo tracker cập nhật peer unreachable.
- Store-and-forward khi peer offline.

## 7. Chức năng nâng cao

- Broadcast nhóm.
- Store-and-forward tin offline trong bộ nhớ tracker.
- Mã hóa tin nhắn theo shared key, ưu tiên Fernet/AES-256 khi cài `cryptography`.
- GUI Tkinter.
- Gửi file trực tiếp tối đa 10MB.

## 8. Kiểm thử đề xuất

1. Chạy tracker, Alice, Bob. Alice `/list`, `/connect Bob`, `/msg Bob Xin chao`.
2. Alice tạo nhóm `/group create team Bob Carol`, sau đó `/broadcast team Chao nhom`.
3. Tắt Bob, Alice `/msg Bob Tin offline`. Mở lại Bob và kiểm tra Bob nhận tin offline từ tracker.
4. Bật mã hóa ở cả hai peer bằng cùng shared key, gửi tin và kiểm tra peer nhận giải mã đúng.
5. Gửi file bằng `/sendfile Bob <path>` hoặc nút đính kèm trong GUI.
6. Dùng `python -m compileall p2p_chat` để kiểm tra lỗi cú pháp.
