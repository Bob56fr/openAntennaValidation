<?php
/**
 * Administration: campaigns, stations and tokens, operators, settings, CSV export.
 *
 * Operators (role oav_operator) manage their own stations and campaigns; administrators
 * (manage_options) manage everything, validate the operators and set the limits.
 */

if (!defined('ABSPATH')) {
    exit;
}

class OAV_Admin
{
    private const SLUG = 'oav';

    public static function menu(): void
    {
        add_menu_page('Antennes HF', 'Antennes HF', OAV_CAP, self::SLUG, [self::class, 'page_campaigns'], 'dashicons-chart-line', 58);
        add_submenu_page(self::SLUG, 'Campagnes', 'Campagnes', OAV_CAP, self::SLUG, [self::class, 'page_campaigns']);
        add_submenu_page(self::SLUG, 'Stations', 'Stations', OAV_CAP, self::SLUG . '-stations', [self::class, 'page_stations']);
        add_submenu_page(self::SLUG, 'Opérateurs', 'Opérateurs', 'manage_options', self::SLUG . '-operators', [self::class, 'page_operators']);
        add_submenu_page(self::SLUG, 'Réglages', 'Réglages', 'manage_options', self::SLUG . '-settings', [self::class, 'page_settings']);
    }

    // --- helpers ---------------------------------------------------------------------------

    private static function is_admin(): bool
    {
        return current_user_can('manage_options');
    }

    /** Operator filter of the lists: null for administrators (everything). */
    private static function scope(): ?int
    {
        return self::is_admin() ? null : get_current_user_id();
    }

    private static function require_cap(string $cap = OAV_CAP): void
    {
        if (!current_user_can($cap)) {
            wp_die('Accès refusé.', 403);
        }
    }

    private static function owns(object $row): bool
    {
        return self::is_admin() || (int) $row->operator_id === get_current_user_id();
    }

    private static function back(string $page, string $notice = '', array $extra = []): void
    {
        $args = array_merge(['page' => $page], $notice ? ['oav_notice' => rawurlencode($notice)] : [], $extra);
        wp_safe_redirect(add_query_arg($args, admin_url('admin.php')));
        exit;
    }

    private static function notice(): void
    {
        if (!empty($_GET['oav_notice'])) {
            echo '<div class="notice notice-info is-dismissible"><p>' . esc_html(rawurldecode(sanitize_text_field(wp_unslash($_GET['oav_notice'])))) . '</p></div>';
        }
    }

    private static function date_ms($ms): string
    {
        return $ms ? esc_html(wp_date('j M Y H:i', (int) round($ms / 1000))) : '—';
    }

    private static function mb($bytes): string
    {
        return esc_html(number_format_i18n($bytes / 1048576, 2)) . ' Mo';
    }

    private static function list_page_url(): string
    {
        $page = (int) get_option('oav_list_page', 0);
        return $page ? (string) get_permalink($page) : '';
    }

    // --- campaigns --------------------------------------------------------------------------

