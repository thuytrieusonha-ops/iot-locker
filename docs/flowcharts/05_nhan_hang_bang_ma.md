# Hình 3.5 - Giải thuật nhận hàng bằng mã mở tủ

```mermaid
flowchart TD
    START([Người nhận yêu cầu mở tủ bằng mã]) --> MODE{Đi từ route nào?}
    MODE -->|POST /nhan-do| FULL[Nhập số điện thoại đầy đủ<br/>và mã mở tủ]
    MODE -->|POST /nhan-do/ma-bao-mat/<pickup_code>| LAST4[Nhập mã mở tủ<br/>và 4 số cuối]

    FULL --> NORMALIZE[Chuẩn hóa dữ liệu đầu vào]
    LAST4 --> NORMALIZE
    NORMALIZE --> RATE{Vượt rate limit?}
    RATE -->|Có| LIMITED[Trả lỗi tạm khóa]
    RATE -->|Không| PREVIEW[Tra đơn stored phù hợp]

    PREVIEW --> FOUND{Tìm thấy đơn hợp lệ?}
    FOUND -->|Không| INVALID[Trả lỗi sai mã hoặc sai số điện thoại]
    FOUND -->|Có| OPEN[open_locker(locker_id)<br/>gửi lệnh MQTT hoặc mô phỏng]

    OPEN --> HW_OK{Phần cứng xác nhận mở tủ?}
    HW_OK -->|Không| HW_ERR[Trả lỗi phần cứng]
    HW_OK -->|Có| COLLECT[Cập nhật đơn thành collected]
    COLLECT --> EMPTY[mark_locker_empty(locker_id)<br/>gửi trạng thái trống]
    EMPTY --> RESULT[Hiển thị mở tủ thành công]

    RESULT --> END([Kết thúc])
    LIMITED --> END
    INVALID --> END
    HW_ERR --> END
```

## Giải thích chi tiết

### 1. Có hai cách nhập nhưng cùng hội tụ về một thuật toán

Hệ thống hỗ trợ hai biến thể:

- người nhận nhập đầy đủ `phone + pickup_code`
- người nhận đi từ trang mã bảo mật hoặc QR, chỉ cần `pickup_code + 4 số cuối`

Sau bước chuẩn hóa và kiểm tra tốc độ, cả hai đều phải tra ra đúng đơn `status='stored'`.

### 2. Mở phần cứng trước, rồi mới đổi trạng thái đơn

Điểm rất quan trọng là code gọi `open_locker()` trước, sau đó mới gọi:

- `collect_record(...)`, hoặc
- `collect_record_by_last4(...)`

Nhờ vậy nếu MQTT broker, gateway hoặc controller không ACK được việc mở tủ thì đơn chưa bị đánh dấu `collected`. Trạng thái dữ liệu vẫn còn để xử lý lại sau.

### 3. Sau khi nhận hàng xong, app publish trạng thái tủ rỗng

Sau khi đơn chuyển sang `collected`, code gọi `mark_locker_empty(locker_id)`. Nếu phần cứng đang bật, đây là message MQTT `set_occupied=false` để gateway báo lại cho bộ điều khiển rằng tủ đã trống.

### 4. Đồng thời hiện tại được chặn ở mức tiến trình

Luồng này không dùng khóa hàng DB. Trong code hiện tại, bảo vệ đồng thời chủ yếu đến từ:

- `state_lock` trong `main.py`
- việc chỉ có một tiến trình app kiosk phục vụ luồng này

Nếu sau này scale nhiều instance app cùng ghi vào một DB chung, bạn nên bổ sung khóa DB hoặc cơ chế điều phối mạnh hơn.

## Điểm cần nhấn mạnh khi báo cáo

- Thứ tự `open hardware -> collect order -> mark empty` là cố ý để tránh dữ liệu báo đã nhận nhưng cửa thực tế chưa mở.
- `mark_locker_empty()` chỉ là thông báo trạng thái phần cứng; trạng thái nghiệp vụ chính vẫn được quyết định trong MySQL.

## Đối chiếu mã nguồn

- `receiver_pickup`, `pickup_code_open` trong [`main.py`](../../main.py)
- `collect_record`, `collect_record_by_last4` trong [`main.py`](../../main.py)
- `open_locker`, `mark_locker_empty` trong [`locker_hardware.py`](../../locker_hardware.py)

## Tài liệu tham khảo

- [MQTT Version 3.1.1 - OASIS Open](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
- [Eclipse Paho Python Client](https://eclipse.dev/paho/clients/python/)
