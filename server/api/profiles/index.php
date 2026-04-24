<?php
/**
 * SlyLED Community Profile Server API
 * Hosted at electricrv.ca/api/profiles/index.php
 *
 * Routes use ?action= parameter to avoid WordPress .htaccess conflicts.
 * GET  ?action=search&q=&category=&limit=50
 * GET  ?action=get&slug=profile-id
 * GET  ?action=recent&limit=20
 * GET  ?action=popular&limit=20
 * GET  ?action=stats
 * GET  ?action=since&ts=2026-01-01T00:00:00
 * POST ?action=upload         (body: {"profile":{...}})
 * POST ?action=update         (body: {"profile":{...}})  — overwrite existing slug; requires same uploader_ip
 * POST ?action=check          (body: {"profile":{...}})
 * POST ?action=check_updates  (body: {"slugs":[{"slug":"x","knownTs":"..."}, ...]})
 */
error_reporting(0);
require_once __DIR__ . '/config.php';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(204); exit; }

function db(): PDO {
    static $pdo = null;
    if (!$pdo) {
        $pdo = new PDO('mysql:host=' . DB_HOST . ';dbname=' . DB_NAME . ';charset=utf8mb4',
            DB_USER, DB_PASS, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    }
    return $pdo;
}

function json_out($data, int $code = 200): void {
    http_response_code($code);
    echo json_encode(['ok' => $code < 400, 'data' => $data], JSON_UNESCAPED_UNICODE);
    exit;
}
function json_err(string $msg, int $code = 400): void {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $msg]);
    exit;
}

// ── Routing via ?action= ────────────────────────────────────────────────
$action = $_GET['action'] ?? '';
$method = $_SERVER['REQUEST_METHOD'];

try {
    if ($method === 'GET') {
        switch ($action) {
            case 'search':  handle_search(); break;
            case 'get':     handle_get($_GET['slug'] ?? ''); break;
            case 'recent':  handle_recent(); break;
            case 'popular': handle_popular(); break;
            case 'stats':   handle_stats(); break;
            case 'since':   handle_since(); break;
            default:        json_err('Unknown action. Use ?action=search|get|recent|popular|stats|since', 400);
        }
    } elseif ($method === 'POST') {
        switch ($action) {
            case 'upload':          handle_upload(); break;
            case 'update':          handle_update(); break;
            case 'check':           handle_check(); break;
            case 'check_updates':   handle_check_updates(); break;
            default:                json_err('Unknown action. Use ?action=upload|update|check|check_updates', 400);
        }
    } else {
        json_err('Method not allowed', 405);
    }
} catch (Exception $e) {
    json_err('Server error: ' . $e->getMessage(), 500);
}

// ── Handlers ────────────────────────────────────────────────────────────

function handle_search(): void {
    $q = trim($_GET['q'] ?? '');
    $cat = trim($_GET['category'] ?? '');
    $limit = min(200, max(1, intval($_GET['limit'] ?? 50)));
    $offset = max(0, intval($_GET['offset'] ?? 0));

    $where = ['flagged = 0']; $params = [];
    if (strlen($q) >= 2) {
        $where[] = '(name LIKE ? OR manufacturer LIKE ? OR slug LIKE ?)';
        $params[] = "%$q%"; $params[] = "%$q%"; $params[] = "%$q%";
    }
    if ($cat) { $where[] = 'category = ?'; $params[] = $cat; }

    $sql = 'SELECT slug, name, manufacturer, category, channel_count, color_mode, beam_width, downloads, upload_ts FROM profiles WHERE ' . implode(' AND ', $where) . ' ORDER BY downloads DESC, upload_ts DESC LIMIT ' . $limit . ' OFFSET ' . $offset;
    $stmt = db()->prepare($sql);
    $stmt->execute($params);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    json_out(['total' => count($rows), 'offset' => $offset, 'profiles' => $rows]);
}

function handle_get(string $slug): void {
    if (!$slug) json_err('slug parameter required');
    $stmt = db()->prepare('SELECT profile_json, downloads, upload_ts, channel_hash FROM profiles WHERE slug = ? AND flagged = 0');
    $stmt->execute([$slug]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$row) json_err('Profile not found', 404);
    db()->prepare('UPDATE profiles SET downloads = downloads + 1 WHERE slug = ?')->execute([$slug]);
    $profile = json_decode($row['profile_json'], true);
    $profile['communityDownloads'] = intval($row['downloads']) + 1;
    // #534 — provenance fields the client stamps as `_community` after
    // import so future check_updates calls can detect drift.
    $profile['communityUploadTs'] = $row['upload_ts'];
    $profile['communityChannelHash'] = $row['channel_hash'];
    json_out($profile);
}

