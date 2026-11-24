"""The letter opening of a message to a contact — `{{customer.salutation}}`
(KAN-217 PR-5; PRD-Contacts D-15, D-22: «drives the letter opening in the
contact's language»).

Contacts owns the salutation and the name, so the opening is formed here and
handed out with the recipient (`message_recipient`); no module above forms it
from its own copy. It follows `salutation` only — never `gender` (D-22) — in
the contact's correspondence language (D-01):

- `herr` / `frau` with a last name: «Sehr geehrter Herr Muster», «Monsieur»,
  «Egregio Signor Rossi», «Dear Mr Smith» (French addresses without the name,
  as a French letter does);
- `firma`, and any contact without a personal name: «Sehr geehrte Damen und
  Herren», «Madame, Monsieur», «Gentili Signore e Signori», «Dear Sir or
  Madam»;
- `neutral`, none, or `herr` / `frau` without a last name: the full name with
  a neutral greeting, «Guten Tag Alex Muster» (D-22: neutral rather than a
  guess).

The wording matches the editor's preview samples (`sample_values`). A
dealer's own text may avoid the token altogether.
"""

from app.contact.models.contact import Contact, Language, Salutation

_HERR = {
    Language.DE: "Sehr geehrter Herr {last}",
    Language.FR: "Monsieur",
    Language.IT: "Egregio Signor {last}",
    Language.EN: "Dear Mr {last}",
}
_FRAU = {
    Language.DE: "Sehr geehrte Frau {last}",
    Language.FR: "Madame",
    Language.IT: "Gentile Signora {last}",
    Language.EN: "Dear Ms {last}",
}
_COMPANY = {
    Language.DE: "Sehr geehrte Damen und Herren",
    Language.FR: "Madame, Monsieur",
    Language.IT: "Gentili Signore e Signori",
    Language.EN: "Dear Sir or Madam",
}
_NEUTRAL = {
    Language.DE: "Guten Tag {name}",
    Language.FR: "Bonjour {name}",
    Language.IT: "Buongiorno {name}",
    Language.EN: "Dear {name}",
}


def letter_salutation(contact: Contact, language: Language | None = None) -> str:
    """The opening in `language`, by default the contact's own. A dealer's
    text may exist only in another language (the library falls back), and
    the opening then follows the text, so a message never mixes two."""

    if language is None:
        language = contact.language if isinstance(contact.language, Language) else Language(contact.language)
    last = (contact.last_name or "").strip()
    name = " ".join(part.strip() for part in (contact.first_name, contact.last_name) if part and part.strip())
    if contact.salutation == Salutation.FIRMA or not name:
        return _COMPANY[language]
    if last and contact.salutation == Salutation.HERR:
        return _HERR[language].format(last=last)
    if last and contact.salutation == Salutation.FRAU:
        return _FRAU[language].format(last=last)
    return _NEUTRAL[language].format(name=name)
