import phonenumbers
import hashlib


def valid_phone_number(num: str) -> bool:
    try:
        if phonenumbers.is_valid_number(
            phonenumbers.parse(num, "AU")
        ) or phonenumbers.is_valid_number(phonenumbers.parse("08" + num, "AU")):
            return True
    except phonenumbers.phonenumberutil.NumberParseException:
        return False
    return False


def calculate_circle_emoji(count: int | float, total: int | float) -> str:
    """Return the appropriate circle percentage emoji based on the count and total.

    Rounds down to the nearest 10%
    """

    # Calculate the percentage rounded down to the nearest 10
    try:
        percentage = int(count / total * 10) * 10
    except ZeroDivisionError:
        raise ValueError("Total cannot be 0")

    if percentage > 100:
        percentage = 100

    return f":circle{percentage}:"


def hash_question(question_text: str) -> str:
    """Converts a string into a hash for use as a repeatable but unique action_id"""

    # strip non alphanumeric/space characters
    question_text = "".join(
        char.lower() for char in question_text if char.isalnum() or char.isspace()
    )

    # strip leading/trailing whitespace
    question_text = question_text.strip()

    return hashlib.md5(question_text.encode()).hexdigest()
