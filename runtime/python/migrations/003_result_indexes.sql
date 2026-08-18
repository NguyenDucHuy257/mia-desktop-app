CREATE INDEX invoice_overviews_account_direction_id_idx
  ON invoice_overviews(account_id, direction, overview_id);
CREATE INDEX invoice_details_overview_id_idx
  ON invoice_details(overview_id, detail_id);
