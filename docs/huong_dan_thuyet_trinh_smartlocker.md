# Huong Dan Thuyet Trinh Khoa Luan SmartLocker

## 1. Muc dich tai lieu

Tai lieu nay duoc viet dua tren ma nguon hien co trong repo `smartlocker`, theo cach trinh bay cua mot do an khoa luan nganh Cong nghe thong tin. Muc tieu la:

- giup ban dung slide theo dung logic cua he thong trong repo
- giup giang vien nghe hieu nhanh kien truc, nghiep vu va ky thuat cua de tai
- giup ban co san loi noi mau cho bai thuyet trinh 8 den 10 phut
- giai thich ro cach he thong xu ly mat dien, mat mang, loi phan cung va loi gui mail

Tai lieu nay bam sat cac file chinh trong repo:

- `main.py`: kiosk app, giao hang, nhan hang, email, worker nen, thong bao su co
- `monitor.py`: portal nguoi dung va dashboard quan tri
- `locker_hardware.py`: lop giao tiep MQTT tu web app den bo dieu khien
- `locker_gateway.py`: gateway Raspberry Pi doi MQTT sang UART
- `arduino/mega_smart_locker/mega_smart_locker.ino`: firmware Arduino Mega
- `database.py`: khoi tao DB, seed du lieu, tu dong bo sung schema
- `model.py`: mo hinh du lieu va rang buoc SQLAlchemy/MySQL
- `mysql_schema.sql`: schema MySQL dau vao
- `docker-compose.yml`: kien truc trien khai bang container
- `order_camera.py`: luu anh don hang tu camera kiosk

## 2. Tom tat de tai theo dung repo hien tai

### 2.1 Bai toan

De tai SmartLocker giai quyet bai toan giao va nhan hang tu dong tai tu thong minh. He thong cho phep:

- nguoi giao hang hoac nhan vien dua kien hang vao tu
- he thong tu dong cap ma mo tu
- neu nguoi nhan da dang ky email thi he thong gui them link nhan hang bao mat
- nguoi nhan den kiosk de mo tu bang ma hoac bang link
- quan tri vien theo doi lich su, tinh trang email, va gui lenh mo tu tu xa

### 2.2 San pham hien tai trong repo

Day khong phai mot web app don gian. Du an hien tai la mot he thong hybrid gom 4 lop:

1. Lop giao dien web: FastAPI + HTML kiosk + monitor web
2. Lop dong bo nghiep vu: MySQL
3. Lop dieu khien thiet bi: MQTT broker Mosquitto
4. Lop chap hanh phan cung: Raspberry Pi gateway + UART + Arduino Mega

### 2.3 Pham vi he thong hien tai

Theo cau hinh mac dinh, he thong quan ly:

- 8 tu
- 2 app FastAPI tach biet
- 1 broker MQTT
- 1 gateway Raspberry Pi
- 1 firmware Arduino Mega
- 1 monitor danh cho admin
- 1 portal cho nguoi dung dang ky email

## 3. Kien truc va cac module trong repo

### 3.1 Kien truc tong the

Luong giao tiep tong quat cua he thong:

`Nguoi dung / admin -> HTTPS / HTTP -> FastAPI -> MySQL`

va dong thoi:

`FastAPI -> MQTT -> Raspberry Pi gateway -> UART -> Arduino Mega`

Kien truc nay cho thay he thong tach ro:

- nghiep vu web
- luu tru du lieu
- dieu khien tu vat ly
- cong khai dich vu ra Internet

### 3.2 Vai tro tung module

#### `main.py`

Day la module trung tam cua he thong, phu trach:

- giao dien kiosk
- luong giao hang
- luong nhan hang bang ma
- luong nhan hang bang link bao mat
- gui email va retry email
- worker dong bo den trang thai tu
- worker xu ly lenh admin tu monitor
- xu ly su co va thong bao cho admin

Noi ngan gon, `main.py` la lop nghiep vu kiosk va lop phoi hop giua web, CSDL va phan cung.

#### `monitor.py`

Module nay tach rieng khoi kiosk de thuc hien 2 vai tro:

- portal cho nguoi dung dang ky email theo so dien thoai
- dashboard cho admin dang nhap, xem lich su, mo tu, xoa lich su, xem su co