function handle_recent(): void {
    $limit = min(50, max(1, intval($_GET['limit'] ?? 20)));
    $stmt = db()->prepare('SELECT slug, name, manufacturer, category, channel_count, downloads, upload_ts FROM profiles WHERE flagged = 0 ORDER BY upload_ts DESC LIMIT ' . $limit);
    $stmt->execute();
    json_out($stmt->fetchAll(PDO::FETCH_ASSOC));
}

function handle_popular(): void {
    $limit = min(50, max(1, intval($_GET['limit'] ?? 20)));
    $stmt = db()->prepare('SELECT slug, name, manufacturer, category, channel_count, downloads, upload_ts FROM profiles WHERE flagged = 0 ORDER BY downloads DESC LIMIT ' . $limit);
    $stmt->execute();
    json_out($stmt->fetchAll(PDO::FETCH_ASSOC));
}

function handle_stats(): void {
    $total = db()->query('SELECT COUNT(*) FROM profiles WHERE flagged = 0')->fetchColumn();
    $cats = db()->query('SELECT category, COUNT(*) as cnt FROM profiles WHERE flagged = 0 GROUP BY category ORDER BY cnt DESC')->fetchAll(PDO::FETCH_ASSOC);
    json_out(['total' => intval($total), 'categories' => $cats]);
}

function handle_since(): void {
    $ts = trim($_GET['ts'] ?? '');
    if (!$ts) json_err('ts parameter required');
    $limit = min(100, max(1, intval($_GET['limit'] ?? 100)));
    $stmt = db()->prepare('SELECT slug, name, manufacturer, category, channel_count, downloads, upload_ts FROM profiles WHERE upload_ts > ? AND flagged = 0 ORDER BY upload_ts ASC LIMIT ' . $limit);
    $stmt->execute([$ts]);
    json_out($stmt->fetchAll(PDO::FETCH_ASSOC));
}

function handle_upload(): void {
    check_rate_limit();
    $body = json_decode(file_get_contents('php://input'), true);
    if (!$body) json_err('Invalid JSON body');
    $profile = $body['profile'] ?? $body;
    $errors = validate_profile($profile);
    if ($errors) json_err('Validation: ' . implode('; ', $errors));

    $slug = $profile['id'];
    $hash = compute_channel_hash($profile);
    $json = json_encode($profile, JSON_UNESCAPED_UNICODE);
    if (strlen($json) > MAX_PROFILE_SIZE) json_err('Profile too large (max ' . MAX_PROFILE_SIZE . ' bytes)');

    $existing = db()->prepare('SELECT slug FROM profiles WHERE slug = ?');
    $existing->execute([$slug]);
    if ($existing->fetch()) json_err("Slug '$slug' already exists", 409);

    $dup = db()->prepare('SELECT slug, name FROM profiles WHERE channel_hash = ?');
    $dup->execute([$hash]);
    $dupRow = $dup->fetch(PDO::FETCH_ASSOC);
    if ($dupRow) {
        http_response_code(409);
        echo json_encode(['ok' => false, 'error' => 'Duplicate channels', 'duplicate_of' => $dupRow['slug'], 'duplicate_name' => $dupRow['name']]);
        exit;
    }

    $stmt = db()->prepare('INSERT INTO profiles (slug,name,manufacturer,category,channel_count,color_mode,beam_width,pan_range,tilt_range,profile_json,channel_hash,uploader_ip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)');
    $stmt->execute([
        $slug, $profile['name'] ?? '', $profile['manufacturer'] ?? 'Generic',
        $profile['category'] ?? 'par', intval($profile['channelCount'] ?? count($profile['channels'] ?? [])),
        $profile['colorMode'] ?? 'rgb', intval($profile['beamWidth'] ?? 0),
        intval($profile['panRange'] ?? 0), intval($profile['tiltRange'] ?? 0),
        $json, $hash, $_SERVER['REMOTE_ADDR'] ?? ''
    ]);
    increment_rate_limit();
    http_response_code(201);
    echo json_encode(['ok' => true, 'slug' => $slug, 'channel_hash' => $hash]);
    exit;
}

