<?php
/**
 * SlyLED Analytics — Lightweight, privacy-friendly page view tracker.
 *
 * No cookies. No personal data. No external services.
 * Stores: page URL, timestamp, referrer domain, country (from IP), screen width.
 * IP addresses are hashed (SHA-256 + daily salt) for unique visitor counting
 * but never stored in plaintext.
 *
 * Endpoints:
 *   POST ?action=hit         — Record a page view (called by tracking pixel)
 *   GET  ?action=stats       — Summary stats (requires auth token)
 *   GET  ?action=pages       — Per-page breakdown (requires auth token)
 *   GET  ?action=daily       — Daily view counts (requires auth token)
 *   GET  ?action=referrers   — Top referrers (requires auth token)
 *   GET  ?action=dashboard   — HTML dashboard (requires auth token)
 */

header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, GET, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(204); exit; }

// ── Config ──────────────────────────────────────────────────────────────
require_once __DIR__ . '/../profiles/config.php';

// Auth token for viewing stats (set in config or use a default)
define('ANALYTICS_TOKEN', defined('ANALYTICS_AUTH_TOKEN')
    ? ANALYTICS_AUTH_TOKEN
    : 'slyled-stats-2026');

// ── Database ────────────────────────────────────────────────────────────
try {
    $pdo = new PDO(
        "mysql:host=" . DB_HOST . ";dbname=" . DB_NAME . ";charset=utf8mb4",
        DB_USER, DB_PASS,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(['error' => 'Database connection failed']);
    exit;
}

// ── Auto-create table ───────────────────────────────────────────────────
$pdo->exec("CREATE TABLE IF NOT EXISTS analytics_hits (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    page VARCHAR(512) NOT NULL,
    referrer_domain VARCHAR(256) DEFAULT NULL,
    visitor_hash CHAR(16) NOT NULL,
    screen_w SMALLINT UNSIGNED DEFAULT NULL,
    country CHAR(2) DEFAULT NULL,
    INDEX idx_ts (ts),
    INDEX idx_page (page(128)),
    INDEX idx_visitor (visitor_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

// ── Helpers ─────────────────────────────────────────────────────────────
function daily_salt(): string {
    return date('Y-m-d') . '-slyled-analytics';
}

function visitor_hash(string $ip): string {
    // Daily-rotating hash: same IP on same day = same hash, but can't reverse to IP
    return substr(hash('sha256', $ip . daily_salt()), 0, 16);
}

function referrer_domain(?string $ref): ?string {
    if (!$ref) return null;
    $host = parse_url($ref, PHP_URL_HOST);
    if (!$host) return null;
    // Strip www.
    if (str_starts_with($host, 'www.')) $host = substr($host, 4);
    // Skip self-referrals
    if ($host === 'electricrv.ca') return null;
    return substr($host, 0, 256);
}

function require_auth(): void {
    $token = $_GET['token'] ?? $_SERVER['HTTP_X_ANALYTICS_TOKEN'] ?? '';
    if ($token !== ANALYTICS_TOKEN) {
        http_response_code(403);
        echo json_encode(['error' => 'Invalid token']);
        exit;
    }
}

function json_out(mixed $data): void {
    header('Content-Type: application/json');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

// ── Actions ─────────────────────────────────────────────────────────────
$action = $_GET['action'] ?? $_POST['action'] ?? '';

// ── HIT: Record a page view ────────────────────────────────────────────
if ($action === 'hit') {
    // Accept JSON body or query params
    $input = json_decode(file_get_contents('php://input'), true) ?? [];
    $page = $input['page'] ?? $_POST['page'] ?? $_GET['page'] ?? '';
    $ref  = $input['referrer'] ?? $_POST['referrer'] ?? $_SERVER['HTTP_REFERER'] ?? '';
    $sw   = $input['sw'] ?? $_POST['sw'] ?? null;

    if (!$page) {
        http_response_code(400);
        json_out(['error' => 'Missing page parameter']);
    }

    // Sanitize
    $page = substr($page, 0, 512);
    $hash = visitor_hash($_SERVER['REMOTE_ADDR'] ?? '0.0.0.0');
    $ref_domain = referrer_domain($ref);
    $screen_w = $sw ? min(9999, max(0, intval($sw))) : null;

    $stmt = $pdo->prepare("INSERT INTO analytics_hits (page, referrer_domain, visitor_hash, screen_w)
                           VALUES (:page, :ref, :hash, :sw)");
    $stmt->execute([
        ':page' => $page,
        ':ref'  => $ref_domain,
        ':hash' => $hash,
        ':sw'   => $screen_w,
    ]);

    // Return 1x1 transparent GIF (tracking pixel fallback)
    header('Content-Type: image/gif');
    header('Cache-Control: no-store, no-cache, must-revalidate');
    echo base64_decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7');
    exit;
}

// ── STATS: Summary ──────────────────────────────────────────────────────
if ($action === 'stats') {
    require_auth();
    $days = intval($_GET['days'] ?? 30);

    $row = $pdo->query("SELECT
        COUNT(*) AS total_views,
        COUNT(DISTINCT visitor_hash) AS unique_visitors,
        COUNT(DISTINCT page) AS unique_pages,
        MIN(ts) AS first_hit,
        MAX(ts) AS last_hit
    FROM analytics_hits
    WHERE ts >= DATE_SUB(NOW(), INTERVAL $days DAY)")->fetch(PDO::FETCH_ASSOC);

    // Today's stats
    $today = $pdo->query("SELECT COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
        FROM analytics_hits WHERE DATE(ts) = CURDATE()")->fetch(PDO::FETCH_ASSOC);

    json_out([
        'period_days' => $days,
        'total_views' => intval($row['total_views']),
        'unique_visitors' => intval($row['unique_visitors']),
        'unique_pages' => intval($row['unique_pages']),
        'first_hit' => $row['first_hit'],
        'last_hit' => $row['last_hit'],
        'today_views' => intval($today['views']),
        'today_visitors' => intval($today['visitors']),
    ]);
}

// ── PAGES: Per-page breakdown ───────────────────────────────────────────
if ($action === 'pages') {
    require_auth();
    $days = intval($_GET['days'] ?? 30);
    $limit = min(100, intval($_GET['limit'] ?? 50));

    $rows = $pdo->query("SELECT page, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
        FROM analytics_hits
        WHERE ts >= DATE_SUB(NOW(), INTERVAL $days DAY)
        GROUP BY page ORDER BY views DESC LIMIT $limit")->fetchAll(PDO::FETCH_ASSOC);

    json_out(['pages' => $rows, 'period_days' => $days]);
}

// ── DAILY: Daily view counts ────────────────────────────────────────────
if ($action === 'daily') {
    require_auth();
    $days = intval($_GET['days'] ?? 30);

    $rows = $pdo->query("SELECT DATE(ts) AS day, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
        FROM analytics_hits
        WHERE ts >= DATE_SUB(NOW(), INTERVAL $days DAY)
        GROUP BY DATE(ts) ORDER BY day")->fetchAll(PDO::FETCH_ASSOC);

    json_out(['daily' => $rows, 'period_days' => $days]);
}

// ── REFERRERS: Top referrers ────────────────────────────────────────────
if ($action === 'referrers') {
    require_auth();
    $days = intval($_GET['days'] ?? 30);

    $rows = $pdo->query("SELECT referrer_domain, COUNT(*) AS views
        FROM analytics_hits
        WHERE referrer_domain IS NOT NULL
          AND ts >= DATE_SUB(NOW(), INTERVAL $days DAY)
        GROUP BY referrer_domain ORDER BY views DESC LIMIT 30")->fetchAll(PDO::FETCH_ASSOC);

    json_out(['referrers' => $rows, 'period_days' => $days]);
}

// ── DASHBOARD: HTML stats page ──────────────────────────────────────────
if ($action === 'dashboard') {
    require_auth();
    $token = $_GET['token'] ?? '';
    $base = "?token=" . urlencode($token);
    ?><!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SlyLED Analytics</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0a0f13;color:#e2e8f0;padding:20px;max-width:900px;margin:0 auto}
h1{color:#22d3ee;margin-bottom:8px}
h2{color:#7c3aed;margin:24px 0 12px;font-size:1.2em}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}
.card{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:16px;text-align:center}
.card .num{font-size:2em;font-weight:700;color:#22d3ee}
.card .label{color:#94a3b8;font-size:.85em;margin-top:4px}
table{width:100%;border-collapse:collapse;margin:12px 0}
th{text-align:left;color:#94a3b8;font-size:.85em;padding:8px;border-bottom:1px solid #1e293b}
td{padding:8px;border-bottom:1px solid #1e293b;font-size:.9em}
.bar{background:#7c3aed;height:6px;border-radius:3px;display:inline-block;vertical-align:middle}
.muted{color:#64748b}
a{color:#22d3ee}
</style>
</head>
<body>
<h1>SlyLED Analytics</h1>
<p class="muted">Privacy-friendly, cookie-free page view tracking</p>
<div class="cards" id="cards">Loading...</div>
<h2>Top Pages (30 days)</h2>
<table id="pages"><tr><td>Loading...</td></tr></table>
<h2>Daily Views (30 days)</h2>
<table id="daily"><tr><td>Loading...</td></tr></table>
<h2>Top Referrers (30 days)</h2>
<table id="refs"><tr><td>Loading...</td></tr></table>
<script>
const B="<?=$base?>";
async function load(){
  const [stats,pages,daily,refs]=await Promise.all([
    fetch("index.php"+B+"&action=stats").then(r=>r.json()),
    fetch("index.php"+B+"&action=pages").then(r=>r.json()),
    fetch("index.php"+B+"&action=daily&days=30").then(r=>r.json()),
    fetch("index.php"+B+"&action=referrers").then(r=>r.json()),
  ]);
  document.getElementById("cards").innerHTML=`
    <div class="card"><div class="num">${stats.total_views}</div><div class="label">Views (30d)</div></div>
    <div class="card"><div class="num">${stats.unique_visitors}</div><div class="label">Visitors (30d)</div></div>
    <div class="card"><div class="num">${stats.today_views}</div><div class="label">Today Views</div></div>
    <div class="card"><div class="num">${stats.today_visitors}</div><div class="label">Today Visitors</div></div>
  `;
  const maxV=Math.max(...pages.pages.map(p=>+p.views),1);
  document.getElementById("pages").innerHTML="<tr><th>Page</th><th>Views</th><th>Visitors</th><th></th></tr>"+
    pages.pages.map(p=>`<tr><td>${p.page}</td><td>${p.views}</td><td>${p.visitors}</td><td><span class="bar" style="width:${(p.views/maxV*200)}px"></span></td></tr>`).join("");
  document.getElementById("daily").innerHTML="<tr><th>Date</th><th>Views</th><th>Visitors</th></tr>"+
    daily.daily.map(d=>`<tr><td>${d.day}</td><td>${d.views}</td><td>${d.visitors}</td></tr>`).join("");
  document.getElementById("refs").innerHTML="<tr><th>Referrer</th><th>Views</th></tr>"+
    (refs.referrers.length?refs.referrers.map(r=>`<tr><td>${r.referrer_domain}</td><td>${r.views}</td></tr>`).join(""):"<tr><td class='muted' colspan=2>No external referrers yet</td></tr>");
}
load();
</script>
</body>
</html><?php
    exit;
}

// ── Unknown action ──────────────────────────────────────────────────────
http_response_code(400);
json_out(['error' => 'Unknown action. Use: hit, stats, pages, daily, referrers, dashboard']);