    public static function page_campaigns(): void
    {
        self::require_cap();
        $rows = OAV_DB::campaigns_admin(self::scope());
        $storage = OAV_DB::storage(true);
        $list = self::list_page_url();
        $states = ['running' => 'en cours', 'interrupted' => 'interrompue', 'done' => 'terminée', 'abandoned' => 'abandonnée', 'preparation' => 'en préparation'];
        echo '<div class="wrap"><h1 class="wp-heading-inline">Campagnes</h1>';
        if (self::is_admin()) {
            echo ' <a class="page-title-action" href="' . esc_url(wp_nonce_url(admin_url('admin-post.php?action=oav_campaigns_csv'), 'oav_campaigns_csv')) . '">Exporter la liste (CSV)</a>';
        }
        self::notice();
        printf('<p>Stockage du plugin : %s sur %s autorisés. Taille maximale du paquet d\'une campagne : %s.</p>',
            self::mb($storage['used_bytes']), self::mb($storage['quota_bytes']),
            OAV_DB::package_max_bytes() ? self::mb(OAV_DB::package_max_bytes()) : 'sans limite');
        if (!$list) {
            echo '<p><em>Pour les liens publics, choisissez la page qui contient [oav_campagnes] dans les Réglages.</em></p>';
        }
        if (!$rows) {
            echo '<p>Aucune campagne reçue. Une station publie ses campagnes dès qu\'elle a un jeton (menu Stations) et que la publication est activée sur sa page locale.</p></div>';
            return;
        }
        echo '<table class="widefat striped"><thead><tr><th>Campagne</th><th>Station</th><th>Bande</th><th>Période</th><th>État</th><th>Sur le site</th><th>Taille</th><th>Actions</th></tr></thead><tbody>';
        foreach ($rows as $r) {
            $title = $r->title_site !== '' ? $r->title_site : $r->title;
            $link = $list ? add_query_arg('campagne', $r->uuid, $list) : '';
            $visible = (int) $r->hidden === 1 ? '<strong>masquée</strong>' : ($r->visibility === 'unlisted' ? 'non listée (lien seul)' : 'publique');
            echo '<tr><td><strong>' . esc_html($title) . '</strong>' . ((int) $r->aa ? ' (A/A)' : '')
                . '<br><code>' . esc_html(substr($r->uuid, 0, 8)) . '</code>'
                . ($r->comment ? '<br><em>' . esc_html($r->comment) . '</em>' : '') . '</td>';
            echo '<td>' . esc_html((string) $r->station_name) . '<br>' . esc_html($r->callsign ?: (string) $r->operator_name) . '</td>';
            echo '<td>' . esc_html($r->band) . '<br>' . (int) $r->n_channels . ' antennes</td>';
            echo '<td>' . self::date_ms($r->start_ms) . '<br>' . self::date_ms($r->end_ms) . '</td>';
            echo '<td>' . esc_html($states[$r->state] ?? $r->state) . ((int) $r->complete ? '' : '<br><small>envoi en cours</small>') . '</td>';
            echo '<td>' . $visible . ($link && (int) $r->hidden === 0 ? '<br><a href="' . esc_url($link) . '" target="_blank" rel="noopener">voir</a>' : '') . '</td>';
            echo '<td>' . self::mb($r->bytes) . '<br><small>' . esc_html(number_format_i18n((int) $r->row_count)) . ' fenêtres</small></td><td>';
            // Edit: title shown on the site, comment, hidden.
            echo '<details><summary>Modifier</summary><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
            wp_nonce_field('oav_campaign_' . $r->id);
            echo '<input type="hidden" name="action" value="oav_campaign_save"><input type="hidden" name="id" value="' . (int) $r->id . '">';
            echo '<p><label>Titre sur le site<br><input type="text" class="regular-text" name="title_site" value="' . esc_attr($r->title_site) . '" placeholder="' . esc_attr($r->title) . '"></label></p>';
            echo '<p><label>Commentaire<br><textarea name="comment" rows="2" class="large-text">' . esc_textarea((string) $r->comment) . '</textarea></label></p>';
            echo '<p><label><input type="checkbox" name="hidden" value="1"' . checked((int) $r->hidden, 1, false) . '> Masquer sur le site</label></p>';
            submit_button('Enregistrer', 'small', 'submit', false);
            echo '</form></details>';
            echo '<p><a href="' . esc_url(wp_nonce_url(admin_url('admin-post.php?action=oav_campaign_csv&id=' . (int) $r->id), 'oav_campaign_csv_' . $r->id)) . '">CSV</a> · ';
            echo '<a href="' . esc_url(wp_nonce_url(admin_url('admin-post.php?action=oav_campaign_delete&id=' . (int) $r->id), 'oav_campaign_delete_' . $r->id)) . '" onclick="return confirm(\'Supprimer cette campagne du site ? La station la garde ; elle ne sera plus acceptée.\')">Supprimer</a></p>';
            echo '</td></tr>';
        }
        echo '</tbody></table></div>';
    }

