<?php
// Database credentials — this file should NOT be publicly accessible
define('DB_HOST', 'localhost');
define('DB_NAME', 'drscapital_slyled');
define('DB_USER', 'drscapital_slyled');
define('DB_PASS', '1234qwerty!@#$');

// Rate limiting
define('RATE_LIMIT_UPLOADS', 10);    // max uploads per window
define('RATE_LIMIT_WINDOW', 3600);   // window in seconds (1 hour)

// Max profile size
define('MAX_PROFILE_SIZE', 8192);    // 8KB max JSON
