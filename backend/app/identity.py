"""Contact email addresses. An email is how someone is reached, never who they are: ownership and sign-off always
follow the directory id (rule B3), so a shared mailbox or a changed address cannot move accountability."""
import re
import unicodedata

EMAIL_DOMAIN = "polibridge.example"  # reserved for documentation; every address in the demo is synthetic
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def email_for(name: str, domain: str = EMAIL_DOMAIN) -> str:
    """first.last@domain, with accents and punctuation removed."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    parts = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in plain.split()]
    parts = [p for p in parts if p]
    return f"{'.'.join([parts[0], parts[-1]] if len(parts) > 1 else parts or ['user'])}@{domain}"


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value or "")) and len(value) <= 254
