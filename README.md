# SmartLocker

SmartLocker is a locker management system built with Python, FastAPI, and MySQL.

The project is designed for a smart locker kiosk scenario where staff or shippers place items into lockers, recipients receive a pickup code or secure link, and admins monitor operations from a separate web portal.

## What This Project Includes

- `main.py`: the main locker app for drop-off, pickup, support, and kiosk-facing flows
- `monitor.py`: the user portal and admin dashboard
- `kiosk.py`: a kiosk window launcher for touchscreen devices
- MySQL persistence for locker orders, users, access tokens, and admin commands
- MQTT communication between the web app and locker controller
- Optional email delivery for secure pickup links
- Docker Compose setup for local or small server deployment

## Core Features

- Manage 8 lockers
- Support both user drop-off and shipper drop-off
- Generate pickup codes for each stored item
- Send secure pickup links by email
- Let users register phone number and email in a portal
- Let admins unlock one locker or all lockers
- Track order history and email delivery status
- Receive issue reports from the kiosk interface

## Tech Stack

- Python 3.12
- FastAPI
- SQLAlchemy
- MQTT / Eclipse Mosquitto
- MySQL
- PyQt6 / PyWebView
- Docker Compose

## Project Structure

- `main.py` - main web app for locker operations
- `locker_hardware.py` - MQTT client used by the web app
- `locker_gateway.py` - Raspberry Pi MQTT-to-UART bridge for Arduino Mega
- `monitor.py` - portal and admin dashboard
- `kiosk.py` - kiosk browser window
- `database.py` - database engine and session setup
- `model.py` - SQLAlchemy models
- `config.py` - environment variable helpers
- `mysql_schema.sql` - database schema
- `docker-compose.yml` - local container stack
- `cloudflared/` - optional Cloudflare Tunnel config
- `scripts/` - helper scripts for kiosk deployment

## System Roles

The system has four main roles:

- User: registers phone number and email in the portal
- Shipper or staff: stores an item in a locker from the main app
- Recipient: opens the locker with a pickup code or secure link
- Admin: monitors activity and sends unlock commands from the dashboard

## Main URLs

When running locally with default ports:

- Main locker app: `http://localhost:8000`
- User portal: `http://localhost:8001/portal`
- Admin login: `http://localhost:8001/admin`
- Admin dashboard: `http://localhost:8001/admin/dashboard`

## Full Project Workflow

### 1. User registration workflow

This flow is handled by `monitor.py`.

1. The user opens the portal at `/portal`.
2. The user enters a phone number and email address.
3. The system stores or updates the user record in `users`.
4. Later, when a locker order is created for that phone number, the system can use the saved email to deliver a secure pickup link.

There is also a kiosk-facing registration route in `main.py`:

- `GET /dang-ky-email`
- `POST /dang-ky-email`

This is useful when registration needs to happen directly on the kiosk screen.

### 2. Drop-off workflow

This flow is handled by `main.py`.

There are two drop-off entry points:

- `GET /gui-do` and `POST /gui-do` for user drop-off
- `GET /giao-do` and `POST /giao-do` for shipper drop-off

The process is:

1. Staff or shipper enters the required information.
2. The system finds an available locker.
3. A pickup code is generated for the order.
4. A locker order record is saved in `locker_orders`.
5. The locker is marked as occupied with status `stored`.
6. If the phone number already has a registered email, the system can create a secure access token and send a pickup email.

Each stored item is linked to:

- locker ID
- phone number
- pickup code
- flow type
- optional order code
- optional recipient email
- email delivery status

### 3. Email notification workflow

If SMTP is configured and the recipient has a registered email:

1. The system creates a secure pickup access token.
2. The raw token is used to build a pickup link.
3. Only the token hash is stored in `locker_access_tokens`.
4. The email is sent to the registered recipient.
5. Email delivery result is saved back to the locker order.

This keeps the pickup link safer than sending only a plain pickup code.

Related data stored in the database:

- token hash
- status
- expiration time
- used time
- delivery channel

### 4. Pickup workflow

The recipient can collect an item in two ways.