    private static function campaign_for_action(string $nonce_prefix): object
    {
        self::require_cap();
        $id = (int) ($_REQUEST['id'] ?? 0);
        check_admin_referer($nonce_prefix . $id);
        $row = OAV_DB::campaign_by_id($id);
        if ($row === null || !self::owns($row)) {
            wp_die('Campagne introuvable.', 404);
        }
        return $row;
    }

    public static function campaign_save(): void
    {
        $row = self::campaign_for_action('oav_campaign_');
        global $wpdb;
        $wpdb->update(OAV_DB::table('campaigns'), [
            'title_site' => substr(sanitize_text_field(wp_unslash($_POST['title_site'] ?? '')), 0, 200),
            'comment' => sanitize_textarea_field(wp_unslash($_POST['comment'] ?? '')),
            'hidden' => empty($_POST['hidden']) ? 0 : 1,
        ], ['id' => $row->id]);
        self::back(self::SLUG, 'Campagne enregistrée.');
    }

    public static function campaign_delete(): void
    {
        $row = self::campaign_for_action('oav_campaign_delete_');
        OAV_DB::delete_campaign((int) $row->id);
        self::back(self::SLUG, 'Campagne supprimée du site.');
    }

    private static function csv_headers(string $name): void
    {
        nocache_headers();
        header('Content-Type: text/csv; charset=utf-8');
        header('Content-Disposition: attachment; filename="' . $name . '"');
        echo "\xEF\xBB\xBF";
    }

    /** Windows of one campaign, as published (French spreadsheet: semicolon, decimal comma). */
    public static function campaign_csv(): void
    {
        $row = self::campaign_for_action('oav_campaign_csv_');
        $meta = json_decode((string) $row->meta, true) ?: ['signals' => [], 'channels' => []];
        $signals = [];
        foreach ($meta['signals'] as $s) {
            $signals[(int) $s['key']] = $s;
        }
        $names = [];
        foreach ($meta['channels'] as $ch) {
            $names[(int) $ch['idx']] = [(string) $ch['letter'], (string) $ch['name']];
        }
        self::csv_headers('oav-' . sanitize_file_name($row->band) . '-' . substr($row->uuid, 0, 8) . '-site.csv');
        $out = fopen('php://output', 'w');
        fputcsv($out, ['debut_utc', 'debut_ms', 'fenetre_s', 'freq_hz', 'classe', 'voie', 'antenne', 'rssi_dbfs', 'plancher_dbfs'], ';');
        global $wpdb;
        $windows = OAV_DB::table('windows');
        $fr = fn($v) => $v === null ? '' : str_replace('.', ',', (string) round((float) $v, 2));
        for ($offset = 0;; $offset += 5000) {
            $rows = $wpdb->get_results($wpdb->prepare(
                "SELECT sig, ch, t, rssi, noise FROM $windows WHERE campaign_id = %d ORDER BY t, sig, ch LIMIT 5000 OFFSET %d",
                $row->id,
                $offset
            ), ARRAY_N);
            foreach ($rows as [$sig, $ch, $t, $r, $f]) {
                $s = $signals[(int) $sig] ?? ['freq_hz' => '', 'class' => ''];
                $n = $names[(int) $ch] ?? ['', ''];
                fputcsv($out, [gmdate('Y-m-d\TH:i:s\Z', intdiv((int) $t, 1000)), $t, (int) $row->step_ms / 1000,
                    $s['freq_hz'], $s['class'], $n[0], $n[1], $fr($r), $fr($f)], ';');
            }
            if (count($rows) < 5000) {
                break;
            }
        }
        fclose($out);
        exit;
    }

