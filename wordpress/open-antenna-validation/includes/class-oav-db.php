<?php
/**
 * Storage: dedicated tables (never post metadata), quota of the plugin tables, tokens.
 *
 * Tables: oav_stations (one per station of an operator, with its token), oav_campaigns
 * (metadata as sent by the station, plus what the site changes: title, comment, hidden),
 * oav_windows (window medians per signal and channel; key = campaign, signal, channel, start).
 */

if (!defined('ABSPATH')) {
    exit;
}

class OAV_DB
{
    public static function table(string $name): string
    {
        global $wpdb;
        return $wpdb->prefix . 'oav_' . $name;
    }

    public static function install(): void
    {
        global $wpdb;
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        $charset = $wpdb->get_charset_collate();
        $stations = self::table('stations');
        $campaigns = self::table('campaigns');
        $windows = self::table('windows');
        // dbDelta: two spaces after PRIMARY KEY, one field per line.
        dbDelta("CREATE TABLE $stations (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  operator_id bigint(20) unsigned NOT NULL,
  name varchar(120) NOT NULL,
  token_hash char(64) NOT NULL DEFAULT '',
  token_hint varchar(8) NOT NULL DEFAULT '',
  station_uid varchar(64) DEFAULT NULL,
  callsign varchar(20) NOT NULL DEFAULT '',
  locator varchar(6) NOT NULL DEFAULT '',
  software varchar(60) NOT NULL DEFAULT '',
  status longtext,
  last_seen datetime DEFAULT NULL,
  created datetime NOT NULL,
  PRIMARY KEY  (id),
  KEY token_hash (token_hash),
  KEY operator_id (operator_id)
) $charset;");
        dbDelta("CREATE TABLE $campaigns (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  uuid varchar(64) NOT NULL,
  station_id bigint(20) unsigned NOT NULL,
  operator_id bigint(20) unsigned NOT NULL,
  title varchar(200) NOT NULL DEFAULT '',
  title_site varchar(200) NOT NULL DEFAULT '',
  comment text,
  callsign varchar(20) NOT NULL DEFAULT '',
  band varchar(20) NOT NULL DEFAULT '',
  low_hz double NOT NULL DEFAULT 0,
  high_hz double NOT NULL DEFAULT 0,
  reception_mode varchar(20) NOT NULL DEFAULT '',
  measure_mode varchar(10) NOT NULL DEFAULT '',
  sdr varchar(200) NOT NULL DEFAULT '',
  n_channels tinyint(3) unsigned NOT NULL DEFAULT 2,
  types varchar(400) NOT NULL DEFAULT '',
  qualified tinyint(1) NOT NULL DEFAULT 0,
  aa tinyint(1) NOT NULL DEFAULT 0,
  state varchar(20) NOT NULL DEFAULT '',
  start_ms bigint(20) DEFAULT NULL,
  end_ms bigint(20) DEFAULT NULL,
  visibility varchar(10) NOT NULL DEFAULT 'public',
  hidden tinyint(1) NOT NULL DEFAULT 0,
  deleted tinyint(1) NOT NULL DEFAULT 0,
  step_ms int(10) unsigned NOT NULL DEFAULT 60000,
  meta longtext,
  row_count int(10) unsigned NOT NULL DEFAULT 0,
  bytes bigint(20) unsigned NOT NULL DEFAULT 0,
  complete tinyint(1) NOT NULL DEFAULT 0,
  updated datetime NOT NULL,
  PRIMARY KEY  (id),
  UNIQUE KEY uuid (uuid),
  KEY station_id (station_id),
  KEY operator_id (operator_id)
) $charset;");
        dbDelta("CREATE TABLE $windows (
  campaign_id bigint(20) unsigned NOT NULL,
  sig int(10) unsigned NOT NULL,
  ch tinyint(3) unsigned NOT NULL,
  t bigint(20) NOT NULL,
  rssi float DEFAULT NULL,
  noise float DEFAULT NULL,
  PRIMARY KEY  (campaign_id,sig,ch,t)
) $charset;");
        add_role('oav_operator', 'Opérateur (antennes HF)', ['read' => true, OAV_CAP => true]);
        add_role('oav_pending', 'Opérateur en attente (antennes HF)', ['read' => true]);
        $admin = get_role('administrator');
        if ($admin) {
            $admin->add_cap(OAV_CAP);
        }
        add_option('oav_quota_mb', OAV_DEFAULT_QUOTA_MB);
        add_option('oav_package_mb', OAV_DEFAULT_PACKAGE_MB);
        add_option('oav_demo', 'auto');
        add_option('oav_theme', 'light');
        update_option('oav_db_version', OAV_DB_VERSION);
    }