Thiet ke tach `monitor.py` va `main.py` la mot diem manh, vi giao dien van hanh va giao dien kiosk khong phu thuoc chat vao nhau.

#### `locker_hardware.py`

Module nay khong noi chuyen truc tiep voi Arduino. No chi:

- publish lenh len MQTT
- cho ACK tra ve qua MQTT
- lang nghe event `door_open`, `door_closed`
- cap nhat `set_occupied=true/false`

Nghia la `locker_hardware.py` dong vai tro adapter giua nghiep vu web va lop dieu khien thiet bi.

#### `locker_gateway.py`

Day la module chay tren Raspberry Pi, co 2 nhiem vu:

- subscribe command MQTT
- doi command do sang lenh UART de gui cho Arduino Mega

Nguoc lai, gateway cung:

- nhan `OK`, `DOOR_OPEN`, `DOOR_CLOSED` tu Mega
- publish ACK va event tro lai MQTT cho `main.py`

Day la cau noi rat quan trong giup tang web khong phai giao tiep truc tiep voi UART.

#### `model.py`

Module nay dinh nghia cac bang du lieu va cac rang buoc nghiep vu trong CSDL:

- `users`
- `locker_sites`
- `lockers`
- `locker_orders`
- `locker_access_tokens`
- `admin_commands`
- `admin_command_lockers`
- `admin_login_events`

#### `database.py`

Module nay phu trach:

- doc `.env`
- tao `DATABASE_URL`
- tao engine va session
- `init_db()`
- `seed_default_lockers()`
- `ensure_schema_updates()`

Diem dac biet la repo nay co co che tu bo sung schema, khong chi tao bang mot lan.

#### `order_camera.py`

Module nay luu anh don hang chup tu kiosk camera:

- nhan du lieu JPEG base64
- kiem tra dung dinh dang
- gioi han kich thuoc 5 MB
- luu vao thu muc `order_photos/<ngay>/...jpg`

Anh nay co the duoc dinh kem khi gui email cho nguoi nhan.

## 4. Co so du lieu va thiet ke du lieu

### 4.1 Cac bang chinh

#### `users`

Luu:

- so dien thoai
- email
- thoi gian tao va cap nhat

Bang nay dung cho portal dang ky email va lien ket nguoi dung voi don hang.

#### `lockers`

Luu thong tin tu vat ly:

- thuoc site nao
- so tu
- ma tu
- ten hien thi
- trang thai `active` hoac `occupied`

#### `locker_orders`

Day la bang nghiep vu trung tam, luu:

- tu nao dang chua don
- so dien thoai nguoi nhan
- pickup code
- flow giao hang
- ma don hang
- email nguoi nhan tai thoi diem luu
- trang thai gui email
- thoi gian gui email
- trang thai don `stored` hoac `collected`

#### `locker_access_tokens`

Luu token nhan hang qua email:

- `token_hash`
- `order_id`
- `locker_id`
- `phone`
- `email`
- `status`: `active`, `used`, `revoked`
- `expires_at`
- `used_at`

#### `admin_commands`

Bang nay dong vai tro hang doi lenh admin:

- mo mot tu
- mo tat ca tu
- pickup handoff
- bao cao su co
- cac thao tac don dep lich su

#### `admin_command_lockers`

Bang lien ket giua lenh admin va danh sach tu muc tieu. Nhieu lenh co the ap dung cho nhieu tu.

#### `admin_login_events`

Luu audit log dang nhap admin:

- su kien dang nhap thanh cong
- dang nhap that bai
- dang xuat
- IP
- thiet bi
- session id

### 4.2 Cac diem thiet ke du lieu noi bat

#### Rang buoc nghiep vu day xuong DB

Repo nay co 2 generated column rat manh:

- `locker_orders.active_locker_slot`
- `locker_access_tokens.active_order_id`

Y nghia:

- neu don dang o trang thai `stored` thi `active_locker_slot = locker_id`
- neu token dang `active` thi `active_order_id = order_id`

Sau do DB dat:

- `UNIQUE(active_locker_slot)`
- `UNIQUE(active_order_id)`

Tac dung:

- mot tu chi chua toi da mot don dang luu
- mot don chi co toi da mot token dang con hieu luc

Day la diem rat nen nhan manh khi bao ve, vi quy tac nghiep vu duoc rang buoc o tang du lieu, khong chi o giao dien hoac Python.

