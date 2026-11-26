from django.urls import path

from . import views

urlpatterns = [
    path("", views.chat_home, name="chat"),
    path("chat/send/", views.send, name="chat_send"),
    path("chat/<int:pk>/", views.chat_home, name="chat_thread"),
    path("chat/<int:pk>/send/", views.send, name="chat_reply"),
    path("chat/card/<int:claim_pk>/", views.card, name="research_card"),
    path("my-checks/", views.my_checks, name="my_checks"),
]