    public static function maybe_upgrade(): void
    {
        if ((int) get_option('oav_db_version', 0) < OAV_DB_VERSION) {
            self::install();
        }
    }

    // --- settings ------------------------------------------------------------------------

    public static function quota_mb(): int
    {
        return max(10, (int) get_option('oav_quota_mb', OAV_DEFAULT_QUOTA_MB));
    }

    /** Largest package of one campaign in bytes (0: no limit). */
    public static function package_max_bytes(): int
    {
        return (int) round(max(0.0, (float) get_option('oav_package_mb', OAV_DEFAULT_PACKAGE_MB)) * 1048576);
    }

    /** auto: demonstrations while no real campaign is public; always; never. */
    public static function demo_mode(): string
    {
        $mode = (string) get_option('oav_demo', 'auto');
        return in_array($mode, ['auto', 'always', 'never'], true) ? $mode : 'auto';
    }

    public static function theme(): string
    {
        $theme = (string) get_option('oav_theme', 'light');
        return in_array($theme, ['light', 'dark', 'auto'], true) ? $theme : 'light';
    }

    public static function now(): string
    {
        return current_time('mysql', true);
    }

    // --- storage guard ---------------------------------------------------------------------

    public static function storage(bool $fresh = false): array
    {
        $cached = $fresh ? false : get_transient('oav_storage');
        if (is_array($cached)) {
            return $cached;
        }
        global $wpdb;
        $rows = $wpdb->get_results($wpdb->prepare(
            'SELECT table_name AS name, COALESCE(data_length + index_length, 0) AS bytes'
            . ' FROM information_schema.TABLES WHERE table_schema = %s AND table_name LIKE %s',
            DB_NAME,
            $wpdb->esc_like($wpdb->prefix . 'oav_') . '%'
        ));
        $used = 0;
        foreach ($rows as $r) {
            $used += (int) $r->bytes;
        }
        $quota = self::quota_mb() * 1048576;
        $storage = ['used_bytes' => $used, 'quota_bytes' => $quota, 'full' => $used >= $quota];
        set_transient('oav_storage', $storage, 5 * MINUTE_IN_SECONDS);
        return $storage;
    }

    // --- stations and tokens -----------------------------------------------------------------

    public static function hash_token(string $token): string
    {
        return hash('sha256', $token);
    }

    /** New random token for a station; only its hash is kept. Returns the token, shown once. */
    public static function new_token(int $station_id): string
    {
        global $wpdb;
        $token = 'oav_' . bin2hex(random_bytes(20));
        $wpdb->update(self::table('stations'), [
            'token_hash' => self::hash_token($token),
            'token_hint' => substr($token, -4),
            // A new token may serve another computer: the station identity is bound again.
            'station_uid' => null,
        ], ['id' => $station_id]);
        return $token;
    }

    public static function create_station(int $operator_id, string $name): array
    {
        global $wpdb;
        $wpdb->insert(self::table('stations'), [
            'operator_id' => $operator_id,
            'name' => $name,
            'created' => self::now(),
        ]);
        $id = (int) $wpdb->insert_id;
        return [$id, self::new_token($id)];
    }

