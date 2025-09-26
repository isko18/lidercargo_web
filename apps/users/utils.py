# apps/users/utils.py
import random
import string

def generate_unique_code(model, field_name="client_code", length=8):
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(chars, k=length))
        if not model.objects.filter(**{field_name: code}).exists():
            return code
