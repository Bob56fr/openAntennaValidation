<?php
/**
 * Public pages.
 *
 *   [oav_campagnes]            list of the campaigns with filters; ?campagne=<uuid> on the same
 *                              page shows the campaign (same view as the station's local page)
 *   [oav_campagne uuid="..."]  one campaign
 *   [oav_stations]             state of the stations
 *
 * The pages are containers: the scripts read the public REST routes.
 */

if (!defined('ABSPATH')) {
    exit;
}

class OAV_Public
{
    public static function register(): void
    {
        add_shortcode('oav_campagnes', [self::class, 'campaigns']);
        add_shortcode('oav_campagne', [self::class, 'campaign']);
        add_shortcode('oav_stations', [self::class, 'stations']);
        $v = OAV_VERSION;
        wp_register_style('oav-design', OAV_URL . 'assets/shared/oav-design.css', [], $v);
        wp_register_style('oav', OAV_URL . 'assets/oav.css', ['oav-design'], $v);
        wp_register_script('oav-echarts', OAV_URL . 'assets/vendor/echarts.min.js', [], '5.5.1', true);
        wp_register_script('oav-stats', OAV_URL . 'assets/shared/stats.js', [], $v, true);
        // Decoded stations (P10): land outlines for the map, then the digital section.
        wp_register_script('oav-world', OAV_URL . 'assets/vendor/world-land.js', [], $v, true);
        wp_register_script('oav-digital', OAV_URL . 'assets/shared/digital.js', ['oav-world'], $v, true);
        wp_register_script('oav-results', OAV_URL . 'assets/shared/results.js', ['oav-echarts', 'oav-stats', 'oav-digital'], $v, true);
        wp_register_script('oav-list', OAV_URL . 'assets/oav-list.js', [], $v, true);
        wp_register_script('oav-stations', OAV_URL . 'assets/oav-stations.js', [], $v, true);
    }

    /** Antenna type labels (code => label), as on the stations. */
    public static function antenna_types(): array
    {
        $file = OAV_DIR . 'assets/antenna-types.json';
        $types = is_readable($file) ? json_decode((string) file_get_contents($file), true) : [];
        return is_array($types) ? $types : [];
    }

    private static function root_open(string $extra = ''): string
    {
        $theme = OAV_DB::theme();
        $attr = $theme === 'auto' ? '' : ' data-theme="' . esc_attr($theme) . '"';
        // alignwide: block themes give the page their wide width instead of the text column.
        return '<div class="oav-root alignwide' . ($extra ? ' ' . esc_attr($extra) : '') . '"' . $attr . '>';
    }

    private static function api(): string
    {
        // rest_url() follows the permalink settings (/wp-json/ or ?rest_route=).
        return esc_url_raw(rest_url(OAV_NAMESPACE . '/public/'));
    }

    public static function campaigns(): string
    {
        $uuid = isset($_GET['campagne']) ? sanitize_text_field(wp_unslash($_GET['campagne'])) : '';
        if ($uuid !== '' && preg_match('/^[A-Za-z0-9_-]{4,64}$/', $uuid)) {
            return self::view($uuid, remove_query_arg('campagne'));
        }
        wp_enqueue_style('oav');
        wp_enqueue_script('oav-list');
        wp_localize_script('oav-list', 'OAV_LIST', [
            'api' => self::api(),
            'page' => esc_url_raw(remove_query_arg('campagne')),
            'types' => self::antenna_types(),
        ]);
        return self::root_open('oav-list') . '<div class="wrap" id="oav-list"><p class="desc">Chargement des campagnes…</p></div></div>';
    }

    public static function campaign($atts): string
    {
        $atts = shortcode_atts(['uuid' => ''], $atts, 'oav_campagne');
        $uuid = (string) $atts['uuid'];
        if (!preg_match('/^[A-Za-z0-9_-]{4,64}$/', $uuid)) {
            return '<p>Campagne non précisée.</p>';
        }
        return self::view($uuid, '');
    }

    private static function view(string $uuid, string $list_url): string
    {
        wp_enqueue_style('oav');
        wp_enqueue_script('oav-results');
        $config = [
            'uuid' => $uuid,
            'api' => self::api(),
            'list' => $list_url ? esc_url_raw($list_url) : '',
        ];
        wp_add_inline_script('oav-results', 'window.OAV_CONFIG = (function (c) { return {'
            . ' uuid: c.uuid, param: "campagne", loginUrl: null, exportUrl: null, listUrl: c.list || "/",'
            . ' resultsUrl: function (u) { return c.api + "campaigns/" + encodeURIComponent(u); } }; })('
            . wp_json_encode($config) . ');', 'before');
        $template = OAV_DIR . 'templates/campaign-view.html';
        $view = is_readable($template) ? (string) file_get_contents($template) : '<p>Vue absente : reconstruire le plugin.</p>';
        $back = $list_url ? '<div class="wrap oav-back"><a class="linkbtn" href="' . esc_url($list_url) . '">← Toutes les campagnes</a></div>' : '';
        return self::root_open('oav-campaign') . $back . $view . '</div>';
    }

    public static function stations(): string
    {
        wp_enqueue_style('oav');
        wp_enqueue_script('oav-stations');
        wp_localize_script('oav-stations', 'OAV_STATIONS', ['api' => self::api()]);
        return self::root_open('oav-stations') . '<div class="wrap" id="oav-stations"><p class="desc">Chargement…</p></div></div>';
    }
}