    public static function campaigns_csv(): void
    {
        self::require_cap('manage_options');
        check_admin_referer('oav_campaigns_csv');
        self::csv_headers('oav-campagnes.csv');
        $out = fopen('php://output', 'w');
        fputcsv($out, ['uuid', 'titre', 'operateur', 'station', 'bande', 'antennes', 'reception', 'debut_utc', 'fin_utc', 'etat', 'visibilite', 'masquee', 'fenetres', 'octets'], ';');
        foreach (OAV_DB::campaigns_admin() as $r) {
            fputcsv($out, [$r->uuid, $r->title_site !== '' ? $r->title_site : $r->title, $r->callsign ?: $r->operator_name,
                $r->station_name, $r->band, $r->n_channels, $r->reception_mode,
                $r->start_ms ? gmdate('Y-m-d\TH:i:s\Z', intdiv((int) $r->start_ms, 1000)) : '',
                $r->end_ms ? gmdate('Y-m-d\TH:i:s\Z', intdiv((int) $r->end_ms, 1000)) : '',
                $r->state, $r->visibility, $r->hidden, $r->row_count, $r->bytes], ';');
        }
        fclose($out);
        exit;
    }

    // --- stations ---------------------------------------------------------------------------

    public static function page_stations(): void
    {
        self::require_cap();
        echo '<div class="wrap"><h1>Stations</h1>';
        self::notice();
        $token = get_transient('oav_new_token_' . get_current_user_id());
        if ($token) {
            delete_transient('oav_new_token_' . get_current_user_id());
            echo '<div class="notice notice-success"><p><strong>Jeton de la station « ' . esc_html($token['name']) . ' »</strong> (affiché une seule fois, copiez-le maintenant) :</p>'
                . '<p><input type="text" readonly class="large-text code" value="' . esc_attr($token['token']) . '" onclick="this.select()"></p>'
                . '<p>Sur la station : page Installation, section « Publication sur le site », adresse <code>' . esc_html(home_url()) . '</code> et ce jeton.</p></div>';
        }
        $rows = OAV_DB::stations(self::scope());
        if ($rows) {
            echo '<table class="widefat striped"><thead><tr><th>Station</th>' . (self::is_admin() ? '<th>Opérateur</th>' : '')
                . '<th>Jeton</th><th>Dernier envoi</th><th>État</th><th>Actions</th></tr></thead><tbody>';
            foreach ($rows as $s) {
                $status = json_decode((string) $s->status, true) ?: [];
                echo '<tr><td><strong>' . esc_html($s->name) . '</strong>' . ($s->callsign ? '<br>' . esc_html($s->callsign) . ' ' . esc_html(substr((string) $s->locator, 0, 4)) : '') . '</td>';
                if (self::is_admin()) {
                    echo '<td>' . esc_html((string) $s->operator_name) . '</td>';
                }
                echo '<td><code>…' . esc_html($s->token_hint) . '</code>' . ($s->station_uid ? '<br><small>lié à la station ' . esc_html(substr($s->station_uid, 0, 8)) . '</small>' : '<br><small>pas encore utilisé</small>') . '</td>';
                echo '<td>' . ($s->last_seen ? esc_html(human_time_diff(strtotime($s->last_seen . ' UTC'))) . '<br><small>' . esc_html($s->software) . '</small>' : '—') . '</td>';
                $words = ['running' => 'en marche', 'absent' => 'SDR absent', 'error' => 'en erreur', 'starting' => 'démarrage', 'stopped' => 'arrêtée'];
                $state = (string) ($status['state'] ?? '');
                echo '<td>' . esc_html($words[$state] ?? ($state ?: '—')) . (!empty($status['sdr']) ? '<br><small>' . esc_html($status['sdr']) . '</small>' : '') . '</td><td>';
                foreach ([['oav_station_token', 'Nouveau jeton', "L'ancien jeton cessera de fonctionner. Continuer ?"],
                          ['oav_station_delete', 'Supprimer', 'Supprimer la station et toutes ses campagnes du site ?']] as [$action, $label, $confirm]) {
                    echo '<a href="' . esc_url(wp_nonce_url(admin_url('admin-post.php?action=' . $action . '&id=' . (int) $s->id), $action . '_' . $s->id))
                        . '" onclick="return confirm(\'' . esc_js($confirm) . '\')">' . esc_html($label) . '</a><br>';
                }
                echo '</td></tr>';
            }
            echo '</tbody></table>';
        } else {
            echo '<p>Aucune station. Créez-en une : un jeton par ordinateur de mesure.</p>';
        }
        echo '<h2>Nouvelle station</h2><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        wp_nonce_field('oav_station_create');
        echo '<input type="hidden" name="action" value="oav_station_create"><p><label>Nom <input type="text" name="name" required maxlength="120" class="regular-text" placeholder="Station de la maison"></label> ';
        submit_button('Créer et afficher le jeton', 'primary', 'submit', false);
        echo '</p></form></div>';
    }

