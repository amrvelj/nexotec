"""KAN-217 PR-5: the letter opening Contacts hands out with the recipient
(`{{customer.salutation}}`; D-15, D-22) — by salutation, never gender, in the
contact's correspondence language."""

import pytest

from app.contact.models.contact import Contact, Language, Salutation
from app.contact.services.letter_salutation import letter_salutation


def _contact(salutation, language=Language.DE, first="Anna", last="Muster", company=None) -> Contact:
    return Contact(salutation=salutation, language=language, first_name=first, last_name=last, company_name=company)


@pytest.mark.parametrize(
    ("salutation", "language", "expected"),
    [
        (Salutation.HERR, Language.DE, "Sehr geehrter Herr Muster"),
        (Salutation.FRAU, Language.DE, "Sehr geehrte Frau Muster"),
        (Salutation.HERR, Language.FR, "Monsieur"),
        (Salutation.FRAU, Language.FR, "Madame"),
        (Salutation.HERR, Language.IT, "Egregio Signor Muster"),
        (Salutation.FRAU, Language.IT, "Gentile Signora Muster"),
        (Salutation.HERR, Language.EN, "Dear Mr Muster"),
        (Salutation.FRAU, Language.EN, "Dear Ms Muster"),
        (Salutation.NEUTRAL, Language.DE, "Guten Tag Anna Muster"),
        (None, Language.FR, "Bonjour Anna Muster"),
        (Salutation.FIRMA, Language.IT, "Gentili Signore e Signori"),
    ],
)
def test_the_opening_follows_the_salutation_in_the_contacts_language(salutation, language, expected):
    assert letter_salutation(_contact(salutation, language)) == expected


def test_a_company_without_a_personal_name_is_greeted_as_a_company():
    assert letter_salutation(_contact(None, Language.EN, None, None, "Muster AG")) == "Dear Sir or Madam"


def test_herr_without_a_last_name_falls_back_to_the_neutral_greeting_not_a_guess():
    assert letter_salutation(_contact(Salutation.HERR, Language.DE, "Alex", None)) == "Guten Tag Alex"
