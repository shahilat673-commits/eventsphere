from .models import SOSAlert


def sos(request):
    u = request.user
    if not u.is_authenticated:
        return {}
    if u.role == 'ADMIN':
        return {'sos_open': SOSAlert.objects.exclude(status='RESOLVED').count()}
    if u.role == 'ORGANISER':
        return {'sos_open': SOSAlert.objects.filter(event__organiser=u).exclude(status='RESOLVED').count()}
    return {}