    public static function station_by_token(string $token): ?object
    {
        if (strlen($token) < 20) {
            return null;
        }
        global $wpdb;
        $row = $wpdb->get_row($wpdb->prepare(
            'SELECT * FROM ' . self::table('stations') . ' WHERE token_hash = %s',
            self::hash_token($token)
        ));
        return $row ?: null;
    }

    public static function station(int $id): ?object
    {
        global $wpdb;
        $row = $wpdb->get_row($wpdb->prepare('SELECT * FROM ' . self::table('stations') . ' WHERE id = %d', $id));
        return $row ?: null;
    }

    /** Stations of an operator (null: all), with the operator's display name. */
    public static function stations(?int $operator_id = null): array
    {
        global $wpdb;
        $sql = 'SELECT s.*, u.display_name AS operator_name FROM ' . self::table('stations')
            . " s LEFT JOIN {$wpdb->users} u ON u.ID = s.operator_id";
        if ($operator_id !== null) {
            $sql .= $wpdb->prepare(' WHERE s.operator_id = %d', $operator_id);
        }
        return $wpdb->get_results($sql . ' ORDER BY s.name');
    }

    public static function delete_station(int $id): void
    {
        global $wpdb;
        foreach ($wpdb->get_col($wpdb->prepare('SELECT id FROM ' . self::table('campaigns') . ' WHERE station_id = %d', $id)) as $cid) {
            self::delete_campaign((int) $cid, true);
        }
        $wpdb->delete(self::table('stations'), ['id' => $id]);
        delete_transient('oav_storage');
    }

    /** What the station says about itself, at each request. */
    public static function station_seen(object $station, array $info): void
    {
        global $wpdb;
        $wpdb->update(self::table('stations'), [
            'station_uid' => (string) $info['id'],
            'callsign' => substr((string) ($info['callsign'] ?? ''), 0, 20),
            'locator' => substr((string) ($info['locator'] ?? ''), 0, 6),
            'software' => substr((string) ($info['software'] ?? ''), 0, 60),
            'status' => wp_json_encode($info['status'] ?? null),
            'last_seen' => self::now(),
        ], ['id' => $station->id]);
    }

    // --- campaigns ---------------------------------------------------------------------------

    public static function campaign(string $uuid): ?object
    {
        global $wpdb;
        $row = $wpdb->get_row($wpdb->prepare('SELECT * FROM ' . self::table('campaigns') . ' WHERE uuid = %s', $uuid));
        return $row ?: null;
    }

    public static function campaign_by_id(int $id): ?object
    {
        global $wpdb;
        $row = $wpdb->get_row($wpdb->prepare('SELECT * FROM ' . self::table('campaigns') . ' WHERE id = %d', $id));
        return $row ?: null;
    }

    /** Fields of the campaign row taken from the metadata sent by the station. */
    private static function meta_fields(array $meta): array
    {
        $c = $meta['campaign'];
        $types = [];
        foreach ($meta['channels'] as $ch) {
            if (!empty($ch['type_code'])) {
                $types[] = (string) $ch['type_code'];
            }
        }
        $types = array_values(array_unique($types));
        sort($types);
        return [
            'title' => substr((string) $c['title'], 0, 200),
            'callsign' => substr((string) $c['operator'], 0, 20),
            'band' => substr((string) $c['band'], 0, 20),
            'low_hz' => (float) $c['low_hz'],
            'high_hz' => (float) $c['high_hz'],
            'reception_mode' => (string) $c['reception_mode'],
            'measure_mode' => (string) $c['measure_mode'],
            'sdr' => substr((string) $c['sdr'], 0, 200),
            'n_channels' => (int) $c['n_channels'],
            'types' => substr(implode(',', $types), 0, 400),
            'qualified' => (!empty($c['margins']) || !empty($c['qualification'])) ? 1 : 0,
            'aa' => !empty($c['aa']) ? 1 : 0,
            'state' => (string) $c['state'],
            'start_ms' => isset($c['start_ms']) ? (int) $c['start_ms'] : null,
            'end_ms' => isset($c['end_ms']) ? (int) $c['end_ms'] : (isset($c['planned_end_ms']) ? (int) $c['planned_end_ms'] : null),
        ];
    }

