"""Public product page, independent of the private tracking workspace."""

from django.contrib.auth.decorators import login_not_required
from django.shortcuts import render
from django.views.decorators.http import require_safe


@login_not_required
@require_safe
def landing(request):
    return render(request, "landing.html")
