<?php
/**
 * REST API: what the stations send (token protected) and what the pages read (public).
 *
 * Contract: docs/wordpress/*.schema.json of the station project. The station always
 * initiates the connection; the site never reaches a station.
 */

if (!defined('ABSPATH')) {
    exit;
}

class OAV_REST
{
    /** Station of the token of the current request. */
    private static ?object $station = null;

    private const CLASSES = ['carrier', 'digital', 'ssb', 'am_fm', 'noise'];
    private const STATES = ['preparation', 'running', 'interrupted', 'done', 'abandoned'];

    public static function register_routes(): void
    {
        $station = ['methods' => 'POST', 'permission_callback' => [self::class, 'authenticate']];
        register_rest_route(OAV_NAMESPACE, '/limits', $station + ['callback' => [self::class, 'limits']]);
        register_rest_route(OAV_NAMESPACE, '/ingest', $station + ['callback' => [self::class, 'ingest']]);

        $public = ['methods' => 'GET', 'permission_callback' => '__return_true'];
        register_rest_route(OAV_NAMESPACE, '/public/campaigns', $public + ['callback' => [self::class, 'public_campaigns']]);
        register_rest_route(OAV_NAMESPACE, '/public/campaigns/(?P<uuid>[A-Za-z0-9_-]{4,64})', $public + [
            'callback' => [self::class, 'public_results'],
        ]);
        register_rest_route(OAV_NAMESPACE, '/public/stations', $public + ['callback' => [self::class, 'public_stations']]);
    }

    // --- authentication --------------------------------------------------------------------

    private static function request_token(WP_REST_Request $request): string
    {
        // Shared hosts (OVH...) may strip Authorization: the station also sends this header.
        $token = (string) $request->get_header('x_oav_token');
        if ($token !== '') {
            return trim($token);
        }
        $auth = (string) $request->get_header('authorization');
        if ($auth === '') {
            $auth = (string) ($_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '');
        }
        return stripos($auth, 'Bearer ') === 0 ? trim(substr($auth, 7)) : '';
    }

    public static function authenticate(WP_REST_Request $request)
    {
        $station = OAV_DB::station_by_token(self::request_token($request));
        if ($station === null) {
            return new WP_Error('oav_token', 'Jeton de station inconnu ou remplacé.', ['status' => 401]);
        }
        $operator = get_userdata((int) $station->operator_id);
        if (!$operator || !user_can($operator, OAV_CAP)) {
            return new WP_Error('oav_operator', "L'opérateur de cette station n'est pas (ou plus) validé sur le site.", ['status' => 403]);
        }
        self::$station = $station;
        return true;
    }

    /** Decoded JSON body (gzip or not). */
    private static function payload(WP_REST_Request $request)
    {
        $body = $request->get_body();
        if (strncmp($body, "\x1f\x8b", 2) === 0) {
            $body = gzdecode($body);
            if ($body === false) {
                return new WP_Error('oav_gzip', 'Corps compressé illisible.', ['status' => 400]);
            }
        }
        $data = json_decode($body, true);
        if (!is_array($data) || ($data['schema'] ?? null) !== OAV_SCHEMA) {
            return new WP_Error('oav_json', 'Contenu illisible ou version du contrat inconnue.', ['status' => 400]);
        }
        return $data;
    }

    /** A token belongs to the first station (computer) that uses it, until it is replaced. */
    private static function check_station(string $uid)
    {
        $station = self::$station;
        if ($uid === '' || strlen($uid) > 64) {
            return new WP_Error('oav_station', 'Identifiant de station manquant.', ['status' => 400]);
        }
        if ($station->station_uid !== null && $station->station_uid !== '' && $station->station_uid !== $uid) {
            return new WP_Error('oav_station', 'Ce jeton sert déjà à une autre station : créez-en un autre.', ['status' => 403]);
        }
        return true;
    }

    // --- station routes ----------------------------------------------------------------------

    public static function limits(WP_REST_Request $request)
    {
        $data = self::payload($request);
        if (is_wp_error($data)) {
            return $data;
        }
        $ok = self::check_station((string) ($data['station_id'] ?? ''));
        if (is_wp_error($ok)) {
            return $ok;
        }
        $storage = OAV_DB::storage();
        $operator = get_userdata((int) self::$station->operator_id);
        return [
            'schema' => OAV_SCHEMA,
            'package_max_bytes' => OAV_DB::package_max_bytes(),
            'row_bytes' => OAV_ROW_BYTES,
            'quota_bytes' => (int) $storage['quota_bytes'],
            'used_bytes' => (int) $storage['used_bytes'],
            'full' => (bool) $storage['full'],
            'operator' => $operator ? $operator->display_name : '',
            'station' => (string) self::$station->name,
        ];
    }

    public static function ingest(WP_REST_Request $request)
    {
        $data = self::payload($request);
        if (is_wp_error($data)) {
            return $data;
        }
        $invalid = self::validate($data);
        if ($invalid !== null) {
            return new WP_Error('oav_invalid', $invalid, ['status' => 400]);
        }
        $ok = self::check_station((string) $data['station']['id']);
        if (is_wp_error($ok)) {
            return $ok;
        }
        OAV_DB::station_seen(self::$station, $data['station']);
        // Storage guard: refuse new data above the quota; the station keeps it and retries.
        $storage = OAV_DB::storage();
        if ($storage['full'] && !empty($data['campaigns'])) {
            return new WP_Error('oav_quota', 'Quota de stockage du site atteint.', ['status' => 507]);
        }
        $out = [];
        foreach ($data['campaigns'] as $c) {
            $result = OAV_DB::ingest_campaign(self::$station, $c);
            if (is_wp_error($result)) {
                return $result;
            }
            $out[$c['uuid']] = $result;
        }
        if (!empty($data['campaigns'])) {
            delete_transient('oav_storage');
        }
        return [
            'ok' => true,
            'campaigns' => (object) $out,
            'limits' => [
                'package_max_bytes' => OAV_DB::package_max_bytes(),
                'full' => (bool) OAV_DB::storage()['full'],
            ],
        ];
    }

