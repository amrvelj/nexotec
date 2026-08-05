"""Shared reference constants that aren't yet promoted to the
ReferenceList/ReferenceValue admin-managed framework (Swiss addendum Round 2
Q3). Cantons are a fixed, rarely-changing 26-value set defined by federal
law, not business taxonomy — safe to hard-code for the shell.
"""

CANTON_CODES: frozenset[str] = frozenset(
    {
        "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE",
        "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH",
    }
)
