# Hình 3.3 - Giải thuật giao hàng vào tủ

```mermaid
flowchart TD
    START([POST /giao-do]) --> VALIDATE[Chuẩn hóa số điện thoại<br/>và mã đơn hàng]
    VALIDATE --> LOOKUP[Tra email đã đăng ký theo số điện thoại]
    LOOKUP --> CREATE[Tạo LockerOrder mới<br/>trạng thái stored]
    CREATE --> UNIQUE{Còn tủ trống<br/>và không vi phạm UNIQUE?}
    UNIQUE -->|Không| FULL[Trả lỗi 409 hoặc xung đột dữ liệu]

    UNIQUE -->|Có| OPEN[open_locker_for_dropoff(locker_id)]
    OPEN --> HW_OK{Nhận ACK mở tủ<br/>và nếu bật phần cứng: door_open rồi door_closed?}
    HW_OK -->|Không| RELEASE[release_record()<br/>giải phóng đơn vừa tạo]
    RELEASE --> HW_ERR[Trả lỗi 503 phần cứng]

    HW_OK -->|Có| PHOTO{Có ảnh chụp đơn?}
    PHOTO -->|Có| SAVE_PHOTO[Lưu ảnh đơn hàng vào order_photos]
    PHOTO -->|Không| EMAIL_CHECK{Người nhận có email đăng ký?}
    SAVE_PHOTO --> EMAIL_CHECK

    EMAIL_CHECK -->|Có| EMAIL[queue_pickup_email_delivery()<br/>cấp token và gửi mail nền]
    EMAIL_CHECK -->|Không| FALLBACK[Chỉ dùng mã mở tủ dự phòng tại kiosk]

    EMAIL --> RESULT[Hiển thị số tủ, mã mở tủ<br/>và trạng thái email]
    FALLBACK --> RESULT
    RESULT --> END([Kết thúc])
    FULL --> END
    HW_ERR --> END
```

## Giải thích chi tiết

### 1. Tạo đơn hàng trước, rồi mới mở tủ vật lý

Trong `shipper_dropoff`, code tạo `LockerOrder` trước bằng `create_record()`, sau đó mới gọi `open_locker_for_dropoff(record.locker_id)`. Thứ tự này có hai ý nghĩa:

- hệ thống biết chắc đã cấp được tủ và mã mở tủ trước khi chạm vào phần cứng
- nếu phần cứng lỗi, `release_record()` sẽ đóng vai trò bù trừ ở tầng ứng dụng

Đây không phải rollback transaction SQL theo nghĩa chặt, mà là một bước “compensation” sau khi đã có bản ghi.

### 2. Bảo vệ “mỗi tủ chỉ chứa một đơn đang lưu”

`create_record()` không dùng `FOR UPDATE`. Thay vào đó nó dựa vào:

- `state_lock` để tuần tự hóa trong một tiến trình app
- generated column `active_locker_slot`
- ràng buộc `UNIQUE(active_locker_slot)`

Nhờ vậy, khi `status='stored'`, một `locker_id` chỉ được phép xuất hiện một lần trong dữ liệu active.

### 3. Luồng phần cứng của giao hàng khác với nhận hàng

`open_locker_for_dropoff()` không chỉ mở tủ. Nếu phần cứng được bật, nó còn:

1. publish lệnh `open`
2. chờ ACK từ controller
3. chờ `door_open`
4. chờ `door_closed`
5. mới publish `set_occupied=true`

Lý do là đèn hoặc trạng thái “đang có hàng” chỉ nên bật sau khi shipper đã đóng cửa tủ.

### 4. Ảnh đơn hàng và email không chặn việc lưu đơn

Sau khi tủ đã mở thành công:

- ảnh chụp đơn hàng được lưu nếu có dữ liệu camera
- email chỉ được xếp hàng nền nếu người nhận đã đăng ký email

Nếu ảnh lỗi hoặc SMTP lỗi, đơn vẫn tồn tại và người nhận vẫn có mã mở tủ dự phòng.

## Điểm cần nhấn mạnh khi báo cáo

- `release_record()` là cơ chế bù trừ khi phần cứng lỗi sau khi DB đã ghi đơn.
- `queue_pickup_email_delivery()` chạy sau khi đơn đã tồn tại, nên mail lỗi không làm mất đơn.
- `save_order_photo()` là tính năng bổ sung, không phải điều kiện bắt buộc để hoàn tất giao hàng.

## Đối chiếu mã nguồn

- route `shipper_dropoff` trong [`main.py`](../../main.py)
- `create_record`, `release_record` trong [`main.py`](../../main.py)
- `open_locker_for_dropoff` trong [`locker_hardware.py`](../../locker_hardware.py)

## Tài liệu tham khảo

- [SQLAlchemy Session Transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
- [MQTT Version 3.1.1 - OASIS Open](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
