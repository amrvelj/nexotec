"""Platform's `documents` sub-package (ADR-163): document templates and their rendering,
letterhead, standard texts and their placeholders, and the branding preview route. It imports
only itself and `identity`, `organisation` and `reference` below it; the frozen
`app/platform/services/standard_text_seed.py` counts as documents (ADR-133) (KAN-635).
"""