#### Snapshot du lieu lich su

Bang `locker_orders` van luu `phone`, `recipient_email`, `order_code` ngay tren ban ghi don, thay vi chi phu thuoc khoa ngoai. Cach nay giup:

- bao toan lich su tai thoi diem giao hang
- tranh mat dau vet khi user doi email sau nay
- de truy vet lich su gui email

#### Rang buoc `CHECK`

Schema co `CHECK` cho:

- `locker_orders.status`
- `locker_orders.flow`
- `locker_orders.email_delivery_status`
- `locker_access_tokens.status`
- `locker_access_tokens.delivery_channel`
- `admin_commands.status`

No giup ngan trang thai sai ngay tu CSDL.

## 5. Kien truc giao tiep phan cung

### 5.1 Duong di lenh mo tu

Khi app muon mo tu, duong di la:

`main.py -> locker_hardware.py -> MQTT broker -> locker_gateway.py -> UART -> Arduino Mega`

### 5.2 Giao thuc MQTT

Topic mac dinh:

- command: `smartlocker/lockers/{locker_id}/command`
- ack: `smartlocker/lockers/{locker_id}/ack`
- event: `smartlocker/lockers/{locker_id}/event`

Payload mo tu co dang:

```json
{"command":"open","locker_id":1,"request_id":"..."}
```

### 5.3 Giao thuc UART

Lenh Raspberry Pi gui cho Mega:

- `OPEN,{locker_id}`
- `LOCKER_USED,{locker_id}`
- `LOCKER_EMPTY,{locker_id}`

Mega phan hoi:

- `OK,{locker_id}`
- `DOOR_OPEN,{locker_id}`
- `DOOR_CLOSED,{locker_id}`

### 5.4 Phan cung Arduino Mega

Firmware trong repo dang dung:

- 8 relay pins: `22..29`
- 8 occupied LED pins: `30..37`
- 8 door sensor pins: `38..45`

UART ket noi:

- Raspberry Pi GPIO14 TX -> Mega RX1 pin 19
- Raspberry Pi GPIO15 RX <- Mega TX1 pin 18

Luu y quan trong:

- can level shifting 5V sang 3.3V tren duong Mega TX -> Pi RX
- firmware dung `Serial1` lam cong giao tiep gateway

### 5.5 Co che ACK va event

Gateway khong chi gui lenh, ma con:

- doi `OK,<locker_id>` de xac nhan lenh mo tu da duoc Mega tiep nhan
- doi `DOOR_OPEN`, `DOOR_CLOSED` de xac nhan cua thuc su duoc mo va dong

Vi vay he thong khong phai mo phong trang thai bang phan mem thuần tuy.

## 6. Cac quyet dinh thiet ke noi bat trong repo

### 6.1 Kien truc hybrid nhieu tang

He thong su dung ket hop:

- HTTPS/HTTP cho giao dien web
- MySQL cho luu tru nghiep vu
- MQTT cho dieu khien thiet bi
- UART cho giao tiep phan cung muc thap

Day la kien truc phu hop voi bai toan tu thong minh, vi:

- web de mo rong
- MQTT phu hop publish/subscribe
- UART phu hop giao tiep voi vi dieu khien

### 6.2 Tach module theo trach nhiem

Repo tach ro:

- nghiep vu kiosk
- monitor admin
- giao tiep hardware
- gateway chuyen doi protocol
- firmware Mega
- database va schema

Kieu tach nay tuan theo tu tuong `separation of concerns`, giup:

- de test
- de thay the tung lop
- de trinh bay khi bao ve

### 6.3 Bao ve dong thoi khong dua vao `SELECT ... FOR UPDATE`

Repo hien tai khong dung `FOR UPDATE`. Thay vao do, no ket hop 3 lop bao ve:

- `state_lock` trong app de tuan tu hoa thao tac trong mot process
- `UNIQUE` va `CHECK` o CSDL
- `request_id` va deduplicate o gateway de tranh mo tu lap lai

Day la diem can noi dung voi giang vien de tranh mo ta sai voi ma nguon.

### 6.4 Compensation thay vi transaction phan cung

Trong luong giao hang, he thong:

1. tao `LockerOrder`
2. mo tu
3. neu mo tu that bai thi `release_record()`

Vi thao tac phan cung khong the rollback nhu SQL transaction, repo da dung cach "compensation". Day la cach xu ly rat thuc te trong he thong co thiet bi ngoai.

