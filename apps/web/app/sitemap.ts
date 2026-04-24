import { MetadataRoute } from 'next'
import { eventService } from '@/lib/api'

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const baseUrl = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000'

  let eventRoutes: MetadataRoute.Sitemap = []
  try {
    // Fetch up to 1000 latest events for the sitemap
    const { items } = await eventService.getEvents({ limit: 1000 })
    eventRoutes = items.map((event) => ({
      url: `${baseUrl}/events/${event.slug}`,
      lastModified: new Date(event.updated_at || event.start_at),
      changeFrequency: 'daily',
      priority: 0.8,
    }))
  } catch (e) {
    console.error('Failed to generate event sitemaps', e)
  }

  const staticRoutes: MetadataRoute.Sitemap = [
    {
      url: `${baseUrl}`,
      lastModified: new Date(),
      changeFrequency: 'hourly',
      priority: 1,
    },
    {
      url: `${baseUrl}/events`,
      lastModified: new Date(),
      changeFrequency: 'hourly',
      priority: 0.9,
    },
    {
      url: `${baseUrl}/calendar`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 0.8,
    },
    {
      url: `${baseUrl}/submit`,
      lastModified: new Date(),
      changeFrequency: 'weekly',
      priority: 0.5,
    },
    {
      url: `${baseUrl}/about`,
      lastModified: new Date(),
      changeFrequency: 'monthly',
      priority: 0.5,
    },
  ]

  return [...staticRoutes, ...eventRoutes]
}
