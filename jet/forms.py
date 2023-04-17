import json
import operator
from functools import reduce

from django import forms
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Q
from jet.models import Bookmark, PinnedApplication
from jet.utils import get_model_instance_label, user_is_authenticated


try:
    from django.apps import apps
    get_model = apps.get_model
except ImportError:
    from django.db.models.loading import get_model


class AddBookmarkForm(forms.ModelForm):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(AddBookmarkForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Bookmark
        fields = ['url', 'title']

    def clean(self):
        data = super(AddBookmarkForm, self).clean()
        if not user_is_authenticated(self.request.user) or not self.request.user.is_staff:
            raise ValidationError('error')
        if not self.request.user.has_perm('jet.change_bookmark'):
            raise ValidationError('error')
        return data

    def save(self, commit=True):
        self.instance.user = self.request.user
        return super(AddBookmarkForm, self).save(commit)


class RemoveBookmarkForm(forms.ModelForm):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(RemoveBookmarkForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Bookmark
        fields = []

    def clean(self):
        data = super(RemoveBookmarkForm, self).clean()
        if not user_is_authenticated(self.request.user) or not self.request.user.is_staff:
            raise ValidationError('error')
        if self.instance.user != self.request.user:
            raise ValidationError('error')
        return data

    def save(self, commit=True):
        if commit:
            self.instance.delete()


class ToggleApplicationPinForm(forms.ModelForm):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(ToggleApplicationPinForm, self).__init__(*args, **kwargs)

    class Meta:
        model = PinnedApplication
        fields = ['app_label']

    def clean(self):
        data = super(ToggleApplicationPinForm, self).clean()
        if not user_is_authenticated(self.request.user) or not self.request.user.is_staff:
            raise ValidationError('error')
        return data

    def save(self, commit=True):
        if commit:
            try:
                pinned_app = PinnedApplication.objects.get(
                    app_label=self.cleaned_data['app_label'],
                    user=self.request.user
                )
                pinned_app.delete()
                return False
            except PinnedApplication.DoesNotExist:
                PinnedApplication.objects.create(
                    app_label=self.cleaned_data['app_label'],
                    user=self.request.user
                )
                return True


class ModelLookupForm(forms.Form):
    app_label = forms.CharField()
    model = forms.CharField()
    q = forms.CharField(required=False)
    page = forms.IntegerField(required=False)
    page_size = forms.IntegerField(required=False, min_value=1, max_value=1000)
    object_id = forms.IntegerField(required=False)
    model_cls = None

    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(ModelLookupForm, self).__init__(*args, **kwargs)

    def clean(self):
        data = super(ModelLookupForm, self).clean()

        if not user_is_authenticated(self.request.user) or not self.request.user.is_staff:
            raise ValidationError('error')

        try:
            self.model_cls = get_model(data['app_label'], data['model'])
        except:
            raise ValidationError('error')

        content_type = ContentType.objects.get_for_model(self.model_cls)

        # User needs view or change permission on the object
        permissions = Permission.objects.filter(
            Q(
                codename__startswith='change_'
            ) | Q(
                codename__startswith='view_'
            ), content_type=content_type
        )

        permission_granted = False
        for permission in permissions:
            if self.request.user.has_perm(f"{data['app_label']}.{permission.codename}"):
                permission_granted = True
                break

        if not permission_granted:
            raise ValidationError(f'Permission denied for {self.model_cls} to the current user.')

        return data

    def lookup(self, user=None):
        if 'q' in self.cleaned_data:
            if hasattr(self.model_cls, 'autocomplete_search_fields') and hasattr(
                self.model_cls,
                'autocomplete_search_query'
            ):
                raise NotImplementedError(
                    f'The model {self.model_cls} cannot have both an autocomplete_search_fields '
                    f'and autocomplete_search_query function. Make up your mind.'
                )
            elif hasattr(self.model_cls, 'autocomplete_search_query'):
                qs = self.model_cls.autocomplete_search_query(self.cleaned_data['q'],
                    user).distinct()
            elif hasattr(self.model_cls, 'autocomplete_search_fields'):
                qs = self.model_cls.objects
                search_fields = self.model_cls.autocomplete_search_fields()
                filter_data = [
                    Q((field + '__icontains', self.cleaned_data['q']))
                    for field in search_fields
                ]
                qs = qs.filter(reduce(operator.or_, filter_data)).distinct()
            else:
                qs = self.model_cls.objects.none()
        else:
            qs = self.model_cls.objects.none()
        limit = self.cleaned_data['page_size'] or 100
        page = self.cleaned_data['page'] or 1
        offset = (page - 1) * limit
        items = qs[offset:offset + limit]
        # Optional post-processing in Python.
        if hasattr(self.model_cls, 'autocomplete_search_filter'):
            items = self.model_cls.autocomplete_search_filter(items)
            # Total query count not known in case of post-processing in Python.
            count = None
        else:
            count = qs.count()
        items = list(
            map(
                lambda instance: {
                    'id': instance.pk,
                    'text': get_model_instance_label(instance)
                }, items
            )
        )
        return items, count
