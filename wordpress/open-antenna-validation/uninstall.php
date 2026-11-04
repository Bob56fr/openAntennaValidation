<?php
/**
 * Removal of the plugin from the administration (not on simple deactivation): tables,
 * options and roles.
 */

if (!defined('WP_UNINSTALL_PLUGIN')) {
    exit;
}

global $wpdb;
foreach (['windows', 'campaigns', 'stations'] as $name) {
    $wpdb->query('DROP TABLE IF EXISTS ' . $wpdb->prefix . 'oav_' . $name);
}
foreach (['oav_db_version', 'oav_quota_mb', 'oav_package_mb', 'oav_demo', 'oav_theme', 'oav_list_page',
         'oav_presentation_url', 'oav_presentation_last'] as $option) {
    delete_option($option);
}
delete_transient('oav_storage');
delete_transient('oav_presentation');
// Images of the presentation: one transient and one last good copy per file.
$wpdb->query("DELETE FROM {$wpdb->options} WHERE option_name LIKE '\_transient\_oav\_presentation\_img\_%'"
    . " OR option_name LIKE '\_transient\_timeout\_oav\_presentation\_img\_%'"
    . " OR option_name LIKE 'oav\_presentation\_img\_%'");
remove_role('oav_operator');
remove_role('oav_pending');
foreach (['administrator'] as $role_name) {
    $role = get_role($role_name);
    if ($role) {
        $role->remove_cap('oav_operate');
    }
}
