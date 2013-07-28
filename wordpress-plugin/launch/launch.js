jQuery(function() {

    var short = jQuery(".cusf-notam-info-short");
    var long = jQuery(".cusf-notam-info-long");

    if (short.length !== 0 || long.length !== 0) {
        jQuery.ajax({
            url: "/notam-ajax/web.json",
            dataType: "json",
        }).always(function () {
            short.text("Unknown")
            long.text("Unknown")
        }).done(function (data) {
            short.text(data.short);
            long.text(data.long);
        });
    }

});
