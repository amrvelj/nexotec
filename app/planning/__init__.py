"""Planning — the thirteenth bounded context (KAN-804, ADR on Decision A of
Aftersales slice 1, amending ADR-131): resources (people, workplaces,
bookable cars) with their types, qualifications and working hours; bookings
and blocks; the overlap rule; the record of an overruled warning.

Empty today, like `aftersales` and `parts`: the package exists so every
contract, registry and layer rule names it from the first day. It sits
directly above `platform` and below `integration`, and calls `platform`
only. An order, a car or a customer is known here only as an id with the
label the caller passes in (the three-column pattern). KAN-808 builds it.
"""
