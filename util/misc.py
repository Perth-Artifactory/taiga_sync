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
