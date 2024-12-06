import phonenumbers


def valid_phone_number(num: str) -> bool:
    try:
        if phonenumbers.is_valid_number(phonenumbers.parse(num, "AU")):
            return True
        elif phonenumbers.is_valid_number(phonenumbers.parse("08" + num, "AU")):
            return True
    except phonenumbers.phonenumberutil.NumberParseException:
        return False
    return False


def calculate_circle_emoji(count, total) -> str:
    """Return the appropriate circle percentage emoji based on the count and total."""

    # Calculate the percentage rounded down to the nearest 10
    percentage = int(count / total * 10) * 10

    # We don't have a 0% right now
    if percentage == 0:
        percentage = 10

    return f":circle{percentage}:"
