# Hình 3.7 - Giải thuật mở tủ vật lý qua MQTT và UART

```mermaid
flowchart TD
    START([main.py gọi open_locker() hoặc open_locker_for_dropoff()]) --> REQUEST[Generate request_id]
    REQUEST --> PUBLISH[Publish MQTT<br/>smartlocker/lockers/<locker_id>/command]
    PUBLISH --> GATEWAY[locker_gateway.py subscribe command]

    GATEWAY --> DEDUP{request_id đã hoàn tất hoặc đang inflight?}
    DEDUP -->|Đã hoàn tất| REPLAY[Publish lại ACK cache]
    DEDUP -->|Đang inflight| WAIT_OTHER[Chờ thread chủ hoàn tất]
    DEDUP -->|Mới| UART[Gateway gửi UART OPEN,<locker_id>]

    UART --> UART_OK{Mega trả OK,<locker_id> đúng hạn?}
    UART_OK -->|Không| ACK_ERR[Publish ACK status = error]
    UART_OK -->|Có| ACK_OK[Publish ACK status = opened]

    ACK_OK --> APP_ACK[App nhận MQTT ACK theo request_id]
    WAIT_OTHER --> APP_ACK
    REPLAY --> APP_ACK
    ACK_ERR --> APP_ACK

    APP_ACK --> DROP{Có phải luồng giao hàng?}
    DROP -->|Không| END([Kết thúc mở tủ])
    DROP -->|Có| EVENT_WAIT[Chờ door_open rồi door_closed]
    EVENT_WAIT --> CLOSE_OK{Đủ chuỗi event trong timeout?}
    CLOSE_OK -->|Không| DROP_ERR[Báo lỗi chưa đóng cửa tủ]
    CLOSE_OK -->|Có| OCCUPIED[Publish set_occupied = true]
    OCCUPIED --> UART_USED[Gateway gửi UART LOCKER_USED,<locker_id>]
    UART_USED --> END
    DROP_ERR --> END
```

## Giải thích chi tiết

### 1. App và gateway giao tiếp kiểu publish/subscribe

`locker_hardware.py` dùng `paho-mqtt` để publish lệnh:

- topic: `smartlocker/lockers/{locker_id}/command`
- payload mở tủ có `request_id`

Gateway trên Pi subscribe topic này, xử lý rồi publish ACK về:

- topic: `smartlocker/lockers/{locker_id}/ack`

Nhờ `request_id`, app có thể ghép đúng ACK về lại request đã phát.

### 2. Gateway có cơ chế chống mở trùng do MQTT redelivery

`PiMegaGateway` duy trì:

- `_inflight_open_requests`
- `_completed_open_requests`

Nếu cùng một `request_id` đến lại vì MQTT QoS 1 redelivery:

- nếu request đang chạy thì thread sau chờ kết quả
- nếu request đã xong thì gateway phát lại ACK cache
- chỉ thread chủ mới thực sự gửi `OPEN,{locker_id}` qua UART

Đây là điểm rất mạnh để trình bày vì nó cho thấy dự án đã xử lý idempotency ở tầng điều khiển phần cứng.

### 3. Gateway còn chặn việc gửi nhiều lệnh `OPEN` xuống Mega cùng lúc

`_open_command_lock` và `_pending_event` buộc gateway chỉ gửi một lệnh `OPEN,{locker_id}` xuống UART tại một thời điểm. Lý do là Mega chỉ phản hồi `OK,{locker_id}` dạng chuỗi đơn giản; nếu mở nhiều lệnh song song thì rất khó map ngược chính xác ACK nào thuộc request nào.

### 4. Luồng giao hàng còn cần door event

Với `open_locker_for_dropoff()`, app chưa đánh dấu tủ là “đang có hàng” ngay sau ACK mở tủ. Nó tiếp tục chờ:

1. `door_open`
2. `door_closed`

Chỉ sau đó mới publish `set_occupied=true`. Điều này mô tả đúng nghiệp vụ: shipper phải mở cửa, bỏ hàng, rồi đóng lại thì trạng thái tủ mới chuyển sang đã sử dụng.

### 5. Luồng nhận hàng thì ngược lại

Sau khi người nhận lấy đồ thành công, `main.py` gọi `mark_locker_empty(locker_id)`, app sẽ publish `set_occupied=false`, và gateway đổi thành `LOCKER_EMPTY,{locker_id}` qua UART.

## Điểm cần nhấn mạnh khi báo cáo

- `MQTT` dùng cho giao tiếp mềm giữa app và gateway.
- `UART` dùng cho giao tiếp cứng giữa Raspberry Pi và Arduino Mega.
- `request_id` + cache ACK là cơ chế chống gửi lệnh mở tủ lặp.
- `door_open` và `door_closed` là event thật từ phía controller, không chỉ là biến trạng thái trong web app.

## Đối chiếu mã nguồn

- [`locker_hardware.py`](../../locker_hardware.py)
- [`locker_gateway.py`](../../locker_gateway.py)
- [`docker-compose.yml`](../../docker-compose.yml)

## Tài liệu tham khảo

- [MQTT Version 3.1.1 - OASIS Open](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
- [Eclipse Paho Python Client](https://eclipse.dev/paho/clients/python/)
- [Eclipse Mosquitto Documentation](https://mosquitto.org/documentation/)
