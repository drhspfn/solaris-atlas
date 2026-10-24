'use client';

import NextLink from 'next/link';
import { usePathname, useRouter, useSearchParams as useNextSearchParams } from 'next/navigation';
import React, { forwardRef } from 'react';

export interface LinkProps extends Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to?: string | { pathname?: string; search?: string; hash?: string };
  href?: string;
  replace?: boolean;
  scroll?: boolean;
  prefetch?: boolean;
  className?: string;
  children?: React.ReactNode;
  state?: any;
}

// Archive source lists can contain hundreds of links; fetch detail routes on navigation.
export const Link = forwardRef<HTMLAnchorElement, LinkProps>(function Link(
  { to, href, children, prefetch = false, ...props },
  ref,
) {
  const destination =
    (href ?? (typeof to === 'object' ? `${to.pathname || ''}${to.search || ''}${to.hash || ''}` : to)) ||
    '/';
  return (
    <NextLink ref={ref} href={destination} prefetch={prefetch} {...props}>
      {children}
    </NextLink>
  );
});

export interface NavLinkProps extends Omit<LinkProps, 'className'> {
  className?: string | ((props: { isActive: boolean }) => string);
  end?: boolean;
}

export const NavLink = forwardRef<HTMLAnchorElement, NavLinkProps>(function NavLink(
  { to, href, className, end, children, prefetch = false, ...props },
  ref,
) {
  const pathname = usePathname();
  const destination =
    (href ?? (typeof to === 'object' ? `${to.pathname || ''}${to.search || ''}${to.hash || ''}` : to)) ||
    '/';

  const isActive = end ? pathname === destination : pathname === destination || pathname.startsWith(`${destination}/`);

  const computedClassName = typeof className === 'function' ? className({ isActive }) : className;

  return (
    <NextLink ref={ref} href={destination} className={computedClassName} prefetch={prefetch} {...props}>
      {children}
    </NextLink>
  );
});

export function useNavigate() {
  const router = useRouter();
  return (to: string, options?: { replace?: boolean }) => {
    if (options?.replace) {
      router.replace(to);
    } else {
      router.push(to);
    }
  };
}

export function useLocation() {
  const pathname = usePathname() || '/';
  const searchParams = useNextSearchParams();
  const search = searchParams?.toString() ? `?${searchParams.toString()}` : '';
  return {
    pathname,
    search,
    key: `${pathname}${search}`,
  };
}

export function Outlet({ children }: { children?: React.ReactNode }) {
  return <>{children}</>;
}

export type SetURLSearchParams = (
  updater:
    | URLSearchParams
    | Record<string, string | number | boolean | undefined | null>
    | ((prev: URLSearchParams) => URLSearchParams | void),
  options?: { replace?: boolean },
) => void;

export type CustomSearchParams = URLSearchParams & [URLSearchParams, SetURLSearchParams];

export function useSearchParams(): CustomSearchParams {
  const router = useRouter();
  const pathname = usePathname() || '';
  const searchParams = useNextSearchParams();
  const current = (searchParams || new URLSearchParams()) as URLSearchParams;

  const setSearchParams: SetURLSearchParams = React.useCallback(
    (updater, options) => {
      const params = new URLSearchParams(searchParams ? searchParams.toString() : '');
      let nextQs = '';

      if (typeof updater === 'function') {
        const res = updater(params);
        const target = res instanceof URLSearchParams ? res : params;
        nextQs = target.toString();
      } else if (updater instanceof URLSearchParams) {
        nextQs = updater.toString();
      } else if (updater && typeof updater === 'object') {
        const next = new URLSearchParams();
        for (const [k, v] of Object.entries(updater)) {
          if (v !== undefined && v !== null && v !== '') {
            next.set(k, String(v));
          }
        }
        nextQs = next.toString();
      }

      const url = nextQs ? `${pathname}?${nextQs}` : pathname;
      if (options?.replace) {
        router.replace(url);
      } else {
        router.push(url);
      }
    },
    [router, pathname, searchParams],
  );

  const tuple = [current, setSearchParams] as any;
  tuple.get = (name: string) => current.get(name);
  tuple.getAll = (name: string) => current.getAll(name);
  tuple.has = (name: string) => current.has(name);
  tuple.toString = () => current.toString();

  return tuple as CustomSearchParams;
}