    /**
     * Store one campaign of an ingest request, atomically. Windows already received are
     * replaced (idempotent). Refused (413) when its package would exceed the site's limit.
     */
    public static function ingest_campaign(object $station, array $c)
    {
        global $wpdb;
        $row = self::campaign((string) $c['uuid']);
        if ($row !== null && (int) $row->station_id !== (int) $station->id) {
            return new WP_Error('oav_owner', 'Cette campagne appartient à une autre station.', ['status' => 403]);
        }
        if ($row !== null && (int) $row->deleted === 1) {
            // Deleted on the site: the station is told it was received, nothing is kept.
            return ['rows' => 0, 'bytes' => 0, 'deleted' => true];
        }
        $meta_json = isset($c['meta']) ? wp_json_encode($c['meta'], JSON_UNESCAPED_UNICODE) : ($row->meta ?? null);
        if (!$meta_json) {
            return new WP_Error('oav_meta', 'Premier envoi de la campagne sans ses métadonnées.', ['status' => 400]);
        }
        $fields = [
            'visibility' => $c['visibility'] === 'unlisted' ? 'unlisted' : 'public',
            'step_ms' => max(1000, (int) $c['step_ms']),
            'complete' => !empty($c['complete']) ? 1 : 0,
            'updated' => self::now(),
        ];
        if (isset($c['meta'])) {
            $fields = array_merge($fields, self::meta_fields($c['meta']), ['meta' => $meta_json]);
        }
        $campaigns = self::table('campaigns');
        $wpdb->query('START TRANSACTION');
        if ($row === null) {
            $ok = $wpdb->insert($campaigns, array_merge($fields, [
                'uuid' => (string) $c['uuid'],
                'station_id' => (int) $station->id,
                'operator_id' => (int) $station->operator_id,
            ]));
            $id = (int) $wpdb->insert_id;
        } else {
            $ok = $wpdb->update($campaigns, $fields, ['id' => $row->id]) !== false;
            $id = (int) $row->id;
        }
        if (!$ok) {
            $wpdb->query('ROLLBACK');
            return new WP_Error('oav_db', 'Écriture de la campagne impossible.', ['status' => 500]);
        }
        $windows = self::table('windows');
        foreach (array_chunk($c['rows'], 500) as $chunk) {
            $values = [];
            foreach ($chunk as $r) {
                // Numbers only, cast here: no text from the request reaches the query.
                $values[] = sprintf('(%d,%d,%d,%d,%s,%s)', $id, (int) $r[0], (int) $r[1], (int) $r[2],
                    self::number($r[3]), self::number($r[4]));
            }
            $done = $wpdb->query("INSERT INTO $windows (campaign_id, sig, ch, t, rssi, noise) VALUES "
                . implode(',', $values) . ' ON DUPLICATE KEY UPDATE rssi = VALUES(rssi), noise = VALUES(noise)');
            if ($done === false) {
                $wpdb->query('ROLLBACK');
                return new WP_Error('oav_db', 'Écriture des fenêtres impossible.', ['status' => 500]);
            }
        }
        $count = (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM $windows WHERE campaign_id = %d", $id));
        $bytes = strlen($meta_json) + $count * OAV_ROW_BYTES;
        $limit = self::package_max_bytes();
        if ($limit > 0 && $bytes > $limit) {
            $wpdb->query('ROLLBACK');
            return new WP_Error('oav_package', sprintf(
                'Paquet de campagne trop gros pour le site : %s pour %s autorisés.',
                size_format($bytes, 1),
                size_format($limit, 1)
            ), ['status' => 413, 'campaign' => (string) $c['uuid'], 'limit_bytes' => $limit]);
        }
        $wpdb->update($campaigns, ['row_count' => $count, 'bytes' => $bytes], ['id' => $id]);
        $wpdb->query('COMMIT');
        return ['rows' => $count, 'bytes' => $bytes];
    }

    private static function number($value): string
    {
        return is_int($value) || is_float($value) ? sprintf('%.2F', (float) $value) : 'NULL';
    }

    public static function delete_campaign(int $id, bool $forget = false): void
    {
        global $wpdb;
        $wpdb->delete(self::table('windows'), ['campaign_id' => $id]);
        if ($forget) {
            $wpdb->delete(self::table('campaigns'), ['id' => $id]);
        } else {
            // Kept as a tombstone: the station would otherwise send it again.
            $wpdb->update(self::table('campaigns'), ['deleted' => 1, 'meta' => null, 'row_count' => 0, 'bytes' => 0], ['id' => $id]);
        }
        delete_transient('oav_storage');
    }

    /** Campaigns for the administration (null: all operators). */
    public static function campaigns_admin(?int $operator_id = null): array
    {
        global $wpdb;
        $sql = 'SELECT c.id, c.uuid, c.title, c.title_site, c.comment, c.band, c.reception_mode, c.n_channels,'
            . ' c.state, c.start_ms, c.end_ms, c.visibility, c.hidden, c.row_count, c.bytes, c.complete, c.aa,'
            . ' c.updated, c.callsign, s.name AS station_name, u.display_name AS operator_name FROM '
            . self::table('campaigns') . ' c LEFT JOIN ' . self::table('stations') . ' s ON s.id = c.station_id'
            . " LEFT JOIN {$wpdb->users} u ON u.ID = c.operator_id WHERE c.deleted = 0";
        if ($operator_id !== null) {
            $sql .= $wpdb->prepare(' AND c.operator_id = %d', $operator_id);
        }
        return $wpdb->get_results($sql . ' ORDER BY c.start_ms DESC');
    }

    /** Campaigns of the public list: published, not hidden, listed. */
    public static function campaigns_public(): array
    {
        global $wpdb;
        $rows = $wpdb->get_results('SELECT c.uuid, c.title, c.title_site, c.comment, c.callsign, c.band, c.low_hz, c.high_hz,'
            . ' c.reception_mode, c.measure_mode, c.sdr, c.n_channels, c.types, c.qualified, c.aa, c.state, c.start_ms,'
            . ' c.end_ms, u.display_name AS operator_name FROM ' . self::table('campaigns') . ' c'
            . " LEFT JOIN {$wpdb->users} u ON u.ID = c.operator_id"
            . " WHERE c.deleted = 0 AND c.hidden = 0 AND c.visibility = 'public' AND c.meta IS NOT NULL"
            . ' ORDER BY c.start_ms DESC');
        $out = [];
        foreach ($rows as $r) {
            $out[] = [
                'uuid' => $r->uuid,
                'title' => $r->title_site !== '' ? $r->title_site : $r->title,
                'comment' => (string) $r->comment,
                'operator' => $r->callsign !== '' ? $r->callsign : (string) $r->operator_name,
                'operator_name' => (string) $r->operator_name,
                'band' => $r->band,
                'low_hz' => (float) $r->low_hz,
                'high_hz' => (float) $r->high_hz,
                'reception_mode' => $r->reception_mode,
                'measure_mode' => $r->measure_mode,
                'sdr' => $r->sdr,
                'n_channels' => (int) $r->n_channels,
                'types' => $r->types === '' ? [] : explode(',', $r->types),
                'qualified' => (bool) $r->qualified,
                'aa' => (bool) $r->aa,
                'state' => $r->state,
                'start_ms' => $r->start_ms === null ? null : (int) $r->start_ms,
                'end_ms' => $r->end_ms === null ? null : (int) $r->end_ms,
                'demo' => false,
            ];
        }
        return $out;
    }

    /** Result set of a campaign, in the format of the station's page. Null if not public. */
    public static function results(string $uuid, bool $any = false): ?array
    {
        global $wpdb;
        $row = self::campaign($uuid);
        if ($row === null || (int) $row->deleted === 1 || $row->meta === null || (!$any && (int) $row->hidden === 1)) {
            return null;
        }
        $meta = json_decode($row->meta, true);
        if (!is_array($meta)) {
            return null;
        }
        $windows = self::table('windows');
        $span = $wpdb->get_row($wpdb->prepare("SELECT MIN(t) AS first, MAX(t) AS last FROM $windows WHERE campaign_id = %d", $row->id));
        $step = max(1000, (int) $row->step_ms);
        $first = $span && $span->first !== null ? (int) $span->first : null;
        $count = $first === null ? 0 : intdiv((int) $span->last - $first, $step) + 1;
        $n_ch = (int) $meta['campaign']['n_channels'];
        $signals = [];
        foreach ($meta['signals'] as $sig) {
            $rssi = array_fill(0, $n_ch, array_fill(0, $count, null));
            $noise = $rssi;
            // One query per signal: memory stays small on shared hosting.
            $rows = $wpdb->get_results($wpdb->prepare(
                "SELECT ch, t, rssi, noise FROM $windows WHERE campaign_id = %d AND sig = %d",
                $row->id,
                (int) $sig['key']
            ), ARRAY_N);
            foreach ($rows as [$ch, $t, $r, $f]) {
                $i = intdiv((int) $t - $first, $step);
                if ((int) $ch < $n_ch && $i >= 0 && $i < $count) {
                    $rssi[(int) $ch][$i] = $r === null ? null : round((float) $r, 2);
                    $noise[(int) $ch][$i] = $f === null ? null : round((float) $f, 2);
                }
            }
            $signals[] = $sig + ['rssi' => $rssi, 'floor' => $noise];
        }
        $meta['campaign']['title'] = $row->title_site !== '' ? $row->title_site : $meta['campaign']['title'];
        $meta['campaign']['comment'] = (string) $row->comment;
        $meta['campaign']['demo'] = false;
        $meta['campaign']['published_step_ms'] = $step;
        $meta['windows'] = ['start_ms' => $first, 'step_ms' => $step, 'count' => $count];
        $meta['signals'] = $signals;
        return $meta;
    }

    /** Stations shown on the public state page. */
    public static function stations_public(): array
    {
        global $wpdb;
        $rows = $wpdb->get_results('SELECT s.id, s.name, s.callsign, s.locator, s.status, s.last_seen, u.display_name AS operator_name'
            . ' FROM ' . self::table('stations') . " s LEFT JOIN {$wpdb->users} u ON u.ID = s.operator_id"
            . ' WHERE s.last_seen IS NOT NULL ORDER BY s.last_seen DESC');
        $out = [];
        foreach ($rows as $r) {
            $status = json_decode((string) $r->status, true) ?: [];
            $campaign = null;
            if (!empty($status['campaign'])) {
                $c = self::campaign((string) $status['campaign']);
                if ($c && (int) $c->hidden === 0 && (int) $c->deleted === 0 && $c->visibility === 'public') {
                    $campaign = ['uuid' => $c->uuid, 'title' => $c->title_site !== '' ? $c->title_site : $c->title];
                }
            }
            $out[] = [
                'name' => $r->name,
                'operator' => $r->callsign !== '' ? $r->callsign : (string) $r->operator_name,
                'locator' => substr((string) $r->locator, 0, 4),
                'last_seen_ms' => strtotime($r->last_seen . ' UTC') * 1000,
                'state' => (string) ($status['state'] ?? ''),
                'sdr' => (string) ($status['sdr'] ?? ''),
                'campaign' => $campaign,
            ];
        }
        return $out;
    }
}
