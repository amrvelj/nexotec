"""The notification catalogue in code (KAN-216; Dealer Administration FR-A-47).

`notification_event` holds the catalogue in the database; this module is the
same list as declarations, so a call site can name a code the type checker
knows (`NotificationEventCode.NEW_DEVICE_LOGIN`) and the push title can be
rendered in the user's UI language without a round trip to the SPA.

A module that raises a notification registers its events here **and** in a
migration of the platform chain that inserts the `notification_event` rows,
in the same PR (A-RULE-6 applied to notifications). Three tests keep the
copies in step: the migrated rows equal `CATALOGUE`
(`tests/test_migrated_notification_catalogue.py`), every title and body key
is in the SPA's four bundles and every push title below is the bundle's text
word for word (`tests/test_notification_catalogue.py`), and every `notify`
call site in `app/` names a code declared here
(`tests/architecture/test_notify_call_sites_use_registered_codes.py`).

Families: only `security` ships in v1 (D-A-40). `administration` and
`assignment` are defined so their events register against the same
catalogue when their sources ship; none of their events exists yet.
"""

import dataclasses
import enum

from app.platform.models.notification import NotificationFamily, NotificationTiming

LANGUAGES = ("de", "fr", "it", "en")
DEFAULT_LANGUAGE = "de"  # the SPA's default UI language, the mail texts' rule too


class NotificationEventCode(str, enum.Enum):
    # FR-AU-12, FR-AU-26 and D-A-40's security row, in the PRD's order.
    NEW_DEVICE_LOGIN = "security.new_device_login"
    FACTOR_ENROLLED = "security.factor_enrolled"
    FACTOR_REMOVED = "security.factor_removed"
    RECOVERY_CODE_USED = "security.recovery_code_used"
    ACCOUNT_LOCKED = "security.account_locked"
    PASSWORD_CHANGED = "security.password_changed"
    SESSION_REVOKED_BY_OTHER = "security.session_revoked_by_other"
    # Registered now; raised when [P3-1] impersonation ships.
    IMPERSONATION_STARTED = "security.impersonation_started"
    IMPERSONATION_ENDED = "security.impersonation_ended"
    # Linked: registered now; raised when linking ships with the Outlook
    # integration (no linking flow exists today). Revoked: live.
    CONNECTED_ACCOUNT_LINKED = "security.connected_account_linked"
    CONNECTED_ACCOUNT_REVOKED = "security.connected_account_revoked"
    PLATFORM_RECOVERY_ACTION = "security.platform_recovery_action"


@dataclasses.dataclass(frozen=True)
class NotificationEventSpec:
    code: NotificationEventCode
    family: NotificationFamily
    default_timing: NotificationTiming
    switchable: bool
    title_key: str
    body_key: str
    # The push title in each UI language — the bundle's text for `title_key`.
    push_title: dict[str, str]


def _security(code: NotificationEventCode, key: str, push_title: dict[str, str]) -> NotificationEventSpec:
    # The security family is always immediate and cannot be switched (FR-A-48).
    return NotificationEventSpec(
        code=code,
        family=NotificationFamily.SECURITY,
        default_timing=NotificationTiming.IMMEDIATE,
        switchable=False,
        title_key=f"notifications.events.{key}.title",
        body_key=f"notifications.events.{key}.body",
        push_title=push_title,
    )


