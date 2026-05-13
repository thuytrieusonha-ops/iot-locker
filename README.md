## MySQL

App co the chay voi MySQL neu ban dat bien moi truong `SMARTLOCKER_DATABASE_URL`.

Ngoai `SMARTLOCKER_DATABASE_URL`, app gio cung ho tro cau hinh tung thanh phan DB rieng de de doi host khi `monitor` chay khac may voi MySQL:

- `SMARTLOCKER_DATABASE_HOST`
- `SMARTLOCKER_DATABASE_PORT`
- `SMARTLOCKER_DATABASE_NAME`
- `SMARTLOCKER_DATABASE_USER`
- `SMARTLOCKER_DATABASE_PASSWORD`

App cung tu dong doc file `.env` trong thu muc du an neu ban khong `export` bien moi truong bang shell.

Vi du:

```bash
export SMARTLOCKER_DATABASE_URL="mysql+pymysql://root:password@127.0.0.1:3306/smartlocker"
export SMARTLOCKER_MONITOR_HOST="0.0.0.0"
export SMARTLOCKER_MONITOR_PORT="8001"
export SMARTLOCKER_MONITOR_URL="http://127.0.0.1:8001"
uv run python monitor.py
```

Hoac tao file `.env`:

```bash
SMARTLOCKER_DATABASE_URL="mysql+pymysql://root:password@127.0.0.1:3306/smartlocker"
SMARTLOCKER_MONITOR_ADMIN_TOKEN="your-admin-token"
SMARTLOCKER_APP_HOST="0.0.0.0"
SMARTLOCKER_APP_PORT="8000"
SMARTLOCKER_BASE_URL="http://127.0.0.1:8000"
SMARTLOCKER_MONITOR_HOST="0.0.0.0"
SMARTLOCKER_MONITOR_PORT="8001"
SMARTLOCKER_MONITOR_URL=""
SMARTLOCKER_ADMIN_USERNAME="admin"
SMARTLOCKER_ADMIN_PASSWORD="dat-mot-mat-khau-manh-rieng"
SMARTLOCKER_SMTP_HOST="smtp.gmail.com"
SMARTLOCKER_SMTP_PORT="587"
SMARTLOCKER_SMTP_USERNAME="your-email@gmail.com"
SMARTLOCKER_SMTP_PASSWORD="your-app-password"
SMARTLOCKER_SMTP_FROM_EMAIL="your-email@gmail.com"
SMARTLOCKER_SMTP_USE_TLS="true"
SMARTLOCKER_SMTP_RETRY_ATTEMPTS="3"
SMARTLOCKER_SMTP_RETRY_DELAY_SECONDS="20"
SMARTLOCKER_ADMIN_LOGIN_MAX_ATTEMPTS="5"
SMARTLOCKER_ADMIN_LOGIN_WINDOW_SECONDS="600"
```

Neu bien nay khong duoc dat, app se tiep tuc dung bo nho tam thoi nhu hien tai.

Neu MySQL nam tren may khac trong cung mang LAN, khong duoc de `127.0.0.1`. Hay doi sang IP cua may chay MySQL, vi du:

```bash
SMARTLOCKER_DATABASE_HOST="192.168.1.10"
```

De test gui mail that trong cung mang LAN truoc khi dung URL ngoai:

```bash
SMARTLOCKER_APP_HOST="0.0.0.0"
SMARTLOCKER_APP_PORT="8000"
SMARTLOCKER_BASE_URL="http://192.168.1.23:8000"
SMARTLOCKER_MONITOR_HOST="0.0.0.0"
SMARTLOCKER_MONITOR_PORT="8001"
SMARTLOCKER_MONITOR_URL="http://192.168.1.23:8001"
SMARTLOCKER_SMTP_HOST="smtp.gmail.com"
SMARTLOCKER_SMTP_PORT="587"
SMARTLOCKER_SMTP_USERNAME="your-email@gmail.com"
SMARTLOCKER_SMTP_PASSWORD="your-16-char-app-password"
SMARTLOCKER_SMTP_FROM_EMAIL="your-email@gmail.com"
SMARTLOCKER_SMTP_USE_TLS="true"
```

Trong vi du tren, `192.168.1.23` la IP LAN cua may dang chay `kiosk.py` / `main.py`.
Neu ban mo kiosk bang `kiosk.py`, app gio se bind theo `SMARTLOCKER_APP_HOST`, khong con khoa cung `127.0.0.1` nhu truoc, nen may cung mang co the mo duoc link trong email.

