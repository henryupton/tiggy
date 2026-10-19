function() {
  var payload = {
    // Envelope
    event_name:        {{DLV - event_name}},
    container_version: {{Container Version}},
    // Page
    page_hostname: {{Page Hostname}},
    page_location: {{Page URL}},
    page_referrer: {{Referrer}},
    utm_campaign:  {{URL - utm_campaign}},
    // Event
    build_number:         {{DLV - event_attributes.build_number}},
    download_format:      {{DLV - event_attributes.download_format}},
    element_text:         {{DLV - event_attributes.element_text}},
    environment:          {{DLV - event_attributes.environment}},
    item_brand:           {{DLV - event_attributes.item_brand}},
    item_category:        {{DLV - event_attributes.item_category}},
    item_category2:       {{DLV - event_attributes.item_category2}},
    item_id:              {{DLV - event_attributes.item_id}},
    item_name:            {{DLV - event_attributes.item_name}},
    item_variant:         {{DLV - event_attributes.item_variant}},
    license_type:         {{DLV - event_attributes.license_type}},
    link_url:             {{DLV - event_attributes.link_url}},
    list_name:            {{DLV - event_attributes.list_name}},
    method:               {{DLV - event_attributes.method}},
    page_section:         {{DLV - event_attributes.page_section}},
    page_template:        {{DLV - event_attributes.page_template}},
    plan_name:            {{DLV - event_attributes.plan_name}},
    platform:             {{DLV - event_attributes.platform}},
    search_filters:       {{DLV - event_attributes.search_filters}},
    search_results_count: {{DLV - event_attributes.search_results_count}},
    search_term:          {{DLV - event_attributes.search_term}},
    site_language:        {{DLV - event_attributes.site_language}},
    sort_order:           {{DLV - event_attributes.sort_order}},
    user_logged_in:       {{DLV - event_attributes.user_logged_in}},
    wishlist_id:          {{DLV - event_attributes.wishlist_id}},
    // Debug
    debug_mode: {{Debug Mode}},
  };
  payload.sent_at = new Date().toISOString();
  return payload;
}