    public static function station_create(): void
    {
        self::require_cap();
        check_admin_referer('oav_station_create');
        $name = substr(sanitize_text_field(wp_unslash($_POST['name'] ?? '')), 0, 120);
        if ($name === '') {
            self::back(self::SLUG . '-stations', 'Nom de station manquant.');
        }
        [, $token] = OAV_DB::create_station(get_current_user_id(), $name);
        set_transient('oav_new_token_' . get_current_user_id(), ['name' => $name, 'token' => $token], 10 * MINUTE_IN_SECONDS);
        self::back(self::SLUG . '-stations');
    }

    private static function station_for_action(string $action): object
    {
        self::require_cap();
        $id = (int) ($_GET['id'] ?? 0);
        check_admin_referer($action . '_' . $id);
        $station = OAV_DB::station($id);
        if ($station === null || !self::owns($station)) {
            wp_die('Station introuvable.', 404);
        }
        return $station;
    }

    public static function station_token(): void
    {
        $station = self::station_for_action('oav_station_token');
        $token = OAV_DB::new_token((int) $station->id);
        set_transient('oav_new_token_' . get_current_user_id(), ['name' => $station->name, 'token' => $token], 10 * MINUTE_IN_SECONDS);
        self::back(self::SLUG . '-stations');
    }

    public static function station_delete(): void
    {
        $station = self::station_for_action('oav_station_delete');
        OAV_DB::delete_station((int) $station->id);
        self::back(self::SLUG . '-stations', 'Station supprimée.');
    }

    // --- operators ----------------------------------------------------------------------------

    public static function page_operators(): void
    {
        self::require_cap('manage_options');
        echo '<div class="wrap"><h1>Opérateurs</h1>';
        self::notice();
        $pending = get_users(['role' => 'oav_pending']);
        echo '<h2>En attente de validation</h2>';
        if (!$pending) {
            echo '<p>Aucune demande. Pour que les nouveaux inscrits arrivent ici, cochez la case dans les Réglages.</p>';
        }
        foreach ($pending as $u) {
            echo '<p><strong>' . esc_html($u->display_name) . '</strong> (' . esc_html($u->user_email) . ', inscrit le ' . esc_html(wp_date('j M Y', strtotime($u->user_registered . ' UTC'))) . ') ';
            foreach (['approve' => 'Valider', 'refuse' => 'Refuser'] as $op => $label) {
                echo '<a class="button button-small" href="' . esc_url(wp_nonce_url(admin_url('admin-post.php?action=oav_operator&op=' . $op . '&user=' . $u->ID), 'oav_operator_' . $u->ID)) . '">' . esc_html($label) . '</a> ';
            }
            echo '</p>';
        }
        echo '<h2>Opérateurs validés</h2>';
        $operators = get_users(['role' => 'oav_operator']);
        if (!$operators) {
            echo '<p>Aucun (les administrateurs peuvent aussi publier).</p>';
        } else {
            echo '<table class="widefat striped"><thead><tr><th>Opérateur</th><th>Stations</th><th>Campagnes</th><th></th></tr></thead><tbody>';
            foreach ($operators as $u) {
                echo '<tr><td>' . esc_html($u->display_name) . '<br><small>' . esc_html($u->user_email) . '</small></td><td>' . count(OAV_DB::stations($u->ID))
                    . '</td><td>' . count(OAV_DB::campaigns_admin($u->ID)) . '</td><td><a href="' . esc_url(wp_nonce_url(admin_url('admin-post.php?action=oav_operator&op=revoke&user=' . $u->ID), 'oav_operator_' . $u->ID))
                    . '" onclick="return confirm(\'Retirer le rôle d\\\'opérateur ? Ses stations ne pourront plus envoyer ; ses campagnes restent.\')">Retirer</a></td></tr>';
            }
            echo '</tbody></table>';
        }
        echo '<h2>Ajouter un opérateur</h2><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        wp_nonce_field('oav_operator_add');
        echo '<input type="hidden" name="action" value="oav_operator"><input type="hidden" name="op" value="add"><p><label>Identifiant ou adresse e-mail d\'un utilisateur existant <input type="text" name="login" class="regular-text" required></label> ';
        submit_button('Ajouter', 'secondary', 'submit', false);
        echo '</p></form></div>';
    }

