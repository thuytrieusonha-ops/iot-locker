CREATE DATABASE IF NOT EXISTS smartlocker
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE smartlocker;

CREATE TABLE IF NOT EXISTS users (
    id INT NOT NULL AUTO_INCREMENT,
    phone VARCHAR(20) NOT NULL,
    email VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_phone (phone),
    UNIQUE KEY uq_users_email (email),
    KEY ix_users_phone (phone),
    KEY ix_users_email (email)
);

CREATE TABLE IF NOT EXISTS locker_sites (
    id INT NOT NULL AUTO_INCREMENT,
    code VARCHAR(80) NOT NULL,
    name VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_locker_sites_code (code),
    KEY ix_locker_sites_code (code)
);

CREATE TABLE IF NOT EXISTS lockers (
    id INT NOT NULL AUTO_INCREMENT,
    site_id INT NOT NULL,
    locker_number INT NOT NULL,
    code VARCHAR(80) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_lockers_code (code),
    UNIQUE KEY uq_lockers_site_number (site_id, locker_number),
    KEY ix_lockers_site_id (site_id),
    KEY ix_lockers_status (status),
    CONSTRAINT fk_lockers_site
        FOREIGN KEY (site_id) REFERENCES locker_sites (id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
);

INSERT INTO locker_sites (id, code, name)
VALUES (1, 'default', 'Default Locker Site')
ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO lockers (id, site_id, locker_number, code, display_name, status)
VALUES
    (1, 1, 1, 'default-0001', 'Tu 1', 'active'),
    (2, 1, 2, 'default-0002', 'Tu 2', 'active'),
    (3, 1, 3, 'default-0003', 'Tu 3', 'active'),
    (4, 1, 4, 'default-0004', 'Tu 4', 'active'),
    (5, 1, 5, 'default-0005', 'Tu 5', 'active'),
    (6, 1, 6, 'default-0006', 'Tu 6', 'active'),
    (7, 1, 7, 'default-0007', 'Tu 7', 'active'),
    (8, 1, 8, 'default-0008', 'Tu 8', 'active')
ON DUPLICATE KEY UPDATE
    site_id = VALUES(site_id),
    locker_number = VALUES(locker_number),
    display_name = VALUES(display_name),
    status = VALUES(status);

CREATE TABLE IF NOT EXISTS locker_orders (
    id INT NOT NULL AUTO_INCREMENT,
    user_id INT NULL,
    locker_id INT NOT NULL,
    phone VARCHAR(20) NOT NULL,
    pickup_code VARCHAR(12) NOT NULL,
    flow VARCHAR(40) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    order_code VARCHAR(80) NULL,
    recipient_email VARCHAR(255) NULL,
    email_delivery_status VARCHAR(20) NULL,
    email_delivery_note VARCHAR(255) NULL,
    email_link_base_url VARCHAR(255) NULL,
    email_sent_at DATETIME NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'stored',
    active_locker_slot INT GENERATED ALWAYS AS (CASE WHEN status = 'stored' THEN locker_id ELSE NULL END) STORED,
    PRIMARY KEY (id),
    UNIQUE KEY uq_locker_orders_pickup_code (pickup_code),
    UNIQUE KEY uq_locker_orders_active_locker_slot (active_locker_slot),
    KEY ix_locker_orders_user_id (user_id),
    KEY ix_locker_orders_locker_id (locker_id),
    KEY ix_locker_orders_phone (phone),
    KEY ix_locker_orders_recipient_email (recipient_email),
    KEY ix_locker_orders_email_delivery_status (email_delivery_status),
    KEY ix_locker_orders_status (status),
    KEY ix_locker_orders_pickup_code (pickup_code),
    KEY ix_locker_orders_user_created_at (user_id, created_at),
    KEY ix_locker_orders_locker_status (locker_id, status),
    KEY ix_locker_orders_phone_created_at (phone, created_at),
    CONSTRAINT fk_locker_orders_user
        FOREIGN KEY (user_id) REFERENCES users (id)
        ON DELETE SET NULL
        ON UPDATE CASCADE,
    CONSTRAINT fk_locker_orders_locker
        FOREIGN KEY (locker_id) REFERENCES lockers (id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,
    CONSTRAINT ck_locker_orders_status CHECK (status IN ('stored', 'collected')),
    CONSTRAINT ck_locker_orders_flow CHECK (flow IN ('user_dropoff', 'shipper_dropoff')),
    CONSTRAINT ck_locker_orders_email_delivery_status CHECK (
        email_delivery_status IS NULL
        OR email_delivery_status IN ('pending', 'sent', 'failed', 'smtp_missing', 'unregistered')
    )
);

CREATE TABLE IF NOT EXISTS locker_access_tokens (
    id INT NOT NULL AUTO_INCREMENT,
    order_id INT NOT NULL,
    locker_id INT NOT NULL,
    phone VARCHAR(20) NOT NULL,
    email VARCHAR(255) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    delivery_channel VARCHAR(20) NOT NULL DEFAULT 'email',
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    used_at DATETIME NULL,
    active_order_id INT GENERATED ALWAYS AS (CASE WHEN status = 'active' THEN order_id ELSE NULL END) STORED,
    PRIMARY KEY (id),
    UNIQUE KEY uq_locker_access_tokens_token_hash (token_hash),
    UNIQUE KEY uq_locker_access_tokens_active_order_id (active_order_id),
    KEY ix_locker_access_tokens_order_id (order_id),
    KEY ix_locker_access_tokens_locker_id (locker_id),
    KEY ix_locker_access_tokens_phone (phone),
    KEY ix_locker_access_tokens_email (email),
    KEY ix_locker_access_tokens_status (status),
    KEY ix_locker_access_tokens_expires_at (expires_at),
    KEY ix_locker_access_tokens_order_status (order_id, status),
    CONSTRAINT fk_locker_access_tokens_order
        FOREIGN KEY (order_id) REFERENCES locker_orders (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,
    CONSTRAINT fk_locker_access_tokens_locker
        FOREIGN KEY (locker_id) REFERENCES lockers (id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,
    CONSTRAINT ck_locker_access_tokens_status CHECK (status IN ('active', 'used', 'revoked')),
    CONSTRAINT ck_locker_access_tokens_delivery_channel CHECK (delivery_channel IN ('email'))
);

CREATE TABLE IF NOT EXISTS admin_commands (
    id INT NOT NULL AUTO_INCREMENT,
    action VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    note VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    PRIMARY KEY (id),
    KEY ix_admin_commands_action (action),
    KEY ix_admin_commands_status (status),
    KEY ix_admin_commands_created_at (created_at),
    KEY ix_admin_commands_status_created_at (status, created_at),
    CONSTRAINT ck_admin_commands_status CHECK (status IN ('pending', 'completed'))
);

CREATE TABLE IF NOT EXISTS admin_command_lockers (
    command_id INT NOT NULL,
    locker_id INT NOT NULL,
    PRIMARY KEY (command_id, locker_id),
    KEY ix_admin_command_lockers_locker_id (locker_id),
    CONSTRAINT fk_admin_command_lockers_command
        FOREIGN KEY (command_id) REFERENCES admin_commands (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,
    CONSTRAINT fk_admin_command_lockers_locker
        FOREIGN KEY (locker_id) REFERENCES lockers (id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
);
