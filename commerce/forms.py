from django import forms


class AddToCartForm(forms.Form):
    variant_id = forms.IntegerField(min_value=1)
    quantity = forms.IntegerField(min_value=1, max_value=10, initial=1)


class ChangeCartQuantityForm(forms.Form):
    quantity = forms.IntegerField(min_value=1, max_value=10)
