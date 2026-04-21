<?php
// Database credentials — this file should NOT be publicly accessible
define('DB_HOST', 'localhost');
define('DB_NAME', 'drscapital_slyled');
define('DB_USER', 'drscapital_slyled');
define('DB_PASS', '1234qwerty!@#$');

// Rate limiting
define('RATE_LIMIT_UPLOADS', 10);    // max uploads per window
define('RATE_LIMIT_WINDOW', 3600);   // window in seconds (1 hour)

// Max profile size — #606 raised from 8192 to 32768 bytes (4×) so full
// OFL-style moving-head profiles with complete WheelSlot / WheelShake /
// ShutterStrobe capability annotations fit. The schema column is TEXT
// (65 KB) so this constant is the single enforcement point; bump both
// handle_upload and handle_update check against it (index.php:156,201).
define('MAX_PROFILE_SIZE', 32768);   // 32KB max JSON