### 6.5 Xu ly lenh admin theo kieu command queue

Admin khong mo tu truc tiep vao phan cung. Monitor chi ghi lenh vao DB:

- `admin_commands`
- `admin_command_lockers`

Sau do `main.py` co worker doc lenh pending va thuc thi. Loi ich:

- tach giao dien quan tri va may kiosk
- kiosk van la noi co quyen mo tu vat ly
- de audit lich su lenh

### 6.6 Bao mat link nhan hang

He thong khong luu `raw_token` trong DB. No:

- tao `raw_token`
- bam SHA-256 thanh `token_hash`
- luu `token_hash`
- gui `raw_token` trong link cho nguoi dung

Khi nguoi dung mo link, he thong con bat nhap 4 so cuoi so dien thoai. Day la xac thuc 2 lop don gian nhung hieu qua.

### 6.7 Worker nen cho email

Email khong lam cham luong kiosk. App:

- tao token
- danh dau `pending`
- tao thread nen de gui mail

Neu Pi mat mang, he thong giu lai trang thai `pending` va worker se gui lai khi mang tro lai.

### 6.8 Tu phuc hoi trang thai sau khoi dong lai

Sau startup, `main.py` goi:

- `reconcile_locker_master_state()`
- `restore_locker_hardware_state()`

Co che nay:

- doc don dang `stored` trong DB
- xac dinh tu nao dang co hang
- bat/tat den occupied cho dung
- khong goi `open_locker()`

Day la diem rat quan trong cho tinh an toan sau mat dien.

### 6.9 MQTT retained cho trang thai occupied

Lenh `set_occupied` duoc publish voi `retain=True`. Loi ich:

- gateway khoi dong lai van co the nhan thong diep trang thai moi nhat
- den bao su dung co the duoc khoi phuc de hon sau restart

Tuy nhien gateway chu dong bo qua `retained open command` de tranh mo cua ngoai y muon sau mat dien.

## 7. Cach he thong xu ly mat dien, mat mang, va loi

### 7.1 Truong hop mat dien

#### Mat dien o Raspberry Pi hoac server

Khi co dien lai:

- MySQL dung volume `mysql-data` nen du lieu don hang van con
- Mosquitto dung volume `mqtt-data` nen persistence co the duoc giu lai
- `main.py` startup se doi chieu DB va khoi phuc den occupied
- he thong khong tu dong mo cua tu sau khi khoi dong lai

Day la co che quan trong de tranh truong hop vua co dien lai la tu bi mo nham.

#### Mat dien luc dang co lenh mo tu

Repo co mot lop bao ve dac biet:

- `locker_gateway.py` bo qua `retained open command`

Dieu nay tranh cho command `open` bi gui lai cho Mega sau khi broker hoac gateway khoi dong lai.

#### Mat dien luc dang luu hang

Neu mat dien truoc khi cua duoc dong va worker nghiep vu hoan tat, trang thai co the o dang chuyen tiep. Khi khoi dong lai:

- DB la nguon su that
- app doi chieu lai `locker_orders.status`
- den occupied duoc dat lai theo DB

Trong bai bao ve, ban nen noi ro:

"He thong uu tien khoi phuc trang thai an toan theo du lieu da commit trong CSDL, khong tu suy dien mo cua tu."

### 7.2 Truong hop mat mang Internet

Can tach 2 loai mang:

#### Mat Internet nhung may kiosk van chay noi bo

Khi Internet mat:

- kiosk local van mo `http://127.0.0.1:8000`
- luong giao va nhan bang ma tai kiosk van co the tiep tuc neu DB, MQTT va phan cung noi bo van chay
- email link moi khong gui duoc ngay
- monitor qua Cloudflare co the khong truy cap duoc tu ben ngoai

#### Mat mang luc gui email

Repo xu ly nhu sau:

- khong doi thanh `failed` ngay
- giu `email_delivery_status = pending`
- ghi chu rang Pi dang mat mang
- worker nen dinh ky kiem tra ket noi
- khi mang tro lai thi gui lai tu dong

Day la diem rat nen dua vao slide vi cho thay he thong co tinh thuc te van hanh.

### 7.3 Mat ket noi MQTT hoac gateway phan cung

Repo co 2 che do:

- `SMARTLOCKER_HARDWARE_REQUIRED=true`
- `SMARTLOCKER_HARDWARE_REQUIRED=false`

Neu `true`:

- khong co ACK hoac loi controller thi request bi bao loi
- day la che do dung cho san pham that

Neu `false`:

- app canh bao va mo phong thanh cong de tiep tuc demo nghiep vu
- day la che do phuc vu phat trien va bao cao

### 7.4 Mat ket noi CSDL

`main.py` co exception handler SQLAlchemy:

- tra trang loi `503`
- tu dong reload lai sau 5 giay

Nghia la app van len giao dien nhung thong bao ro he thong dang mat ket noi du lieu.

## 8. Huong dan xay dung va trien khai du an

### 8.1 Thanh phan can chuan bi

#### Phan cung

- 1 Raspberry Pi
- 1 Arduino Mega
- 8 relay dieu khien khoa
- 8 LED bao occupied
- 8 cam bien cua
- man hinh cam ung cho kiosk
- camera USB hoac camera tren thiet bi di dong de chup don

#### Phan mem

- Python 3.12
- `uv`
- Docker va Docker Compose
- MySQL 8.4
- Eclipse Mosquitto
- cloudflared neu muon public ra Internet

### 8.2 Cai dat phan mem local

```bash
uv sync
```

### 8.3 Cau hinh `.env`

Co the bat dau tu:

```bash
cp .env.docker.example .env.docker
```

Neu chay local thu cong, tao `.env` voi cac bien quan trong:

```bash
SMARTLOCKER_DATABASE_URL="mysql+pymysql://root:password@127.0.0.1:3307/smartlocker"
SMARTLOCKER_APP_HOST="0.0.0.0"
SMARTLOCKER_APP_PORT="8000"
SMARTLOCKER_BASE_URL="http://127.0.0.1:8000"
SMARTLOCKER_MONITOR_HOST="0.0.0.0"
SMARTLOCKER_MONITOR_PORT="8001"
SMARTLOCKER_MONITOR_URL="http://127.0.0.1:8001"
SMARTLOCKER_ADMIN_USERNAME="admin"
SMARTLOCKER_ADMIN_PASSWORD="your-password"
```

Neu bat email:

```bash
SMARTLOCKER_SMTP_HOST="smtp.gmail.com"
SMARTLOCKER_SMTP_PORT="587"
SMARTLOCKER_SMTP_USERNAME="your-email@gmail.com"
SMARTLOCKER_SMTP_PASSWORD="your-app-password"
SMARTLOCKER_SMTP_FROM_EMAIL="your-email@gmail.com"
SMARTLOCKER_SMTP_USE_TLS="true"
SMARTLOCKER_SMTP_PENDING_RETRY_SECONDS="60"
SMARTLOCKER_SMTP_PENDING_BATCH_SIZE="10"
```

Neu bat hardware MQTT:

```bash
SMARTLOCKER_HARDWARE_ENABLED="true"
SMARTLOCKER_HARDWARE_REQUIRED="true"
SMARTLOCKER_MQTT_HOST="127.0.0.1"
SMARTLOCKER_MQTT_PORT="1883"
SMARTLOCKER_MQTT_TOPIC_PREFIX="smartlocker"
SMARTLOCKER_MQTT_CLIENT_ID="smartlocker-app"
SMARTLOCKER_MQTT_COMMAND_TIMEOUT="5.0"
SMARTLOCKER_DOOR_CLOSE_TIMEOUT="120.0"
SMARTLOCKER_GATEWAY_MQTT_CLIENT_ID="smartlocker-pi-gateway"
SMARTLOCKER_UART_PORT="/dev/serial0"
SMARTLOCKER_UART_BAUDRATE="115200"
SMARTLOCKER_UART_COMMAND_TIMEOUT="3.0"
```

### 8.4 Tao schema CSDL

Neu khong dung Docker:

```bash
mysql -u root -p < mysql_schema.sql
```

### 8.5 Chay bang Docker

Khoi dong toan bo stack:

```bash
docker compose up --build
```

Neu co hardware gateway:

```bash
docker compose --profile hardware up --build
```

Service mac dinh:

- app: `http://localhost:8000`
- monitor: `http://localhost:8001`
- mysql: `localhost:3307`
- mqtt: `localhost:1883`

