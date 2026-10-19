import { permanentRedirect } from 'next/navigation';
import { publicPath, type PublicAddress } from './routeKey';

export type RouteQuery = Record<string, string | string[] | undefined>;

/** Old addresses remain valid entry points; preserve all version/locale/deep-link parameters. */
export async function canonicalRoute(
  group: string,
  key: string,
  entity: PublicAddress | undefined,
  searchParams?: Promise<RouteQuery>,
) {
  if (!entity?.slug) return;
  const destination = publicPath(group, entity);
  if (destination === `/${group}/${encodeURIComponent(key)}`) return;
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries((await searchParams) ?? {})) {
    for (const entry of Array.isArray(value) ? value : value === undefined ? [] : [value])
      query.append(name, entry);
  }
  permanentRedirect(destination + (query.size ? `?${query}` : ''));
}
