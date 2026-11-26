from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from .models import Event, TicketType, User, Category, Feedback, Coupon

DT = '%Y-%m-%dT%H:%M'


class BootstrapMixin:
    def _style(self):
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxSelectMultiple):
                continue
            if isinstance(w, forms.CheckboxInput):
                w.attrs['class'] = 'form-check-input'
                continue
            w.attrs['class'] = ('form-select' if isinstance(w, forms.Select) else 'form-control')


class LoginForm(BootstrapMixin, AuthenticationForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self._style()


PHONE_ATTRS = {'maxlength': '10', 'inputmode': 'numeric', 'pattern': '[0-9]{10}', 'placeholder': '10-digit mobile number',
               'autocomplete': 'tel-national'}


class RegisterForm(BootstrapMixin, UserCreationForm):
    role = forms.ChoiceField(choices=[('ATTENDEE', 'Attendee (book events)'), ('ORGANISER', 'Event Organiser (needs admin approval)')])
    email = forms.EmailField(required=True, help_text='Your OTP will be sent to this email - use a real, working address.')
    phone = forms.RegexField(r'^\d{10}$', max_length=10, label='Phone',
                             widget=forms.TextInput(attrs=PHONE_ATTRS),
                             error_messages={'invalid': 'Enter exactly 10 digits (numbers only).'})
    organisation = forms.CharField(required=False, label='College / Company / Club (organisers)')

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'phone', 'role', 'organisation')

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self._style()

    def clean_email(self):
        e = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=e).exists():
            raise forms.ValidationError('This email is already registered.')
        return e

    def clean(self):
        d = super().clean()
        if d.get('role') == 'ORGANISER' and not d.get('organisation'):
            self.add_error('organisation', 'Organisers must enter their college/company/club name.')
        return d


class ProfileForm(BootstrapMixin, forms.ModelForm):
    interests = forms.ModelMultipleChoiceField(queryset=Category.objects.all(), required=False,
                                               widget=forms.CheckboxSelectMultiple)
    phone = forms.RegexField(r'^\d{10}$', max_length=10, required=False, label='Phone',
                             widget=forms.TextInput(attrs=PHONE_ATTRS),
                             error_messages={'invalid': 'Enter exactly 10 digits (numbers only).'})

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'phone', 'location', 'preferred_type', 'interests')

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self._style()


class EventForm(BootstrapMixin, forms.ModelForm):
    start = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format=DT), input_formats=[DT])
    end = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format=DT), input_formats=[DT])

    class Meta:
        model = Event
        fields = ('title', 'category', 'description', 'banner', 'venue', 'city', 'start', 'end', 'speakers',
                  'seat_limit', 'seating_enabled', 'seats_per_row', 'parking_info', 'emergency_contact', 'faq')
        widgets = {'description': forms.Textarea(attrs={'rows': 4}), 'faq': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self._style()

    def clean(self):
        d = super().clean()
        if d.get('start') and d.get('end') and d['end'] <= d['start']:
            self.add_error('end', 'End time must be after start time.')
        # ----- reserved seating rules -----
        if d.get('seating_enabled'):
            per, limit = d.get('seats_per_row') or 10, d.get('seat_limit') or 0
            if not 4 <= per <= 20:
                self.add_error('seats_per_row', 'Seats per row must be between 4 and 20.')
            elif limit > 500:
                self.add_error('seat_limit', 'Reserved-seating events support up to 500 seats. Use common entry for bigger events.')
            elif self.instance.pk:
                taken = self.instance.taken_seats()
                new_labels = set(Event(seat_limit=limit, seats_per_row=per).all_seat_labels())
                if taken and not taken <= new_labels:
                    self.add_error('seat_limit', 'Some seats are already booked - you cannot shrink the seat plan below them.')
        if self.instance.pk and 'seating_enabled' in self.changed_data and self.instance.seats_sold:
            self.add_error('seating_enabled', 'Tickets are already sold, so you cannot switch between reserved and common seating.')
        return d


class TicketTypeForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TicketType
        fields = ('name', 'kind', 'price', 'quantity')

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self._style()

    def clean(self):
        d = super().clean()
        if d.get('kind') == 'FREE':
            d['price'] = 0
        return d


class FeedbackForm(BootstrapMixin, forms.ModelForm):
    rating = forms.IntegerField(min_value=1, max_value=5)

    class Meta:
        model = Feedback
        fields = ('rating', 'comment')
        widgets = {'comment': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self._style()


class CouponForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Coupon
        fields = ('code', 'percent', 'max_uses')

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self._style()
        self.fields['code'].widget.attrs['placeholder'] = 'CODE e.g. EARLY20'

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()

    def clean_percent(self):
        p = self.cleaned_data['percent']
        if not 1 <= p <= 100:
            raise forms.ValidationError('Percent must be 1-100.')
        return p