#### Option A: pickup by phone number and pickup code

Routes:

- `GET /nhan-do`
- `POST /nhan-do`

Process:

1. The recipient enters the phone number and pickup code.
2. The system verifies the matching active locker order.
3. If valid, the order is marked as collected.
4. The locker becomes available again.

#### Option B: pickup by secure email link

Routes:

- `GET /nhan-do/link/{raw_token}`
- `POST /nhan-do/link/{raw_token}`

Process:

1. The recipient opens the secure link from email.
2. The system checks the token hash, status, and expiration time.
3. The recipient confirms identity using the last 4 digits of the phone number.
4. If valid, the locker is opened and the token is marked as used.
5. The order status changes to `collected`.

There is also a pickup-code specific route:

- `GET /nhan-do/ma-bao-mat/{pickup_code}`
- `POST /nhan-do/ma-bao-mat/{pickup_code}`

And a kiosk handoff route:

- `GET /nhan-do/kiosk/{raw_token}`

### 5. Admin workflow

This flow is handled by `monitor.py`.

Admin entry points:

- `GET /admin`
- `POST /admin/login`
- `POST /admin/logout`
- `GET /admin/dashboard`

Admin capabilities:

- view locker status
- view order history
- view email delivery status
- unlock all lockers
- unlock a specific locker
- purge collected history
- purge all history
- review issue reports

Unlock operations work through the `admin_commands` and `admin_command_lockers` tables:

1. An admin triggers an action from the dashboard.
2. A command is stored in the database with status `pending`.
3. The main locker app polls or reads the command state.
4. The kiosk or locker app displays or completes the action.
5. The command is marked completed.

This design helps separate the admin dashboard from the kiosk-side execution.

### 6. Support and issue reporting workflow

The kiosk app includes support pages:

- `GET /ho-tro`
- `GET /bao-cao-su-co`
- `POST /bao-cao-su-co`

Users can report issues such as:

- locker not opening
- forgotten pickup code
- slow screen
- wrong locker state
- cannot receive email
- other support requests

Issue reports are recorded as admin commands so they can appear in the admin dashboard.

## Data Model

Main tables:

- `users`: phone and email registrations
- `locker_sites`: physical sites or groups that contain lockers
- `lockers`: locker master data
- `locker_orders`: active and historical locker orders
- `locker_access_tokens`: secure pickup tokens
- `admin_commands`: unlock actions and issue reports
- `admin_command_lockers`: target lockers for admin commands

Important statuses:

- locker order status: `stored`, `collected`
- access token status: typically active or used
- admin command status: `pending`, `completed`

## Local Development Setup

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

Optional SMTP configuration:

```bash
SMARTLOCKER_SMTP_HOST="smtp.gmail.com"
SMARTLOCKER_SMTP_PORT="587"
SMARTLOCKER_SMTP_USERNAME="your-email@gmail.com"
SMARTLOCKER_SMTP_PASSWORD="your-app-password"
SMARTLOCKER_SMTP_FROM_EMAIL="your-email@gmail.com"
SMARTLOCKER_SMTP_USE_TLS="true"
```

Optional MQTT locker control:

```bash
SMARTLOCKER_HARDWARE_ENABLED="true"
SMARTLOCKER_HARDWARE_REQUIRED="true"
SMARTLOCKER_MQTT_HOST="127.0.0.1"
SMARTLOCKER_MQTT_PORT="1883"
SMARTLOCKER_MQTT_TOPIC_PREFIX="smartlocker"
SMARTLOCKER_MQTT_CLIENT_ID="smartlocker-app"
SMARTLOCKER_MQTT_QOS="1"
SMARTLOCKER_MQTT_COMMAND_TIMEOUT="5.0"
SMARTLOCKER_DOOR_CLOSE_TIMEOUT="120.0"
SMARTLOCKER_GATEWAY_MQTT_CLIENT_ID="smartlocker-pi-gateway"
SMARTLOCKER_UART_PORT="/dev/serial0"
SMARTLOCKER_UART_BAUDRATE="9600"
SMARTLOCKER_UART_COMMAND_TIMEOUT="3.0"
```