function handle_update(): void {
    // Overwrite an existing profile row, keyed by slug. Uploader-IP must
    // match the original uploader of that slug — "the same person can
    // update their own submission". If the IP has changed (VPN, mobile
    // network, etc.) the operator can always DELETE the row in cPanel
    // phpMyAdmin and re-submit via ?action=upload.
    check_rate_limit();
    $body = json_decode(file_get_contents('php://input'), true);
    if (!$body) json_err('Invalid JSON body');
    $profile = $body['profile'] ?? $body;
    $errors = validate_profile($profile);
    if ($errors) json_err('Validation: ' . implode('; ', $errors));

    $slug = $profile['id'];
    $hash = compute_channel_hash($profile);
    $json = json_encode($profile, JSON_UNESCAPED_UNICODE);
    if (strlen($json) > MAX_PROFILE_SIZE) json_err('Profile too large (max ' . MAX_PROFILE_SIZE . ' bytes)');

    $existing = db()->prepare('SELECT slug, uploader_ip FROM profiles WHERE slug = ?');
    $existing->execute([$slug]);
    $row = $existing->fetch(PDO::FETCH_ASSOC);
    if (!$row) json_err("Slug '$slug' does not exist — use ?action=upload for new profiles", 404);

    $caller = $_SERVER['REMOTE_ADDR'] ?? '';
    if ($row['uploader_ip'] && $caller && $row['uploader_ip'] !== $caller) {
        json_err("Update forbidden: only the original uploader (same IP) can overwrite this slug", 403);
    }

    // Allow channel-hash collision against SELF (slug match), reject against
    // any OTHER slug — otherwise an update could stomp onto a different
    // profile's identity.
    $dup = db()->prepare('SELECT slug, name FROM profiles WHERE channel_hash = ? AND slug != ?');
    $dup->execute([$hash, $slug]);
    $dupRow = $dup->fetch(PDO::FETCH_ASSOC);
    if ($dupRow) {
        http_response_code(409);
        echo json_encode(['ok' => false, 'error' => 'Duplicate channels match another slug',
                          'duplicate_of' => $dupRow['slug'], 'duplicate_name' => $dupRow['name']]);
        exit;
    }

    $stmt = db()->prepare('UPDATE profiles SET name=?, manufacturer=?, category=?, channel_count=?, color_mode=?, beam_width=?, pan_range=?, tilt_range=?, profile_json=?, channel_hash=? WHERE slug=?');
    $stmt->execute([
        $profile['name'] ?? '', $profile['manufacturer'] ?? 'Generic',
        $profile['category'] ?? 'par', intval($profile['channelCount'] ?? count($profile['channels'] ?? [])),
        $profile['colorMode'] ?? 'rgb', intval($profile['beamWidth'] ?? 0),
        intval($profile['panRange'] ?? 0), intval($profile['tiltRange'] ?? 0),
        $json, $hash, $slug
    ]);
    increment_rate_limit();
    echo json_encode(['ok' => true, 'slug' => $slug, 'channel_hash' => $hash, 'updated' => true]);
    exit;
}

function handle_check(): void {
    check_rate_limit();
    $body = json_decode(file_get_contents('php://input'), true);
    if (!$body) json_err('Invalid JSON body');
    $profile = $body['profile'] ?? $body;
    $hash = compute_channel_hash($profile);
    $slug = $profile['id'] ?? '';
    $result = ['slug_available' => true, 'duplicate' => false];
    if ($slug) {
        $s = db()->prepare('SELECT slug FROM profiles WHERE slug = ?');
        $s->execute([$slug]);
        if ($s->fetch()) $result['slug_available'] = false;
    }
    $d = db()->prepare('SELECT slug, name FROM profiles WHERE channel_hash = ?');
    $d->execute([$hash]);
    $dup = $d->fetch(PDO::FETCH_ASSOC);
    if ($dup) { $result['duplicate'] = true; $result['duplicate_of'] = $dup['slug']; $result['duplicate_name'] = $dup['name']; }
    json_out($result);
}

