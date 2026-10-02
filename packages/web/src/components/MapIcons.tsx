type MapObject = { category: string; metadata: { icon_source?: string } };

type Icons = Record<string, { url: string }> | undefined;

const symbols: Record<string, string> = {
  chest: 'M3 9h18v12H3z M3 9l3-6h12l3 6 M10 12h4v4h-4z',

  resource: 'M12 21V10 M12 15C3 16 3 5 3 5c9 0 10 5 9 10 M12 18c9 0 9-10 9-10-8 0-9 5-9 10',

  collectible: 'M12 2l3 7 7 3-7 3-3 7-3-7-7-3 7-3z',

  teleport: 'M12 2l8 10-8 10-8-10z M8 12h8 M12 8v8',

  monster: 'M5 5l4 2h6l4-2v10l-7 6-7-6z M8 11h2 M14 11h2 M10 16h4',

  boss: 'M4 4l6 6 M20 4l-6 6 M5 20l15-15 M4 16l4 4 M16 20L4 8 M16 4l4 4',

  shop: 'M3 8l2-5h14l2 5v4H3z M5 12v9h14v-9 M9 21v-6h6v6',

  tacet_field: 'M12 3v18 M3 12h18 M5 5l14 14 M19 5L5 19',

  exploration: 'M12 2l8 5v10l-8 5-8-5V7z M12 7v6 M12 16v1',

  activity: 'M9 3l-4 9h6l-2 9 10-13h-7l2-5z',

  treasure_spot: 'M3 4l6-2 6 2 6-2v18l-6 2-6-2-6 2z M9 2v18 M15 4v18',
};

export function iconNode(marker: MapObject, icons: Icons): HTMLElement {
  const container = document.createElement('span');

  container.className = 'atlas-object-icon';

  const url = icons?.[marker.metadata.icon_source ?? '']?.url;

  if (url) {
    const image = document.createElement('img');

    image.src = url;

    image.alt = '';

    image.style.width = '26px';

    image.style.height = '26px';

    image.style.objectFit = 'contain';

    container.append(image);
  } else {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');

    svg.setAttribute('viewBox', '0 0 24 24');

    svg.setAttribute('fill', 'none');

    svg.setAttribute('stroke', '#c6dbdb');

    svg.setAttribute('stroke-width', '1.7');

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');

    path.setAttribute('d', symbols[marker.category] ?? symbols.exploration);

    svg.append(path);

    container.append(svg);
  }

  return container;
}

export function ObjectIcon({
  marker,

  icons,
}: {
  marker: MapObject;

  icons: Icons;
}) {
  const url = icons?.[marker.metadata.icon_source ?? '']?.url;

  return url ? (
    <img className="atlas-item-icon" src={url} alt="" loading="lazy" />
  ) : (
    <svg
      className="atlas-item-icon"

      viewBox="0 0 24 24"

      fill="none"

      stroke="currentColor"

      strokeWidth="1.7"

      aria-hidden="true"
    >
      <path d={symbols[marker.category] ?? symbols.exploration} />
    </svg>
  );
}
