<?php
/*
Plugin Name: Launch Announcement
Plugin URI: http://www.cusf.co.uk
Description: Shows launch announcement in sidebar on homepage
Authors: Edward Cunningham, Daniel Richman
Version: 2
Author URI: http://www.cusf.co.uk
*/

function launch_print_stylesheet()
{
    ?>
        <style type="text/css">
        .launch {
          border: 5px solid orange;
          padding: 1em;
          font-weight: bold;
          width: 100%;
          font-size: 1.2em;
          text-align: center;
          background: #333;
        }
        </style>
    <?php
}

function launch_get_text()
{
    $beta = (isset($_GET["notam-info-beta"]) ? '-beta' : '');
    $url = "http://www.danielrichman.co.uk/cusf-notam-info$beta/web.json";

    $curl = curl_init($url);
    curl_setopt($curl, CURLOPT_RETURNTRANSFER, 1);
    curl_setopt($curl, CURLOPT_CONNECTTIMEOUT, 1);
    $data = curl_exec($curl);
    $code = curl_getinfo($curl, CURLINFO_HTTP_CODE);
    curl_close($curl);

    if ($data === false || $code !== 200)
        return array("short" => "Unknown", "long" => "");
    else
        return json_decode($data, true);
}

function launch_short_text()
{
    $d = launch_get_text();
    return $d["short"];
}

function launch_long_text()
{
    $d = launch_get_text();
    return $d["long"];
}

function widget_launch($args)
{
    if (is_home()) {
        extract($args);
        echo $before_widget;
        echo "<div class=\"launch\"><a href=\"launches\">";
        echo launch_short_text();
        echo "</a></div>";
        echo $after_widget;
    }
}
 
function launch_init()
{
    register_sidebar_widget(__('Launch Announcement'), 'widget_launch');
}

add_action('wp_print_styles', 'launch_print_stylesheet');
add_action("plugins_loaded", "launch_init");
add_shortcode("launch-announcement", "launch_long_text");

?>