CATALOGUE: tuple[NotificationEventSpec, ...] = (
    _security(
        NotificationEventCode.NEW_DEVICE_LOGIN,
        "newDeviceLogin",
        {
            "de": "Anmeldung von einem neuen Gerät",
            "fr": "Connexion depuis un nouvel appareil",
            "it": "Accesso da un nuovo dispositivo",
            "en": "Sign-in from a new device",
        },
    ),
    _security(
        NotificationEventCode.FACTOR_ENROLLED,
        "factorEnrolled",
        {
            "de": "Zweiter Faktor eingerichtet",
            "fr": "Deuxième facteur ajouté",
            "it": "Secondo fattore aggiunto",
            "en": "Second factor added",
        },
    ),
    _security(
        NotificationEventCode.FACTOR_REMOVED,
        "factorRemoved",
        {
            "de": "Zweiter Faktor entfernt",
            "fr": "Deuxième facteur supprimé",
            "it": "Secondo fattore rimosso",
            "en": "Second factor removed",
        },
    ),
    _security(
        NotificationEventCode.RECOVERY_CODE_USED,
        "recoveryCodeUsed",
        {
            "de": "Wiederherstellungscode verwendet",
            "fr": "Code de récupération utilisé",
            "it": "Codice di recupero utilizzato",
            "en": "Recovery code used",
        },
    ),
    _security(
        NotificationEventCode.ACCOUNT_LOCKED,
        "accountLocked",
        {
            "de": "Konto vorübergehend gesperrt",
            "fr": "Compte temporairement verrouillé",
            "it": "Account temporaneamente bloccato",
            "en": "Account temporarily locked",
        },
    ),
    _security(
        NotificationEventCode.PASSWORD_CHANGED,
        "passwordChanged",
        {
            "de": "Passwort geändert",
            "fr": "Mot de passe modifié",
            "it": "Password modificata",
            "en": "Password changed",
        },
    ),
    _security(
        NotificationEventCode.SESSION_REVOKED_BY_OTHER,
        "sessionRevokedByOther",
        {
            "de": "Sitzungen von jemand anderem beendet",
            "fr": "Sessions terminées par une autre personne",
            "it": "Sessioni terminate da un'altra persona",
            "en": "Sessions ended by someone else",
        },
    ),
    _security(
        NotificationEventCode.IMPERSONATION_STARTED,
        "impersonationStarted",
        {
            "de": "Der Nexotec-Support arbeitet in Ihrem Konto",
            "fr": "Le support Nexotec travaille dans votre compte",
            "it": "Il supporto Nexotec lavora nel suo account",
            "en": "Nexotec support is working in your account",
        },
    ),
    _security(
        NotificationEventCode.IMPERSONATION_ENDED,
        "impersonationEnded",
        {
            "de": "Support-Sitzung beendet",
            "fr": "Session de support terminée",
            "it": "Sessione di supporto terminata",
            "en": "Support session ended",
        },
    ),
    _security(
        NotificationEventCode.CONNECTED_ACCOUNT_LINKED,
        "connectedAccountLinked",
        {
            "de": "Verbundenes Konto verknüpft",
            "fr": "Compte connecté associé",
            "it": "Account collegato aggiunto",
            "en": "Connected account linked",
        },
    ),
    _security(
        NotificationEventCode.CONNECTED_ACCOUNT_REVOKED,
        "connectedAccountRevoked",
        {
            "de": "Verbundenes Konto getrennt",
            "fr": "Compte connecté dissocié",
            "it": "Account collegato scollegato",
            "en": "Connected account disconnected",
        },
    ),
    _security(
        NotificationEventCode.PLATFORM_RECOVERY_ACTION,
        "platformRecoveryAction",
        {
            "de": "Wiederherstellung durch den Nexotec-Support",
            "fr": "Récupération par le support Nexotec",
            "it": "Recupero da parte del supporto Nexotec",
            "en": "Recovery by Nexotec support",
        },
    ),
)

BY_CODE: dict[str, NotificationEventSpec] = {spec.code.value: spec for spec in CATALOGUE}

# The one push the daily digest sends a user (title + link only, FR-A-50).
DIGEST_PUSH_TITLE_KEY = "notifications.digest.pushTitle"
DIGEST_PUSH_TITLE = {
    "de": "Neue Benachrichtigungen",
    "fr": "Nouvelles notifications",
    "it": "Nuove notifiche",
    "en": "New notifications",
}
# The SPA's inbox page — where the digest push points.
INBOX_LINK = "/notifications"


def language_or_default(language: str | None) -> str:
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def push_title(code: str, language: str | None) -> str:
    return BY_CODE[code].push_title[language_or_default(language)]


def digest_push_title(language: str | None) -> str:
    return DIGEST_PUSH_TITLE[language_or_default(language)]