### 8.6 Chay thu cong khong Docker

App kiosk:

```bash
uv run python main.py
```

Monitor:

```bash
uv run python monitor.py
```

Gateway tren Pi:

```bash
uv run python locker_gateway.py
```

### 8.7 Kiosk mode tren Raspberry Pi

Repo co script:

- `scripts/pi-up.sh`: khoi dong Docker Compose
- `scripts/pi-kiosk.sh`: doi app san sang roi mo browser kiosk

Chay:

```bash
./scripts/pi-kiosk.sh .env.docker
```

Script se:

- check URL kiosk
- tu khoi dong app neu can
- mo Chromium kiosk neu co
- neu khong co Chromium thi fallback sang Firefox kiosk

### 8.8 Nap firmware cho Arduino Mega

Dung file:

- `arduino/mega_smart_locker/mega_smart_locker.ino`

Can kiem tra lai:

- `RELAY_PINS`
- `OCCUPIED_LED_PINS`
- `DOOR_SENSOR_PINS`
- `OPEN_PULSE_MS`
- `DOOR_CLOSED_LOW`

de phu hop voi dau noi thuc te.

### 8.9 Public he thong bang Cloudflare Tunnel

Repo da co:

- `cloudflared/config.yml`
- `cloudflared/config.docker.yml`

Can dat:

```bash
SMARTLOCKER_MONITOR_URL="https://monitor-your-domain"
SMARTLOCKER_BASE_URL="https://app-your-domain"
```

Luu y:

- Cloudflare chi public web
- khong thay the DB
- khong thay the MQTT noi bo

## 9. Kich ban 10 slide cho bai thuyet trinh 8 den 10 phut

Tong thoi gian de xuat: 9 phut.

- Slide 1: 45 giay
- Slide 2: 45 giay
- Slide 3: 60 giay
- Slide 4: 60 giay
- Slide 5: 55 giay
- Slide 6: 55 giay
- Slide 7: 60 giay
- Slide 8: 60 giay
- Slide 9: 70 giay
- Slide 10: 50 giay

### Slide 1. Gioi thieu de tai

#### Noi dung nen dat tren slide

- Ten de tai: He thong tu thong minh SmartLocker
- Muc tieu: tu dong hoa giao nhan hang
- Doi tuong: shipper, nguoi nhan, admin
- Nen co 1 hinh tong quan tu thong minh

#### Loi noi mau

"Kinh thua quy thay co, de tai em trinh bay la he thong tu thong minh SmartLocker. Muc tieu cua de tai la so hoa quy trinh giao va nhan hang tai tu, giup giam su phu thuoc vao nhan su truc tiep, dong thoi tang kha nang theo doi va dieu khien tu xa. He thong huong toi 3 nhom nguoi dung chinh, gom nguoi giao hang, nguoi nhan hang va quan tri vien."

### Slide 2. Bai toan va muc tieu

#### Noi dung nen dat tren slide

- Bai toan hien tai
- Muc tieu chuc nang
- Muc tieu ky thuat

#### Loi noi mau

"Bai toan dat ra la lam sao de mot kien hang duoc luu vao tu, tao ma nhan, gui thong bao cho nguoi nhan, va cho phep nguoi nhan mo dung tu mot cach an toan. Ngoai ra, he thong phai co kha nang luu lich su, giam sat tu xa, va ho tro xu ly su co trong truong hop mat mang hoac khoi dong lai thiet bi."

### Slide 3. Kien truc tong the cua he thong

#### Noi dung nen dat tren slide

- FastAPI app
- Monitor app
- MySQL
- MQTT broker
- Raspberry Pi gateway
- Arduino Mega
- Cloudflare Tunnel

#### Loi noi mau

"Diem noi bat cua de tai la kien truc hybrid nhieu tang. Tang web duoc xay dung bang FastAPI, trong do `main.py` phuc vu kiosk va `monitor.py` phuc vu portal va dashboard. Tang du lieu dung MySQL. Tang dieu khien thiet bi dung MQTT broker Mosquitto. Tu web app, lenh mo tu khong di thang den Arduino ma di qua Raspberry Pi gateway, sau do gateway moi doi sang UART de giao tiep voi Arduino Mega. Cach tach nay giup he thong de mo rong va de bao tri hon."

### Slide 4. Cac module trong repo va chuc nang

#### Noi dung nen dat tren slide

