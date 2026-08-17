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
]
