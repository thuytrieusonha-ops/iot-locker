# Lưu đồ giải thuật Smart Locker

Thư mục này chỉ giữ các lưu đồ cốt lõi của kiến trúc hybrid `HTTP/HTTPS + MQTT + UART + MySQL`. Firefox chạy ở chế độ kiosk để hiển thị giao diện web; hệ thống không xem một trình khởi chạy kiosk Python là thành phần kiến trúc riêng.

## Danh mục lưu đồ

| Mã hình gợi ý | Nội dung | File | Mã nguồn chính |
|---|---|---|---|
| Hình 3.1 | Kiến trúc tổng quan hybrid | [01_tong_quan_he_thong.md](01_tong_quan_he_thong.md) | `docker-compose.yml`, `main.py`, `monitor.py`, `locker_hardware.py`, `locker_gateway.py` |
| Hình 3.2 | Khởi động ứng dụng, CSDL và phần cứng | [02_khoi_dong_csdl.md](02_khoi_dong_csdl.md) | `database.py`, `main.py`, `monitor.py`, `docker-compose.yml` |
| Hình 3.3 | Nhân viên giao hàng lưu kiện vào tủ | [03_giao_hang.md](03_giao_hang.md) | `shipper_dropoff`, `create_record`, `open_locker_for_dropoff` |
| Hình 3.4 | Cấp token và gửi email nền | [04_gui_email.md](04_gui_email.md) | `issue_pickup_access`, `queue_pickup_email_delivery`, `retry_email_delivery_for_phone` |
| Hình 3.5 | Nhận hàng bằng mã mở tủ | [05_nhan_hang_bang_ma.md](05_nhan_hang_bang_ma.md) | `receiver_pickup`, `pickup_code_open`, `collect_record` |
| Hình 3.6 | Nhận hàng bằng link bảo mật | [06_nhan_hang_bang_link.md](06_nhan_hang_bang_link.md) | `resolve_pickup_access`, `mark_pickup_access_used`, `pickup_link_open` |
| Hình 3.7 | Điều khiển mở tủ vật lý qua MQTT và UART | [07_mqtt_gateway_mo_tu.md](07_mqtt_gateway_mo_tu.md) | `locker_hardware.py`, `locker_gateway.py` |

Các luồng quản trị, đăng ký/gửi lại email, handoff và báo cáo sự cố được mô tả bằng văn bản trong báo cáo thay vì tách thành lưu đồ riêng. Chúng là luồng hỗ trợ, không làm thay đổi thuật toán nghiệp vụ chính.

## Quy ước ký hiệu

- Hình chữ nhật bo tròn: điểm bắt đầu hoặc kết thúc.
- Hình chữ nhật: bước xử lý chính.
- Hình thoi: điều kiện rẽ nhánh.
- Hình trụ: dữ liệu lưu trong MySQL hoặc message broker.
- Nét đứt: tín hiệu hoặc thành phần phụ trợ, không phải luồng điều khiển chính.

## Các điểm cần nói đúng trong báo cáo

### 1. Hệ thống hiện tại là hybrid thật, không còn chỉ là web app thuần HTTP

Luồng web công khai vẫn đi qua `HTTPS -> Cloudflare Edge -> cloudflared -> FastAPI`, nhưng điều khiển phần cứng đã tách sang một mặt phẳng riêng:

- `main.py` publish lệnh điều khiển tủ qua MQTT.
- Mosquitto làm broker trung gian.
- `locker_gateway.py` trên Raspberry Pi nhận message MQTT rồi đổi sang lệnh UART cho Arduino Mega.
- Arduino trả `OK`, `DOOR_OPEN`, `DOOR_CLOSED` về gateway; gateway publish ngược lại MQTT ACK hoặc event.

Vì vậy khi thuyết minh, bạn nên tách hệ thống thành 4 tầng:

- Tầng truy cập web: `HTTP/HTTPS`
- Tầng điều khiển thiết bị: `MQTT`
- Tầng chấp hành phần cứng: `UART`
- Tầng lưu trữ và đồng bộ nghiệp vụ: `MySQL`

### 2. Bảo vệ đồng thời hiện tại không dựa vào `SELECT ... FOR UPDATE`

Code hiện tại không dùng `with_for_update()` hay `SELECT ... FOR UPDATE`. Thay vào đó, dự án kết hợp ba lớp bảo vệ:

- `state_lock` để tuần tự hóa thao tác trong một tiến trình ứng dụng.
- Ràng buộc `UNIQUE` và `CHECK` ở MySQL để chặn trạng thái không hợp lệ.
- `request_id` trên MQTT và cơ chế deduplicate ở `locker_gateway.py` để tránh gửi lệnh mở tủ vật lý lặp lại.

Điểm này rất quan trọng, vì nếu bạn nói hệ thống đang khóa hàng DB bằng `FOR UPDATE` thì sẽ sai với code hiện tại.

### 3. Hai generated column là điểm đáng chú ý để đưa vào khóa luận

Trong [model.py](../../model.py) và [database.py](../../database.py), dự án dùng:

- `active_locker_slot`: chỉ nhận giá trị `locker_id` khi đơn đang ở trạng thái `stored`, sau đó đặt `UNIQUE`.
- `active_order_id`: chỉ nhận giá trị `order_id` khi token đang `active`, sau đó đặt `UNIQUE`.

Cách làm này giúp biểu diễn hai quy tắc nghiệp vụ rất rõ:

- Một tủ chỉ được chứa tối đa một đơn đang lưu.
- Một đơn chỉ được có tối đa một token nhận đồ còn hiệu lực.

Đây là điểm mạnh về thiết kế dữ liệu vì quy tắc nghiệp vụ được đẩy xuống DB, không chỉ kiểm tra ở giao diện hay Python.

### 4. Phần cứng có thể chạy mô phỏng hoặc bắt buộc ACK thật

Hai biến môi trường cần nêu rõ trong báo cáo:

- `SMARTLOCKER_HARDWARE_ENABLED=true`: bật luồng MQTT/UART thật.
- `SMARTLOCKER_HARDWARE_REQUIRED=true`: nếu không nhận được ACK hay event thì request bị lỗi, không giả lập thành công.

Nếu `SMARTLOCKER_HARDWARE_REQUIRED=false`, code sẽ ghi cảnh báo và cho phép mô phỏng mở tủ để tiếp tục demo luồng nghiệp vụ. Điều này hữu ích khi phát triển, nhưng khi đánh giá sản phẩm thật thì phải nói rõ đây là chế độ fallback.

### 5. Dữ liệu vật lý nằm ở Docker volume

Trong triển khai Docker:

- MySQL ghi dữ liệu vào volume `mysql-data`.
- Mosquitto ghi persistence vào volume `mqtt-data`.

Nghĩa là dữ liệu vật lý vẫn nằm trên đĩa của máy chủ hoặc Raspberry Pi đang chạy Docker, không tự động ở trên cloud nếu bạn chưa gắn volume đó vào dịch vụ cloud storage riêng.

## Gợi ý trình bày trong khóa luận

Bạn có thể trình bày theo thứ tự sau để người đọc đi từ tổng quát đến chi tiết:

1. Hình 3.1 để mô tả toàn bộ kiến trúc và vị trí của `HTTP`, `MQTT`, `UART`, `MySQL`.
2. Hình 3.2 để giải thích khởi động hệ thống, CSDL và phần cứng.
3. Hình 3.3 đến 3.6 cho các ca sử dụng nghiệp vụ chính: giao hàng, cấp quyền nhận và nhận hàng.
4. Hình 3.7 để trình bày đường điều khiển phần cứng MQTT–UART và cơ chế ACK/event.

## Tài liệu tham khảo

### Chuẩn lưu đồ và mô tả web

- [Mermaid Flowchart](https://mermaid.js.org/syntax/flowchart.html): cú pháp để dựng toàn bộ sơ đồ trong thư mục này.
- [OMG UML 2.5.1 Specification](https://www.omg.org/spec/UML/2.5.1/PDF): tham khảo nếu cần chuyển Mermaid sang Activity Diagram chuẩn UML.
- [FastAPI SQL Databases](https://fastapi.tiangolo.com/tutorial/sql-databases/): tham khảo tổ chức route, session và mô hình ứng dụng web dùng SQLAlchemy.

### CSDL và ràng buộc dữ liệu

- [SQLAlchemy Session Transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html): nền tảng cho `commit`, `rollback` và phạm vi transaction.
- [SQLAlchemy Constraints](https://docs.sqlalchemy.org/en/20/core/constraints.html): tham khảo `CHECK`, `UNIQUE` và `FOREIGN KEY` dùng trong `model.py`.

### MQTT, broker và tunnel

- [MQTT Version 3.1.1 - OASIS Open](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html): chuẩn giao thức publish/subscribe dùng làm cơ sở cho luồng điều khiển tủ.
- [Eclipse Paho Python Client](https://eclipse.dev/paho/clients/python/): tài liệu chính thức của thư viện MQTT client đang dùng trong `locker_hardware.py`.
- [Eclipse Mosquitto Documentation](https://mosquitto.org/documentation/): tài liệu broker MQTT tương ứng với service `mqtt` trong Docker Compose.
- [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/): tài liệu chính thức cho luồng publish ứng dụng web ra Internet qua `cloudflared`.

### Bài báo hệ thống locker tương tự

- [Smart Modular Parcel Locker System using Internet of Things (IoT)](https://doi.org/10.1109/ICSET53708.2021.9612542)
- [Multi-functional Parcel Delivery Locker system](https://doi.org/10.1109/ICCACS.2015.7361351)
- [An Unmanned Smart Parcel Locker System with a Parcel Sterilizer](https://doi.org/10.1166/jctn.2021.9596)

Các bài báo trên phù hợp để đối chiếu ý tưởng hệ thống và cách tách module. Tuy nhiên phần sơ đồ trong thư mục này được suy ra trực tiếp từ mã nguồn của chính dự án nên có giá trị chứng minh triển khai cao hơn.