- `main.py`
- `monitor.py`
- `locker_hardware.py`
- `locker_gateway.py`
- `database.py`
- `model.py`
- `order_camera.py`

#### Loi noi mau

"Trong repo hien tai, moi file co vai tro ro rang. `main.py` la trung tam nghiep vu kiosk. `monitor.py` la giao dien cho nguoi dung va admin. `locker_hardware.py` la adapter MQTT. `locker_gateway.py` la cau noi giua MQTT va UART. `database.py` xu ly khoi tao va cap nhat schema. `model.py` mo ta thiet ke du lieu. `order_camera.py` dung de luu anh don hang, phuc vu truy vet va dinh kem email."

### Slide 5. Thiet ke co so du lieu

#### Noi dung nen dat tren slide

- Bang `users`
- Bang `lockers`
- Bang `locker_orders`
- Bang `locker_access_tokens`
- Bang `admin_commands`
- 2 generated column quan trong

#### Loi noi mau

"Ve du lieu, bang quan trong nhat la `locker_orders`, luu toan bo don dang o trong tu hoac da duoc nhan. Bang `locker_access_tokens` luu token nhan hang qua email. Diem em muon nhan manh la repo nay su dung generated column va unique constraint de rang buoc nghiep vu ngay tai DB. Cu the, mot tu chi duoc chua mot don dang luu, va mot don chi duoc co toi da mot token active. Day la diem manh ve thiet ke du lieu va tinh an toan nghiep vu."

### Slide 6. Luong giao hang vao tu

#### Noi dung nen dat tren slide

- Nhap so dien thoai va ma don
- Tao record va pickup code
- Mo tu qua MQTT/UART
- Chup anh don
- Gui email neu da dang ky

#### Loi noi mau

"Khi shipper giao hang, he thong chuan hoa du lieu dau vao, tim email nguoi nhan neu da dang ky, tao ban ghi `LockerOrder`, sau do moi mo tu vat ly. Neu tu mo thanh cong va cua duoc dong lai dung quy trinh, he thong moi danh dau tu da co hang. Neu co anh don hang, he thong luu vao thu muc `order_photos`. Neu nguoi nhan da dang ky email, he thong cap token va gui link nhan hang nen."

### Slide 7. Luong nhan hang va bao mat

#### Noi dung nen dat tren slide

- Nhan bang pickup code
- Nhan bang link email
- Xac thuc 4 so cuoi SDT
- Token hash
- Trang thai `used`

#### Loi noi mau

"Nguoi nhan co 2 cach lay hang. Cach thu nhat la nhap so dien thoai va ma mo tu. Cach thu hai la mo link nhan hang duoc gui qua email. Voi link email, he thong khong luu raw token ma chi luu hash SHA-256 trong DB. Khi mo link, nguoi dung phai nhap them 4 so cuoi so dien thoai. Sau khi mo tu thanh cong, token duoc danh dau `used` va don duoc chuyen sang `collected`, tranh viec tai su dung link."

### Slide 8. Giao tiep phan cung va gateway

#### Noi dung nen dat tren slide

- MQTT topic
- Raspberry Pi gateway
- UART command
- ACK `OK`
- Event `DOOR_OPEN`, `DOOR_CLOSED`
- `request_id`

#### Loi noi mau

"Phan dieu khien phan cung la mot diem noi bat cua de tai. Web app publish lenh `open` len MQTT, gateway tren Raspberry Pi nhan lenh nay va doi sang UART de gui cho Arduino Mega. Mega phan hoi `OK` khi nhan lenh mo, va gui them `DOOR_OPEN`, `DOOR_CLOSED` de phan mem biet trang thai cua thuc te. Repo con co co che `request_id` va deduplicate o gateway, giup tranh gui lap lenh mo tu khi MQTT redelivery."

### Slide 9. Xu ly mat dien, mat mang va tinh on dinh

#### Noi dung nen dat tren slide

- Khoi phuc den tu tu DB
- Khong tu dong mo cua sau restart
- Email pending khi mat mang
- Retry tu dong khi co mang lai
- `HARDWARE_REQUIRED`

#### Loi noi mau

