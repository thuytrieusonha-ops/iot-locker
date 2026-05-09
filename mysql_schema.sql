CREATE DATABASE IF NOT EXISTS smartlocker
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE smartlocker;

CREATE TABLE IF NOT EXISTS locker_orders (
    id INT NOT NULL AUTO_INCREMENT,
    locker_id INT NOT NULL,
    phone VARCHAR(20) NOT NULL,
    pickup_code VARCHAR(12) NOT NULL,
    flow VARCHAR(40) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    order_code VARCHAR(80) NULL,
    recipient_email VARCHAR(255) NULL,
    email_delivery_status VARCHAR(20) NULL,
    email_delivery_note VARCHAR(255) NULL,
    email_sent_at DATETIME NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'stored',
    PRIMARY KEY (id),
    UNIQUE KEY uq_locker_orders_pickup_code (pickup_code),
    KEY ix_locker_orders_locker_id (locker_id),
    KEY ix_locker_orders_phone (phone),
    KEY ix_locker_orders_recipient_email (recipient_email),
    KEY ix_locker_orders_email_delivery_status (email_delivery_status),
    KEY ix_locker_orders_status (status),
    KEY ix_locker_orders_pickup_code (pickup_code)
);

CREATE TABLE IF NOT EXISTS user_accounts (
    id INT NOT NULL AUTO_INCREMENT,
    phone VARCHAR(20) NOT NULL,
    email VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_accounts_phone (phone),
    UNIQUE KEY uq_user_accounts_email (email),
    KEY ix_user_accounts_phone (phone),
    KEY ix_user_accounts_email (email)
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
    PRIMARY KEY (id),
    UNIQUE KEY uq_locker_access_tokens_token_hash (token_hash),
    KEY ix_locker_access_tokens_order_id (order_id),
    KEY ix_locker_access_tokens_locker_id (locker_id),
    KEY ix_locker_access_tokens_phone (phone),
    KEY ix_locker_access_tokens_email (email),
    KEY ix_locker_access_tokens_status (status),
    KEY ix_locker_access_tokens_expires_at (expires_at)
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
    KEY ix_admin_commands_created_at (created_at)
);
