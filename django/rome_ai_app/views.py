from django.conf import settings
from django.contrib import messages
from django.contrib.sites.models import Site
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_http_methods
from django.views.generic import ListView, DetailView, TemplateView, FormView

from .forms import ContactForm
from .models import BlogPost, Team, Project, Event, Category

@require_http_methods(["GET"])
def robots_txt(request):
    site = Site.objects.get(pk=settings.SITE_ID)
    domain = site.domain

    lines = [
        "User-agent: *",
        "Allow: /",
        "",
        "Disallow: /admin/",
        "",
        f"Sitemap: https://{domain}/sitemap.xml",
    ]

    return HttpResponse("\n".join(lines), content_type="text/plain")

class IndexView(TemplateView):
    template_name = 'rome_ai_app/index.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['latest_posts'] = BlogPost.objects.filter(
            is_published=True, published_at__lte=timezone.now()
        ).order_by('-published_at')[:3]
        context['featured_projects'] = Project.objects.filter(
            is_active=True
        ).order_by('-start_date')[:3]
        context['upcoming_events'] = Event.objects.filter(
            date__gte=timezone.now()
        ).order_by('date')[:3]
        return context

class AboutView(TemplateView):
    template_name = 'rome_ai_app/about.html'

class ResearchView(TemplateView):
    template_name = 'rome_ai_app/research.html'

class TeamListView(ListView):
    model = Team
    template_name = 'rome_ai_app/team.html'
    context_object_name = 'team_members'

class ProjectListView(ListView):
    model = Project
    template_name = 'rome_ai_app/projects.html'
    context_object_name = 'projects'
    
    def get_queryset(self):
        return Project.objects.filter(is_active=True).order_by('-start_date')

class EventListView(ListView):
    model = Event
    template_name = 'rome_ai_app/events.html'
    context_object_name = 'events'
    
    def get_queryset(self):
        return Event.objects.filter(date__gte=timezone.now()).order_by('date')

class BlogListView(ListView):
    model = BlogPost
    template_name = 'rome_ai_app/blog/list.html'
    context_object_name = 'posts'
    paginate_by = 10
    
    def get_queryset(self):
        return BlogPost.objects.filter(
            is_published=True, published_at__lte=timezone.now()
        ).order_by('-published_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = Category.objects.all()
        context['recent_posts'] = BlogPost.objects.filter(
            is_published=True, published_at__lte=timezone.now()
        ).order_by('-published_at')[:5]
        return context

class BlogDetailView(DetailView):
    model = BlogPost
    template_name = 'rome_ai_app/blog/detail.html'
    context_object_name = 'post'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = Category.objects.all()
        context['recent_posts'] = BlogPost.objects.filter(
            is_published=True, published_at__lte=timezone.now()
        ).exclude(pk=self.object.pk).order_by('-published_at')[:5]
        return context

class ContactView(View):
    template_name = 'rome_ai_app/contact.html'
    
    def get(self, request, *args, **kwargs):
        form = ContactForm()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request, *args, **kwargs):
        form = ContactForm(request.POST)
        if form.is_valid():
            # Process the form data (in a real app, you might send an email here)
            name = form.cleaned_data['name']
            email = form.cleaned_data['email']
            subject = form.cleaned_data['subject']
            message = form.cleaned_data['message']
            
            # For now, just add a success message
            messages.success(request, f"Thank you {name}! Your message has been received. We'll get back to you shortly.")
            return redirect('contact')
        
        return render(request, self.template_name, {'form': form})
