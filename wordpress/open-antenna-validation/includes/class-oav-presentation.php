<?php
/**
 * [oav_presentation]: the presentation of the project, read from GitHub (docs/presentation.md
 * of the repository), so that the site follows the project without being edited.
 *
 * Kept for an hour; the last good copy is kept as an option for when GitHub does not answer,
 * and the copy shipped with the plugin (assets/presentation.md) comes last.
 * Attributes: titre="oui" keeps the first heading (default: dropped, the page has its title).
 */

if (!defined('ABSPATH')) {
    exit;
}

class OAV_Presentation
{
    public const DEFAULT_URL = 'https://raw.githubusercontent.com/Bob56fr/openAntennaValidation/main/docs/presentation.md';
    private const CACHE = 'oav_presentation';
    private const LAST_GOOD = 'oav_presentation_last';

    public static function register(): void
    {
        add_shortcode('oav_presentation', [self::class, 'shortcode']);
    }

    public static function url(): string
    {
        $url = (string) get_option('oav_presentation_url', self::DEFAULT_URL);
        return $url !== '' ? $url : self::DEFAULT_URL;
    }

    /** Markdown source: cache, GitHub, last good copy, copy shipped with the plugin. */
    public static function source(bool $fresh = false): string
    {
        $cached = $fresh ? false : get_transient(self::CACHE);
        if (is_string($cached) && $cached !== '') {
            return $cached;
        }
        $response = wp_remote_get(self::url(), ['timeout' => 8]);
        if (!is_wp_error($response) && wp_remote_retrieve_response_code($response) === 200) {
            $text = (string) wp_remote_retrieve_body($response);
            if ($text !== '' && strlen($text) < 500000) {
                set_transient(self::CACHE, $text, HOUR_IN_SECONDS);
                update_option(self::LAST_GOOD, $text, false);
                return $text;
            }
        }
        // GitHub unreachable: try again in ten minutes, show what we have meanwhile.
        $last = (string) get_option(self::LAST_GOOD, '');
        if ($last === '') {
            $file = OAV_DIR . 'assets/presentation.md';
            $last = is_readable($file) ? (string) file_get_contents($file) : '';
        }
        if ($last !== '') {
            set_transient(self::CACHE, $last, 10 * MINUTE_IN_SECONDS);
        }
        return $last;
    }

    public static function shortcode($atts): string
    {
        $atts = shortcode_atts(['titre' => 'non'], $atts, 'oav_presentation');
        $text = self::source();
        if ($text === '') {
            return '<p>Présentation momentanément indisponible.</p>';
        }
        if ($atts['titre'] !== 'oui') {
            $text = preg_replace('/\A\s*# [^\n]*\n/', '', $text, 1);
        }
        wp_enqueue_style('oav');
        $theme = OAV_DB::theme();
        $attr = $theme === 'auto' ? '' : ' data-theme="' . esc_attr($theme) . '"';
        return '<div class="oav-root oav-doc alignwide"' . $attr . '><div class="wrap">' . self::render($text) . '</div></div>';
    }

    // --- a small Markdown renderer: what docs/presentation.md uses, nothing more --------------

    /** Headings, paragraphs, lists, tables, bold, italics, code, links. Everything escaped. */
    public static function render(string $markdown): string
    {
        $lines = preg_split('/\r\n|\r|\n/', $markdown);
        $html = '';
        $para = [];
        $list = null;
        $table = [];
        $flush_para = function () use (&$para, &$html): void {
            if ($para) {
                $html .= '<p>' . self::inline(implode(' ', $para)) . "</p>\n";
                $para = [];
            }
        };
        $flush_list = function () use (&$list, &$html): void {
            if ($list) {
                $html .= '<' . $list['tag'] . '>' . implode('', array_map(fn($i) => '<li>' . self::inline($i) . '</li>', $list['items'])) . '</' . $list['tag'] . ">\n";
                $list = null;
            }
        };
        $flush_table = function () use (&$table, &$html): void {
            if (count($table) >= 2) {
                $cells = fn($row) => array_map('trim', explode('|', trim(trim($row), '|')));
                $head = $cells($table[0]);
                $html .= '<div class="tbl-wrap"><table><thead><tr>' . implode('', array_map(fn($c) => '<th>' . self::inline($c) . '</th>', $head)) . '</tr></thead><tbody>';
                foreach (array_slice($table, 2) as $row) {
                    $html .= '<tr>' . implode('', array_map(fn($c) => '<td>' . self::inline($c) . '</td>', $cells($row))) . '</tr>';
                }
                $html .= "</tbody></table></div>\n";
            }
            $table = [];
        };
        foreach ($lines as $line) {
            $trim = trim($line);
            if ($trim !== '' && $trim[0] === '|') {
                $flush_para();
                $flush_list();
                $table[] = $trim;
                continue;
            }
            $flush_table();
            if ($trim === '') {
                $flush_para();
                $flush_list();
            } elseif (preg_match('/^(#{1,4})\s+(.*)$/', $trim, $m)) {
                $flush_para();
                $flush_list();
                $level = strlen($m[1]);
                $html .= "<h$level>" . self::inline($m[2]) . "</h$level>\n";
            } elseif (preg_match('/^[-*]\s+(.*)$/', $trim, $m) || preg_match('/^\d+\.\s+(.*)$/', $trim, $n)) {
                $flush_para();
                $tag = isset($m[1]) ? 'ul' : 'ol';
                if ($list && $list['tag'] !== $tag) {
                    $flush_list();
                }
                $list = $list ?: ['tag' => $tag, 'items' => []];
                $list['items'][] = $m[1] ?? $n[1];
            } elseif ($list && preg_match('/^\s{2,}\S/', $line)) {
                $list['items'][count($list['items']) - 1] .= ' ' . $trim;  // continued item
            } else {
                $flush_list();
                $para[] = $trim;
            }
        }
        $flush_table();
        $flush_para();
        $flush_list();
        return $html;
    }

    private static function inline(string $text): string
    {
        $out = esc_html($text);
        $out = preg_replace('/`([^`]+)`/', '<code>$1</code>', $out);
        $out = preg_replace('/\*\*([^*]+)\*\*/', '<strong>$1</strong>', $out);
        $out = preg_replace('/(^|[\s(«])\*([^*\s][^*]*)\*/u', '$1<em>$2</em>', $out);
        return preg_replace_callback('/\[([^\]]+)\]\(([^)\s]+)\)/', function ($m) {
            $url = html_entity_decode($m[2], ENT_QUOTES);
            if (!preg_match('#^https?://#', $url)) {
                return $m[1];
            }
            return '<a href="' . esc_url($url) . '" rel="noopener">' . $m[1] . '</a>';
        }, $out);
    }
}