"Ve tinh on dinh, repo xu ly kha thuc te. Neu mat dien, du lieu van duoc giu trong volume MySQL, va khi khoi dong lai he thong se doc DB de khoi phuc trang thai den occupied, nhung khong tu dong mo cua tu. Neu Raspberry Pi mat mang luc gui email, don se duoc giu o trang thai `pending`, worker nen se tu gui lai khi mang phuc hoi. Ngoai ra, he thong co bien `SMARTLOCKER_HARDWARE_REQUIRED` de phan biet che do demo va che do san pham that."

### Slide 10. Ket luan va huong phat trien

#### Noi dung nen dat tren slide

- Dat duoc muc tieu giao nhan tu dong
- Tach module ro rang
- Co co che bao mat va phuc hoi
- Huong phat trien

#### Loi noi mau

"Tong ket lai, de tai da xay dung duoc mot he thong SmartLocker co tinh toan ven tu giao dien, co so du lieu, giao tiep phan cung va dashboard quan tri. He thong khong chi giai quyet duoc bai toan giao nhan hang tu dong ma con co co che bao mat link nhan hang, co che retry email, va co kha nang phuc hoi trang thai sau mat dien. Trong tuong lai, he thong co the phat trien them app mobile, thong bao da kenh, camera AI nhan dien kien hang, va dashboard phan tich du lieu van hanh."

## 10. Goi y trinh bay de giang vien de theo doi

Ban nen trinh bay theo dung thu tu sau:

1. Bai toan
2. Muc tieu
3. Kien truc tong the
4. Cac module chinh
5. Co so du lieu
6. Luong giao hang
7. Luong nhan hang
8. Giao tiep phan cung
9. Xu ly mat dien, mat mang
10. Ket luan

Khong nen di sau vao CSS, HTML giao dien, hay cac chi tiet nho cua JavaScript neu thoi gian chi co 8 den 10 phut. Giang vien thuong quan tam:

- em giai bai toan gi
- em tach he thong nhu the nao
- du lieu luu ra sao
- mo tu vat ly nhu the nao
- mat dien mat mang thi sao
- he thong co bao mat khong

## 11. Cac cau hoi phan bien de bi va cach tra loi

### Cau hoi 1. Vi sao em dung MQTT ma khong goi UART truc tiep tu web app?

Tra loi goi y:

"Em dung MQTT de tach lop nghiep vu web khoi lop phan cung. Khi do `main.py` khong phai phu thuoc truc tiep vao UART, va Raspberry Pi gateway co the dong vai tro cau noi trung gian. Cach nay de mo rong, de doi controller, va de xu ly ACK/event tot hon."

### Cau hoi 2. Vi sao em khong luu raw token trong DB?

Tra loi goi y:

"Vi neu DB bi lo, nguoi xau se co ngay link nhan hang. Do do he thong chi luu hash SHA-256, con raw token chi ton tai o phia email nguoi dung."

### Cau hoi 3. Neu mat dien thi co bi mo nham tu khi khoi dong lai khong?

Tra loi goi y:

"Khong. Vi he thong chi khoi phuc den occupied theo DB, khong goi `open_locker()` khi startup. Ngoai ra gateway bo qua retained open command de tranh mo nham sau restart."

### Cau hoi 4. Neu mat mang khi gui mail thi sao?

Tra loi goi y:

"He thong giu trang thai `pending`, ghi chu Pi dang mat mang, va worker nen se thu gui lai khi ket noi phuc hoi. Neu la loi cau hinh SMTP that su, he thong moi danh dau `failed` hoac `smtp_missing`."

### Cau hoi 5. Neu 2 nguoi cung luc thao tac mot tu thi sao?

Tra loi goi y:

"Repo dung `state_lock` trong app, ket hop `UNIQUE(active_locker_slot)` o DB, va co che `request_id` + deduplicate o gateway. Nghia la phong tranh dong thoi duoc xu ly ca o tang app, tang DB va tang phan cung."

## 12. Phan ket ngan gon ban co the doc o cuoi bai

"Thong qua de tai nay, em da xay dung duoc mot he thong tu thong minh co kien truc hybrid, tich hop tu giao dien, co so du lieu, gui email, dashboard quan tri va dieu khien phan cung thuc. Gia tri chinh cua de tai khong chi nam o chuc nang giao nhan, ma con nam o cach he thong duoc thiet ke de an toan, de mo rong va co kha nang van hanh trong cac tinh huong thuc te nhu mat dien, mat mang va loi thiet bi."
