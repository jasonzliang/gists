from django.urls import path
from . import views

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('about/', views.AboutView.as_view(), name='about'),
    path('research/', views.ResearchView.as_view(), name='research'),
    path('projects/', views.ProjectListView.as_view(), name='projects'),
    path('team/', views.TeamListView.as_view(), name='team'),
    path('events/', views.EventListView.as_view(), name='events'),
    path('blog/', views.BlogListView.as_view(), name='blog_list'),
    path('blog/<slug:slug>/', views.BlogDetailView.as_view(), name='blog_detail'),
    path('contact/', views.ContactView.as_view(), name='contact'),
]
