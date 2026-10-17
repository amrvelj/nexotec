"""The two platform e-mails (FR-AU-13/14/15, KAN-196) in the four first-class
languages (DE, FR, IT, EN). Picked by the user's UI language preference; a
user who never set one reads German, the default UI language — the same
rule the SPA applies. No link carries anything but the single-use token."""

import dataclasses

_LANGUAGES = ("de", "fr", "it", "en")


@dataclasses.dataclass(frozen=True)
class MailText:
    subject: str
    body: str


_INVITATION = {
    "de": MailText(
        subject="Ihre Einladung zu Nexotec DMS",
        body=(
            "Guten Tag {first_name}\n\n"
            "{dealership} hat für Sie einen Zugang zu Nexotec DMS eingerichtet. "
            "Wählen Sie Ihr Passwort über diesen Link (gültig 14 Tage, einmal verwendbar):\n\n{link}\n\n"
            "Falls Sie diese Einladung nicht erwarten, ignorieren Sie diese Nachricht.\n"
        ),
    ),
    "fr": MailText(
        subject="Votre invitation à Nexotec DMS",
        body=(
            "Bonjour {first_name}\n\n"
            "{dealership} vous a ouvert un accès à Nexotec DMS. "
            "Choisissez votre mot de passe via ce lien (valable 14 jours, utilisable une fois) :\n\n{link}\n\n"
            "Si vous n'attendiez pas cette invitation, ignorez ce message.\n"
        ),
    ),
    "it": MailText(
        subject="Il suo invito a Nexotec DMS",
        body=(
            "Buongiorno {first_name}\n\n"
            "{dealership} le ha creato un accesso a Nexotec DMS. "
            "Scelga la sua password tramite questo link (valido 14 giorni, utilizzabile una volta):\n\n{link}\n\n"
            "Se non si aspettava questo invito, ignori questo messaggio.\n"
        ),
    ),
    "en": MailText(
        subject="Your invitation to Nexotec DMS",
        body=(
            "Hello {first_name}\n\n"
            "{dealership} has set up your access to Nexotec DMS. "
            "Choose your password through this link (valid for 14 days, usable once):\n\n{link}\n\n"
            "If you were not expecting this invitation, ignore this message.\n"
        ),
    ),
}

_RESET = {
    "de": MailText(
        subject="Nexotec DMS: Passwort zurücksetzen",
        body=(
            "Guten Tag {first_name}\n\n"
            "Über diesen Link setzen Sie Ihr Passwort neu (gültig 1 Stunde, einmal verwendbar):\n\n{link}\n\n"
            "Falls Sie das nicht angefordert haben, ignorieren Sie diese Nachricht; Ihr Passwort bleibt unverändert.\n"
        ),
    ),
    "fr": MailText(
        subject="Nexotec DMS : réinitialiser le mot de passe",
        body=(
            "Bonjour {first_name}\n\n"
            "Réinitialisez votre mot de passe via ce lien (valable 1 heure, utilisable une fois) :\n\n{link}\n\n"
            "Si vous n'avez rien demandé, ignorez ce message ; votre mot de passe reste inchangé.\n"
        ),
    ),
    "it": MailText(
        subject="Nexotec DMS: reimpostare la password",
        body=(
            "Buongiorno {first_name}\n\n"
            "Reimposti la sua password tramite questo link (valido 1 ora, utilizzabile una volta):\n\n{link}\n\n"
            "Se non ha richiesto nulla, ignori questo messaggio; la sua password resta invariata.\n"
        ),
    ),
    "en": MailText(
        subject="Nexotec DMS: reset your password",
        body=(
            "Hello {first_name}\n\n"
            "Reset your password through this link (valid for 1 hour, usable once):\n\n{link}\n\n"
            "If you did not ask for this, ignore this message; your password stays as it is.\n"
        ),
    ),
}


def _pick(texts: dict[str, MailText], language: str | None) -> MailText:
    return texts[language if language in _LANGUAGES else "de"]


def invitation(*, language: str | None, first_name: str, dealership: str, link: str) -> MailText:
    text = _pick(_INVITATION, language)
    return MailText(text.subject, text.body.format(first_name=first_name, dealership=dealership, link=link))


_EMAIL_CHANGED = {
    "de": MailText(
        subject="Nexotec DMS: Ihre Anmeldeadresse wurde geändert",
        body=(
            "Guten Tag {first_name}\n\n"
            "Die E-Mail-Adresse Ihres Nexotec-Zugangs wurde auf {new_email} geändert. Ihre Sitzungen wurden "
            "beendet; Anmeldung und Passwort-Links laufen ab jetzt über die neue Adresse.\n\n"
            "Falls Sie diese Änderung nicht erwartet haben, wenden Sie sich sofort an Ihren Betrieb.\n"
        ),
    ),
    "fr": MailText(
        subject="Nexotec DMS : votre adresse de connexion a été modifiée",
        body=(
            "Bonjour {first_name}\n\n"
            "L’adresse e-mail de votre accès Nexotec a été remplacée par {new_email}. Vos sessions ont été "
            "terminées ; la connexion et les liens de mot de passe passent désormais par la nouvelle adresse.\n\n"
            "Si vous n’attendiez pas ce changement, contactez immédiatement votre entreprise.\n"
        ),
    ),
    "it": MailText(
        subject="Nexotec DMS: il tuo indirizzo di accesso è stato modificato",
        body=(
            "Buongiorno {first_name}\n\n"
            "L’indirizzo e-mail del tuo accesso Nexotec è stato cambiato in {new_email}. Le tue sessioni sono "
            "state terminate; l’accesso e i link per la password passano d’ora in poi dal nuovo indirizzo.\n\n"
            "Se non ti aspettavi questa modifica, contatta subito la tua azienda.\n"
        ),
    ),
    "en": MailText(
        subject="Nexotec DMS: your sign-in address was changed",
        body=(
            "Hello {first_name}\n\n"
            "The e-mail address of your Nexotec account was changed to {new_email}. Your sessions were ended; "
            "sign-in and password links now go through the new address.\n\n"
            "If you did not expect this change, contact your Betrieb at once.\n"
        ),
    ),
}


def email_changed(*, language: str | None, first_name: str, new_email: str) -> MailText:
    text = _pick(_EMAIL_CHANGED, language)
    return MailText(text.subject, text.body.format(first_name=first_name, new_email=new_email))


def password_reset(*, language: str | None, first_name: str, link: str) -> MailText:
    text = _pick(_RESET, language)
    return MailText(text.subject, text.body.format(first_name=first_name, link=link))
