"""Aftersales — the workshop (Aftersales build slice 1; ADR-211 to ADR-215).

Since KAN-810: the workshop order with its jobs and positions
(`models/service_order.py`), its rules (`services/`), its API under
`/v1/service-orders` and its `public` door. The stay of the car, the clock,
the check and «Rechnungsbereit» come with the slice's later tickets.
"""
