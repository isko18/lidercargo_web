# # users/signals.py
# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from .models import User

# @receiver(post_save, sender=User)
# def generate_code_on_create(sender, instance: User, created, **kwargs):
#     if created and not instance.client_code:
#         instance.assign_client_code()


from django.db.models.signals import pre_save
from django.dispatch import receiver
from .models import User

@receiver(pre_save, sender=User)
def assign_client_code_before_save(sender, instance, **kwargs):
    # только для новых юзеров (ещё нет client_code)
    if not instance.client_code:
        instance.assign_client_code(save=False)  # без повторного save()
