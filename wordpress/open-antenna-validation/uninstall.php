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
foreach (['oav_db_version', 'oav_quota_mb', 'oav_package_mb', 'oav_demo', 'oav_theme'] as $option) {
    delete_option($option);
}
delete_transient('oav_storage');
remove_role('oav_operator');
remove_role('oav_pending');
foreach (['administrator'] as $role_name) {
    $role = get_role($role_name);
    if ($role) {
        $role->remove_cap('oav_operate');
    }
}
