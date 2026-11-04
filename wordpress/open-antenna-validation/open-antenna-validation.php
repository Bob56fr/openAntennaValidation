<?php
/**
 * Plugin Name:       openAntennaValidation
 * Plugin URI:        https://github.com/Bob56fr/openAntennaValidation
 * Description:       Publie les campagnes de comparaison d'antennes HF des stations openAntennaValidation : plusieurs opérateurs, liste filtrée, page de résultats, état des stations.
 * Version:           0.2.2
 * Requires at least: 6.0
 * Requires PHP:      8.0
 * Author:            openAntennaValidation
 * License:           GPL-3.0-or-later
 * License URI:       https://www.gnu.org/licenses/gpl-3.0.html
 * Text Domain:       open-antenna-validation
 */

if (!defined('ABSPATH')) {
    exit;
}

define('OAV_VERSION', '0.2.2');
define('OAV_DB_VERSION', 1);
define('OAV_FILE', __FILE__);
define('OAV_DIR', plugin_dir_path(__FILE__));
define('OAV_URL', plugin_dir_url(__FILE__));
// Contract with the stations: docs/wordpress/*.schema.json of the station project.
define('OAV_SCHEMA', 1);
define('OAV_NAMESPACE', 'open-antenna-validation/v1');
// Size of one stored window in the package of a campaign (same figure on the station).
define('OAV_ROW_BYTES', 40);
define('OAV_DEFAULT_QUOTA_MB', 200);
define('OAV_DEFAULT_PACKAGE_MB', 5);
// Capability of the operators (role oav_operator; administrators get it too).
define('OAV_CAP', 'oav_operate');

require_once OAV_DIR . 'includes/class-oav-db.php';
require_once OAV_DIR . 'includes/class-oav-rest.php';
require_once OAV_DIR . 'includes/class-oav-admin.php';
require_once OAV_DIR . 'includes/class-oav-public.php';
require_once OAV_DIR . 'includes/class-oav-presentation.php';

register_activation_hook(__FILE__, ['OAV_DB', 'install']);

add_action('plugins_loaded', ['OAV_DB', 'maybe_upgrade']);
add_action('rest_api_init', ['OAV_REST', 'register_routes']);
add_action('admin_menu', ['OAV_Admin', 'menu']);
add_action('admin_post_oav_station_create', ['OAV_Admin', 'station_create']);
add_action('admin_post_oav_station_token', ['OAV_Admin', 'station_token']);
add_action('admin_post_oav_station_delete', ['OAV_Admin', 'station_delete']);
add_action('admin_post_oav_campaign_save', ['OAV_Admin', 'campaign_save']);
add_action('admin_post_oav_campaign_delete', ['OAV_Admin', 'campaign_delete']);
add_action('admin_post_oav_campaign_csv', ['OAV_Admin', 'campaign_csv']);
add_action('admin_post_oav_campaigns_csv', ['OAV_Admin', 'campaigns_csv']);
add_action('admin_post_oav_operator', ['OAV_Admin', 'operator_action']);
add_action('admin_post_oav_settings', ['OAV_Admin', 'save_settings']);
add_action('init', ['OAV_Public', 'register']);
add_action('init', ['OAV_Presentation', 'register']);
