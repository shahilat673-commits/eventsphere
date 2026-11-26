from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import *

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (('EventSphere', {'fields': ('role', 'phone', 'organisation', 'is_approved', 'is_verified', 'location', 'interests', 'preferred_type')}),)
    list_display = ('username', 'email', 'role', 'is_approved', 'is_verified')
    list_filter = ('role', 'is_approved')

for m in (Category, Event, TicketType, Booking, Ticket, Feedback, NotificationLog, OTP):
    admin.site.register(m)
