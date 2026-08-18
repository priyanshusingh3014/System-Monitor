from django.urls import path
from . import views

app_name = 'monitor'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/report/', views.api_report, name='api_report'),
    path('api/agents/', views.api_agents, name='api_agents'),
    path('api/agents/delete/<str:agent_id>/', views.api_delete_agent, name='api_delete_agent'),
    path('api/agents/uninstall/', views.api_uninstall_agent, name='api_uninstall_agent'),
    path('api/activities/create/', views.api_trigger_activity, name='api_trigger_activity'),
    path('api/activities/clear/', views.api_clear_activities, name='api_clear_activities'),
    # File upload, download, delete-notify, and listing
    path('api/files/upload/', views.api_file_upload, name='api_file_upload'),
    path('api/files/delete-notify/', views.api_file_delete_notify, name='api_file_delete_notify'),
    path('api/files/download/<int:file_id>/', views.api_file_download, name='api_file_download'),
    path('api/files/', views.api_file_list, name='api_file_list'),
]
