def seo_context(request):
    return {
        'site_name': 'Rome AI Initiative',
        'site_url': request.build_absolute_uri('/').rstrip('/'),
        'meta_description': 'Rome AI Initiative is leading AI research and innovation in the heart of Rome, connecting ancient wisdom with cutting-edge technology.',
        'meta_keywords': 'artificial intelligence, Rome, AI research, machine learning, deep learning, Italian AI, innovation',
        'og_image': request.build_absolute_uri('/static/rome_ai_app/img/og-image.jpg'),
    }
