from django.urls import path

from . import views

urlpatterns = [
    path("", views.chat_home, name="chat"),
    path("send/", views.send, name="chat_send"),
    path("<int:pk>/", views.chat_home, name="chat_thread"),
    path("<int:pk>/send/", views.send, name="chat_reply"),
    path("card/<int:claim_pk>/", views.card, name="research_card"),
]
