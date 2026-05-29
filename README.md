# SmartLocker

SmartLocker is a simple locker management system built with Python, FastAPI, and MySQL.

It includes:

- a locker app for drop-off and pickup
- a monitor portal for users and admins
- an optional kiosk window for touchscreen devices
- Docker support for local deployment

## Features

- Manage 8 lockers
- Store and track locker orders
- Pickup with code or secure token link
- User portal for phone and email registration
- Admin dashboard for locker control and history
- Optional email delivery notifications

## Tech Stack

- Python 3.12
- FastAPI
- SQLAlchemy
- MySQL
- PyQt6 / PyWebView
- Docker Compose

## Project Structure

- `main.py` - main locker web app
- `monitor.py` - user portal and admin dashboard
- `kiosk.py` - kiosk window launcher
- `database.py` - database setup
- `model.py` - database models
- `config.py` - environment config helpers
- `mysql_schema.sql` - MySQL schema
- `docker-compose.yml` - local services

## Run Locally

### 1. Install dependencies

This project uses `uv`:

```bash
uv sync
```

### 2. Configure environment

Create a `.env` file in the project root.

Example:

```bash
SMARTLOCKER_DATABASE_URL="mysql+pymysql://root:password@127.0.0.1:3306/smartlocker"
SMARTLOCKER_APP_HOST="0.0.0.0"
SMARTLOCKER_APP_PORT="8000"
SMARTLOCKER_BASE_URL="http://127.0.0.1:8000"
SMARTLOCKER_MONITOR_HOST="0.0.0.0"
SMARTLOCKER_MONITOR_PORT="8001"
SMARTLOCKER_MONITOR_URL="http://127.0.0.1:8001"
SMARTLOCKER_ADMIN_USERNAME="admin"
SMARTLOCKER_ADMIN_PASSWORD="your-password"
```

### 3. Create the database

```bash
mysql -u root -p < mysql_schema.sql
```

### 4. Start the apps

Run the locker app:

```bash
uv run python main.py
```

Run the monitor app:

```bash
uv run python monitor.py
```

Optional kiosk mode:

```bash
uv run python kiosk.py
```

## Run With Docker

You can also run the project with Docker Compose:

```bash
docker compose up --build
```

Default services:

- app: `http://localhost:8000`
- monitor: `http://localhost:8001`
- mysql: `localhost:3307`

## Notes

- If no database is configured, the app can fall back to temporary in-memory data in some flows.
- Email sending requires SMTP environment variables.
- Cloudflare Tunnel files are included for remote access setup.

## License

This project is for learning and internal deployment. Add a license here if you plan to publish it publicly.
