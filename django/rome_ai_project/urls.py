from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from rome_ai_app.views import robots_txt  # ADD THIS LINE
from rome_ai_app.sitemaps import (
    StaticViewSitemap,
    BlogSitemap,
    ProjectSitemap,
    EventSitemap
)

sitemaps = {
    'static': StaticViewSitemap,
    'blog': BlogSitemap,
    'projects': ProjectSitemap,
    'events': EventSitemap,
}

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('rome_ai_app.urls')),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps},
         name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', robots_txt, name='robots_txt'),  # Now this will work
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
