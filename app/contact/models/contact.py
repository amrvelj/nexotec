"""Contact: a person or business who is a counterparty to a dealership
transaction (buyer, lessee, service contact) — not an internal User/
employee. GROUP-owned since WP-3 PR-2 (ADR-014) — exactly one contact
record per group, deduplicated group-wide, readable by every dealership in
the group; the pre-WP-3 "not shared cross-tenant in v1" note (spec §1) is
superseded by that migration.

contact_type gates a set of nullable columns rather than splitting into two
tables (Contact PRD ruling, 2026-08-07, same pattern as Vehicle's
`condition` field): company_name/legal_form/tax_id are business-only,
birth_date/nationality are individual-only. Mutual exclusivity is enforced
at the schema/service boundary (ContactCreate validator), not a DB CHECK
constraint, matching every other cross-field business rule in this codebase.

Phase B (Contact PRD v1.0, decisions D-01 to D-16) changed this model in
four ways that are worth reading before touching it:

* `contact_number` — the human-readable business key (K-000001), allocated
  per tenant from `contact_number_sequence`. Staff quote this on the phone
  and it prints on documents; the UUID never leaves the API. Immutable.
* `language` — the contact's correspondence language, mandatory. Distinct
  from the *user's* UI language: a German-speaking advisor routinely serves
  an Italian-speaking contact, and the contract must print in Italian.
* The flat `email`/`phone` columns and `preferred_contact_method` are GONE.
  ContactPhone/ContactEmail are now the single source of truth, and
  `preferred_channel` is the only contact-preference field (D-03, D-04).
  Because the flat columns backed the "at least one contact point" rule,
  ContactCreate now accepts nested phones/emails and writes them in the
  same transaction — see app/schemas/contact.py.
* The tenant-wide unique constraint on `email` is GONE (D-05). Two family
  members or two employees of the same company legitimately share an
  address; shared contact details are a duplicate-detection *signal*
  (FR-04), not a database rule.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin, utcnow
from app.core.blind_index import uid_search_key
from app.core.enum_type import StoredEnum
from app.core.text_collation import IDENTIFIER_TEXT, text_sort_index_key
from app.core.types import GUID, EncryptedString, UTCDateTime
from app.db import Base


class ContactType(str, enum.Enum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class Language(str, enum.Enum):
    """The contact's correspondence language (Contact PRD D-01/FR-13).
    Fixed to the three Swiss national languages plus English — this is the
    set the application ships translations for, not a per-tenant list.
    """

    DE = "de"
    FR = "fr"
    IT = "it"
    EN = "en"


class Salutation(str, enum.Enum):
    """Drives the letter opening in the contact's language (D-15).
    NEUTRAL produces a gender-neutral salutation rather than guessing.
    """

    HERR = "herr"
    FRAU = "frau"
    FIRMA = "firma"
    NEUTRAL = "neutral"


class LegalForm(str, enum.Enum):
    """Fixed Swiss legal-entity taxonomy (Contact PRD) — hardcoded, not
    reference-data, same reasoning as Dealership.FranchiseType: this is a legal
    classification, not a per-tenant configurable list.
    """

    AG = "ag"
    GMBH = "gmbh"
    EINZELFIRMA = "einzelfirma"
    VEREIN = "verein"
    GENOSSENSCHAFT = "genossenschaft"
    WEITERE = "weitere"


class Gender(str, enum.Enum):
    """FR-17, added 2026-08-21. Distinct from `Salutation` (a form of
    address): `gender` is a segmentation fact and NOTHING infers it — not
    the salutation, not the first name. Correspondence always follows
    `salutation`. Defaults to `unspecified`.
    """

    FEMALE = "female"
    MALE = "male"
    OTHER = "other"
    UNSPECIFIED = "unspecified"


class PaymentTerms(str, enum.Enum):
    """FR-17 / FR-18 region 4. The contact's default terms, carried onto
    the invoice by Finance. A per-deal override belongs to the deal, not
    here. Hardcoded, not reference data — a fixed commercial vocabulary,
    same call as `LegalForm`.
    """

    PREPAYMENT = "prepayment"
    NET_10 = "net_10"
    NET_30 = "net_30"
    NET_60 = "net_60"
    ON_DELIVERY = "on_delivery"


class PreferredChannel(str, enum.Enum):
    """How the contact wants to be reached. Sole contact-preference field
    since Phase B — the old `preferred_contact_method` (email/phone/sms)
    overlapped with it and was dropped (D-03). "Which specific number" is
    answered by ContactPhone.is_primary, not by a second enum.

    D-21 (ruled 2026-09-07): the vocabulary is `email` / `phone` / `post` /
    `whatsapp`. WhatsApp is a real Swiss-dealership channel that was absent,
    and the rename ends the `mail`-vs-`letter` ambiguity. `mail` -> `email`,
    `call` -> `phone`, `letter` -> `post` in one migration.

    MESSAGE is retained as a LEGACY member with no clean target — it meant
    SMS, and WhatsApp is not SMS. Pre-D-21 rows holding `message` still load
    through this enum; it is not offered anywhere in the UI and awaits a
    product decision (KAN-54). Do not map it silently.
    """

    EMAIL = "email"
    PHONE = "phone"
    POST = "post"
    WHATSAPP = "whatsapp"
    MESSAGE = "message"  # legacy, D-21 — no target, retained so old rows load


class ConsentScope(str, enum.Enum):
    """FR-23 §1 (added 2026-09-07) — WHAT a per-channel consent covers.
    ADR-067's "invoices yes, marketing no, at the same address" is exactly
    the case a bare `granted` boolean cannot hold. Marketing selection
    reads `consent_granted && consent_scope == MARKETING`; an
    invoicing-scoped grant never authorises a campaign.

    NULL on an existing row means `marketing` — that is what the flag has
    meant until now, and inventing a different reading rewrites history. A
    NEW grant must name its scope.
    """

    MARKETING = "marketing"
    INVOICING = "invoicing"
    SERVICE = "service"


class ConsentSource(str, enum.Enum):
    """FR-23 §1 — HOW consent was given. A closed enum (was free text): a
    free string cannot be localised (FR-13), cannot be reported on, and
    cannot be evidenced consistently, which is the one thing revDSG asks of
    it.
    """

    FORM = "form"
    COUNTER = "counter"
    WEB = "web"
    PHONE = "phone"


class PhoneType(str, enum.Enum):
    """Remapped in WP-3 PR-5 (ADR-067): MOBILE unchanged, PRIVATE->LANDLINE,
    OFFICE->WORK — see that migration's own docstring for the reasoning.
    FAX is new, no existing data.
    """

    MOBILE = "mobile"
    LANDLINE = "landline"
    WORK = "work"
    FAX = "fax"


class EmailType(str, enum.Enum):
    """Remapped in WP-3 PR-5 (ADR-067): PRIVATE->PERSONAL, BUSINESS->WORK —
    same concept either way, "reachable at an address tied to their job/
    company," whether the contact is an individual or a business.
    INVOICING is new, no existing data.
    """

    PERSONAL = "personal"
    WORK = "work"
    INVOICING = "invoicing"


class AddressType(str, enum.Enum):
    DOMICILE = "domicile"
    BILLING = "billing"
    DELIVERY = "delivery"


class ContactLifecycleStatus(str, enum.Enum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    INACTIVE = "inactive"
    MERGED = "merged"
    DO_NOT_CONTACT = "do_not_contact"


class ContactRole(str, enum.Enum):
    """The roles a contact holds (KAN-464; Anto, 2026-10-26: one register,
    roles). A contact is never duplicated because it gains a role (ADR-014).

    Q1 (Anto, 2026-11-14, A): the lifecycle status (prospect / active /
    inactive), the credit block and the advisor belong to the `contact`
    role; contact points, consent, do-not-contact, tags, contact groups and
    the legal basis belong to every contact."""

    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class ContactSource(str, enum.Enum):
    WALK_IN = "walk_in"
    PHONE = "phone"
    WEB_LEAD = "web_lead"
    MARKETPLACE = "marketplace"
    OTHER = "other"


class ContactNumberSequence(Base):
    """Per-GROUP allocator for `Contact.contact_number` (D-02, moved from
    per-dealership to per-group in WP-3 PR-2, ADR-014 — exactly one contact
    record per group, so its number sequence is a group-wide fact too).

    A dedicated counter row rather than a Postgres SEQUENCE: sequences are
    global objects, and we need one independent, gap-tolerant counter per
    group without issuing DDL whenever a dealer group is onboarded.
    Allocation takes a row lock (SELECT ... FOR UPDATE) inside the
    contact-creation transaction, so two concurrent creates for the same
    group serialise on this row and can never receive the same number.
    """

    __tablename__ = "contact_number_sequence"

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, comment="Owned by the platform context (DealerGroup). No DB-level FK (PR-2, ADR-015)."
    )
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Contact(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    """Group-scoped, not TenantScopedMixin (WP-3 PR-2, ADR-014): exactly one
    contact record per group, deduplicated group-wide — the dealership stays
    the tenant for every other entity, but the contact record itself is
    genuinely shared across a group's dealerships, not owned by just one.
    """

    __tablename__ = "contact"
    __table_args__ = (
        UniqueConstraint("group_id", "contact_number", name="uq_contact_group_id_contact_number"),
        # The contact-list sort keys within a group (d4a1f6c8e3b7, ADR-014); the surname one,
        # under the text sort collation, is declared below the class (KAN-740).
        Index("ix_contact_group_id_updated_at", "group_id", "updated_at"),
        Index("ix_contact_group_id_created_at", "group_id", "created_at"),
    )

    # No DB-level FK to dealer_group.id (PR-2, ADR-015). Owned by the
    # platform context; reconciled nightly. Resolved from the token's
    # groupId claim, never from the request body.
    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )

    # Immutable business key, allocated at creation. Indexed because it is a
    # first-class search term (FR-01) — staff search by it constantly.
    contact_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True, info=IDENTIFIER_TEXT)

    contact_type: Mapped[ContactType] = mapped_column(
        StoredEnum(ContactType, length=32), nullable=False, default=ContactType.INDIVIDUAL
    )
    language: Mapped[Language] = mapped_column(
        StoredEnum(Language, length=8), nullable=False, default=Language.DE
    )
    salutation: Mapped[Salutation | None] = mapped_column(
        StoredEnum(Salutation, length=16), nullable=True
    )
    # Individual-only; nullable since a BUSINESS contact has no
    # first/last name (company_name below instead). Mutual exclusivity by
    # contact_type is enforced at the schema/service boundary.
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Individual-only.
    birth_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # FR-17, individual-only. `title` is an academic/professional title
    # (Dr., Prof., lic. iur.) and is FREE TEXT, not an enum — the list is
    # open and a wrong enum forces staff to drop the title rather than
    # record it. Rendered before the name in the letter opening, after the
    # salutation. `gender` is segmentation-only and never inferred (see the
    # Gender docstring); non-null, defaulting to UNSPECIFIED.
    title: Mapped[str | None] = mapped_column(String(50), nullable=True)
    gender: Mapped[Gender] = mapped_column(
        StoredEnum(Gender, length=16), nullable=False, default=Gender.UNSPECIFIED
    )
    # Business-only. Indexed: without it, business contacts were unfindable
    # by name at all (D-06) — the single worst gap Phase B closes.
    # Its sort index, under the text sort collation, is declared below the class (KAN-740).
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    legal_form: Mapped[LegalForm | None] = mapped_column(
        StoredEnum(LegalForm, length=32), nullable=True
    )
    # Encrypted at rest (EncryptedString), same as Dealership.tax_id — CTO
    # ruling, 2026-08-07: consistency, cheap to apply. Format + mod-11 check
    # digit validated at the schema boundary since Phase B (D-16).
    tax_id: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    # KAN-819: the keyed blind index of `tax_id` (app/core/blind_index.py), so
    # the contact search finds a business contact by its UID without
    # decrypting a row. Written by `_keep_tax_id_search_key` below on every
    # assignment of `tax_id` — create, PATCH, merge and erasure alike — never
    # by hand. Says nothing without the application's key.
    tax_id_search_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="KAN-819: HMAC-SHA256 of the canonical UID in tax_id (app/core/blind_index.py); kept by the model.",
    )
    preferred_channel: Mapped[PreferredChannel | None] = mapped_column(
        StoredEnum(PreferredChannel, length=16), nullable=True
    )

    # Swiss address, optional at creation (all-or-nothing: either every
    # sub-field is provided or none are — see schemas.contact.ContactCreate).
    address_street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_house_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Widened from 4 chars: foreign postal codes are longer than Switzerland's
    # (D-11), and the column was silently truncating them.
    address_postal_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    address_locality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # STALE COMMENT CORRECTED (KAN-30, 2026-09-04): the D-13 postal-code ->
    # canton dataset landed (app.core.postal_codes.derive_canton) and the
    # contact-list `?canton=` filter depends on it — "nothing depends on
    # it" is no longer true anywhere in this codebase. What IS still true
    # of THIS column specifically: it is the WP-3 PR-5 (ADR-067) frozen
    # legacy mirror (see legacy_address_mirror below), never written to
    # since that migration, so it stays NULL forever regardless. The live,
    # derived value is ContactAddress.address_canton on the primary
    # domicile row, never accepted from the client, left NULL for foreign
    # addresses.
    address_canton: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    lifecycle_status: Mapped[ContactLifecycleStatus] = mapped_column(
        StoredEnum(ContactLifecycleStatus, length=32),
        nullable=False,
        default=ContactLifecycleStatus.PROSPECT,
    )
    source: Mapped[ContactSource | None] = mapped_column(
        StoredEnum(ContactSource, length=32), nullable=True
    )
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Self-FK, set on merge — points at the surviving record this one was
    # merged into. Only ever written by POST /v1/contacts/{id}/merge.
    duplicate_of_contact_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("contact.id"), nullable=True)

    # No enforcement/logging beyond standard audit (Swiss addendum Round 2
    # Q4 #8: TCPA/CAN-SPAM consent capture dropped from v1 acceptance
    # criteria) — field stays, default false, no method/timestamp tracking.
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # WP-8 PR-6 (ADR-065/S-D19) — a genuinely new concept, distinct from
    # `lifecycle_status == DO_NOT_CONTACT` above: a credit block stops the
    # CONTRACT only (quoting a blocked contact is often how the block
    # gets resolved), while do-not-contact stops both offer and contract.
    # No existing hook to build on — confirmed by grep before this field
    # was added.
    credit_block: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credit_block_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    credit_blocked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # --- FR-17 / FR-18, ratified 2026-08-21, built in Phase B2 (KAN-50) ---
    # Contact (both types). `website` is scheme-normalised at the schema
    # boundary and rendered as a live link on the 360 — NOT business-gated
    # (D-23, a sole trader is both). `newsletter` is gate 3 of the D-17
    # three-gate hierarchy (legal basis = marketing_consent; where-exercised
    # = per-channel consent scope; subscription = this) — a single boolean,
    # not named lists; the send rule lives in Marketing, not here.
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    newsletter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Commercial standing (both types) — FR-18 region 4, "on what terms may
    # I sell to them?". Finance's region. `credit_limit` is ADVISORY in v1:
    # surfaced, never enforced (enforcement needs an open-balance figure
    # only Finance holds). `iban` is a payout destination for a refund or a
    # trade-in, mod-97 validated — never a payment instrument (ADR-037).
    # `vat_registered` does NOT change the sales document (one gross price,
    # ADR-057); it is recorded because Finance and Stock's purchase booking
    # need it. Its `false` is also the default, so `vat_status_confirmed`
    # says someone has checked it (KAN-295): a trade-in from an unconfirmed
    # non-registered contact books a fiktiver Vorsteuerabzug on a guess.
    payment_terms: Mapped[PaymentTerms | None] = mapped_column(
        StoredEnum(PaymentTerms, length=20), nullable=True
    )
    credit_limit: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    vat_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vat_status_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # Relationship (both types) — FR-18 region 5, "what is our history and
    # what happens next?".
    #
    # advisor_* is the P-2 three-column denormalised-label pattern (id +
    # label + refresh timestamp, NO cross-context FK), same shape as
    # vehicle/models/configuration.py's catalogue/vehicle links. The
    # responsible sales advisor is a platform User; on create it defaults to
    # the acting user (D-24), but the column stays nullable so "unassigned"
    # is expressible and the UI can clear it, and the default is never
    # re-applied on update.
    advisor_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the platform context (User). No DB-level FK (P-2)."
    )
    advisor_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    advisor_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # When the relationship began — DISTINCT from created_at (when the row
    # was typed): a migrated contact of twenty years' standing has
    # yesterday's created_at and must not read as new.
    contact_since: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # The next planned contact — the one field in this region a human writes
    # (last_contact_at and service_due are derived, Phase C).
    next_follow_up: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # Free internal note. PII BY DEFAULT — audit-logged (FR-11, via
    # _PII_FIELDS in the service), and the attach point for the revDSG
    # export and FR-14 anonymisation once those are built.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance — the dealership that CREATED the record, not the owner
    # (the GROUP owns the contact, ADR-014; group_id above is the tenancy
    # key). Reporting and provenance only: never scopes a read, never gates
    # a write, and a contact is never moved between dealerships. Plain GUID
    # with a comment naming the owner (P-2), no FK.
    dealership_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the platform context (Dealership). No DB-level FK."
    )

    @validates("tax_id")
    def _keep_tax_id_search_key(self, _key: str, value: str | None) -> str | None:
        """KAN-819: the search key follows every write of `tax_id`, so no
        writer can leave the two apart."""

        self.tax_id_search_key = uid_search_key(value)
        return value

    @property
    def legacy_address_mirror(self) -> dict[str, str | None] | None:
        """READ-ONLY MIRROR since WP-3 PR-5 (ADR-067) — frozen at whatever
        value existed before that migration, no longer written to by
        create_contact/update_contact, dropped entirely in Phase C.
        ContactAddress child rows are the single source of truth now; the
        API-facing `address` projection comes from there (the primary
        `domicile` row), not from this property — see
        app.contact.services.projections' projection function. Deliberately
        NOT named `address`: ContactRead.address is a ContactAddressRead
        (a full child row shape) now, and this property's plain dict would
        silently mismatch it if pydantic ever picked this up by attribute
        name during from_attributes validation.
        """

        if self.address_street is None:
            return None
        return {
            "street": self.address_street,
            "house_number": self.address_house_number,
            "postal_code": self.address_postal_code,
            "locality": self.address_locality,
            "canton": self.address_canton,
            "country": self.address_country,
        }


# KAN-740 (ADR-190): the indexes behind the contact list's word sorts are built under the text
# sort collation, the order those sorts run in — an index in the default collation serves neither
# the ORDER BY nor the keyset cursor's `>`.
Index(
    "ix_contact_group_id_last_name",
    Contact.__table__.c.group_id,
    text_sort_index_key(Contact.__table__.c.last_name),
)
Index("ix_contact_company_name", text_sort_index_key(Contact.__table__.c.company_name))


class ContactChannelMixin:
    """The six facts every contact-channel row carries, regardless of kind
    (WP-3 PR-5, ADR-067). Not a table itself — contact_phone/email/address
    stay three separate tables (a polymorphic contact_point table "sounds
    tidier and produces a table where half the columns are null on every
    row" — explicitly rejected).

    is_primary: exactly one per (contact_id, type) — enforced in the
    service layer transactionally (unset the previous primary in the same
    transaction), same "not a high-contention field" reasoning as before,
    now scoped to the type-group rather than the whole contact.

    valid_from/valid_to: a contact who moves keeps their old address —
    last year's invoices were sent somewhere. A row with valid_to in the
    past is "closed": it stays readable but is excluded from the six
    projections and from document rendering.

    do_not_use/do_not_use_reason: a bounced email or dead number is a fact
    worth recording, not a row to delete — deleting it means the next
    advisor re-enters it from the same business card. Excluded from
    projections, same as a closed row.

    consent_*: per channel, not one global flag on Contact — a contact
    may accept invoices at an address and refuse marketing at the same one.
    Contact.marketing_consent remains the legal basis; this records WHERE
    it was exercised, per revDSG's evidentiary expectation.
    """

    label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_from: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    valid_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    do_not_use: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    do_not_use_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # FR-23 §1 (KAN-52). `consent_scope` NULL means `marketing` on rows that
    # predate the scope; a new grant names it. `consent_source` is the
    # closed enum that replaced the free-text column — unmappable legacy
    # values were set NULL and reported by the migration, not guessed.
    consent_scope: Mapped[ConsentScope | None] = mapped_column(
        StoredEnum(ConsentScope, length=16), nullable=True
    )
    consent_source: Mapped[ConsentSource | None] = mapped_column(
        StoredEnum(ConsentSource, length=16), nullable=True
    )
    consent_timestamp: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ContactPhone(ContactChannelMixin, PrimaryKeyMixin, TimestampMixin, Base):
    """Multi-valued phone numbers (Contact PRD, 2026-08-07 CTO ruling).
    group_id is denormalized from Contact.group_id at insert time (moved
    from tenant_id in WP-3 PR-2, ADR-014 — a child collection of a record
    that is now genuinely group-owned inherits the parent's scoping key),
    same reasoning as every other group-scoped child table — Postgres
    unique constraints can't be enforced across a join.
    """

    __tablename__ = "contact_phone"
    __table_args__ = (UniqueConstraint("contact_id", "phone_e164", name="uq_contact_phone_contact_id_e164"),)

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("contact.id"), nullable=False, index=True)
    phone_type: Mapped[PhoneType] = mapped_column(StoredEnum(PhoneType, length=16), nullable=False)
    # E.164 with the country prefix.
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    # Digits-only projection of phone_e164, maintained by the service layer.
    # Exists so a counter clerk can type '079 123 45 67' and match a number
    # stored as '+41791234567' (FR-01) with an indexed LIKE instead of a
    # per-row regex. Never returned by the API.
    phone_normalised: Mapped[str] = mapped_column(String(20), nullable=False, index=True)


class ContactEmail(ContactChannelMixin, PrimaryKeyMixin, TimestampMixin, Base):
    """Multi-valued email addresses — same shape/reasoning as ContactPhone.
    RFC-validated at the schema boundary.
    """

    __tablename__ = "contact_email"
    __table_args__ = (
        UniqueConstraint("contact_id", "email_address", name="uq_contact_email_contact_id_address"),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("contact.id"), nullable=False, index=True)
    email_type: Mapped[EmailType] = mapped_column(StoredEnum(EmailType, length=16), nullable=False)
    email_address: Mapped[str] = mapped_column(String(254), nullable=False, index=True)


class ContactExternalId(PrimaryKeyMixin, TimestampMixin, Base):
    """Per-group CRM/OEM system linkage (Contact PRD "external IDs, settable
    by system administrator"), group-scoped since WP-3 PR-2 (PRD-Contacts:
    "unique per (group, systemName, externalId)") — same rationale as
    Contact itself: one contact per group, so its external-system links
    are a group-wide fact. Write access is platform_admin-only (Anto's
    ruling, 2026-08-07, overriding the earlier dealer_admin default) — read
    stays open to any authenticated tenant role, same pattern as an audit-log
    endpoint being readable but not writable by regular roles.

    system_name is a plain validated string, not backed by a lookup table —
    each dealer names their own CRM/OEM systems independently, and this is
    the opposite of ReferenceList's platform_admin-write/globally-shared
    model (CTO correction, 2026-08-07: don't extend that framework here).
    """

    __tablename__ = "contact_external_id"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "system_name", "external_id", name="uq_contact_external_id_group_system_external"
        ),
        UniqueConstraint("contact_id", "system_name", name="uq_contact_external_id_contact_system"),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("contact.id"), nullable=False, index=True)
    system_name: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)


class ContactAddress(ContactChannelMixin, PrimaryKeyMixin, TimestampMixin, Base):
    """Multi-valued postal addresses (WP-3 PR-5, ADR-067) — new in this
    package; Contact.address_* (below) becomes a read-only mirror, frozen
    at its pre-migration value, dropped in Phase C. The six read-model
    projections read from here, never from those columns.

    The all-or-nothing address rule (every sub-field supplied or none)
    applies PER ROW now, not per contact. address_line2 (FR-17) is exempt
    from that rule — c/o, department, building or PO box, optional on a row
    that otherwise has a complete address.
    """

    __tablename__ = "contact_address"

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("contact.id"), nullable=False, index=True)
    address_type: Mapped[AddressType] = mapped_column(
        StoredEnum(AddressType, length=16), nullable=False
    )
    address_street: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_house_number: Mapped[str] = mapped_column(String(20), nullable=False)
    address_postal_code: Mapped[str] = mapped_column(String(12), nullable=False)
    address_locality: Mapped[str] = mapped_column(String(100), nullable=False)
    address_canton: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address_country: Mapped[str] = mapped_column(String(2), nullable=False, default="CH")


class ContactTag(PrimaryKeyMixin, TimestampMixin, Base):
    """A free per-group label on a contact (FR-17, FR-18 region 5):
    *Flottenkunde*, *Oldtimer*, *Preisbewusst*. **Deliberately NOT a
    reference list** — the value of a tag is that a dealership invents it on
    Tuesday, which is the opposite of ReferenceList's platform-admin-managed,
    globally-shared model. A child row rather than a JSON/ARRAY column on
    `contact` so "filterable as one predicate per tag" (ADR-058) is a plain
    EXISTS on both the Postgres lane of record and the SQLite fast lane.

    group_id is denormalised from Contact.group_id at insert time, same as
    every other group-scoped child table (a unique constraint cannot be
    enforced across a join).
    """

    __tablename__ = "contact_tag"
    __table_args__ = (UniqueConstraint("contact_id", "tag", name="uq_contact_tag_contact_id_tag"),)

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("contact.id"), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(60), nullable=False)


class ContactGroupAssignment(PrimaryKeyMixin, TimestampMixin, Base):
    """A contact group on a contact (KAN-466; Anto, 2026-10-26): Privat,
    Gewerbe, Flotte, Händler, … — values of the platform reference list
    `contact_group`, **not** free text (that is `ContactTag`). A contact may
    carry several. Which codes may be set is the acting Betrieb's choice in
    Dealer Administration (`platform.public.contact_group_codes`); a code
    that Betrieb has not switched on — switched off since, or set by a sister
    Betrieb of the group — stays on the contact and is kept by every write
    from there.

    `value_code` names the platform's reference value by its code, like every
    reference value a record carries; no FK across contexts (rule 2).
    `group_id` is denormalised from Contact.group_id, as on `contact_tag`.
    """

    __tablename__ = "contact_group_assignment"
    __table_args__ = (
        UniqueConstraint("contact_id", "value_code", name="uq_contact_group_assignment_contact_id_value_code"),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("contact.id"), nullable=False, index=True)
    value_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Owned by the platform context (reference list contact_group, by value_code). No DB-level FK.",
    )


class ContactRoleAssignment(PrimaryKeyMixin, TimestampMixin, Base):
    """A role a contact holds, with the period it held it (KAN-464 PR-1).

    One open row (`valid_to` NULL) per (contact, role): giving a role opens
    a row, taking it away closes it — the closed rows are the role's history,
    never deleted. Every contact migrated as `contact`
    (alembic contact `a4c1e6b9d2f7`); a create without `roles` still gets
    `contact`, so no existing writer changes.

    The table is `contact_role` until the breaking rename (KAN-464 PR-3)
    moves every `contact_*` table to `contact_*` together; it sorts after
    `contact` so a PATCH flushes the contact row first and waits on a
    merge's lock before inserting a role row (the KAN-390 ordering,
    `services/contact.py::update_contact`).
    """

    __tablename__ = "contact_role"
    __table_args__ = (
        Index(
            "uq_contact_role_contact_id_role_open",
            "contact_id",
            "role",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
            sqlite_where=text("valid_to IS NULL"),
        ),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("contact.id"), nullable=False, index=True)
    role: Mapped[ContactRole] = mapped_column(StoredEnum(ContactRole, length=16), nullable=False)
    valid_from: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    valid_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
