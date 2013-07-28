<?php
/*
Plugin Name: Launch Announcement
Plugin URI: http://www.cusf.co.uk
Description: Shows launch announcement in sidebar on homepage
Authors: Edward Cunningham, Daniel Richman
Version: 3
Author URI: http://www.cusf.co.uk
*/

function launch_long_text()
{
    return "<span class='cusf-notam-info-long'></span>";
}

function widget_launch($args)
{
    if (is_home())
    {
        extract($args);
        echo $before_widget;
        echo "<div class='launch'><a href='launches'>";
        echo "<span class='cusf-notam-info-short'></span>";
        echo "</a></div>";
        echo $after_widget;
    }
}
 
function launch_init()
{
    register_sidebar_widget(__('Launch Announcement'), 'widget_launch');
}

function launch_enqueue_files()
{
    wp_enqueue_style('launch', plugins_url("launch/launch.css"), false);
    wp_enqueue_script('launch', plugins_url("launch/launch.js"), array("jquery"));
}

add_action('wp_enqueue_scripts', 'launch_enqueue_files');
add_action("plugins_loaded", "launch_init");
add_shortcode("launch-announcement", "launch_long_text");

?>