Khuyen nghi bao mat:

- Khong dung mat khau mac dinh hoac dung chung `SMARTLOCKER_MONITOR_ADMIN_TOKEN` cho tai khoan admin.
- Neu publish monitor ra ngoai, dat `SMARTLOCKER_MONITOR_URL` bang `https://...` de session cookie duoc ep chay o che do secure.
- Ma mo tu du phong khong nen gui qua email; nguoi nhan nen uu tien link nhan do.

Neu muon chay giao dien kiosk thay vi monitor:

```bash
export SMARTLOCKER_DATABASE_URL="mysql+pymysql://root:password@127.0.0.1:3306/smartlocker"
uv run python kiosk.py
```

Ban cung co the tao schema bang file:

```bash
mysql -u root -p < mysql_schema.sql
```

## Web nhan do an toan hon

He thong hien tai gom 2 web FastAPI dung chung MySQL:

- `main.py`: kiosk van hanh gui do, giao do, nhan do.
- `monitor.py`: web hop nhat cho `nguoi dung` va `quan tri`.

Sau khi nguoi dung vao `monitor.py` va luu `so dien thoai + email`, kiosk co the cap `link nhan do` an toan:

- `POST /giao-do`: he thong tao `ma mo tu` 6 so va neu co email thi gui link nhan do qua mail.
- `GET /nhan-do/link/<token>`: nguoi nhan mo link, nhap them 4 so cuoi so dien thoai de xac nhan.
- `GET /nhan-do/ma-bao-mat/<pickup_code>`: luong nhan hang bang ma mo tu 6 so.
- `POST /nhan-do`: nhap `so dien thoai + ma mo tu 6 so` de nhan hang.
- `monitor.py`: hien them `email`, `trang thai gui mail`, `ghi chu mail`, `thoi diem gui` va dashboard dang nhap admin.

Luu y:

- Token nhan do duoc luu duoi dang `hash`, co han dung va chi dung 1 lan.
- He thong co `rate limit` co ban de giam brute-force va spam.
- Neu chua cau hinh SMTP, kiosk van tao link noi bo nhung se khong gui email tu dong.
- Khi app khoi dong voi MySQL da ton tai, `init_db()` se tu bo sung cac cot `recipient_email`, `email_delivery_status`, `email_delivery_note`, `email_sent_at` trong `locker_orders` neu con thieu.

## Monitor tach ro portal va quan tri

`monitor.py` gio duoc tach route ro rang:

- `GET /portal`: cổng người dùng để nhập `so dien thoai + email`, luu vao co so du lieu va tra cuu don
- `GET /admin`: trang dang nhap quan tri
- `GET /admin/dashboard`: dashboard quan tri sau dang nhap

Route cu `GET /nguoi-dung` van con nhung se tu chuyen sang `/portal` de giu tuong thich.

Kiosk va QR nguoi dung chi tro toi `portal`, khong tro thang vao giao dien quan tri nua.

Dashboard admin van co:

- lenh `mo tat ca tu`
- lenh `mo mot tu cu the`
- xoa du lieu voi chu xac nhan `XOA_DU_LIEU`

## Cloudflare Tunnel

De `monitor` truy cap duoc khi may client khong cung mang voi server, chay app bind `0.0.0.0` va tao public hostname qua Cloudflare Tunnel.

Vi du file `cloudflared/config.yml`:

```yaml
tunnel: smartlocker
credentials-file: /root/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: monitor-smartlocker.example.com
    service: http://localhost:8001
  - service: http_status:404
```

Bien `.env` tuong ung:

```bash
SMARTLOCKER_MONITOR_URL="https://monitor-smartlocker.example.com"
SMARTLOCKER_BASE_URL="https://app-smartlocker.example.com"
SMARTLOCKER_ADMIN_USERNAME="admin"
SMARTLOCKER_ADMIN_PASSWORD="admin123"
```

Lenh chay:

```bash
uv run python monitor.py
cloudflared tunnel run smartlocker
```

URL se dung sau khi publish:

- portal nguoi dung: `https://monitor-smartlocker.example.com/portal`
- quan tri: `https://monitor-smartlocker.example.com/admin`
- kiosk va pickup links: `https://app-smartlocker.example.com`

Ghi chu quan trong:

