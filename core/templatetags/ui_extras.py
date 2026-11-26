from django import template

register = template.Library()

# keyword -> picture. First match wins (checked against category name + event title).
_KINDS = [
    ('dance', ('dance', 'concert', 'music', 'cultural', 'band', 'live', 'melod', 'festival', 'karaoke', 'dj')),
    ('tech', ('tech', 'hack', 'coding', 'robot', 'ai ', 'software', 'cyber', 'developer')),
    ('workshop', ('workshop', 'training', 'bootcamp', 'seminar', 'course', 'class')),
    ('sports', ('sport', 'cricket', 'football', 'marathon', 'tournament', 'match', 'run')),
    ('conference', ('conference', 'summit', 'meetup', 'expo', 'startup', 'business', 'talk')),
]


@register.filter
def art_kind(event):
    """Pick the banner picture for an event without an uploaded banner."""
    cat = (str(getattr(event, 'category', '') or '')).lower()
    # category name is the strongest hint; fall back to the title
    for text in (cat, (event.title or '').lower() + ' '):
        for kind, words in _KINDS:
            if any(w in text for w in words):
                return kind
    return 'party'


@register.filter
def mask_email(email):
    """john.doe@gmail.com -> jo****@gmail.com"""
    email = email or ''
    if '@' not in email:
        return email
    name, domain = email.split('@', 1)
    return name[:2] + '*' * max(len(name) - 2, 2) + '@' + domain
