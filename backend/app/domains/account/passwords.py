import secrets
import string

ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0Oo1lI")


def generate_password() -> str:
    while True:
        password = "".join(secrets.choice(ALPHABET) for _ in range(12))
        if any(c.isalpha() for c in password) and any(c.isdigit() for c in password):
            return password