- Cloudflare Tunnel chi publish web `monitor` va `app`; no khong tu dong mo ket noi toi MySQL.
- Neu `monitor` hoac `app` chay tren may khac voi MySQL, moi service van phai ket noi duoc toi `SMARTLOCKER_DATABASE_HOST` qua LAN, VPN, WireGuard hoac kenh private khac.
- Khi `SMARTLOCKER_MONITOR_URL` bo trong, monitor se tu hien URL LAN de dung trong cung mang.

Vi du `monitor` chay tren may chu A con MySQL chay tren may chu B:

```bash
SMARTLOCKER_DATABASE_HOST="192.168.1.10"
SMARTLOCKER_DATABASE_PORT="3306"
SMARTLOCKER_DATABASE_NAME="smartlocker"
SMARTLOCKER_DATABASE_USER="smartlocker_user"
SMARTLOCKER_DATABASE_PASSWORD="mat-khau-db"
```

Cloudflare Tunnel khong thay the duong ket noi private nay.

### Quick Tunnel de test nhanh tren dien thoai

Neu ban chua co domain Cloudflare, co the dung Quick Tunnel tam thoi. Cach nay phu hop de test link email mo tu dien thoai.

Luu y:

- Dien thoai khong mo duoc link `http://127.0.0.1:8000` vi `127.0.0.1` tren dien thoai la chinh may dien thoai.
- Ban phai dat `SMARTLOCKER_BASE_URL` thanh URL public cua Quick Tunnel de link trong email dung duoc.

Trinh tu:

```bash
uv run python monitor.py
uv run python kiosk.py
bash cloudflared/quick-tunnel.sh
```

Script se in ra 2 URL dang:

```bash
https://something.trycloudflare.com
```

Sau do sua `.env`:

```bash
SMARTLOCKER_BASE_URL="https://<app-quick-tunnel>.trycloudflare.com"
SMARTLOCKER_MONITOR_URL="https://<monitor-quick-tunnel>.trycloudflare.com"
```

Roi restart lai:

```bash
uv run python monitor.py
uv run python kiosk.py
```

Giu cua so `bash cloudflared/quick-tunnel.sh` mo trong luc test. Neu tat cua so do, link public se mat hieu luc.

Khi do cac duong dan se la:

- `https://<monitor-quick-tunnel>.trycloudflare.com/portal`
- `https://<monitor-quick-tunnel>.trycloudflare.com/admin`
- `https://<app-quick-tunnel>.trycloudflare.com`

## Docker hoa toan bo stack

Thu muc nay da duoc chuan bi de chay bang Docker cho toan bo stack, gom ca kiosk khi can:

- `mysql`: co mount `mysql_schema.sql` de tao schema luc khoi dong lan dau.
- `app`: web kiosk FastAPI tai cong `8000`.
- `monitor`: web hop nhat cho nguoi dung va quan tri tai cong `8001`.
- `kiosk`: giao dien GUI `pywebview + Qt`, bat khi can bang profile `kiosk`.
- `cloudflared`: tuy chon, bat bang profile `tunnel`.

Dockerfile da tach 2 target:

- `server`: dung cho `app` va `monitor`
- `kiosk`: dung cho `kiosk.py`, da kem runtime Qt/WebEngine de chay tren Raspberry Pi OS 64-bit

### 1. Tao file env cho Docker

Khong nen dung truc tiep `.env` local hien tai vi trong container `127.0.0.1` se tro vao chinh container do, khong phai MySQL.

Tao file `.env.docker` tu mau:

```bash
cp .env.docker.example .env.docker
```

Sau do sua cac bien can thiet, dac biet:

- `MYSQL_ROOT_PASSWORD`
- `MYSQL_PASSWORD`
- `SMARTLOCKER_ADMIN_USERNAME`
- `SMARTLOCKER_ADMIN_PASSWORD`
- cac bien SMTP neu muon gui email that
- `SMARTLOCKER_BASE_URL`, `SMARTLOCKER_MONITOR_URL`

Goi y khi chay tren Raspberry Pi trong LAN:

```bash
SMARTLOCKER_BASE_URL='http://192.168.1.23:8000'
SMARTLOCKER_MONITOR_URL='http://192.168.1.23:8001'
```

Neu publish ra ngoai qua Cloudflare Tunnel hay reverse proxy, doi sang URL `https://...` that.

### 2. Build va chay local

