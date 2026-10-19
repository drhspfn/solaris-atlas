import type { MetadataRoute } from 'next';
import { serverApi } from '@/api/serverApi';
import { entityPath, type Entity } from '@/data/entities';

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';
  const now = new Date();

  const staticRoutes: MetadataRoute.Sitemap = [
    { url: `${siteUrl}/legal`, lastModified: new Date('2026-10-19'), changeFrequency: 'monthly', priority: 0.3 },
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
    const groups = await Promise.all(['character', 'quest', 'item', 'location'].map(async (category) => {
      const entries: Entity[] = [];
      // Stay within the API's page limit and the 50,000 URL sitemap limit.
      for (let offset = 0; offset < 12000; offset += 100) {
        const page = await serverApi<{ results: Entity[]; total: number }>(
          `/catalog?category=${category}&locale=en&limit=100&offset=${offset}`, { revalidate: 3600 });
        if (!page?.results.length) break;
        entries.push(...page.results);
        if (page.results.length < 100 || entries.length >= page.total) break;
      }
      return entries;
    }));
    const dynamicRoutes: MetadataRoute.Sitemap = groups.flat().map((item) => ({
      url: `${siteUrl}${entityPath(item)}`,
      lastModified: now,
      changeFrequency: 'weekly',
      priority: 0.7,
    }));

    return [...staticRoutes, ...dynamicRoutes];
  } catch {
    return staticRoutes;
  }
}
