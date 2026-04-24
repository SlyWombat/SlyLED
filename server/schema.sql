-- SlyLED Community Profile Server schema

CREATE TABLE IF NOT EXISTS profiles (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    slug          VARCHAR(128) NOT NULL,
    name          VARCHAR(200) NOT NULL,
    manufacturer  VARCHAR(100) NOT NULL DEFAULT 'Generic',
    category      VARCHAR(20) NOT NULL DEFAULT 'par',
    channel_count TINYINT UNSIGNED NOT NULL,
    color_mode    VARCHAR(32) NOT NULL DEFAULT 'rgb',
    beam_width    SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    pan_range     SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    tilt_range    SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    profile_json  TEXT NOT NULL,
    channel_hash  CHAR(40) NOT NULL,
    uploader_ip   VARCHAR(45) NOT NULL DEFAULT '',
    upload_ts     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    downloads     INT UNSIGNED NOT NULL DEFAULT 0,
    flagged       TINYINT UNSIGNED NOT NULL DEFAULT 0,
    UNIQUE KEY uq_slug (slug),
    INDEX idx_hash (channel_hash),
    INDEX idx_cat (category),
    FULLTEXT idx_search (name, manufacturer)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rate_limits (
    ip           VARCHAR(45) NOT NULL PRIMARY KEY,
    upload_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
    window_start DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