    public static function operator_action(): void
    {
        self::require_cap('manage_options');
        $op = sanitize_key($_REQUEST['op'] ?? '');
        if ($op === 'add') {
            check_admin_referer('oav_operator_add');
            $login = sanitize_text_field(wp_unslash($_POST['login'] ?? ''));
            $user = get_user_by(is_email($login) ? 'email' : 'login', $login);
            if (!$user) {
                self::back(self::SLUG . '-operators', 'Utilisateur introuvable.');
            }
            $user->remove_role('oav_pending');
            $user->add_role('oav_operator');
            self::back(self::SLUG . '-operators', $user->display_name . ' est opérateur.');
        }
        $id = (int) ($_GET['user'] ?? 0);
        check_admin_referer('oav_operator_' . $id);
        $user = get_userdata($id);
        if (!$user) {
            self::back(self::SLUG . '-operators', 'Utilisateur introuvable.');
        }
        if ($op === 'approve') {
            $user->set_role('oav_operator');
            $message = $user->display_name . ' est validé comme opérateur.';
        } elseif ($op === 'refuse' || $op === 'revoke') {
            $user->remove_role('oav_pending');
            $user->remove_role('oav_operator');
            if (!$user->roles) {
                $user->set_role('subscriber');
            }
            $message = $user->display_name . ' n\'est pas opérateur.';
        } else {
            $message = 'Action inconnue.';
        }
        self::back(self::SLUG . '-operators', $message);
    }

    // --- settings -----------------------------------------------------------------------------

