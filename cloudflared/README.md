## Cloudflare Tunnel Setup

He thong hien tai tach ro:

- `monitor` co `portal nguoi dung` tai duong dan `/portal`
- `monitor` co `quan tri` tai duong dan `/admin`
- `app` (`main.py`) phuc vu kiosk va link nhan do tai cong `8000`

Thay cac gia tri mau truoc khi dung:

- `smartlocker` trong `config.yml` thanh ten tunnel that cua ban
- duong dan credentials trong `config.yml`
- `monitor-smartlocker.example.com` va `app-smartlocker.example.com` thanh hostname that

Trinh tu thuc hien:

```bash
cloudflared tunnel login
cloudflared tunnel create smartlocker
cloudflared tunnel route dns smartlocker monitor-smartlocker.example.com
cloudflared tunnel route dns smartlocker app-smartlocker.example.com
cloudflared tunnel --config cloudflared/config.yml run
```

Bien `.env` nen dat:

```bash
SMARTLOCKER_MONITOR_URL="https://monitor-smartlocker.example.com"
SMARTLOCKER_BASE_URL="https://app-smartlocker.example.com"
SMARTLOCKER_MONITOR_HOST="0.0.0.0"
SMARTLOCKER_MONITOR_PORT="8001"
SMARTLOCKER_APP_HOST="0.0.0.0"
SMARTLOCKER_APP_PORT="8000"
```

URL su dung:

- portal nguoi dung: `https://monitor-smartlocker.example.com/portal`
- quan tri: `https://monitor-smartlocker.example.com/admin`
- kiosk / pickup links: `https://app-smartlocker.example.com`

Neu app hoac monitor chay tren may khac voi MySQL, dat tung bien DB ro rang:

```bash
SMARTLOCKER_DATABASE_HOST="192.168.1.10"
SMARTLOCKER_DATABASE_PORT="3306"
SMARTLOCKER_DATABASE_NAME="smartlocker"
SMARTLOCKER_DATABASE_USER="smartlocker_user"
SMARTLOCKER_DATABASE_PASSWORD="mat-khau-db"
```

Luu y:

- `SMARTLOCKER_BASE_URL` phai tro toi web `main.py` vi link nhan do `/nhan-do/link/<token>` dang duoc phuc vu tai cong `8000`
- `SMARTLOCKER_MONITOR_URL` phai tro toi web `monitor.py`; kiosk se tao QR va link sang `.../portal`
- Cloudflare Tunnel chi publish HTTP/HTTPS; no khong thay the ket noi toi MySQL
- Neu monitor chay o may chu A va MySQL chay o may chu B, monitor van phai ket noi private toi `SMARTLOCKER_DATABASE_HOST` cua may B qua LAN, VPN, WireGuard hoac kenh private khac