`SMARTLOCKER_HARDWARE_REQUIRED=false` allows the web flow to simulate hardware when the broker or controller is unavailable. Set it to `true` in production so an order is not completed without a controller acknowledgement.

### MQTT protocol

With the default `smartlocker` prefix, the app and locker controller use these topics:

- App publishes commands to `smartlocker/lockers/{locker_id}/command`.
- Controller publishes acknowledgements to `smartlocker/lockers/{locker_id}/ack`.
- Controller can publish door and sensor events to `smartlocker/lockers/{locker_id}/event`.

Open command payload:

```json
{"command":"open","locker_id":1,"request_id":"a-unique-request-id"}
```

The controller must echo the same `request_id` before `SMARTLOCKER_MQTT_COMMAND_TIMEOUT` expires:

```json
{"request_id":"a-unique-request-id","locker_id":1,"status":"opened"}
```

Accepted success statuses are `ok`, `opened`, and `accepted`. Occupancy changes use the same command topic and do not require an acknowledgement:

```json
{"command":"set_occupied","locker_id":1,"occupied":true}
```

During shipper dropoff, the app waits for a `door_open` event followed by a
`door_closed` event for the same locker. Only then does it publish
`set_occupied=true`, so the occupied light stays off while the door is open.
`SMARTLOCKER_DOOR_CLOSE_TIMEOUT` controls how long the app waits for that sequence.

### Raspberry Pi to Arduino Mega UART

MQTT does not replace the physical UART connection. `locker_gateway.py` runs on the Raspberry Pi and translates between the two protocols:

```text
Web app -> MQTT broker -> Raspberry Pi gateway -> UART -> Arduino Mega
Web app <- MQTT ACK    <- Raspberry Pi gateway <- UART <- Arduino Mega
```

UART commands sent by the Pi:

- `OPEN,{locker_id}`
- `LOCKER_USED,{locker_id}`
- `LOCKER_EMPTY,{locker_id}`

UART messages sent by the Mega:

- `OK,{locker_id}` acknowledges an open command.
- `DOOR_OPEN,{locker_id}` publishes a `door_open` MQTT event.
- `DOOR_CLOSED,{locker_id}` publishes a `door_closed` MQTT event.

Only one open command is sent to the Mega at a time, allowing its `OK,{locker_id}` response to be correlated with the MQTT `request_id`.

### 3. Create the database

```bash
mysql -u root -p < mysql_schema.sql
```

### 4. Run the services

Start the main locker app:

```bash
uv run python main.py
```

Start the monitor app:

```bash
uv run python monitor.py
```

Optional kiosk mode:

```bash
uv run python kiosk.py
```

## Docker Workflow

You can run the main services with Docker Compose:

```bash
docker compose up --build
```

Default services:

- app: `http://localhost:8000`
- monitor: `http://localhost:8001`
- mysql: `localhost:3307`
- mqtt: `localhost:1883`

Optional profiles in `docker-compose.yml`:

- `hardware` for the Raspberry Pi MQTT-to-UART gateway
- `kiosk` for kiosk container
- `tunnel` for Cloudflare Tunnel container

Examples:

```bash
docker compose --profile hardware up --build
```

```bash
docker compose --profile kiosk up --build
```

```bash
docker compose --profile tunnel up --build
```

## Recommended Real-World Flow

For a typical deployment, the workflow is:

1. A user registers phone number and email in the portal.
2. Staff or a shipper stores the package in a locker.
3. The system generates a pickup code.
4. If email is available, the system sends a secure pickup link.
5. The recipient comes to the locker and opens it with the code or link.
6. The admin monitors locker activity and handles exceptions from the dashboard.

## Notes

- Some flows can fall back to in-memory data if the database is not configured, but MySQL is recommended for real usage.
- Email delivery requires SMTP configuration.
- Remote access can be exposed with the included Cloudflare Tunnel setup.
- The UI route names are currently Vietnamese because they match the original kiosk flows.

## License

This project is for learning and internal deployment. Add a license if you plan to publish it publicly.
