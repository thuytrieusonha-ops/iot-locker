# TiDB + TiFlash MPP guide for SmartLocker

Tài liệu này hướng dẫn theo đúng repo hiện tại, với mục tiêu:

- giữ nguyên phần lớn code nghiệp vụ FastAPI hiện có
- đổi backend database từ MySQL sang TiDB bằng MySQL protocol
- thêm bảng event / ảnh / inference để tạo workload analytics
- bật TiFlash để benchmark HTAP / MPP
- lấy số liệu ngay cả khi chưa có hệ thống phần cứng thật

## 1. Vì sao gần như không cần viết lại toàn bộ app

TiDB tương thích MySQL protocol, nên app đang dùng `SQLAlchemy + PyMySQL` có thể trỏ sang TiDB chỉ bằng cách đổi DSN trong nhiều trường hợp.

Nguồn chính thức:

- TiDB Architecture: https://docs.pingcap.com/tidb/stable/tidb-architecture/
- TiFlash Overview: https://docs.pingcap.com/tidb/stable/tiflash-overview/
- Use TiFlash MPP Mode: https://docs.pingcap.com/tidb/stable/use-tiflash-mpp-mode/
- Create TiFlash Replicas: https://docs.pingcap.com/tidb/stable/create-tiflash-replicas/

## 2. Chọn cách có TiDB nhanh nhất

Bạn có 2 đường:

### Cách A - nhanh nhất để thử

Dùng TiDB Cloud Starter.

Ưu điểm:

- không phải tự dựng cluster
- có thể test nhanh MPP/TiFlash trên môi trường thật

### Cách B - phù hợp cho nghiên cứu local hoặc lab

Dùng TiDB Self-Managed theo tài liệu Deploy bằng TiUP.

Nguồn chính thức:

- https://docs.pingcap.com/tidb/stable/deploy-tidb-using-tiup/

Nếu mục tiêu là “có kết quả nhanh để show”, nên bắt đầu bằng TiDB Cloud Starter hoặc một cụm TiDB lab đã dựng sẵn.

## 3. Trỏ app từ MySQL sang TiDB

Repo này đã dùng:

- dialect `mysql+pymysql`
- `SQLAlchemy`
- `SMARTLOCKER_DATABASE_URL`

Ví dụ DSN TiDB local:

```env
SMARTLOCKER_DATABASE_URL="mysql+pymysql://root:@127.0.0.1:4000/smartlocker"
```

Ví dụ DSN TiDB Cloud: dùng đúng endpoint/username/password mà PingCAP cấp cho cluster của bạn.

Bạn có thể đặt trong:

- `.env`
- hoặc biến môi trường shell

Repo hiện đã có sẵn file `.env` benchmark mặc định cho MySQL localhost:

```env
SMARTLOCKER_DATABASE_DIALECT='mysql+pymysql'
SMARTLOCKER_DATABASE_HOST='127.0.0.1'
SMARTLOCKER_DATABASE_PORT='3307'
SMARTLOCKER_DATABASE_NAME='smartlocker_benchmark'
SMARTLOCKER_DATABASE_USER='smartlocker_user'
SMARTLOCKER_DATABASE_PASSWORD='Locker123!'
```

## 4. Cài dependency và tạo schema

Từ thư mục project:

```bash
uv sync
```

Sau đó chạy app hoặc script khởi tạo để SQLAlchemy tự tạo bảng:

```bash
uv run python scripts/prepare_benchmark_db.py
```

Lệnh trên sẽ tự tạo database benchmark theo cấu hình trong `.env` trước khi tạo schema.

Sau đó chạy:

```bash
uv run python -c "from database import init_db; init_db()"
```

Repo đã được mở rộng thêm 3 bảng phục vụ benchmark MPP:

- `locker_events`
- `parcel_image_assets`
- `parcel_inference_results`

Các bảng này được định nghĩa trong:

- `model.py`
- `mysql_schema.sql`

## 5. Tạo dữ liệu synthetic đa site

Vì chưa có hệ thống thực, hãy tạo dữ liệu giả lập.

Ví dụ workload hiện tại:

```bash
uv run python scripts/seed_synthetic_data.py --sites 4 --lockers-per-site 10 --days 30 --orders-per-site-per-day 20 --wipe
```

Ví dụ workload mở rộng:

```bash
uv run python scripts/seed_synthetic_data.py --sites 20 --lockers-per-site 10 --days 180 --orders-per-site-per-day 80 --wipe
```

Script sẽ sinh:

- `locker_orders`
- `locker_events`
- `parcel_image_assets`
- `parcel_inference_results`

Mục tiêu là tạo đủ dữ liệu để:

- benchmark query analytics
- benchmark join lớn
- benchmark MPP mode

## 6. Bật TiFlash replica cho các bảng analytics

Sau khi seed dữ liệu xong, mở MySQL client trỏ vào TiDB và chạy:

```sql
SOURCE scripts/tiflash_setup.sql;
```

Hoặc copy từng lệnh trong file:

- `ALTER TABLE locker_events SET TIFLASH REPLICA 1;`
- `ALTER TABLE parcel_image_assets SET TIFLASH REPLICA 1;`
- `ALTER TABLE parcel_inference_results SET TIFLASH REPLICA 1;`
- `ALTER TABLE locker_orders SET TIFLASH REPLICA 1;`

Kiểm tra tiến độ replication:

```sql
SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    REPLICA_COUNT,
    AVAILABLE,
    PROGRESS
FROM information_schema.tiflash_replica
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME;
```

Khi `AVAILABLE = 1` và `PROGRESS` gần `1`, bạn có thể benchmark MPP.

## 7. Chạy benchmark baseline

Đầu tiên đo không bật MPP:

```bash
uv run python scripts/benchmark_tidb_mpp.py --days 30 --repeat 3 --output results/baseline.json
```

Script sẽ chạy 3 nhóm query:

- `daily_site_order_summary`
- `locker_utilization_summary`
- `ml_model_quality_summary`

## 8. Chạy benchmark với TiDB MPP

Sau khi TiFlash replica đã sẵn sàng:

```bash
uv run python scripts/benchmark_tidb_mpp.py --days 30 --repeat 3 --use-tidb-mpp --output results/tidb_mpp.json
```

Nếu muốn ép TiDB dùng MPP trong phiên benchmark:

```bash
uv run python scripts/benchmark_tidb_mpp.py --days 30 --repeat 3 --use-tidb-mpp --enforce-mpp --output results/tidb_mpp_forced.json
```

Script sẽ tự thử:

```sql
SET @@session.tidb_allow_mpp = 1;
SET @@session.tidb_enforce_mpp = 0; -- hoặc 1 nếu force
```

## 9. Cách đọc kết quả

Trong file JSON kết quả, chú ý các trường:

- `elapsed_ms`: thời gian trung bình chạy query
- `row_count`: số dòng trả về
- `mpp_used`: script phát hiện plan có `ExchangeSender`, `ExchangeReceiver`, hoặc `mpp[tiflash]`
- `explain_text`: plan đầy đủ để chụp làm minh chứng

Bạn nên chụp hoặc trích:

- kết quả baseline
- kết quả có TiFlash
- đoạn `EXPLAIN` có `ExchangeSender` / `ExchangeReceiver`

Theo docs chính thức, sự xuất hiện của `ExchangeSender` và `ExchangeReceiver` là dấu hiệu plan MPP của TiFlash đã có hiệu lực:

- https://docs.pingcap.com/tidb/stable/use-tiflash-mpp-mode/

## 10. Cách show “nó quan trọng trong tương lai”

Đừng chỉ chạy một workload nhỏ.

Hãy lặp lại benchmark ở 3 mức:

### Mức 1 - hiện tại

- 4 site
- 10 tủ/site
- 20 đơn/ngày/site
- 30 ngày dữ liệu

### Mức 2 - mở rộng vừa

- 20 site
- 10 tủ/site
- 80 đơn/ngày/site
- 180 ngày dữ liệu

### Mức 3 - tương lai

- 100 site
- 20 tủ/site
- 200 đơn/ngày/site
- 365 ngày dữ liệu
- nhiều inference rows hơn

Bạn cần chỉ ra:

- baseline MySQL hoặc TiDB không TiFlash bắt đầu chậm ở query analytics
- TiFlash giữ thời gian query ổn định hơn
- phần giao dịch vẫn giữ được nhờ TiKV row-store

Đây chính là giá trị HTAP/MPP mà bạn cần chứng minh.

## 11. “Viết lại code có MPP” trong repo này đã được làm ở đâu

Phần mở rộng dành cho benchmark MPP nằm ở:

- `analytics_queries.py`
- `scripts/seed_synthetic_data.py`
- `scripts/benchmark_tidb_mpp.py`
- `scripts/tiflash_setup.sql`

Schema mới nằm ở:

- `model.py`
- `mysql_schema.sql`

Ý nghĩa:

- app nghiệp vụ chính vẫn chạy như cũ vì TiDB tương thích MySQL
- phần “có MPP” được thể hiện ở lớp analytics data + benchmark + TiFlash replica + EXPLAIN plan

## 12. Bước tiếp theo sau khi benchmark DB

Sau khi có số liệu DB, mới làm tiếp benchmark mức ứng dụng:

- dùng Locust hoặc k6 để bắn tải HTTP
- vừa ghi đơn vừa chạy benchmark analytics
- đo ảnh hưởng lẫn nhau giữa OLTP và OLAP

Đó sẽ là vòng nghiên cứu tiếp theo. Nhưng để có kết quả nhanh, chỉ cần hoàn thành các bước 1 đến 10 trước.