```bash
docker compose --env-file .env.docker up -d --build
```

Sau khi chay xong:

- `http://localhost:8000` -> app
- `http://localhost:8001` -> monitor

Xem log:

```bash
docker compose --env-file .env.docker logs -f
```

Dung stack:

```bash
docker compose --env-file .env.docker down
```

Neu muon xoa ca du lieu MySQL volume:

```bash
docker compose --env-file .env.docker down -v
```

### 3. Chay kiosk trong container

Service `kiosk` dung profile rieng vi no can GUI cua host. Cach nay phu hop nhat voi Raspberry Pi co man hinh cam ung.

Tren Raspberry Pi OS Desktop, cap quyen cho container mo cua so X11:

```bash
xhost +SI:localuser:root
```

Sau do chay kiosk:

```bash
docker compose --env-file .env.docker --profile kiosk up -d --build kiosk
```

Service nay se:

- dung image `target: kiosk`
- noi vao X11 qua `/tmp/.X11-unix`
- dung `kiosk.py` de tu mo full-screen webview
- ket noi MySQL bang hostname noi bo `mysql`

Neu Raspberry Pi khong co GUI, khong nen bat profile `kiosk`; khi do chi chay `app`, `monitor`, `mysql`.

### 4. Bat Cloudflare Tunnel trong Docker

Dat file credential tunnel vao:

```bash
cloudflared/credentials.json
```

Sua `cloudflared/config.docker.yml`:

- thay `tunnel: smartlocker` bang tunnel name hoac tunnel id that
- doi cac hostname `*.example.com` thanh domain that

Chay them service tunnel:

```bash
docker compose --env-file .env.docker --profile tunnel up -d cloudflared
```

Trong moi truong Docker, `cloudflared` duoc cau hinh truy cap noi bo qua ten service:

- `http://app:8000`
- `http://monitor:8001`

### 5. Chay tren Raspberry Pi

Khuyen nghi dung Raspberry Pi OS 64-bit. Stack nay duoc huong toi `linux/arm64`; Raspberry Pi OS 32-bit se de gap loi voi Qt WebEngine va cac wheel Python.

Lenh khoi dong day du tren Pi:

```bash
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker --profile kiosk up -d kiosk
```

Hoac dung script da chuan bi san:

```bash
chmod +x scripts/pi-up.sh scripts/pi-kiosk.sh scripts/pi-down.sh
./scripts/pi-up.sh
./scripts/pi-kiosk.sh
```

Neu Pi dong vai tro server that, nen dat them:

- `SMARTLOCKER_BASE_URL` = IP LAN hoac domain that cua Pi cho cong `8000`
- `SMARTLOCKER_MONITOR_URL` = IP LAN hoac domain that cua Pi cho cong `8001`

Khuyen nghi khi dua sang Pi:

- Cai Raspberry Pi OS 64-bit de de build image hon.
- Dung SSD hoac USB 3.0 cho volume MySQL neu co the, tranh ghi nhieu len the nho.
- Neu can hardware acceleration cho kiosk, dam bao `/dev/dri` ton tai tren host.
- Neu chi can server API va monitor, bo profile `kiosk` de image nhe hon va khoi dong nhanh hon.

### 6. Kiem tra nhanh

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs app
docker compose --env-file .env.docker logs monitor
docker compose --env-file .env.docker logs kiosk
```

## Dua len Git va chay ngay tren Raspberry Pi

Repo nen dua len Git voi file mau, khong dua file bi mat that:

- giu lai `.env.docker.example`
- khong commit `.env`, `.env.docker`, `.kiosk_state.json`, `__pycache__`

Quy trinh de xuat:

```bash
git init
git rm --cached --ignore-unmatch .env .env.docker .kiosk_state.json
git rm --cached -r --ignore-unmatch __pycache__
git add .
git status
git commit -m "Initial Dockerized Smart Locker setup"
git branch -M main
git remote add origin <git-repo-url>
git push -u origin main
```

Tren Raspberry Pi:

```bash
git clone <git-repo-url>
cd smartlocker
cp .env.docker.example .env.docker
chmod +x scripts/pi-up.sh scripts/pi-kiosk.sh scripts/pi-down.sh
./scripts/pi-up.sh
./scripts/pi-kiosk.sh
```

Neu Pi chi chay server, khong can giao dien kiosk:

```bash
./scripts/pi-up.sh
```
