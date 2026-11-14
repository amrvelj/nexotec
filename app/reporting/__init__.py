"""The reporting context — projections built from events (CLAUDE.md; ADR-131
puts it on top of the layers, consuming events only).

Its first projection is the public stock projection (KAN-225, FR-A-27): what
the Nexotec-hosted dealer website may show of a Betrieb's stock, built from
Stock's `inventory.stock_item.website_listing_changed`. Nothing here calls
another context synchronously; the nightly reconciliation alone reads across
(ADR-107).
"""
