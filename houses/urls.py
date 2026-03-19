from django.urls import path
from . import views

app_name = "houses"
urlpatterns = [
    path("", views.HouseListView.as_view(), name="list"),
    path("<int:pk>/", views.HouseDetailView.as_view(), name="detail"),
    path("create/", views.HouseCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.HouseUpdateView.as_view(), name="edit"),
]