    public static function page_settings(): void
    {
        self::require_cap('manage_options');
        $storage = OAV_DB::storage(true);
        echo '<div class="wrap"><h1>Réglages</h1>';
        self::notice();
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        wp_nonce_field('oav_settings');
        echo '<input type="hidden" name="action" value="oav_settings"><table class="form-table">';
        echo '<tr><th>Quota de stockage</th><td><input type="number" name="quota_mb" min="10" step="10" value="' . esc_attr((string) OAV_DB::quota_mb()) . '"> Mo'
            . '<p class="description">Utilisé : ' . self::mb($storage['used_bytes']) . '. Au-delà, le site refuse les envois (code 507) ; les stations gardent leurs données et réessaient.</p></td></tr>';
        echo '<tr><th>Taille maximale d\'une campagne</th><td><input type="number" name="package_mb" min="0" step="0.5" value="' . esc_attr((string) get_option('oav_package_mb', OAV_DEFAULT_PACKAGE_MB)) . '"> Mo'
            . '<p class="description">0 : pas de limite. Les stations lisent cette limite avant de lancer une campagne et élargissent la fenêtre publiée pour qu\'elle tienne ; le nombre d\'antennes n\'est jamais un motif de refus.</p></td></tr>';
        echo '<tr><th>Campagnes de démonstration</th><td><select name="demo">';
        foreach (['auto' => 'Tant qu\'aucune vraie campagne n\'est publique', 'always' => 'Toujours (page d\'aide)', 'never' => 'Jamais'] as $v => $label) {
            echo '<option value="' . esc_attr($v) . '"' . selected(OAV_DB::demo_mode(), $v, false) . '>' . esc_html($label) . '</option>';
        }
        echo '</select></td></tr>';
        echo '<tr><th>Thème des pages publiques</th><td><select name="theme">';
        foreach (['light' => 'Clair', 'dark' => 'Sombre', 'auto' => 'Selon le visiteur'] as $v => $label) {
            echo '<option value="' . esc_attr($v) . '"' . selected(OAV_DB::theme(), $v, false) . '>' . esc_html($label) . '</option>';
        }
        echo '</select></td></tr>';
        echo '<tr><th>Page de la liste</th><td>';
        wp_dropdown_pages(['name' => 'list_page', 'selected' => (int) get_option('oav_list_page', 0), 'show_option_none' => '—', 'option_none_value' => '0']);
        echo '<p class="description">La page qui contient le code court <code>[oav_campagnes]</code> (liens depuis l\'administration). Autres codes : <code>[oav_stations]</code>, <code>[oav_campagne uuid="…"]</code>, <code>[oav_presentation]</code>.</p></td></tr>';
        echo '<tr><th>Présentation du projet</th><td><input type="url" class="large-text" name="presentation_url" value="' . esc_attr(OAV_Presentation::url()) . '">'
            . '<p class="description">Fichier Markdown affiché par le code court <code>[oav_presentation]</code>, relu toutes les heures (par défaut : docs/presentation.md du dépôt GitHub du projet).</p></td></tr>';
        echo '<tr><th>Inscriptions</th><td><label><input type="checkbox" name="pending_role" value="1"' . checked(get_option('default_role'), 'oav_pending', false)
            . '> Les nouveaux inscrits sont des opérateurs en attente de validation</label><p class="description">Les inscriptions elles-mêmes s\'ouvrent dans Réglages › Général (« Tout le monde peut s\'inscrire »).</p></td></tr>';
        echo '</table>';
        submit_button('Enregistrer');
        echo '</form></div>';
    }

    public static function save_settings(): void
    {
        self::require_cap('manage_options');
        check_admin_referer('oav_settings');
        update_option('oav_quota_mb', max(10, (int) ($_POST['quota_mb'] ?? OAV_DEFAULT_QUOTA_MB)));
        update_option('oav_package_mb', max(0.0, round((float) ($_POST['package_mb'] ?? OAV_DEFAULT_PACKAGE_MB), 1)));
        $demo = sanitize_key($_POST['demo'] ?? 'auto');
        update_option('oav_demo', in_array($demo, ['auto', 'always', 'never'], true) ? $demo : 'auto');
        $theme = sanitize_key($_POST['theme'] ?? 'light');
        update_option('oav_theme', in_array($theme, ['light', 'dark', 'auto'], true) ? $theme : 'light');
        update_option('oav_list_page', (int) ($_POST['list_page'] ?? 0));
        $presentation = esc_url_raw(wp_unslash($_POST['presentation_url'] ?? ''));
        update_option('oav_presentation_url', preg_match('#^https://#', $presentation) ? $presentation : OAV_Presentation::DEFAULT_URL);
        delete_transient('oav_presentation');
        if (!empty($_POST['pending_role'])) {
            update_option('default_role', 'oav_pending');
        } elseif (get_option('default_role') === 'oav_pending') {
            update_option('default_role', 'subscriber');
        }
        delete_transient('oav_storage');
        self::back(self::SLUG . '-settings', 'Réglages enregistrés.');
    }
}