    /** Structural checks of an ingest body (the full contract is the JSON schema). */
    private static function validate(array $data): ?string
    {
        $s = $data['station'] ?? null;
        if (!is_array($s) || !isset($s['id'], $s['name'], $s['status']) || !is_array($s['status'])) {
            return 'Station incomplète.';
        }
        if (!is_array($data['campaigns'] ?? null) || count($data['campaigns']) > 20) {
            return 'Liste de campagnes invalide.';
        }
        foreach ($data['campaigns'] as $c) {
            if (!is_array($c) || !is_string($c['uuid'] ?? null) || !preg_match('/^[A-Za-z0-9_-]{8,64}$/', $c['uuid'])) {
                return 'Identifiant de campagne invalide.';
            }
            if (!in_array($c['visibility'] ?? '', ['public', 'unlisted'], true) || !is_int($c['step_ms'] ?? null)
                || !is_array($c['rows'] ?? null) || count($c['rows']) > 50000) {
                return 'Campagne ' . $c['uuid'] . ' : champs invalides.';
            }
            foreach ($c['rows'] as $r) {
                if (!is_array($r) || count($r) !== 5 || !is_int($r[0]) || !is_int($r[1]) || $r[1] < 0 || $r[1] > 9
                    || !is_int($r[2]) || !($r[3] === null || is_numeric($r[3])) || !($r[4] === null || is_numeric($r[4]))) {
                    return 'Campagne ' . $c['uuid'] . ' : fenêtre invalide.';
                }
            }
            if (isset($c['meta'])) {
                $m = $c['meta'];
                $mc = $m['campaign'] ?? null;
                if (!is_array($mc) || !is_array($m['channels'] ?? null) || !is_array($m['signals'] ?? null)) {
                    return 'Campagne ' . $c['uuid'] . ' : métadonnées incomplètes.';
                }
                $n = (int) ($mc['n_channels'] ?? 0);
                if ($n < 2 || $n > 10 || count($m['channels']) !== $n || !in_array($mc['state'] ?? '', self::STATES, true)) {
                    return 'Campagne ' . $c['uuid'] . ' : voies ou état invalides.';
                }
                foreach ($m['signals'] as $sig) {
                    if (!is_int($sig['key'] ?? null) || !in_array($sig['class'] ?? '', self::CLASSES, true)) {
                        return 'Campagne ' . $c['uuid'] . ' : signal invalide.';
                    }
                }
            }
        }
        return null;
    }

    // --- public routes ------------------------------------------------------------------------

    /** Demonstration campaigns shipped with the plugin (assets/demo), list entries. */
    public static function demo_index(): array
    {
        $file = OAV_DIR . 'assets/demo/index.json';
        $items = is_readable($file) ? json_decode((string) file_get_contents($file), true) : [];
        $out = [];
        foreach (is_array($items) ? $items : [] as $d) {
            $out[] = [
                'uuid' => $d['uuid'], 'title' => $d['title'], 'comment' => '', 'operator' => $d['operator'],
                'operator_name' => $d['operator'], 'band' => $d['band'], 'low_hz' => $d['low_hz'], 'high_hz' => $d['high_hz'],
                'reception_mode' => $d['reception_mode'], 'measure_mode' => $d['measure_mode'], 'sdr' => $d['sdr'],
                'n_channels' => $d['n_channels'], 'types' => $d['type_codes'] ?? [], 'type_labels' => $d['antenna_types'],
                'qualified' => true, 'aa' => false, 'state' => $d['state'], 'start_ms' => $d['start_ms'],
                'end_ms' => $d['end_ms'], 'demo' => true,
            ];
        }
        return $out;
    }

    public static function show_demos(array $real): bool
    {
        $mode = OAV_DB::demo_mode();
        return $mode === 'always' || ($mode === 'auto' && !$real);
    }

    public static function public_campaigns()
    {
        $real = OAV_DB::campaigns_public();
        $items = self::show_demos($real) ? array_merge($real, self::demo_index()) : $real;
        return ['campaigns' => $items, 'types' => OAV_Public::antenna_types()];
    }

    public static function public_results(WP_REST_Request $request)
    {
        $uuid = (string) $request['uuid'];
        if (str_starts_with($uuid, 'demo-')) {
            $file = OAV_DIR . 'assets/demo/' . basename($uuid) . '.json';
            if (OAV_DB::demo_mode() !== 'never' && is_readable($file)) {
                return rest_ensure_response(json_decode((string) file_get_contents($file), true));
            }
        } else {
            $results = OAV_DB::results($uuid);
            if ($results !== null) {
                return rest_ensure_response($results);
            }
        }
        return new WP_Error('oav_not_found', 'Campagne introuvable.', ['status' => 404]);
    }

    public static function public_stations()
    {
        return ['stations' => OAV_DB::stations_public()];
    }
}
