# Hình 3.4 - Giải thuật cấp token và gửi email nền

```mermaid
flowchart TD
    START([Đơn stored có recipient_email]) --> TOKEN[Sinh raw token ngẫu nhiên]
    TOKEN --> HASH[Băm SHA-256 thành token_hash]
    HASH --> EXPIRE[Đặt expires_at theo TTL]
    EXPIRE --> REVOKE[Thu hồi token active cũ của cùng order]
    REVOKE --> INSERT[Thêm LockerAccessToken mới<br/>status = active]
    INSERT --> UNIQUE{Vi phạm quy tắc một token active cho một order?}
    UNIQUE -->|Có| FAIL_TOKEN[Không cấp được link]
    UNIQUE -->|Không| PENDING[Cập nhật LockerOrder:<br/>email_delivery_status = pending]
    PENDING --> WORKER[Thread nền bắt đầu gửi email]

    WORKER --> TRY_SEND[send_pickup_email()<br/>link + mã dự phòng + QR/ảnh nếu có]
    TRY_SEND --> SENT{Gửi thành công?}
    SENT -->|Có| STATUS_SENT[Cập nhật status = sent<br/>email_sent_at = now]
    SENT -->|Không, chưa có SMTP| STATUS_MISSING[Cập nhật status = smtp_missing]
    SENT -->|Lỗi mạng / SMTP| RETRY{Còn lượt retry và lỗi còn retry được?}
    RETRY -->|Có| SLEEP[Ngủ theo SMTP_RETRY_DELAY_SECONDS]
    SLEEP --> TRY_SEND
    RETRY -->|Không| STATUS_FAILED[Cập nhật status = failed]

    STATUS_SENT --> END([Kết thúc])
    STATUS_MISSING --> END
    STATUS_FAILED --> END
    FAIL_TOKEN --> END
```

## Giải thích chi tiết

### 1. Link gửi đi dùng raw token, còn DB chỉ giữ hash

`issue_pickup_access()` tạo:

- `raw_token` để ghép vào URL người dùng nhận được
- `token_hash = SHA-256(raw_token)` để lưu xuống DB

Thiết kế này giúp nếu DB bị lộ thì attacker vẫn không có ngay URL nhận đồ gốc.

### 2. Mỗi đơn chỉ giữ một token đang active

Trước khi thêm token mới, code gọi `revoke_active_tokens(order_id)`. Ở tầng schema, `LockerAccessToken` còn có:

- generated column `active_order_id`
- `UNIQUE(active_order_id)`

Nói cách khác, logic Python và constraint MySQL cùng bảo vệ quy tắc “một đơn chỉ có một link nhận đồ còn hiệu lực”.

### 3. Gửi mail là tác vụ nền, không chặn luồng chính

`queue_pickup_email_delivery()` chỉ:

1. cấp token
2. cập nhật `email_delivery_status='pending'`
3. tạo `Thread(..., daemon=True)` để gửi mail

Nhờ vậy giao diện không phải đợi SMTP hoàn tất mới trả kết quả cho người giao hàng hay người dùng.

### 4. Retry có chọn lọc

Hàm `is_retryable_email_error()` chỉ cho retry với các lỗi như:

- `TimeoutError`
- `OSError`
- `smtplib.SMTPException` có mã lỗi 4xx hoặc không xác định

Nếu hết số lần thử hoặc gặp lỗi không nên retry, trạng thái cuối cùng sẽ là `failed`.

### 5. Ý nghĩa các trạng thái mail

- `pending`: đã xếp hàng gửi
- `sent`: gửi thành công
- `smtp_missing`: chưa cấu hình SMTP hoặc không thể dùng SMTP
- `failed`: đã thử nhưng thất bại
- `unregistered`: người nhận chưa đăng ký email

Đây là các trạng thái được ràng buộc thật bằng `CHECK` trong schema.

## Điểm cần nhấn mạnh khi báo cáo

- Email là kênh thuận tiện hơn, nhưng mã mở tủ tại kiosk vẫn là phương án dự phòng.
- Hệ thống không lưu raw token trong DB.
- Retry mail là retry ở tầng ứng dụng, không phải hàng đợi phân tán như RabbitMQ hay Celery.

## Đối chiếu mã nguồn

- `issue_pickup_access`, `queue_pickup_email_delivery`, `send_pickup_email`, `retry_email_delivery_for_phone` trong [`main.py`](../../main.py)
- `LockerAccessToken` trong [`model.py`](../../model.py)

## Tài liệu tham khảo

- [SQLAlchemy Constraints](https://docs.sqlalchemy.org/en/20/core/constraints.html)
- [FastAPI SQL Databases](https://fastapi.tiangolo.com/tutorial/sql-databases/)
