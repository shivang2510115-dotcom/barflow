"""What a password has to be before this application will store it.

The rule that was here was "eight characters", which `password`, `12345678`, `qwerty12`
and `iloveyou` all satisfy — all four were accepted by POST /api/staff against the
previous code. The rule now is length plus a list of the passwords people actually
choose, and deliberately *not* a symbol-and-capital requirement: composition rules do
not produce strong passwords, they produce `Password1!`, and they push people towards
writing the result on the monitor. Length and a denylist are what the current NIST
guidance recommends, and they are what this does.

The list below is short and unapologetic about it. It is not the ten thousand from a
breach corpus — that belongs in a file loaded at startup and is worth doing when someone
is being paid to look after this. It is the passwords that a small hotel's staff account
actually gets set to: the top of every leaked-password chart, and every credential
published in this repository's own README and seed data, because a login whose password
is printed in a public git history is not a login.

The one thing this must not do is change the rule for a password that already exists.
Nothing here runs on login — only where a password is being set — so no existing account
is locked out by tightening it.
"""

# Kept at 8 rather than raised. Every account created since this app existed was allowed
# eight, raising it locks nobody out but does refuse an owner who is trying to change
# their own password to something no worse than the one they already have, and the
# denylist is what actually carries this rule. If it is ever raised, it belongs in the
# same change as a way to tell people why.
MIN_PASSWORD_LENGTH = 8

# Lowercased, compared exactly. Substring matching was considered and dropped: it refuses
# `passageway-heron-42`, which is a fine password, for containing "passage"… and the
# people it would catch are already caught by the exact list.
COMMON_PASSWORDS = frozenset({
    # The top of every leaked-password chart.
    "123456", "12345678", "123456789", "1234567890", "12345", "1234567", "111111",
    "000000", "121212", "123123", "654321", "666666", "888888", "7777777", "1q2w3e4r",
    "qwerty", "qwerty1", "qwerty12", "qwerty123", "qwertyuiop", "qazwsx", "zaq12wsx",
    "asdfgh", "asdfghjk", "zxcvbnm", "abc123", "abcd1234", "aa123456", "a1b2c3d4",
    "password", "password1", "password12", "password123", "passw0rd", "p@ssw0rd",
    "letmein", "letmein123", "welcome", "welcome1", "welcome123", "changeme",
    "trustno1", "iloveyou", "iloveyou1", "sunshine", "princess", "monkey", "dragon",
    "shadow", "master", "superman", "batman", "football", "baseball", "soccer",
    "hockey", "hunter", "ranger", "phoenix", "matrix", "freedom", "whatever",
    "computer", "internet", "starwars", "pokemon", "michael", "jessica", "charlie",
    "jordan", "daniel", "thomas", "robert", "william", "samantha", "michelle",
    "nicole", "ashley", "hannah", "bailey", "ginger", "pepper", "cookie", "cheese",
    "chocolate", "flower", "purple", "yellow", "silver", "summer", "thunder",
    "tigger", "killer", "knight", "ninja", "lovely", "secret", "access", "mustang",
    "harley", "default", "guest123", "root1234", "test1234", "temp1234", "admin1234",
    "administrator",
    # Published in this repository — the seed logins, the default admin, and the words
    # somebody sets up a hotel with at four in the afternoon. A password anyone can read
    # in a public git history is not one.
    "admin123", "manager123", "waiter123", "kitchen123", "desk123",
    "frontdesk", "frontdesk123", "barflow", "barflow123", "barflow1", "hotel123",
    "hotel1234", "reception", "reception123", "restaurant", "supersecret",
    "supersecret1", "supersecret-key-123456789",
})


def password_problem(password: str, email: str | None = None) -> str | None:
    """What is wrong with this password, or None if nothing is.

    Returns the message rather than raising, so that `services/` stays free of HTTP the
    way services/registration.py does; each router turns it into its own 400.

    The messages say what to do rather than what was done wrong — "choose a longer one"
    outperforms "too short" for the same reason error text everywhere else here names
    the next action.
    """
    if password is None:
        password = ""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"

    normalised = password.strip().lower()
    if normalised in COMMON_PASSWORDS:
        return ("That is one of the most commonly used passwords, so it is one of the "
                "first anyone tries. Choose something else.")

    # One character repeated. Long enough to pass the length rule and worth nothing:
    # "aaaaaaaa" and "11111111" are two guesses, not eight characters of entropy.
    if len(set(password)) == 1:
        return "A password of one repeated character is not one. Choose something else."

    if email:
        local = str(email).strip().lower().split("@")[0]
        if normalised in (str(email).strip().lower(), local):
            return ("A password that is your own email address is the first thing "
                    "anyone tries. Choose something else.")

    return None
