# Smart Locker ERD

This schema keeps the current operating flow intact while making the database relationships explicit enough for MySQL Workbench EER diagrams.

```mermaid
erDiagram
    users ||--o{ locker_orders : places_or_receives
    locker_sites ||--o{ lockers : contains
    lockers ||--o{ locker_orders : stores
    locker_orders ||--o{ locker_access_tokens : issues
    admin_commands ||--o{ admin_command_lockers : targets
    lockers ||--o{ admin_command_lockers : receives

    users {
        int id PK
        varchar phone UK
        varchar email UK
        datetime created_at
        datetime updated_at
    }

    locker_sites {
        int id PK
        varchar code UK
        varchar name
        datetime created_at
        datetime updated_at
    }

    lockers {
        int id PK
        int site_id FK
        int locker_number
        varchar code UK
        varchar display_name
        varchar status
    }

    locker_orders {
        int id PK
        int user_id FK
        int locker_id FK
        varchar phone
        varchar pickup_code UK
        varchar flow
        varchar recipient_email
        varchar status
        datetime created_at
    }

    locker_access_tokens {
        int id PK
        int order_id FK
        int locker_id FK
        varchar phone
        varchar email
        varchar token_hash UK
        varchar status
        datetime expires_at
        datetime used_at
    }

    admin_commands {
        int id PK
        varchar action
        varchar status
        varchar note
        datetime created_at
        datetime completed_at
    }

    admin_command_lockers {
        int command_id PK,FK
        int locker_id PK,FK
    }
```

## Important Behavior

- `users.email` is nullable so an order can still be created before the recipient registers an email.
- `locker_orders.user_id` is nullable for legacy data, but new orders create or reuse a `users` row by `phone`.
- `locker_orders.phone` and `locker_orders.recipient_email` stay as snapshots so the old pickup and email history flows keep working.
- `locker_access_tokens.phone` and `locker_access_tokens.email` also stay as snapshots of the delivery moment.
- `admin_command_lockers` stores one or many target lockers for admin commands; `admin_commands.note` remains for human notes and backward compatibility.

## Indexes For Growth

- `users(phone)` and `users(email)` support registration and lookup.
- `locker_orders(user_id, created_at)` supports user history.
- `locker_orders(locker_id, status)` supports active locker occupancy.
- `locker_orders(phone, created_at)` keeps the current phone lookup flow fast.
- `locker_access_tokens(order_id, status)` supports revoking and validating pickup links.
- `admin_commands(status, created_at)` supports pending command polling.

## MySQL Workbench

Use `Database -> Reverse Engineer...` against the `smartlocker` schema. Workbench should now draw these relationships automatically because they are real foreign keys in `mysql_schema.sql` and the SQLAlchemy models.
