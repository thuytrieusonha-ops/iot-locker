# Hình 3.2 - Khởi động ứng dụng, cơ sở dữ liệu và phần cứng

```mermaid
flowchart TD
    START([docker compose up]) --> MYSQL[Khởi động MySQL<br/>mount volume mysql-data]
    START --> MOSQUITTO[Khởi động Mosquitto MQTT Broker]

    MYSQL --> HEALTH{MySQL healthcheck đạt?}
    HEALTH -->|Không| WAIT[Chờ rồi thử lại]
    WAIT --> LIMIT{Quá số lần retry?}
    LIMIT -->|Không| HEALTH
    LIMIT -->|Có| UNHEALTHY[MySQL unhealthy<br/>app và monitor không khởi động]

    HEALTH -->|Có| DB_READY[MySQL service_healthy]
    MOSQUITTO --> MQTT_READY[MQTT service_started]

    DB_READY --> APP_DEPS((AND))
    MQTT_READY --> APP_DEPS
    APP_DEPS --> APP_ENV[Service app đọc biến môi trường<br/>tạo DATABASE_URL và engine]
    DB_READY --> MON_ENV[Service monitor đọc biến môi trường<br/>tạo DATABASE_URL và engine]

    APP_ENV --> APP_INIT[main.py gọi init_db()]
    MON_ENV --> MON_INIT[monitor.py gọi init_db() trong lifespan]

    APP_INIT --> APP_SCHEMA[Tạo bảng ORM và ensure_schema_updates()]
    MON_INIT --> MON_SCHEMA[Tạo bảng ORM và ensure_schema_updates()]

    APP_SCHEMA --> APP_DB_OK{init_db() lỗi SQLAlchemy?}
    MON_SCHEMA --> MON_DB_OK{init_db() lỗi SQLAlchemy?}

    APP_DB_OK -->|Có lỗi| APP_WARN[main.py ghi cảnh báo startup]
    APP_WARN --> APP_RUN[App vẫn khởi động]
    APP_DB_OK -->|Không| APP_RUN

    MON_DB_OK -->|Có lỗi| MON_FAIL[monitor startup thất bại]
    MON_DB_OK -->|Không| MON_RUN[Monitor sẵn sàng]

    APP_RUN --> HW{SMARTLOCKER_HARDWARE_ENABLED?}
    HW -->|Không| READY([App sẵn sàng nhận request])
    HW -->|Có| MQTT_START[start_hardware()<br/>kết nối MQTT broker]
    MQTT_START --> MQTT_OK{HARDWARE_REQUIRED và MQTT lỗi?}
    MQTT_OK -->|Có| HW_FAIL[App lỗi startup phần cứng]
    MQTT_OK -->|Không| READY

    READY --> FIREFOX[Firefox mở giao diện bằng<br/>--kiosk http://127.0.0.1:8000]

    PROFILE([docker compose up --profile hardware]) --> GW_DEPS((AND))
    MQTT_READY --> GW_DEPS
    GW_DEPS --> GATEWAY[Khởi động locker_gateway.py<br/>mở UART và kết nối MQTT]

    APP_RUN -.-> RETRY503[Request DB lỗi về sau trả 503<br/>trang web tự reload sau 5 giây]
```

## Giải thích chi tiết

### 1. Docker kiểm tra dependency trước khi start service

Trong `docker-compose.yml`, `app` chờ MySQL `service_healthy` và MQTT `service_started`; `monitor` chỉ chờ MySQL `service_healthy`. Gateway được khởi động khi dùng profile `hardware` và chờ MQTT chạy. Firefox chỉ mở giao diện kiosk sau khi app đã sẵn sàng và không chạy server Python riêng.

Nếu MySQL hết số lần healthcheck mà vẫn lỗi, Compose không khởi động app và monitor. Điểm này giúp giảm lỗi khởi động sớm khi DB chưa mở cổng.

### 2. `database.py` chỉ tạo engine nếu có cấu hình DB

`database.py` đọc `.env`, dựng `DATABASE_URL`, rồi mới tạo:

- `engine = create_engine(..., pool_pre_ping=True)`
- `SessionLocal = sessionmaker(...)`

Nếu không có cấu hình DB thì:

- `engine = None`
- `SessionLocal = None`
- `init_db()` thoát sớm

Nhánh này chủ yếu dùng cho phát triển hoặc demo đơn giản.

### 3. `main.py` và `monitor.py` xử lý lỗi startup khác nhau

`main.py` gọi `init_db()` trong `startup()` và có `try/except SQLAlchemyError`, nên nếu DB lỗi:

- app vẫn lên
- các request đụng DB về sau sẽ trả trang lỗi `503`
- trang lỗi có JavaScript reload sau 5 giây

Ngược lại, `monitor.py` gọi `init_db()` trong `lifespan` mà không bọc `try/except`, nên nếu DB lỗi ở giai đoạn này thì monitor có thể fail startup.

Đây là khác biệt rất đáng nhấn mạnh khi bảo vệ khóa luận.

### 4. Schema update chạy tự động khi startup

`ensure_schema_updates()` dùng `inspect()` để so sánh bảng hiện tại với schema mong muốn, rồi bổ sung:

- các cột email mới cho `locker_orders`
- generated column `active_locker_slot`
- generated column `active_order_id`
- `CHECK`, `UNIQUE`, `FOREIGN KEY` còn thiếu

Nhờ đó dự án có thể tự vá một số khác biệt schema khi update phiên bản.

### 5. Phần cứng MQTT khởi động sau DB ở `main.py`

Sau khi xử lý `init_db()`, `main.py` mới gọi `start_hardware()` nếu `SMARTLOCKER_HARDWARE_ENABLED=true`. Điều này có nghĩa:

- DB là điều kiện nền cho nghiệp vụ web
- phần cứng là tầng bổ sung cho thao tác vật lý

Nếu `HARDWARE_REQUIRED=false`, lỗi MQTT có thể chỉ tạo warning và app tiếp tục chạy ở chế độ mô phỏng.

## Điểm cần nói đúng khi báo cáo

- Nhánh “không có DB thì app chạy bộ nhớ” chỉ áp dụng mạnh cho `main.py`; `monitor.py` không có chế độ portal đầy đủ khi thiếu DB.
- `pool_pre_ping=True` không tự biến startup thành healthcheck phần cứng; nó chỉ giúp kiểm tra kết nối khi SQLAlchemy lấy connection từ pool.
- Hệ thống hiện tại có tự cập nhật schema, nhưng không phải framework migration đầy đủ như Alembic.
- Firefox kiosk chỉ là lớp hiển thị; toàn bộ route và nghiệp vụ vẫn do `main.py` phục vụ.

## Đối chiếu mã nguồn

- [`database.py`](../../database.py)
- [`main.py`](../../main.py)
- [`monitor.py`](../../monitor.py)
- [`docker-compose.yml`](../../docker-compose.yml)

## Tài liệu tham khảo

- [SQLAlchemy Session Transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
- [SQLAlchemy Constraints](https://docs.sqlalchemy.org/en/20/core/constraints.html)
- [FastAPI SQL Databases](https://fastapi.tiangolo.com/tutorial/sql-databases/)
