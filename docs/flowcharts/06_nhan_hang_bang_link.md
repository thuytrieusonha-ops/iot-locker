# Hình 3.6 - Giải thuật nhận hàng bằng link bảo mật

```mermaid
flowchart TD
    START([GET /nhan-do/link/<raw_token>]) --> HASH[Băm raw token thành token_hash]
    HASH --> LOOKUP[Tra LockerAccessToken active]
    LOOKUP --> TOKEN_OK{Token tồn tại và chưa hết hạn?}
    TOKEN_OK -->|Không| LINK_ERR[Thông báo link không còn hiệu lực]
    TOKEN_OK -->|Có| ORDER_OK{Đơn liên kết còn status = stored?}
    ORDER_OK -->|Không| LINK_ERR
    ORDER_OK -->|Có| FORM[Hiển thị form nhập 4 số cuối<br/>và email đã che]

    FORM --> SUBMIT([POST /nhan-do/link/<raw_token>])
    SUBMIT --> RATE{Vượt rate limit?}
    RATE -->|Có| LIMITED[Trả lỗi tạm khóa]
    RATE -->|Không| PREVIEW[resolve_pickup_access()<br/>đọc lại token và đơn]

    PREVIEW --> OPEN[open_locker(locker_id)]
    OPEN --> HW_OK{Phần cứng xác nhận mở tủ?}
    HW_OK -->|Không| HW_ERR[Trả lỗi phần cứng]
    HW_OK -->|Có| CHECK4[Kiểm tra 4 số cuối]
    CHECK4 -->|Sai| FORBIDDEN[Trả lỗi 403]
    CHECK4 -->|Đúng| USE[Đặt token = used<br/>và order = collected]
    USE --> EMPTY[mark_locker_empty(locker_id)]
    EMPTY --> RESULT[Hiển thị mở tủ thành công<br/>link không dùng lại được]

    RESULT --> END([Kết thúc])
    LINK_ERR --> END
    LIMITED --> END
    FORBIDDEN --> END
    HW_ERR --> END
```

## Giải thích chi tiết

### 1. Link an toàn hơn mã mở tủ vì có hai lớp xác thực

Để mở tủ bằng link, người nhận phải có:

- `raw_token` đúng trong URL
- `4 số cuối số điện thoại` đúng

Như vậy việc lộ link một mình chưa đủ để mở tủ ngay.

### 2. Token và đơn được cập nhật cùng lúc trong `mark_pickup_access_used()`

Sau khi phần cứng mở thành công, hàm `mark_pickup_access_used()`:

- đổi token từ `active` sang `used`
- lưu `used_at`
- đổi `LockerOrder.status` từ `stored` sang `collected`

Đây là bước chống reuse của link. Sau khi commit xong, link cũ không còn dùng lại được.

### 3. Tại sao vẫn mở phần cứng trước khi đổi trạng thái DB

Giống luồng nhận bằng mã, code gọi `open_locker()` trước rồi mới đổi trạng thái dữ liệu. Cách này tránh trường hợp token đã bị tiêu hao nhưng phần cứng chưa mở cửa.

### 4. Firefox hiển thị trực tiếp giao diện nhận hàng

Máy kiosk mở `main.py` trực tiếp bằng Firefox ở chế độ `--kiosk`. Vì vậy không cần một tiến trình Python hoặc một lưu đồ riêng cho lớp hiển thị kiosk.

## Điểm cần nhấn mạnh khi báo cáo

- Cơ chế “link + 4 số cuối” là xác thực hai lớp đơn giản nhưng hiệu quả cho môi trường kiosk.
- `token_hash` mới là thứ lưu trong DB; `raw_token` chỉ tồn tại ở phía người dùng.
- `mark_locker_empty()` là bước đồng bộ trạng thái vật lý sau khi nhận xong.

## Đối chiếu mã nguồn

- `resolve_pickup_access`, `mark_pickup_access_used` trong [`main.py`](../../main.py)
- `pickup_link_form`, `pickup_link_open` trong [`main.py`](../../main.py)
- `open_locker`, `mark_locker_empty` trong [`locker_hardware.py`](../../locker_hardware.py)

## Tài liệu tham khảo

- [SQLAlchemy Session Transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
- [MQTT Version 3.1.1 - OASIS Open](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
