-- Run these statements after the schema and synthetic data are created on TiDB.
-- They enable TiFlash replicas for the analytics-oriented tables used in the MPP benchmark.

ALTER TABLE locker_events SET TIFLASH REPLICA 1;
ALTER TABLE parcel_image_assets SET TIFLASH REPLICA 1;
ALTER TABLE parcel_inference_results SET TIFLASH REPLICA 1;
ALTER TABLE locker_orders SET TIFLASH REPLICA 1;

SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    REPLICA_COUNT,
    AVAILABLE,
    PROGRESS
FROM information_schema.tiflash_replica
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME;

-- Optional session-level controls for testing MPP behavior.
-- The optimizer can choose MPP automatically when tidb_allow_mpp=1.
SET @@session.tidb_allow_mpp = 1;
SET @@session.tidb_enforce_mpp = 0;

-- Use this only when you want to force MPP in an isolated test session.
-- SET @@session.tidb_allow_mpp = 1;
-- SET @@session.tidb_enforce_mpp = 1;
