import type { MetadataRoute } from 'next';
import { serverApi } from '@/api/serverApi';
import type { Entity } from '@/data/entities';

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';
  const now = new Date();

  const staticRoutes: MetadataRoute.Sitemap = [
    {
      url: `${siteUrl}/`,
      lastModified: now,
      changeFrequency: 'daily',
      priority: 1.0,
    },
    {
      url: `${siteUrl}/map`,
      lastModified: now,
      changeFrequency: 'weekly',
      priority: 0.9,
    },
    {
      url: `${siteUrl}/story-map`,
      lastModified: now,
      changeFrequency: 'weekly',
      priority: 0.9,
    },
    {
      url: `${siteUrl}/events`,
      lastModified: now,
      changeFrequency: 'daily',
      priority: 0.9,
    },
    {
      url: `${siteUrl}/catalog/character`,
      lastModified: now,
      changeFrequency: 'weekly',
      priority: 0.8,
    },
    {
      url: `${siteUrl}/catalog/quest`,
      lastModified: now,
      changeFrequency: 'weekly',
      priority: 0.8,
    },
    {
      url: `${siteUrl}/catalog/location`,
      lastModified: now,
      changeFrequency: 'weekly',
      priority: 0.8,
    },
    {
      url: `${siteUrl}/catalog/item`,
      lastModified: now,
      changeFrequency: 'weekly',
      priority: 0.8,
    },
    {
      url: `${siteUrl}/mcp-docs`,
      lastModified: now,
      changeFrequency: 'monthly',
      priority: 0.6,
    },
  ];

  try {
    // Try to fetch character and quest slugs for rich sitemap coverage
    const [characters, quests] = await Promise.all([
      serverApi<{ results: Entity[] }>('/catalog?category=character&limit=500&offset=0', { revalidate: 3600 }),
      serverApi<{ results: Entity[] }>('/catalog?category=quest&limit=500&offset=0', { revalidate: 3600 }),
    ]);

    const dynamicRoutes: MetadataRoute.Sitemap = [];

    if (characters?.results) {
      for (const item of characters.results) {
        dynamicRoutes.push({
          url: `${siteUrl}/characters/${encodeURIComponent(item.canonical_key)}`,
          lastModified: now,
          changeFrequency: 'weekly',
          priority: 0.7,
        });
      }
    }

    if (quests?.results) {
      for (const item of quests.results) {
        const questId = item.canonical_key.split(':').at(-1);
        if (questId) {
          dynamicRoutes.push({
            url: `${siteUrl}/quests/${encodeURIComponent(questId)}`,
            lastModified: now,
            changeFrequency: 'weekly',
            priority: 0.7,
          });
        }
      }
    }

    return [...staticRoutes, ...dynamicRoutes];
  } catch {
    return staticRoutes;
  }
}