function handle_check_updates(): void {
    // Batch check — given a list of locally-held slugs + the upload_ts
    // the client last saw, return only the ones that have a newer
    // upload_ts on the server. Rate-limit bucket is shared with other
    // POSTs so a polling client can't hammer the DB.
    check_rate_limit();
    $body = json_decode(file_get_contents('php://input'), true);
    if (!$body) json_err('Invalid JSON body');
    $slugs = $body['slugs'] ?? [];
    if (!is_array($slugs) || !$slugs) {
        json_out(['updates' => []]);
        return;
    }
    // Cap the batch size so a malformed client can't pull the whole table.
    $slugs = array_slice($slugs, 0, 200);

    $wantedSlugs = [];
    $known = [];
    foreach ($slugs as $row) {
        $s = trim((string)($row['slug'] ?? ''));
        if (!$s) continue;
        $wantedSlugs[] = $s;
        $known[$s] = (string)($row['knownTs'] ?? '');
    }
    if (!$wantedSlugs) { json_out(['updates' => []]); return; }

    $placeholders = implode(',', array_fill(0, count($wantedSlugs), '?'));
    $stmt = db()->prepare("SELECT slug, upload_ts, channel_hash, name FROM profiles WHERE slug IN ($placeholders) AND flagged = 0");
    $stmt->execute($wantedSlugs);
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);

    $updates = [];
    foreach ($rows as $row) {
        $serverTs = (string)($row['upload_ts'] ?? '');
        $clientTs = $known[$row['slug']] ?? '';
        // Strict newer comparison on the ISO-like timestamp string.
        if ($serverTs !== '' && $serverTs > $clientTs) {
            $updates[] = [
                'slug'        => $row['slug'],
                'name'        => $row['name'],
                'uploadTs'    => $serverTs,
                'channelHash' => $row['channel_hash'],
            ];
        }
    }
    json_out(['updates' => $updates, 'checked' => count($wantedSlugs)]);
}

// ── Helpers ─────────────────────────────────────────────────────────────

function compute_channel_hash(array $profile): string {
    $channels = $profile['channels'] ?? [];
    usort($channels, fn($a, $b) => ($a['offset'] ?? 0) <=> ($b['offset'] ?? 0));
    $parts = [];
    foreach ($channels as $ch) {
        $capTypes = [];
        foreach ($ch['capabilities'] ?? [] as $cap) { $capTypes[] = $cap['type'] ?? 'Generic'; }
        sort($capTypes);
        $parts[] = ($ch['offset'] ?? 0) . ':' . ($ch['type'] ?? 'dimmer') . ':' . ($ch['bits'] ?? 8) . ':' . implode(',', array_unique($capTypes));
    }
    return sha1(implode('|', $parts));
}

function validate_profile(array $p): array {
    $errors = [];
    if (empty($p['id']) || !preg_match('/^[a-z0-9][a-z0-9\-]{1,127}$/', $p['id'])) $errors[] = 'Invalid slug';
    if (empty($p['name'])) $errors[] = 'Name required';
    if (empty($p['channels']) || !is_array($p['channels'])) $errors[] = 'Channels required';
    if (isset($p['name']) && strip_tags($p['name']) !== $p['name']) $errors[] = 'Name contains HTML';
    // Column limits — prefer a 400 with a specific field message over a
    // bare SQLSTATE[22001] from the DB driver. Lengths MUST match
    // schema.sql so adding a value that needs more headroom is a
    // schema-migration decision, not a silent truncate.
    if (isset($p['colorMode']) && strlen($p['colorMode']) > 32) {
        $errors[] = 'colorMode too long (max 32 chars — bump schema if you add a new mode)';
    }
    if (isset($p['category']) && strlen($p['category']) > 20) {
        $errors[] = 'category too long (max 20 chars)';
    }
    if (isset($p['manufacturer']) && strlen($p['manufacturer']) > 100) {
        $errors[] = 'manufacturer too long (max 100 chars)';
    }
    if (isset($p['name']) && strlen($p['name']) > 200) {
        $errors[] = 'name too long (max 200 chars)';
    }
    return $errors;
}

function check_rate_limit(): void {
    $ip = $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';
    $stmt = db()->prepare('SELECT upload_count, window_start FROM rate_limits WHERE ip = ?');
    $stmt->execute([$ip]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if ($row) {
        if (time() - strtotime($row['window_start']) > RATE_LIMIT_WINDOW) {
            db()->prepare('UPDATE rate_limits SET upload_count=0, window_start=NOW() WHERE ip=?')->execute([$ip]);
        } elseif (intval($row['upload_count']) >= RATE_LIMIT_UPLOADS) {
            json_err('Rate limit exceeded', 429);
        }
    }
}

function increment_rate_limit(): void {
    $ip = $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';
    db()->prepare('INSERT INTO rate_limits (ip,upload_count,window_start) VALUES (?,1,NOW()) ON DUPLICATE KEY UPDATE upload_count=upload_count+1')->execute([$ip]);
}
